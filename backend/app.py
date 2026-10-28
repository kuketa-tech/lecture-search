"""HTTP API и раздача фронтенда."""
import json
import re
import shutil
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db, media, storage
from .jobs import WORKER
from .ml import course, fragtypes, llm, models, qa, search, text
from .ml.index import INDEX

ALLOWED_EXT = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v", ".mp3", ".m4a", ".wav", ".ogg", ".flac"}


def _warmup():
    """Модели поиска грузятся в фоне при старте, чтобы первый запрос не ждал 20 секунд."""
    try:
        models.embedder().query("прогрев")
        r = models.reranker()
        if r:
            r.score("прогрев", ["прогрев"])
        print(f"[app] модели готовы, устройство: {models.device()}")
        if llm.status()["kind"] == "local":  # иначе первый ответ в чате ждёт загрузку LLM ~20 с
            llm.chat("Ответь одним словом: готов?", max_tokens=3)
            print("[app] локальная LLM загружена")
    except Exception as e:
        print(f"[app] не удалось загрузить модели: {e}")


@asynccontextmanager
async def lifespan(_app):
    db.init()
    INDEX.reload()
    WORKER.start()
    threading.Thread(target=_warmup, daemon=True).start()
    yield


app = FastAPI(title="Суть — семантический поиск по видеолекциям", lifespan=lifespan)


@app.middleware("http")
async def no_stale_frontend(request: Request, call_next):
    """Фронтенд без сборки и без версий в именах файлов: просим браузер каждый раз сверяться с сервером
    (ответ 304 почти бесплатный), иначе после обновления он мешает старый styles.css с новым app.js."""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or (path.startswith("/static/") and not path.startswith("/static/vendor/")):
        response.headers["Cache-Control"] = "no-cache"
    return response


def _lecture_or_404(lecture_id: str) -> dict:
    lec = db.one("SELECT * FROM lectures WHERE id = ?", (lecture_id,))
    if lec is None:
        raise HTTPException(404, "Лекция не найдена")
    return lec


def _public(lec: dict) -> dict:
    d = config.LECTURES_DIR / lec["id"]
    out = {k: lec[k] for k in ("id", "title", "source_type", "source_url", "duration", "status", "stage", "progress",
                               "searchable", "error", "llm", "created_at", "processed_at")}
    out["timings"] = db.loads(lec.get("timings"), {})
    out["thumb"] = f"/api/lectures/{lec['id']}/thumb" if (d / "thumb.jpg").exists() else None
    out["queue"] = WORKER.queue_position(lec["id"]) if lec["status"] == "queued" else 0
    return out


# ---------- лекции ----------

@app.get("/api/lectures")
def list_lectures():
    rows = db.query("SELECT * FROM lectures ORDER BY created_at DESC")
    counts = {r["lecture_id"]: r for r in db.query(
        "SELECT lecture_id, count(*) AS sections FROM sections WHERE level = 1 GROUP BY lecture_id")}
    out = []
    for r in rows:
        item = _public(r)
        item["sections"] = counts.get(r["id"], {}).get("sections", 0)
        out.append(item)
    return out


@app.post("/api/lectures")
def upload_lecture(file: UploadFile = File(...), title: str = Form("")):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Формат {ext or 'без расширения'} не поддерживается. Подходят: MP4, WebM, MKV, MOV, MP3, WAV")
    lecture_id = uuid.uuid4().hex[:12]
    dst = storage.lecture_dir(lecture_id) / f"source{ext}"
    with dst.open("wb") as f:
        shutil.copyfileobj(file.file, f, length=4 * 1024 * 1024)
    if dst.stat().st_size == 0:
        storage.remove(lecture_id)
        raise HTTPException(400, "Файл пустой")
    name = title.strip() or Path(file.filename).stem.replace("_", " ")
    db.execute("INSERT INTO lectures (id, title, source_type, filename, created_at) VALUES (?, ?, 'file', ?, ?)",
               (lecture_id, name, file.filename, db.now()))
    WORKER.enqueue(lecture_id)
    return _public(_lecture_or_404(lecture_id))


class UrlIn(BaseModel):
    url: str
    title: str = ""


@app.post("/api/lectures/url")
def add_by_url(body: UrlIn):
    url = body.url.strip()
    if not re.match(r"^https?://", url):
        raise HTTPException(400, "Нужна ссылка, начинающаяся с http:// или https://")
    lecture_id = uuid.uuid4().hex[:12]
    storage.lecture_dir(lecture_id)
    db.execute("INSERT INTO lectures (id, title, source_type, source_url, created_at) VALUES (?, ?, 'url', ?, ?)",
               (lecture_id, body.title.strip() or url, url, db.now()))
    WORKER.enqueue(lecture_id)
    return _public(_lecture_or_404(lecture_id))


