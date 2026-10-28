"""Векторы на уровне курса: связи между разделами разных лекций и карта тем курса."""
import threading

import numpy as np

from .. import db
from .index import INDEX
from . import essence, text
from .segmentation import section_vector

_map_cache: dict = {"version": -1, "data": None}
_map_lock = threading.Lock()
SERVICE_TITLES = {"Вступление", "Итоги", "Организационные моменты"}


def course_map() -> dict:
    """Карта курса: каждый содержательный раздел — точка на плоскости; похожие темы рядом и собираются в группы.
    Расстояние между разделами — 1 − похожесть по выравниванию фраз (как у связей между лекциями):
    средние векторы разделов у e5 почти одинаковы и сливают всё в одну кучу."""
    with _map_lock:
        if _map_cache["version"] == INDEX.version and _map_cache["data"] is not None:
            return _map_cache["data"]
        secs = [s for s in _sections_with_vectors() if s["title"] not in SERVICE_TITLES]
        data = {"points": [], "clusters": [], "lectures": []}
        if secs:
            n = len(secs)
            S = np.eye(n, dtype=np.float32)
            for i in range(n):
                for j in range(i + 1, n):
                    a, _ = _align(secs[i]["svecs"], secs[j]["svecs"])
                    b, _ = _align(secs[j]["svecs"], secs[i]["svecs"])
                    S[i, j] = S[j, i] = (a + b) / 2
            off = S[~np.eye(n, dtype=bool)]
            # растягиваем похожести на [0, 1], чтобы расстояния различались (у e5 всё лежит в 0.8–0.9)
            lo, hi = (float(off.min()), float(off.max())) if off.size else (0.0, 1.0)
            D = 1 - np.clip((S - lo) / max(hi - lo, 1e-6), 0, 1)
            np.fill_diagonal(D, 0)
            xy = _project(D)
            labels, k = _cluster(D)
            clusters = []
            for c in range(k):
                members = [i for i in range(n) if labels[i] == c]
                if not members:
                    continue
                rep = min(members, key=lambda i: float(D[i, members].mean()))  # самый «центральный» раздел группы
                clusters.append({"id": c, "title": secs[rep]["title"], "size": len(members),
                                 "x": float(xy[members, 0].mean()), "y": float(xy[members, 1].mean()),
                                 "lectures": len({secs[i]["lecture_id"] for i in members})})
            data["points"] = [{"lecture_id": s["lecture_id"], "lecture_title": s["lecture_title"], "idx": s["idx"],
                               "title": s["title"], "t": s["start"], "end": s["end"],
                               "x": float(xy[i, 0]), "y": float(xy[i, 1]), "cluster": int(labels[i])}
                              for i, s in enumerate(secs)]
            data["clusters"] = clusters
            lects = sorted({(s["lecture_id"], s["lecture_title"]) for s in secs}, key=lambda x: x[1])
            data["lectures"] = [{"id": a, "title": b} for a, b in lects]
        _map_cache.update(version=INDEX.version, data=data)
        return data


