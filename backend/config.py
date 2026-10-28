"""Настройки. Любой параметр переопределяется переменной окружения или строкой в файле .env."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
LECTURES_DIR = DATA_DIR / "lectures"
DB_PATH = DATA_DIR / "app.db"
EVAL_DIR = DATA_DIR / "eval"
MODELS_DIR = Path(os.getenv("MODELS_DIR", ROOT / "models"))
FRONTEND_DIR = ROOT / "frontend"

# Все веса моделей лежат в проекте: после первого скачивания можно работать без интернета (OFFLINE=1).
os.environ.setdefault("HF_HOME", str(MODELS_DIR / "hf"))
if os.getenv("OFFLINE", "0") == "1":
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

DEVICE = os.getenv("DEVICE", "auto")  # auto | cuda | cpu

ASR_MODEL = os.getenv("ASR_MODEL", "large-v3-turbo")
ASR_BATCH = int(os.getenv("ASR_BATCH", "16"))
ASR_LANGUAGE = os.getenv("ASR_LANGUAGE", "ru")

EMBED_MODEL = os.getenv("EMBED_MODEL", "intfloat/multilingual-e5-large")
RERANK_MODEL = os.getenv("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")  # пустая строка — без реранкера

# LLM нужна только для формулировок (названия разделов, конспект, ответы в чате).
# auto: OpenAI-совместимый сервер (Ollama, LM Studio, DeepSeek...) если доступен, иначе локальная модель, иначе без LLM.
LLM_BACKEND = os.getenv("LLM_BACKEND", "auto")  # auto | openai | local | none
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://127.0.0.1:11434/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen2.5:7b")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "Qwen/Qwen2.5-3B-Instruct")

# Поиск
CHUNK_SECONDS = float(os.getenv("CHUNK_SECONDS", "45"))
CHUNK_OVERLAP = float(os.getenv("CHUNK_OVERLAP", "0.5"))
RERANK_TOP = int(os.getenv("RERANK_TOP", "30"))
# Порог уверенности реранкера: ниже — считаем, что в лекциях об этом не говорится. Подбирается backend.eval:
# на живых лекциях «пустые» запросы («как работает протокол TCP») набирают до 0.02, верные ответы почти всегда выше 0.07.
RERANK_THRESHOLD = float(os.getenv("RERANK_THRESHOLD", "0.03"))

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))

for d in (DATA_DIR, LECTURES_DIR, EVAL_DIR, MODELS_DIR):
    d.mkdir(parents=True, exist_ok=True)
