"""SQLite: по соединению на поток, WAL, чтобы обработка и запросы не блокировали друг друга."""
import json
import sqlite3
import threading
import time

from . import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS lectures (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source_type TEXT NOT NULL,          -- file | url
    source_url TEXT,
    filename TEXT,
    video_file TEXT,                    -- имя файла для плеера внутри папки лекции
    duration REAL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'queued',   -- queued | processing | ready | error
    stage TEXT DEFAULT '',
    progress REAL DEFAULT 0,
    searchable INTEGER DEFAULT 0,       -- 1, как только построен индекс (конспект может ещё готовиться)
    error TEXT,
    llm TEXT,                           -- чем сделан конспект: имя модели или 'vectors'
    created_at REAL NOT NULL,
    processed_at REAL,
    timings TEXT                        -- json: секунды на каждый этап
);
CREATE TABLE IF NOT EXISTS sentences (
    lecture_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    start REAL NOT NULL,
    end REAL NOT NULL,
    text TEXT NOT NULL,
    type TEXT DEFAULT 'explanation',
    PRIMARY KEY (lecture_id, idx)
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lecture_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    start REAL NOT NULL,
    end REAL NOT NULL,
    s_from INTEGER NOT NULL,
    s_to INTEGER NOT NULL,              -- включительно
    text TEXT NOT NULL,
    lemmas TEXT NOT NULL,               -- леммы через пробел, для BM25
    tags TEXT DEFAULT ''                -- типы фрагмента через запятую: definition,example,...
);
CREATE INDEX IF NOT EXISTS chunks_lecture ON chunks(lecture_id);
CREATE TABLE IF NOT EXISTS sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lecture_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    level INTEGER NOT NULL,             -- 1 — раздел, 2 — подраздел
    parent INTEGER,                     -- idx родительского раздела
    start REAL NOT NULL,
    end REAL NOT NULL,
    s_from INTEGER NOT NULL,
    s_to INTEGER NOT NULL,
    title TEXT DEFAULT '',
    bullets TEXT DEFAULT '[]',          -- json: [{text, t, support, verified}]
    essence TEXT DEFAULT '[]',          -- json: индексы предложений-сути
    coverage REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS sections_lecture ON sections(lecture_id);
CREATE TABLE IF NOT EXISTS summaries (
    lecture_id TEXT PRIMARY KEY,
    overview TEXT DEFAULT '',
    key_points TEXT DEFAULT '[]',       -- json: [{text, t, support, verified}]
    method TEXT DEFAULT 'vectors',
    coverage REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS terms (
    lecture_id TEXT NOT NULL,
    term TEXT NOT NULL,
    lemma TEXT NOT NULL,
    score REAL,
    count INTEGER,
    first_time REAL,
    def_text TEXT,
    def_time REAL,
    mentions TEXT DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS terms_lecture ON terms(lecture_id);
CREATE TABLE IF NOT EXISTS qa_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    question TEXT NOT NULL,
    vec BLOB NOT NULL,
    answer TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        c = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        _local.conn = c
    return c


def init() -> None:
    conn().executescript(SCHEMA)
    conn().commit()


def query(sql: str, args=()) -> list[dict]:
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def one(sql: str, args=()) -> dict | None:
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None


def execute(sql: str, args=()) -> int:
    c = conn()
    cur = c.execute(sql, args)
    c.commit()
    return cur.lastrowid


def executemany(sql: str, rows) -> None:
    c = conn()
    c.executemany(sql, rows)
    c.commit()


def update_lecture(lecture_id: str, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    execute(f"UPDATE lectures SET {cols} WHERE id = ?", (*fields.values(), lecture_id))


def delete_lecture_rows(lecture_id: str) -> None:
    c = conn()
    for table in ("sentences", "chunks", "sections", "summaries", "terms"):
        c.execute(f"DELETE FROM {table} WHERE lecture_id = ?", (lecture_id,))
    c.execute("DELETE FROM qa_cache")
    c.execute("DELETE FROM lectures WHERE id = ?", (lecture_id,))
    c.commit()


def loads(s, default=None):
    if s is None or s == "":
        return default
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return default


def now() -> float:
    return time.time()
