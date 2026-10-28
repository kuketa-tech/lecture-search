"""Семантический поиск: векторы + BM25 → слияние RRF → реранкер → точный таймкод → склейка дублей → порог."""
import re
import time

import numpy as np

from .. import config, db
from . import models, text
from .index import INDEX

CANDIDATES = 50
RRF_K = 60
# Итоговый порядок — слияние порядка реранкера и гибрида (как RRF): реранкер уверенно выбирает один ответ,
# но иногда опускает верный фрагмент, который гибрид держал высоко. Вес 0.5 подобран backend.eval (0 — только реранкер).
RERANK_FUSE = 0.5
WORD_SPLIT_RE = re.compile(r"([a-zA-Zа-яА-ЯёЁ0-9]+(?:-[a-zA-Zа-яА-ЯёЁ0-9]+)*)")
MODES = ("bm25", "dense", "hybrid", "rerank")


def _pieces(sentence: str, q_lemmas: set[str]) -> list:
    """Разбивает предложение на куски и помечает слова запроса (по леммам: «теоремы» ~ «теорема»)."""
    out: list = []
    for part in WORD_SPLIT_RE.split(sentence):
        if not part:
            continue
        hl = 1 if WORD_SPLIT_RE.fullmatch(part) and text.lemma(part) in q_lemmas else 0
        if out and out[-1][1] == hl == 0:
            out[-1][0] += part
        else:
            out.append([part, hl])
    return out


def _rrf(*rankings: np.ndarray) -> list[int]:
    """Reciprocal Rank Fusion: складываем 1/(60 + место) из каждого списка. Шкалы оценок приводить не нужно."""
    score: dict[int, float] = {}
    for ranking in rankings:
        for r, i in enumerate(ranking):
            score[int(i)] = score.get(int(i), 0.0) + 1.0 / (RRF_K + r + 1)
    return sorted(score, key=score.get, reverse=True)


def heat(lecture_id: str, qv: np.ndarray, bins: int = 200) -> dict | None:
    """Тепловая полоса: насколько каждый момент лекции близок к запросу (0..1 по времени)."""
    lectures, _, _, _, sents, _ = INDEX.snapshot()
    sl = sents.get(lecture_id)
    if sl is None or not len(sl["start"]):
        return None
    dur = float(lectures[lecture_id]["duration"] or sl["end"][-1]) or 1.0
    sims = sl["vecs"] @ qv
    z = (sims - sims.mean()) / (sims.std() + 1e-6)
    h = np.full(bins, -3.0, dtype=np.float32)
    a = np.clip((sl["start"] / dur * bins).astype(int), 0, bins - 1)
    b = np.clip((sl["end"] / dur * bins).astype(int), 0, bins - 1)
    for i in range(len(z)):
        h[a[i]:b[i] + 1] = np.maximum(h[a[i]:b[i] + 1], z[i])
    h = np.convolve(h, [0.2, 0.6, 0.2], mode="same")
    v = np.clip((h - 0.8) / 2.2, 0, 1)
    return {"bins": [round(float(x), 3) for x in v], "duration": dur, "max_sim": round(float(sims.max()), 4)}


def _relocalize(query: str, results: list[dict], sents: dict) -> None:
    """Точный таймкод для верхних результатов: реранкер читает запрос вместе с каждой фразой фрагмента
    и выбирает ту, что отвечает лучше всего. Это точнее, чем сравнивать векторы фраз."""
    rer = models.reranker()
    if rer is None or not results:
        return
    pairs, owners = [], []
    for ri, r in enumerate(results):
        sl = sents[r["lecture_id"]]
        idxs = [s["idx"] for s in r["sentences"]]
        for si in idxs:
            pairs.append(sl["text"][si])
            owners.append((ri, si))
    scores = rer.score(query, pairs)
    best: dict[int, tuple[float, int]] = {}
    for (ri, si), sc in zip(owners, scores):
        if ri not in best or sc > best[ri][0]:
            best[ri] = (float(sc), si)
    for ri, (_, si) in best.items():
        r = results[ri]
        sl = sents[r["lecture_id"]]
        r["best_sentence"] = si
        r["t"] = round(max(0.0, float(sl["start"][si]) - 2.0), 1)
        for s in r["sentences"]:
            s["best"] = s["idx"] == si


def _add_sections(results: list[dict]) -> None:
    """Подписывает результат названием раздела, в который попала найденная фраза («Лекция · Раздел»)."""
    for r in results:
        row = db.one("SELECT title FROM sections WHERE lecture_id = ? AND level = 1 AND s_from <= ? AND s_to >= ? LIMIT 1",
                     (r["lecture_id"], r["best_sentence"], r["best_sentence"]))
        r["section"] = row["title"] if row else None


