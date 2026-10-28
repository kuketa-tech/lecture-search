"""Обработка лекции от файла до готового индекса и конспекта. Каждый этап пишет статус и прогресс в БД."""
import json
import os
import time
import traceback

import numpy as np

from . import config, db, media, storage
from .ml import asr, chunking, fragtypes, models, segmentation, summarize, terms, text
from .ml.index import INDEX

STAGES = {
    "download": ("Скачивание видео", 0.00, 0.08),
    "prepare": ("Подготовка видео и звука", 0.08, 0.12),
    "asr": ("Распознавание речи", 0.12, 0.62),
    "index": ("Векторы и поисковый индекс", 0.62, 0.72),
    "structure": ("Разделы и термины", 0.72, 0.80),
    "summary": ("Суть и конспект", 0.80, 0.99),
}
CONTEXT_CHUNKS = os.getenv("CONTEXT_CHUNKS", "1") == "1"


class Cancelled(Exception):
    pass


class Progress:
    def __init__(self, lecture_id: str, cancelled):
        self.id = lecture_id
        self.cancelled = cancelled
        self.stage = ""
        self.last = -1.0
        self.timings: dict[str, float] = {}
        self._t0 = time.time()

    def enter(self, stage: str):
        if self.cancelled():
            raise Cancelled()
        if self.stage:
            self.timings[self.stage] = round(time.time() - self._t0, 1)
        self.stage, self._t0 = stage, time.time()
        label, a, _ = STAGES[stage]
        db.update_lecture(self.id, status="processing", stage=label, progress=a)
        print(f"[pipeline] {self.id}: {label}")

    def __call__(self, frac: float):
        _, a, b = STAGES[self.stage]
        p = a + (b - a) * max(0.0, min(1.0, frac))
        if p - self.last >= 0.004:
            self.last = p
            db.update_lecture(self.id, progress=round(p, 4))

    def finish(self):
        if self.stage:
            self.timings[self.stage] = round(time.time() - self._t0, 1)


def _source_file(d) -> object:
    files = [f for f in d.glob("source.*") if f.suffix not in (".part", ".ytdl")]
    return files[0] if files else None


def process(lecture_id: str, cancelled=lambda: False, from_stage: str | None = None) -> None:
    lec = db.one("SELECT * FROM lectures WHERE id = ?", (lecture_id,))
    if lec is None:
        return
    d = storage.lecture_dir(lecture_id)
    pr = Progress(lecture_id, cancelled)
    db.update_lecture(lecture_id, error=None)
    redo_from = from_stage or "download"
    order = list(STAGES)

    def need(stage: str) -> bool:
        return order.index(stage) >= order.index(redo_from)

    # 1. Источник
    src = _source_file(d)
    if src is None:
        if lec["source_type"] != "url":
            raise media.MediaError("Исходный файл лекции не найден")
        pr.enter("download")
        src, remote_title = media.download_url(lec["source_url"], d, progress=pr)
        if lec["title"] == lec["source_url"] or not lec["title"].strip():
            db.update_lecture(lecture_id, title=" ".join(remote_title.split()))

    # 2. Видео для плеера и звук для распознавания
    audio = d / "audio.wav"
    if need("prepare") or not audio.exists() or not lec["video_file"]:
        pr.enter("prepare")
        info = media.probe(src)
        if media.is_playable(info):
            video_file = src.name
        else:
            pr(0.1)
            media.transcode(src, d / "video.mp4", info["has_video"])
            video_file = "video.mp4"
        pr(0.6)
        media.extract_audio(src, audio)
        if info["has_video"]:
            media.thumbnail(d / video_file, d / "thumb.jpg", info["duration"])
        db.update_lecture(lecture_id, duration=info["duration"], video_file=video_file)
        lec = db.one("SELECT * FROM lectures WHERE id = ?", (lecture_id,))

    # 3. Речь → слова с таймкодами
    words = storage.load_json(lecture_id, "words.json")
    if need("asr") or words is None:
        pr.enter("asr")
        words = asr.transcribe(str(audio), lec["duration"], prompt=asr.make_prompt(lec["title"]), progress=pr)
        if not words:
            raise media.MediaError("В записи не удалось распознать речь")
        storage.save_json(lecture_id, "words.json", words)
        (d / "subs.vtt").write_text(text.make_vtt(words), encoding="utf-8")

    # 4. Предложения, фрагменты, векторы, поисковый индекс
    emb = models.embedder()
    sent_vecs = storage.load_vecs(lecture_id, "sent_vecs")
    if need("index") or sent_vecs is None:
        pr.enter("index")
        sentences = text.build_sentences(words)
        texts = [s["text"] for s in sentences]
        sent_vecs = emb.passages(texts)
        pr(0.4)
        types = fragtypes.classify(sent_vecs, texts, emb.name)
        for s, t in zip(sentences, types):
            s["type"] = t
        chunks = chunking.make_chunks(sentences, config.CHUNK_SECONDS, config.CHUNK_OVERLAP)
        chunk_vecs = emb.passages([c["text"] for c in chunks])
        pr(0.8)
        _write_index(lecture_id, sentences, chunks, types, sent_vecs, chunk_vecs)
        db.update_lecture(lecture_id, searchable=1)
        INDEX.reload()  # лекция уже ищется, пока строится конспект

    sentences = db.query("SELECT idx, start, end, text, type FROM sentences WHERE lecture_id = ? ORDER BY idx", (lecture_id,))
    starts = np.array([s["start"] for s in sentences], dtype=np.float32)
    ends = np.array([s["end"] for s in sentences], dtype=np.float32)

    # 5. Разделы и термины — по векторам
    pr.enter("structure")
    sections = segmentation.segment(sent_vecs, starts, ends)
    pr(0.4)
    other_docs = []
    for r in db.query("SELECT lecture_id, group_concat(lemmas, ' ') AS l FROM chunks WHERE lecture_id != ? GROUP BY lecture_id",
                      (lecture_id,)):
        other_docs.append(set((r["l"] or "").split()))
    term_list, cands, term_vecs = terms.extract(sentences, sent_vecs, emb, other_docs)

    # 6. Суть и конспект
    pr.enter("summary")
    title = db.one("SELECT title FROM lectures WHERE id = ?", (lecture_id,))["title"]
    sections, summary = summarize.build(title, sentences, sent_vecs, sections, cands, term_vecs, term_list, emb, progress=pr)
    _write_summary(lecture_id, sections, summary, term_list)

    # Контекстное обогащение: фрагмент «знает», из какого он раздела и лекции, — «докажем это» начинает
    # находиться по запросу о теореме, которую доказывают. Эффект меряется в backend.eval (абляция).
    if CONTEXT_CHUNKS:
        chunks = db.query("SELECT idx, start, text FROM chunks WHERE lecture_id = ? ORDER BY idx", (lecture_id,))
        level1 = [s for s in sections if s["level"] == 1]
        ctx_texts = [contextual_text(title, _section_title_at(level1, c["start"]), c["text"]) for c in chunks]
        storage.save_vecs(lecture_id, "chunk_vecs", emb.passages(ctx_texts))

    pr.finish()
    db.update_lecture(lecture_id, status="ready", stage="Готово", progress=1.0, processed_at=db.now(),
                      llm=summary["method"], timings=_json(pr.timings))
    INDEX.reload()


