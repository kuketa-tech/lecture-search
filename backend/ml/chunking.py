"""Нарезка лекции на фрагменты для поиска: окна из целых предложений с перекрытием."""


def make_chunks(sents: list[dict], target: float = 45.0, overlap: float = 0.5) -> list[dict]:
    """Одна фраза — слишком мало контекста, целый раздел — нужная мысль растворяется в векторе.
    Окно ~45 с, следующее начинается с середины предыдущего, чтобы мысль на стыке не потерялась."""
    chunks: list[dict] = []
    n = len(sents)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and sents[j]["end"] - sents[i]["start"] < target:
            j += 1
        chunks.append({
            "idx": len(chunks),
            "start": sents[i]["start"],
            "end": sents[j]["end"],
            "s_from": i,
            "s_to": j,
            "text": " ".join(s["text"] for s in sents[i:j + 1]),
        })
        if j == n - 1:
            break
        shift_to = sents[i]["start"] + target * (1 - overlap)
        k = i + 1
        while k <= j and sents[k]["start"] < shift_to:
            k += 1
        i = max(i + 1, min(k, j))
    return chunks