@app.get("/api/lectures/{lecture_id}")
def get_lecture(lecture_id: str):
    lec = _public(_lecture_or_404(lecture_id))
    sections = db.query("SELECT idx, level, parent, start, end, s_from, s_to, title, bullets, essence, coverage FROM sections "
                        "WHERE lecture_id = ? ORDER BY idx", (lecture_id,))
    for s in sections:
        s["bullets"] = db.loads(s["bullets"], [])
        s["essence"] = db.loads(s["essence"], [])
    summary = db.one("SELECT overview, key_points, method, coverage FROM summaries WHERE lecture_id = ?", (lecture_id,))
    if summary:
        summary["key_points"] = db.loads(summary["key_points"], [])
    terms = db.query("SELECT term, lemma, score, count, first_time, def_text, def_time, mentions FROM terms "
                     "WHERE lecture_id = ? ORDER BY score DESC", (lecture_id,))
    for t in terms:
        t["mentions"] = db.loads(t["mentions"], [])
    lec.update(sections=sections, summary=summary, terms=terms,
               video=f"/api/lectures/{lecture_id}/video", subtitles=f"/api/lectures/{lecture_id}/subtitles.vtt")
    return lec


class LecturePatch(BaseModel):
    title: str


@app.patch("/api/lectures/{lecture_id}")
def rename_lecture(lecture_id: str, body: LecturePatch):
    _lecture_or_404(lecture_id)
    if not body.title.strip():
        raise HTTPException(400, "Пустое название")
    db.update_lecture(lecture_id, title=body.title.strip())
    INDEX.reload()
    return {"ok": True}


@app.delete("/api/lectures/{lecture_id}")
def delete_lecture(lecture_id: str):
    _lecture_or_404(lecture_id)
    WORKER.delete(lecture_id)
    return {"ok": True}


class ReprocessIn(BaseModel):
    from_stage: str = "structure"


@app.post("/api/lectures/{lecture_id}/reprocess")
def reprocess(lecture_id: str, body: ReprocessIn):
    lec = _lecture_or_404(lecture_id)
    if lec["status"] in ("queued", "processing"):
        raise HTTPException(409, "Лекция уже обрабатывается")
    if body.from_stage not in ("prepare", "asr", "index", "structure"):
        raise HTTPException(400, "Неизвестный этап")
    WORKER.enqueue(lecture_id, body.from_stage)
    return {"ok": True}


@app.get("/api/lectures/{lecture_id}/transcript")
def transcript(lecture_id: str):
    _lecture_or_404(lecture_id)
    rows = db.query("SELECT idx, start, end, text, type FROM sentences WHERE lecture_id = ? ORDER BY idx", (lecture_id,))
    return {"sentences": rows, "types": fragtypes.LABELS}


@app.get("/api/lectures/{lecture_id}/video")
def video(lecture_id: str, request: Request):
    lec = _lecture_or_404(lecture_id)
    if not lec["video_file"]:
        raise HTTPException(404, "Видео ещё готовится")
    return _ranged_file(config.LECTURES_DIR / lecture_id / lec["video_file"], request)


@app.get("/api/lectures/{lecture_id}/thumb")
def thumb(lecture_id: str):
    p = config.LECTURES_DIR / lecture_id / "thumb.jpg"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg")


@app.get("/api/lectures/{lecture_id}/subtitles.vtt")
def subtitles(lecture_id: str):
    p = config.LECTURES_DIR / lecture_id / "subs.vtt"
    if not p.exists():
        return PlainTextResponse("WEBVTT\n\n", media_type="text/vtt")
    return FileResponse(p, media_type="text/vtt; charset=utf-8")


@app.get("/api/lectures/{lecture_id}/heat")
def lecture_heat(lecture_id: str, q: str, bins: int = 200):
    _lecture_or_404(lecture_id)
    if not q.strip():
        return {"bins": []}
    h = search.heat(lecture_id, models.embedder().query(q.strip()), bins=max(20, min(bins, 600)))
    return h or {"bins": []}


@app.get("/api/lectures/{lecture_id}/links")
def lecture_links(lecture_id: str):
    _lecture_or_404(lecture_id)
    return {str(k): v for k, v in course.links(lecture_id).items()}