def contextual_text(lecture_title: str, section_title: str, chunk_text: str) -> str:
    head = f"Лекция: {lecture_title}."
    if section_title:
        head += f" Раздел: {section_title}."
    return f"{head} {chunk_text}"


def _section_title_at(level1: list[dict], t: float) -> str:
    title = ""
    for s in level1:
        if s["start"] <= t + 1:
            title = s["title"]
    return title


def _write_index(lecture_id, sentences, chunks, types, sent_vecs, chunk_vecs):
    c = db.conn()
    c.execute("DELETE FROM sentences WHERE lecture_id = ?", (lecture_id,))
    c.execute("DELETE FROM chunks WHERE lecture_id = ?", (lecture_id,))
    c.executemany("INSERT INTO sentences (lecture_id, idx, start, end, text, type) VALUES (?, ?, ?, ?, ?, ?)",
                  [(lecture_id, s["idx"], s["start"], s["end"], s["text"], s["type"]) for s in sentences])
    c.executemany("INSERT INTO chunks (lecture_id, idx, start, end, s_from, s_to, text, lemmas, tags) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  [(lecture_id, ch["idx"], ch["start"], ch["end"], ch["s_from"], ch["s_to"], ch["text"],
                    " ".join(text.lemmas(ch["text"])), ",".join(fragtypes.chunk_tags(types, ch["s_from"], ch["s_to"])))
                   for ch in chunks])
    c.commit()
    storage.save_vecs(lecture_id, "sent_vecs", sent_vecs)
    storage.save_vecs(lecture_id, "chunk_vecs", chunk_vecs)


def _write_summary(lecture_id, sections, summary, term_list):
    c = db.conn()
    for table in ("sections", "summaries", "terms"):
        c.execute(f"DELETE FROM {table} WHERE lecture_id = ?", (lecture_id,))
    c.executemany("INSERT INTO sections (lecture_id, idx, level, parent, start, end, s_from, s_to, title, bullets, "
                  "essence, coverage) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  # int()/float(): numpy-числа SQLite сохранил бы как байты
                  [(lecture_id, int(s["idx"]), int(s["level"]), None if s["parent"] is None else int(s["parent"]),
                    float(s["start"]), float(s["end"]), int(s["s_from"]), int(s["s_to"]), s["title"],
                    _json(s.get("bullets", [])), _json([int(x) for x in s.get("essence", [])]),
                    float(s.get("coverage", 0))) for s in sections])
    c.execute("INSERT INTO summaries (lecture_id, overview, key_points, method, coverage) VALUES (?, ?, ?, ?, ?)",
              (lecture_id, summary["overview"], _json(summary["key_points"]), summary["method"], summary["coverage"]))
    c.executemany("INSERT INTO terms (lecture_id, term, lemma, score, count, first_time, def_text, def_time, mentions) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  [(lecture_id, t["term"], t["lemma"], t["score"], t["count"], t["first_time"], t["def_text"],
                    t["def_time"], _json(t["mentions"])) for t in term_list])
    c.execute("DELETE FROM qa_cache")
    c.commit()


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def safe_process(lecture_id: str, cancelled, from_stage: str | None = None) -> None:
    try:
        process(lecture_id, cancelled, from_stage)
    except Cancelled:
        raise
    except Exception as e:
        traceback.print_exc()
        msg = str(e) if isinstance(e, media.MediaError) else f"{type(e).__name__}: {e}"
        db.update_lecture(lecture_id, status="error", error=msg[:500])
