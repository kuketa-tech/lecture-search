"""Структура лекции: границы разделов и подразделов по векторам (TextTiling на эмбеддингах).

На каждой границе между предложениями сравниваем средний вектор окна слева и окна справа.
Получаем кривую похожести во времени: смена темы — провал. Глубина провала = насколько кривая
поднимается от него влево и вправо. Самые глубокие провалы с ограничением на длину раздела — границы."""
import numpy as np


def gap_similarity(vecs: np.ndarray, window: int) -> np.ndarray:
    n = len(vecs)
    if n < 2:
        return np.zeros(0, dtype=np.float32)
    csum = np.vstack([np.zeros((1, vecs.shape[1]), dtype=np.float32), np.cumsum(vecs, axis=0)])
    sims = np.zeros(n - 1, dtype=np.float32)
    for g in range(n - 1):  # граница после предложения g
        left = csum[g + 1] - csum[max(0, g - window + 1)]
        right = csum[min(n, g + 1 + window)] - csum[g + 1]
        sims[g] = float(left @ right / (np.linalg.norm(left) * np.linalg.norm(right) + 1e-9))
    if len(sims) >= 3:
        sims = np.convolve(np.pad(sims, 1, mode="edge"), [0.25, 0.5, 0.25], mode="valid")
    return sims


def depth_scores(sims: np.ndarray) -> np.ndarray:
    n = len(sims)
    depth = np.zeros(n, dtype=np.float32)
    for i in range(n):
        if (i > 0 and sims[i - 1] < sims[i]) or (i < n - 1 and sims[i + 1] < sims[i]):
            continue  # не локальный минимум
        lpk = i
        while lpk > 0 and sims[lpk - 1] >= sims[lpk]:
            lpk -= 1
        rpk = i
        while rpk < n - 1 and sims[rpk + 1] >= sims[rpk]:
            rpk += 1
        depth[i] = (sims[lpk] - sims[i]) + (sims[rpk] - sims[i])
    return depth


def split(vecs: np.ndarray, starts: np.ndarray, ends: np.ndarray, max_parts: int, min_dur: float,
          window: int) -> tuple[list[int], np.ndarray]:
    """Возвращает индексы предложений, с которых начинаются новые части (без первой), и кривую похожести."""
    n = len(vecs)
    if n < 4 or max_parts < 2:
        return [], np.zeros(max(n - 1, 0))
    sims = gap_similarity(vecs, window)
    depth = depth_scores(sims)
    cand = np.where(depth > 0)[0]
    if not len(cand):
        return [], sims
    # Порог Хёрст («среднее минус половина σ») плюс отсечка мелкой ряби: провал должен быть сопоставим с самым глубоким.
    thr = max(depth[cand].mean() - 0.5 * depth[cand].std(), 0.3 * depth[cand].max())
    t0, t1 = float(starts[0]), float(ends[-1])
    chosen: list[int] = []
    for g in sorted(cand, key=lambda g: -depth[g]):
        if len(chosen) >= max_parts - 1:
            break
        if depth[g] < thr and chosen:
            break
        t = float(starts[g + 1])
        if t - t0 < min_dur or t1 - t < min_dur:
            continue
        if any(abs(t - float(starts[c])) < min_dur for c in chosen):
            continue
        chosen.append(g + 1)
    return sorted(chosen), sims


def _window(starts, ends, seconds: float) -> int:
    avg = float(np.mean(ends - starts)) if len(starts) else 5.0
    return int(max(2, round(seconds / max(avg, 1.0))))


def segment(vecs: np.ndarray, starts: np.ndarray, ends: np.ndarray) -> list[dict]:
    """Разделы (level=1) и подразделы (level=2). Число разделов зависит от длины лекции и глубины провалов."""
    n = len(vecs)
    if n == 0:
        return []
    duration = float(ends[-1] - starts[0])
    max_sections = int(np.clip(np.ceil(duration / 60), 2, 15))  # верхняя граница; реальное число решают провалы
    min_dur = float(np.clip(duration / 14, 25, 180))
    window = _window(starts, ends, float(np.clip(duration / 25, 15, 60)))
    bounds, _ = split(vecs, starts, ends, max_sections, min_dur, window)
    edges = [0, *bounds, n]

    sections: list[dict] = []
    for si in range(len(edges) - 1):
        a, b = edges[si], edges[si + 1] - 1
        sec = {"level": 1, "parent": None, "s_from": a, "s_to": b,
               "start": float(starts[a]), "end": float(ends[b])}
        sections.append(sec)
        sec_dur = sec["end"] - sec["start"]
        max_sub = int(np.clip(round(sec_dur / 120), 0, 5))
        min_sub = float(np.clip(sec_dur / 5, 20, 90))
        if max_sub >= 2 and b - a >= 6:
            sub_win = _window(starts[a:b + 1], ends[a:b + 1], float(np.clip(sec_dur / 10, 10, 30)))
            sub_bounds, _ = split(vecs[a:b + 1], starts[a:b + 1], ends[a:b + 1], max_sub, min_sub, sub_win)
            sub_edges = [a, *[a + x for x in sub_bounds], b + 1]
            if len(sub_edges) > 2:
                for k in range(len(sub_edges) - 1):
                    sa, sb = sub_edges[k], sub_edges[k + 1] - 1
                    sections.append({"level": 2, "parent": si, "s_from": sa, "s_to": sb,
                                     "start": float(starts[sa]), "end": float(ends[sb])})
    # порядок: раздел, его подразделы, следующий раздел...
    sections.sort(key=lambda s: (s["start"], s["level"]))
    level1 = [i for i, s in enumerate(sections) if s["level"] == 1]
    for i, s in enumerate(sections):
        s["idx"] = i
        if s["level"] == 2:
            s["parent"] = level1[s["parent"]]
    return sections


def section_vector(vecs: np.ndarray) -> np.ndarray:
    v = vecs.mean(axis=0)
    return v / (np.linalg.norm(v) + 1e-9)