def search(query: str, lecture_id: str | None = None, types: list[str] | None = None, top_k: int = 10,
           mode: str = "rerank", with_heat: bool = True, candidates: int = CANDIDATES) -> dict:
    t0 = time.perf_counter()
    query = query.strip()
    lectures, chunks, cvecs, clect, sents, bm25 = INDEX.snapshot()
    empty = {"query": query, "results": [], "maybe": [], "found": False, "heat": {}, "mode": mode,
             "ms": 0, "reranked": False}
    if not query or not chunks:
        return empty

    mask = np.ones(len(chunks), dtype=bool)
    if lecture_id:
        mask &= clect == lecture_id
    if types:
        mask &= np.array([any(t in c["tags"] for t in types) for c in chunks], dtype=bool)
    if not mask.any():
        return empty

    q_lem = text.lemmas(query)
    qv = models.embedder().query(query)

    dense = cvecs @ qv
    dense[~mask] = -np.inf
    dense_rank = np.argsort(-dense)[:candidates]
    dense_rank = dense_rank[np.isfinite(dense[dense_rank])]

    bm = bm25.scores(q_lem)
    bm[~mask] = 0
    bm_rank = np.argsort(-bm)[:candidates]
    bm_rank = bm_rank[bm[bm_rank] > 0]

    if mode == "bm25":
        order = [int(i) for i in bm_rank]
    elif mode == "dense":
        order = [int(i) for i in dense_rank]
    else:
        order = _rrf(dense_rank, bm_rank)

    rr_scores = None
    reranked = False
    if mode == "rerank":
        rer = models.reranker()
        if rer is not None and order:
            top = order[:config.RERANK_TOP]
            rr = rer.score(query, [chunks[i]["text"] for i in top])
            pairs = sorted(zip(top, rr), key=lambda p: -p[1])
            if RERANK_FUSE > 0:
                rr_rank = {p[0]: k for k, p in enumerate(pairs)}
                hy_rank = {i: k for k, i in enumerate(top)}
                pairs.sort(key=lambda p: -(1 / (RRF_K + rr_rank[p[0]]) + RERANK_FUSE / (RRF_K + hy_rank[p[0]])))
            order = [p[0] for p in pairs]
            rr_scores = {p[0]: float(p[1]) for p in pairs}
            reranked = True

    q_set = set(q_lem)
    results, kept = [], []
    for i in order:
        c = chunks[i]
        lid = c["lecture_id"]
        sl = sents[lid]
        sv = sl["vecs"][c["s_from"]:c["s_to"] + 1] @ qv
        lex = np.array([len(q_set & set(text.lemmas(t))) for t in sl["text"][c["s_from"]:c["s_to"] + 1]])
        best = int(np.argmax(sv + 0.015 * lex))
        b_idx = c["s_from"] + best
        t_seek = max(0.0, float(sl["start"][b_idx]) - 2.0)  # пара секунд до мысли, чтобы услышать её начало

        # Склейка дублей: окна перекрываются, соседние фрагменты одной лекции находятся вместе.
        if any(k["lecture_id"] == lid and (abs(k["t"] - t_seek) < 25 or
               min(k["end"], c["end"]) - max(k["start"], c["start"]) > 0.5 * (c["end"] - c["start"])) for k in kept):
            continue
        score = rr_scores[i] if rr_scores else float(dense[i]) if np.isfinite(dense[i]) else 0.0
        item = {
            "lecture_id": lid,
            "lecture_title": lectures[lid]["title"],
            "chunk_id": c["id"],
            "start": c["start"],
            "end": c["end"],
            "t": round(t_seek, 1),
            "score": round(score, 4),
            "dense": round(float(dense[i]), 4) if np.isfinite(dense[i]) else None,
            "bm25": round(float(bm[i]), 3),
            "tags": c["tags"],
            "best_sentence": b_idx,
            "sentences": [{"idx": k, "t": float(sl["start"][k]), "best": k == b_idx,
                           "pieces": _pieces(sl["text"][k], q_set)} for k in range(c["s_from"], c["s_to"] + 1)],
        }
        kept.append(item)
        if len(kept) >= top_k * 2:
            break

    if reranked:
        confident = [r for r in kept if r["score"] >= config.RERANK_THRESHOLD]
        maybe = [r for r in kept if r["score"] < config.RERANK_THRESHOLD][:3]
        _relocalize(query, confident[:3], sents)
    else:
        confident, maybe = kept, []
    results = confident[:top_k]
    _add_sections(results + maybe)

    heat_map = {}
    if with_heat:
        for lid in dict.fromkeys(r["lecture_id"] for r in results or maybe):
            h = heat(lid, qv, bins=120)
            if h:
                heat_map[lid] = h
    return {
        "query": query, "results": results, "maybe": maybe if not results else [],
        "found": bool(results), "heat": heat_map, "mode": mode, "reranked": reranked,
        "ms": round((time.perf_counter() - t0) * 1000, 1),
    }
