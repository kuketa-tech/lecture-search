"""BM25 — классический поиск по словам. Ловит то, что векторы ловят плохо: термины, фамилии, числа."""
import math
from collections import Counter

import numpy as np
from scipy import sparse


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.4, b: float = 0.75):
        self.vocab: dict[str, int] = {}
        rows, cols, tfs = [], [], []
        lengths = np.zeros(len(docs), dtype=np.float32)
        for d, tokens in enumerate(docs):
            lengths[d] = len(tokens)
            for term, tf in Counter(tokens).items():
                col = self.vocab.setdefault(term, len(self.vocab))
                rows.append(d)
                cols.append(col)
                tfs.append(tf)
        n_docs = max(len(docs), 1)
        avgdl = float(lengths.mean()) if len(docs) else 1.0
        tf = np.asarray(tfs, dtype=np.float32)
        rows_a = np.asarray(rows, dtype=np.int64)
        cols_a = np.asarray(cols, dtype=np.int64)
        df = np.bincount(cols_a, minlength=len(self.vocab)).astype(np.float32)
        self.idf = np.log(1 + (n_docs - df + 0.5) / (df + 0.5)).astype(np.float32)
        norm = k1 * (1 - b + b * lengths[rows_a] / max(avgdl, 1e-6)) if len(rows) else np.zeros(0)
        weights = self.idf[cols_a] * tf * (k1 + 1) / (tf + norm) if len(rows) else np.zeros(0)
        # Вес каждого слова в каждом документе считаем заранее — запрос сводится к сумме столбцов.
        self.matrix = sparse.csc_matrix((weights, (rows_a, cols_a)), shape=(len(docs), len(self.vocab)))
        self.n_docs = len(docs)

    def scores(self, query_tokens: list[str]) -> np.ndarray:
        cols = sorted({self.vocab[t] for t in query_tokens if t in self.vocab})
        if not cols or self.n_docs == 0:
            return np.zeros(self.n_docs, dtype=np.float32)
        return np.asarray(self.matrix[:, cols].sum(axis=1)).ravel().astype(np.float32)

    def idf_of(self, term: str) -> float:
        col = self.vocab.get(term)
        return float(self.idf[col]) if col is not None else math.log(1 + self.n_docs)
