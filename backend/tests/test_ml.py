"""Быстрые тесты алгоритмов без загрузки нейросетей: на синтетических векторах и словах."""
import numpy as np

from backend.ml import bm25, chunking, essence, segmentation, text


def _words(sentences, gap=0.3, word_dur=0.35):
    words, t = [], 0.0
    for s in sentences:
        for w in s.split():
            words.append({"w": " " + w, "s": round(t, 2), "e": round(t + word_dur, 2), "p": 0.9})
            t += word_dur + 0.05
        t += gap
    return words


def test_build_sentences_by_punctuation():
    words = _words(["Это первое предложение лекции.", "А это второе, чуть длиннее первого.", "Третье."])
    sents = text.build_sentences(words)
    assert sents[0]["text"] == "Это первое предложение лекции."
    # обрывок из одного слова приклеивается к предыдущей фразе
    assert len(sents) == 2 and sents[1]["text"] == "А это второе, чуть длиннее первого. Третье."
    assert sents[0]["start"] == 0.0 and sents[1]["start"] > sents[0]["end"]


def test_long_run_is_split():
    words = _words([" ".join(["слово"] * 120)])
    sents = text.build_sentences(words, max_words=45)
    assert len(sents) >= 3


def test_lemmas_and_stopwords():
    assert text.lemmas("Теоремы Пифагора и теорему") == ["теорема", "пифагор", "теорема"]
    assert "ну" not in text.lemmas("ну вот значит граф")


def test_clean_for_summary():
    assert text.clean_for_summary("ну вот значит граф это граф") == "Граф это граф."


def test_chunks_overlap_and_cover():
    sents = [{"idx": i, "start": i * 10.0, "end": i * 10.0 + 9, "text": f"фраза {i}"} for i in range(20)]
    chunks = chunking.make_chunks(sents, target=45, overlap=0.5)
    assert chunks[0]["s_from"] == 0 and chunks[-1]["s_to"] == 19
    assert all(b["s_from"] <= a["s_to"] for a, b in zip(chunks, chunks[1:])), "соседние окна должны перекрываться"


def test_bm25_prefers_matching_doc():
    idx = bm25.BM25([["граф", "вершина"], ["регрессия", "вес"], ["граф", "ребро", "граф"]])
    s = idx.scores(["граф"])
    assert s[1] == 0 and s[2] > 0 and s[0] > 0


def _topic_vectors(sizes, dim=32, noise=0.15, seed=0):
    rng = np.random.default_rng(seed)
    centers = rng.normal(size=(len(sizes), dim))
    vecs = np.vstack([c + noise * rng.normal(size=(n, dim)) for c, n in zip(centers, sizes)]).astype(np.float32)
    return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)


def test_segmentation_finds_topic_changes():
    vecs = _topic_vectors([20, 20, 20])
    starts = np.arange(60, dtype=np.float32) * 6
    secs = segmentation.segment(vecs, starts, starts + 5)
    level1 = [s for s in secs if s["level"] == 1]
    found = sorted(s["s_from"] for s in level1)[1:]
    assert len(found) == 2 and abs(found[0] - 20) <= 1 and abs(found[1] - 40) <= 1


def test_essence_covers_both_subtopics():
    """Среднее двух подтем лежит между ними; покрытие должно выбрать по представителю каждой."""
    vecs = _topic_vectors([10, 10], noise=0.1)
    sim, _ = essence.similarity(vecs)
    w = np.ones(20, dtype=np.float32)
    chosen, cov = essence.select(sim, w, np.ones(20, dtype=bool), max_k=2, target=1.0)
    assert len(chosen) == 2 and (chosen[0] < 10) != (chosen[1] < 10)
    assert cov > 0.5


def test_faithful_catches_negation_and_numbers():
    from backend.ml.summarize import _faithful
    src = "Алгоритм Дейкстры не работает с отрицательными весами рёбер."
    assert _faithful("Алгоритм Дейкстры не подходит для графов с отрицательными весами.", src)
    assert not _faithful("Алгоритм Дейкстры работает с отрицательными весами.", src)
    assert not _faithful("Время работы составляет 5 минут.", "Перерыв 10 минут.")
    assert _faithful("Граф неориентированный, если у рёбер нет направления.", "Если направления нет, граф неориентированный.")


def test_definition_detection():
    from backend.ml.terms import _defines
    s = "Степенью вершины называют число ребер, которые из неё выходят."
    assert _defines(s, ("степень",)) and not _defines(s, ("число", "ребро"))
    assert _defines("Граф — это вершины и рёбра.", ("граф",))
    assert not _defines("Для графа из миллиона вершин это триллион ячеек.", ("вершина",))


def test_heat_and_vtt_format():
    vtt = text.make_vtt(_words(["Первая фраза.", "Вторая фраза подлиннее."]))
    assert vtt.startswith("WEBVTT") and "-->" in vtt


def test_essence_skips_fragments_and_fillers():
    texts = [
        "Обход в глубину идёт по рёбрам до тупика, а потом возвращается к развилке.",  # содержательная
        "Если мы посмотрим на.",  # обрывок
        "Ну вот здесь у меня это показано.",  # указание на слайд
        "Давайте рассмотрим задачу более детально.",  # связка
        "Помните?",
    ]
    _, ok = essence.sentence_weights(texts, ["explanation"] * len(texts))
    assert ok.tolist() == [True, False, False, False, False]


def test_title_must_be_grounded_in_section():
    from backend.ml.summarize import _grounded, _dedupe_titles
    sec = "Поиск в ширину использует очередь: достаём вершину и кладём в очередь её соседей."
    assert _grounded("Очередь в поиске в ширину", sec)
    assert not _grounded("Алгоритм Дейкстры", sec)
    secs = [{"idx": 0, "level": 1, "parent": None, "title": "Массив", "keyphrases": ["Массив", "Множество"]},
            {"idx": 1, "level": 2, "parent": 0, "title": "Массив", "keyphrases": ["Массив", "Множество посещённых вершин"]}]
    _dedupe_titles(secs)
    assert secs[1]["title"] == "Множество посещённых вершин"