@app.get("/api/lectures/{lecture_id}/export.md")
def export_md(lecture_id: str):
    lec = get_lecture(lecture_id)
    ft = text.fmt_time
    lines = [f"# {lec['title']}", ""]
    s = lec["summary"]
    if s:
        lines += [s["overview"], "", "## Главное", ""]
        lines += [f"- {k['text']} ({ft(k['t'])})" for k in s["key_points"]]
    lines += ["", "## Конспект по разделам", ""]
    for sec in lec["sections"]:
        if sec["level"] == 1:
            lines += [f"### {ft(sec['start'])} — {sec['title']}", ""]
            lines += [f"- {b['text']} ({ft(b['t'])})" for b in sec["bullets"]]
            lines.append("")
        else:
            lines.append(f"  - {ft(sec['start'])} {sec['title']}")
    if lec["terms"]:
        lines += ["", "## Термины", ""]
        for t in lec["terms"]:
            lines.append(f"- **{t['term']}** ({ft(t['first_time'])})" + (f" — {t['def_text']}" if t["def_text"] else ""))
    body = "\n".join(lines) + "\n"
    fname = re.sub(r"[^\w\- ]+", "", lec["title"])[:60].strip() or "konspekt"
    return Response(body, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{_quote(fname)}.md"})


# ---------- поиск, вопрос-ответ, курс ----------

@app.get("/api/search")
def api_search(q: str, lecture: str | None = None, types: str = "", k: int = 10, mode: str = "rerank"):
    if mode not in search.MODES:
        raise HTTPException(400, "Неизвестный режим поиска")
    tl = [t for t in types.split(",") if t in fragtypes.TYPES]
    return search.search(q, lecture_id=lecture or None, types=tl or None, top_k=max(1, min(k, 30)), mode=mode)


class AskIn(BaseModel):
    question: str
    lecture_id: str | None = None
    history: list[dict] = []


@app.post("/api/ask")
def api_ask(body: AskIn):
    if not body.question.strip():
        raise HTTPException(400, "Пустой вопрос")
    return qa.ask(body.question, body.lecture_id or None, body.history)


@app.get("/api/map")
def api_map():
    return course.course_map()


@app.get("/api/status")
def api_status():
    import torch
    gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    return {
        "device": models.device(), "gpu": gpu, "asr_model": config.ASR_MODEL, "embed_model": config.EMBED_MODEL,
        "rerank_model": config.RERANK_MODEL or None, "llm": llm.status(), "tools": media.check_tools(),
        "lectures": len(INDEX.lectures), "chunks": len(INDEX.chunks), "types": fragtypes.LABELS,
        "rerank_threshold": config.RERANK_THRESHOLD,
    }


@app.get("/api/eval")
def api_eval():
    p = config.EVAL_DIR / "results.json"
    if not p.exists():
        return JSONResponse({"available": False})
    data = json.loads(p.read_text(encoding="utf-8"))
    data["available"] = True
    return data


# ---------- раздача видео с поддержкой перемотки ----------

def _ranged_file(path: Path, request: Request):
    """Браузер перематывает видео запросами Range: «дай байты с N». Без поддержки Range переход по таймкоду не работает."""
    if not path.exists():
        raise HTTPException(404, "Файл не найден")
    size = path.stat().st_size
    ctype = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".mkv": "video/webm", ".mp3": "audio/mpeg",
             ".m4a": "audio/mp4", ".wav": "audio/wav", ".ogg": "audio/ogg"}.get(path.suffix.lower(), "application/octet-stream")
    rng = request.headers.get("range")
    m = re.match(r"bytes=(\d*)-(\d*)", rng or "")
    if not m or (not m.group(1) and not m.group(2)):
        return FileResponse(path, media_type=ctype, headers={"Accept-Ranges": "bytes"})
    if m.group(1):
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else size - 1
    else:  # bytes=-N — последние N байт
        start = max(0, size - int(m.group(2)))
        end = size - 1
    end = min(end, size - 1)
    if start > end or start >= size:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    length = end - start + 1

    def body():
        with path.open("rb") as f:
            f.seek(start)
            left = length
            while left > 0:
                data = f.read(min(1024 * 1024, left))
                if not data:
                    break
                left -= len(data)
                yield data

    return StreamingResponse(body(), status_code=206, media_type=ctype, headers={
        "Content-Range": f"bytes {start}-{end}/{size}", "Accept-Ranges": "bytes", "Content-Length": str(length)})


def _quote(s: str) -> str:
    from urllib.parse import quote
    return quote(s)


# ---------- фронтенд ----------

app.mount("/static", StaticFiles(directory=config.FRONTEND_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(config.FRONTEND_DIR / "index.html")
