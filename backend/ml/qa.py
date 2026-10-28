"""Вопрос-ответ по лекциям (RAG): наш поиск находит фрагменты, LLM формулирует ответ только по ним
и ставит ссылки [n], которые на фронте становятся кнопками перехода к моменту видео.
Если поиск ничего уверенно не нашёл — честно отвечаем, что в лекции об этом не говорилось, без генерации."""
import re

import numpy as np

from .. import db
from . import llm, models, search, text

CACHE_SIM = 0.97
CITE_RE = re.compile(r"\[(\d+)\]")


def _cached(scope: str, qv: np.ndarray) -> dict | None:
    for r in db.query("SELECT question, vec, answer FROM qa_cache WHERE scope = ? ORDER BY id DESC LIMIT 300", (scope,)):
        v = np.frombuffer(r["vec"], dtype=np.float32)
        if v.shape == qv.shape and float(v @ qv) >= CACHE_SIM:
            ans = db.loads(r["answer"], {})
            ans["cached"] = True
            ans["similar_question"] = r["question"]
            return ans
    return None


def _condense(question: str, history: list[dict]) -> str:
    """Уточняющий вопрос («а почему так?») переписываем в самостоятельный — иначе поиску не за что зацепиться."""
    lines = []
    for h in history[-6:]:
        who = "Студент" if h.get("role") == "user" else "Ассистент"
        lines.append(f"{who}: {str(h.get('content', ''))[:400]}")
    prompt = ("История диалога:\n" + "\n".join(lines) + f"\n\nНовый вопрос студента: {question}\n\n"
              "Перепиши новый вопрос так, чтобы он был понятен без истории: подставь, о чём именно идёт речь. "
              "Выведи только переписанный вопрос, одной строкой.")
    try:
        out = llm.chat(prompt, max_tokens=80, temperature=0.0).strip().strip("\"«»")
        return out.splitlines()[0] if out else question
    except llm.LLMError:
        return question


def ask(question: str, lecture_id: str | None = None, history: list[dict] | None = None) -> dict:
    question = question.strip()
    scope = lecture_id or "*"
    qv = models.embedder().query(question)
    if not history:
        hit = _cached(scope, qv)
        if hit:
            return hit

    backend = llm.backend()
    search_q = _condense(question, history) if history and backend else question
    res = search.search(search_q, lecture_id=lecture_id, top_k=5, with_heat=False)
    if not res["found"]:
        return {"answer": "В лекциях об этом не говорилось — я не нашёл фрагментов, которые отвечают на этот вопрос.",
                "sources": [], "grounded": False, "search_query": search_q, "llm": backend.name if backend else None,
                "maybe": [_source(r, i + 1) for i, r in enumerate(res["maybe"][:2])]}

    sources = [_source(r, i + 1) for i, r in enumerate(res["results"])]
    if backend is None:
        best = [f"{s['best']} [{s['n']}]" for s in sources[:3]]
        answer = "LLM не подключена, поэтому вот самые подходящие места из лекций:\n\n" + "\n\n".join(best)
        return {"answer": answer, "sources": sources, "grounded": True, "search_query": search_q, "llm": None}

    frags = "\n\n".join(f"[{s['n']}] «{s['lecture_title']}», {text.fmt_time(s['t'])}: {s['text']}" for s in sources)
    prompt = (f"Вопрос студента: {question}\n\nФрагменты расшифровки лекций (возможны ошибки распознавания речи):\n\n"
              f"{frags}\n\nОтветь на вопрос, опираясь только на эти фрагменты. После каждого утверждения ставь "
              "ссылку на фрагмент в квадратных скобках, например [1] или [2][3]. Если во фрагментах ответа нет, "
              "так и скажи. Объём — 2–6 предложений.")
    try:
        answer = llm.chat(prompt, max_tokens=600, temperature=0.2)
    except llm.LLMError as e:
        best = [f"{s['best']} [{s['n']}]" for s in sources[:3]]
        answer = f"LLM сейчас недоступна ({e}). Самые подходящие места из лекций:\n\n" + "\n\n".join(best)
        return {"answer": answer, "sources": sources, "grounded": True, "search_query": search_q, "llm": None}

    used = {int(n) for n in CITE_RE.findall(answer)}
    for s in sources:
        s["cited"] = s["n"] in used
    result = {"answer": answer, "sources": sources, "grounded": True, "search_query": search_q, "llm": backend.name}
    if not history:
        db.execute("INSERT INTO qa_cache (scope, question, vec, answer, created_at) VALUES (?, ?, ?, ?, ?)",
                   (scope, question, qv.astype(np.float32).tobytes(), _dumps(result), db.now()))
    return result


def _source(r: dict, n: int) -> dict:
    full = " ".join("".join(p[0] for p in s["pieces"]) for s in r["sentences"])
    best = next(("".join(p[0] for p in s["pieces"]) for s in r["sentences"] if s["best"]), full)
    return {"n": n, "lecture_id": r["lecture_id"], "lecture_title": r["lecture_title"], "t": r["t"],
            "text": full, "best": best, "score": r["score"]}


def _dumps(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)
