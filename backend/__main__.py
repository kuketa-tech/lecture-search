"""Запуск сервера: python -m backend"""
import sys
import threading
import webbrowser

import uvicorn

from . import config

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # консоль Windows в cp1251
    url = f"http://{config.HOST}:{config.PORT}"
    print(f"Суть: открываю {url}")
    threading.Timer(2.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("backend.app:app", host=config.HOST, port=config.PORT, log_level="warning")
