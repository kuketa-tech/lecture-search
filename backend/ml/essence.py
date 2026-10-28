"""«Суть» раздела по векторам — экстрактивный конспект без LLM.

Идея: суть — это предложения, вокруг которых сгущаются остальные. Но просто взять предложения, ближайшие
к среднему вектору раздела, нельзя: если в разделе две подтемы, среднее лежит между ними, и ближайшие к нему
фразы не про одну и не про другую. К тому же все они будут говорить одно и то же.

Поэтому решаем задачу покрытия (facility location, как k-medoids): выбрать несколько настоящих предложений так,
чтобы каждое предложение раздела было похоже хотя бы на одно из выбранных. Жадный алгоритм на каждом шаге
добавляет предложение, которое сильнее всего увеличивает покрытие, — получаются «представители» всех подтем
без повторов. Для такой функции жадный выбор гарантированно не хуже (1 − 1/e) ≈ 63% от оптимума,
а доля покрытия — понятная метрика: «конспект передаёт 85% смысла раздела»."""
import re

import numpy as np

from . import text


def similarity(vecs: np.ndarray, base: float | None = None) -> tuple[np.ndarray, float]:
    """Косинусы между предложениями, растянутые от «фона» до 1. У e5 даже несвязанные фразы дают ~0.75,
    поэтому за ноль берём медианную похожесть пар внутри лекции."""
    sims = vecs @ vecs.T
    if base is None:
        n = len(vecs)
        base = float(np.median(sims[np.triu_indices(n, 1)])) if n > 2 else 0.7
    s = np.clip((sims - base) / max(1e-6, 1 - base), 0, 1)
    np.fill_diagonal(s, 1.0)
    return s, base


def select(sim: np.ndarray, weights: np.ndarray, eligible: np.ndarray, max_k: int,
           target: float = 0.8) -> tuple[list[int], float]:
    """Жадное покрытие: возвращает индексы выбранных предложений (по порядку в тексте) и долю покрытия."""
    n = len(sim)
    if n == 0:
        return [], 0.0
    total = float(weights.sum()) or 1.0
    cover = np.zeros(n, dtype=np.float32)
    chosen: list[int] = []
    avail = eligible.copy()
    if not avail.any():
        avail[:] = True
    while len(chosen) < max_k:
        gains = (weights[:, None] * np.maximum(sim - cover[:, None], 0)).sum(axis=0)
        gains[~avail] = -1
        j = int(np.argmax(gains))
        if gains[j] <= 1e-6:
            break
        chosen.append(j)
        avail[j] = False
        cover = np.maximum(cover, sim[:, j])
        if float((weights * cover).sum()) / total >= target:
            break
    return sorted(chosen), float((weights * cover).sum()) / total


DEIXIS = {"вот", "здесь", "тут", "там", "это", "этот", "эта", "эти", "такой", "так", "сюда", "туда"}
# Фраза оборвана: заканчивается предлогом, союзом или тире («Если мы посмотрим на.», «Производная в этой точке –.»)
DANGLING_RE = re.compile(r"(?:(?<![а-яёa-z])(?:на|в|во|с|к|по|от|до|из|и|а|но|что|как|для|при|о|об|у|за|это|или)|[–—-])\s*[.!?…]*$",
                         re.IGNORECASE)
# Связки лектора без содержания: «Давайте рассмотрим задачу подробнее», «Теперь разберём…», «Для этого зададим вот другое»
PROCEDURAL_RE = re.compile(r"^(?:(?:теперь|ну|итак|а|и|так|далее|вот)[,\s]+)*(?:давайте|посмотрим|рассмотрим|рассматривать будем|"
                           r"перейд[её]м|начн[её]м|вернёмся|для этого|разбер[её]м|поговорим|что мы делаем)\b", re.IGNORECASE)
# Анонс без самой мысли: «А в матрице ситуация следующая.», «Логика следующая.»
ANNOUNCE_RE = re.compile(r"(?:следующ(?:ая|ее|ий|ие|им образом)|таким образом|вот так)\s*[.:!…]*$", re.IGNORECASE)
# Косвенный вопрос вместо утверждения: «Что представляет собой список смежности и как будет производиться обработка.»
QUESTION_RE = re.compile(r"^(?:(?:а|и|ну)\s+)?(?:что|как|какой|какая|какие|каков\w*|почему|зачем|сколько)\b", re.IGNORECASE)


def sentence_weights(texts: list[str], types: list[str]) -> tuple[np.ndarray, np.ndarray]:
    """Вес предложения в покрытии и можно ли брать его в конспект.
    Организационные моменты и обрывки почти ничего не весят; определения — чуть важнее.
    В конспект не попадают фразы, которые без видео не понять: обрывки, «вот здесь у меня показано»,
    связки «давайте рассмотрим подробнее» — в живой речи их много, а смысла в них нет."""
    w = np.ones(len(texts), dtype=np.float32)
    ok = np.ones(len(texts), dtype=bool)
    for i, (t, ty) in enumerate(zip(texts, types)):
        n_words = len(t.split())
        content = len(text.lemmas(t))
        deixis = sum(1 for tok in text.tokenize(t) if tok.lower() in DEIXIS)
        if ty == "org":
            w[i], ok[i] = 0.1, False
        elif ty == "definition":
            w[i] = 1.3
        if n_words < 6 or content < 4:
            w[i] *= 0.4
            ok[i] = False
        elif n_words > 60:
            ok[i] = False
        s = t.strip()
        if DANGLING_RE.search(s) or (deixis >= 2 and content < 7) or (PROCEDURAL_RE.search(s) and content < 8) \
                or (ANNOUNCE_RE.search(s) and content < 8) or (QUESTION_RE.search(s) and content < 9) \
                or (s.endswith("?") and content < 6):
            w[i] *= 0.6
            ok[i] = False
    return w, ok


def coverage_of(sim: np.ndarray, weights: np.ndarray, chosen_cols: np.ndarray) -> float:
    if sim.size == 0 or not len(chosen_cols):
        return 0.0
    cover = sim[:, chosen_cols].max(axis=1)
    return float((weights * cover).sum() / (weights.sum() or 1.0))
