"""Файлы лекции: видео, звук, слова с таймкодами, векторы предложений и фрагментов."""
import json
import shutil
from pathlib import Path

import numpy as np

from . import config


def lecture_dir(lecture_id: str) -> Path:
    d = config.LECTURES_DIR / lecture_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_json(lecture_id: str, name: str, obj) -> None:
    (lecture_dir(lecture_id) / name).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def load_json(lecture_id: str, name: str, default=None):
    p = config.LECTURES_DIR / lecture_id / name
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def save_vecs(lecture_id: str, name: str, vecs: np.ndarray) -> None:
    np.save(lecture_dir(lecture_id) / f"{name}.npy", vecs.astype(np.float16))


def load_vecs(lecture_id: str, name: str) -> np.ndarray | None:
    p = config.LECTURES_DIR / lecture_id / f"{name}.npy"
    if not p.exists():
        return None
    return np.load(p).astype(np.float32)


def remove(lecture_id: str) -> None:
    shutil.rmtree(config.LECTURES_DIR / lecture_id, ignore_errors=True)
