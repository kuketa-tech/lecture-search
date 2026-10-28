"""Общий индекс всех лекций в памяти: векторы фрагментов и предложений + BM25.
Отдельная векторная БД не нужна: 10 лекций — это ~5 тыс. фрагментов, перемножение матриц занимает доли миллисекунды.
Интерфейс тот же, что у векторной БД, поэтому при росте данных внутренность меняется на Qdrant/FAISS."""
import threading

import numpy as np

from .. import db, storage
from .bm25 import BM25


class LectureIndex:
    def __init__(self):
        self.lock = threading.RLock()
        self.version = 0
        self.lectures: dict[str, dict] = {}
        self.chunks: list[dict] = []
        self.chunk_vecs = np.zeros((0, 1), dtype=np.float32)
        self.chunk_lecture = np.zeros(0, dtype=object)
        self.sents: dict[str, dict] = {}
        self.bm25 = BM25([])

    def reload(self) -> None:
        lectures = {r["id"]: r for r in db.query(
            "SELECT id, title, duration FROM lectures WHERE searchable = 1 ORDER BY created_at")}
        chunks, vec_parts, sents = [], [], {}
        for lid in lectures:
            rows = db.query("SELECT id, lecture_id, idx, start, end, s_from, s_to, text, lemmas, tags "
                            "FROM chunks WHERE lecture_id = ? ORDER BY idx", (lid,))
            cv = storage.load_vecs(lid, "chunk_vecs")
            sv = storage.load_vecs(lid, "sent_vecs")
            srows = db.query("SELECT idx, start, end, text, type FROM sentences WHERE lecture_id = ? ORDER BY idx", (lid,))
            if cv is None or sv is None or len(cv) != len(rows) or len(sv) != len(srows):
                continue  # лекция ещё в обработке или файлы повреждены
            for r in rows:
                r["tags"] = [t for t in (r["tags"] or "").split(",") if t]
            chunks.extend(rows)
            vec_parts.append(cv)
            sents[lid] = {
                "start": np.array([s["start"] for s in srows], dtype=np.float32),
                "end": np.array([s["end"] for s in srows], dtype=np.float32),
                "text": [s["text"] for s in srows],
                "type": [s["type"] for s in srows],
                "vecs": sv,
            }
        lectures = {k: v for k, v in lectures.items() if k in sents}
        chunk_vecs = np.vstack(vec_parts) if vec_parts else np.zeros((0, 1), dtype=np.float32)
        bm25 = BM25([c["lemmas"].split() for c in chunks])
        with self.lock:
            self.lectures = lectures
            self.chunks = chunks
            self.chunk_vecs = chunk_vecs
            self.chunk_lecture = np.array([c["lecture_id"] for c in chunks], dtype=object)
            self.sents = sents
            self.bm25 = bm25
            self.version += 1

    def snapshot(self):
        with self.lock:
            return self.lectures, self.chunks, self.chunk_vecs, self.chunk_lecture, self.sents, self.bm25


INDEX = LectureIndex()