def _project(D: np.ndarray) -> np.ndarray:
    """Расстояния → координаты на плоскости: MDS для небольшого курса, t-SNE — для большого."""
    n = len(D)
    if n == 1:
        return np.full((1, 2), 0.5)
    if n == 2:
        return np.array([[0.25, 0.5], [0.75, 0.5]])
    if n < 30:
        from sklearn.manifold import MDS
        xy = MDS(n_components=2, metric="precomputed", init="random", random_state=0, n_init=4).fit_transform(D)
    else:
        from sklearn.manifold import TSNE
        xy = TSNE(n_components=2, metric="precomputed", init="random", perplexity=min(30, (n - 1) // 3),
                  random_state=0).fit_transform(D)
    xy = xy - xy.min(axis=0)
    rng = xy.max(axis=0)
    rng[rng == 0] = 1
    return xy / rng


def _cluster(D: np.ndarray) -> tuple[np.ndarray, int]:
    """Группы тем: иерархическая кластеризация, число групп — по лучшему силуэту."""
    n = len(D)
    if n < 4:
        return np.zeros(n, dtype=int), 1
    from sklearn.cluster import AgglomerativeClustering
    from sklearn.metrics import silhouette_score
    best, best_k, best_s = np.zeros(n, dtype=int), 1, -1.0
    min_size = max(3, round(0.1 * n))  # группа из одной-двух точек — выброс, а не тема
    for k in range(2, min(10, n - 1) + 1):
        labels = AgglomerativeClustering(n_clusters=k, metric="precomputed", linkage="average").fit_predict(D)
        if np.bincount(labels).min() < min_size:
            continue
        s = silhouette_score(D, labels, metric="precomputed")
        if s > best_s:
            best, best_k, best_s = labels, k, s
    return best, best_k


_CONTENT: dict[str, np.ndarray] = {}


def _content_mask(lecture_id: str, sl: dict) -> np.ndarray:
    """Какие фразы лекции несут содержание (те же правила, что для конспекта), с кэшем по лекции."""
    m = _CONTENT.get(lecture_id)
    if m is None or len(m) != len(sl["text"]):
        m = essence.sentence_weights(list(sl["text"]), list(sl["type"]))[1]
        _CONTENT[lecture_id] = m
    return m


def _sections_with_vectors() -> list[dict]:
    lectures, _, _, _, sents, _ = INDEX.snapshot()
    if not lectures:
        return []
    marks = ",".join("?" * len(lectures))
    rows = db.query(f"SELECT id, lecture_id, idx, start, end, s_from, s_to, title FROM sections "
                    f"WHERE level = 1 AND lecture_id IN ({marks}) ORDER BY lecture_id, idx", tuple(lectures))
    out = []
    for r in rows:
        sl = sents.get(r["lecture_id"])
        if sl is None or r["s_to"] >= len(sl["vecs"]):
            continue
        r["vec"] = section_vector(sl["vecs"][r["s_from"]:r["s_to"] + 1])
        # для связей — только содержательные фразы: «добрый день», «что мы делаем дальше?» и «до встречи»
        # звучат в любой лекции и иначе связывают обход графа с градиентным спуском
        ok = _content_mask(r["lecture_id"], sl)
        keep = [i for i in range(r["s_from"], r["s_to"] + 1) if sl["type"][i] not in ("org", "recap") and ok[i]]
        keep = keep or list(range(r["s_from"], r["s_to"] + 1))
        r["svecs"] = sl["vecs"][keep]
        r["lemmas"] = {w for i in keep for w in text.lemmas(sl["text"][i])}
        r["stimes"] = [float(sl["start"][i]) for i in keep]
        r["lecture_title"] = lectures[r["lecture_id"]]["title"]
        out.append(r)
    return out


def _align(a: np.ndarray, b: np.ndarray, k: int = 3) -> tuple[float, int]:
    """Похожесть разделов через выравнивание фраз: для каждой фразы ищем самую похожую фразу другого раздела
    и усредняем лучшие совпадения в обе стороны. Средние векторы разделов для этого не годятся: у e5 они
    почти одинаковы (≈0.95 у любых двух разделов), а совпадение конкретных мыслей видно только на уровне фраз.
    Возвращает оценку и номер фразы раздела b, которая совпала лучше всего, — туда и ведёт ссылка."""
    s = a @ b.T
    top_a = np.sort(s.max(axis=1))[::-1][:k]
    best_b = s.max(axis=0)
    top_b = np.sort(best_b)[::-1][:k]
    return float((top_a.mean() + top_b.mean()) / 2), int(best_b.argmax())


def links(lecture_id: str) -> dict[int, list[dict]]:
    """Для каждого раздела лекции — где ещё в курсе разбирают ту же тему: «также в лекции 5 на 23:10».
    Берём только взаимно лучшие пары разделов (каждый — лучший для другого среди разделов той лекции) и только
    если связь заметно выделяется на фоне похожести всех пар разделов разных лекций."""
    secs = _sections_with_vectors()
    if not any(s["lecture_id"] == lecture_id for s in secs) or not any(s["lecture_id"] != lecture_id for s in secs):
        return {}
    n = len(secs)
    score = np.full((n, n), np.nan, dtype=np.float32)
    anchor = np.zeros((n, n), dtype=np.int32)  # anchor[i, j] — лучшая фраза раздела j для раздела i
    for i in range(n):
        for j in range(i + 1, n):
            if secs[i]["lecture_id"] != secs[j]["lecture_id"]:
                score[i, j], anchor[i, j] = _align(secs[i]["svecs"], secs[j]["svecs"])
                score[j, i], anchor[j, i] = _align(secs[j]["svecs"], secs[i]["svecs"])
    cross = score[~np.isnan(score)]
    thr = max(float(cross.mean() + 1.5 * cross.std()), 0.88)
    lect = [s["lecture_id"] for s in secs]
    # Ключевые термины лекций (без предлогов): «поиск в ширину» → {поиск, ширина}
    term_words: dict[str, list[tuple[str, set[str]]]] = {}
    for r in db.query("SELECT lecture_id, term, lemma FROM terms"):
        words = {w for w in r["lemma"].replace("ё", "е").split() if w not in text.STOPWORDS}
        if words:
            term_words.setdefault(r["lecture_id"], []).append((r["term"], words))

    def shared_term(i: int, j: int) -> str | None:
        """Общий ключевой термин двух разделов. Похожие фразы бывают и у далёких тем («пока очередь не пуста» и
        «пока шаг не станет нулём»), а одна и та же тема почти всегда называется одними и теми же словами."""
        for lid in (lect[i], lect[j]):
            for term, words in term_words.get(lid, []):
                if words <= secs[i]["lemmas"] and words <= secs[j]["lemmas"]:
                    return term
        return None

    def best_in(i: int, other_lecture: str) -> int:
        cand = [j for j in range(n) if lect[j] == other_lecture]
        return max(cand, key=lambda j: score[i, j])

    res: dict[int, list[dict]] = {}
    for i, s in enumerate(secs):
        if lect[i] != lecture_id:
            continue
        for other in dict.fromkeys(l for l in lect if l != lecture_id):
            j = best_in(i, other)
            common = shared_term(i, j) if score[i, j] >= thr and best_in(j, lecture_id) == i else None
            if common:
                res.setdefault(s["idx"], []).append({
                    "lecture_id": secs[j]["lecture_id"], "lecture_title": secs[j]["lecture_title"],
                    "section_idx": secs[j]["idx"], "title": secs[j]["title"],
                    "t": secs[j]["stimes"][anchor[i, j]], "score": round(float(score[i, j]), 3), "term": common})
    return res


