"""Загрузка моделей по требованию. Кодировщик и реранкер живут в памяти постоянно (нужны поиску),
Whisper и локальная LLM — только пока работают, чтобы всё влезало в 12 ГБ видеопамяти."""
import gc
import threading
from functools import lru_cache

import numpy as np

from .. import config

# Тяжёлые модели (Whisper, локальная LLM) не должны грузиться одновременно.
heavy_lock = threading.Lock()


@lru_cache(maxsize=1)
def device() -> str:
    if config.DEVICE in ("cuda", "cpu"):
        return config.DEVICE
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def free_gpu() -> None:
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


class Embedder:
    """Кодировщик предложений: текст → вектор единичной длины, близкие по смыслу тексты — близкие векторы."""

    def __init__(self, name: str):
        from sentence_transformers import SentenceTransformer
        self.name = name
        self.model = SentenceTransformer(name, device=device())
        if device() == "cuda":
            self.model.half()
        # e5 обучали с префиксами: вопрос и документ кодируются по-разному (асимметричный поиск).
        is_e5 = "e5" in name.lower()
        self.q_prefix = "query: " if is_e5 else ""
        self.p_prefix = "passage: " if is_e5 else ""
        self.dim = self.model.get_sentence_embedding_dimension()
        self._lock = threading.Lock()
        self._qcache: dict[str, np.ndarray] = {}

    def _encode(self, texts: list[str], batch_size: int = 64) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        with self._lock:
            v = self.model.encode(texts, batch_size=batch_size, normalize_embeddings=True,
                                  convert_to_numpy=True, show_progress_bar=False)
        return v.astype(np.float32)

    def passages(self, texts: list[str]) -> np.ndarray:
        return self._encode([self.p_prefix + t for t in texts])

    def queries(self, texts: list[str]) -> np.ndarray:
        return self._encode([self.q_prefix + t for t in texts])

    def query(self, text: str) -> np.ndarray:
        v = self._qcache.get(text)
        if v is None:
            v = self.queries([text])[0]
            if len(self._qcache) > 2000:
                self._qcache.clear()
            self._qcache[text] = v
        return v


class Reranker:
    """Cross-encoder: читает запрос и фрагмент вместе и оценивает, отвечает ли фрагмент на запрос (0..1)."""

    def __init__(self, name: str):
        from sentence_transformers import CrossEncoder
        self.name = name
        self.model = CrossEncoder(name, device=device(), max_length=512)
        if device() == "cuda":
            self.model.model.half()
        self._lock = threading.Lock()

    def score(self, query: str, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros(0, dtype=np.float32)
        with self._lock:
            s = np.asarray(self.model.predict([(query, t) for t in texts], batch_size=32,
                                              show_progress_bar=False), dtype=np.float32)
        if s.min() < 0 or s.max() > 1:  # модель вернула логиты — переводим в вероятность
            s = 1 / (1 + np.exp(-s))
        return s


_embedders: dict[str, Embedder] = {}
_reranker: Reranker | None = None
_reranker_failed = False
_load_lock = threading.Lock()


def embedder(name: str | None = None) -> Embedder:
    name = name or config.EMBED_MODEL
    with _load_lock:
        if name not in _embedders:
            _embedders[name] = Embedder(name)
        return _embedders[name]


def reranker() -> Reranker | None:
    global _reranker, _reranker_failed
    if not config.RERANK_MODEL or _reranker_failed:
        return None
    with _load_lock:
        if _reranker is None:
            try:
                _reranker = Reranker(config.RERANK_MODEL)
            except Exception as e:  # без реранкера поиск работает, просто чуть хуже
                print(f"[models] реранкер не загрузился: {e}")
                _reranker_failed = True
                return None
        return _reranker
