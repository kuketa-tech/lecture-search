"""Скачивает все модели в папку models/ заранее — после этого система работает без интернета (OFFLINE=1).

Запуск:  python -m tools.download_models            (Whisper, кодировщик, реранкер)
         python -m tools.download_models --local-llm (+ локальная LLM для конспектов, ~6 ГБ)"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config  # noqa: E402  — выставляет HF_HOME=models/hf


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # консоль Windows в cp1251
    ap = argparse.ArgumentParser()
    ap.add_argument("--local-llm", action="store_true")
    args = ap.parse_args()
    from huggingface_hub import snapshot_download
    from faster_whisper import download_model

    print(f"Whisper {config.ASR_MODEL}…")
    download_model(config.ASR_MODEL, cache_dir=str(config.MODELS_DIR / "whisper"))
    for name in (config.EMBED_MODEL, config.RERANK_MODEL):
        if name:
            print(f"{name}…")
            snapshot_download(name, allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model", "sentencepiece*",
                                                    "*tokenizer*", "1_Pooling/*", "modules.json"])
    if args.local_llm:
        print(f"{config.LOCAL_LLM_MODEL}…")
        snapshot_download(config.LOCAL_LLM_MODEL, allow_patterns=["*.json", "*.safetensors", "*.txt", "merges.txt", "vocab*"])
    print("Готово. Модели лежат в", config.MODELS_DIR)


if __name__ == "__main__":
    main()
