"""Распознавание речи: faster-whisper со временем каждого слова и фильтром галлюцинаций."""
import re
import wave

import numpy as np

from .. import config
from . import models

# Whisper обучен в том числе на субтитрах с YouTube и в паузах «вспоминает» их подписи.
HALLUCINATION_RE = re.compile(
    r"субтитр|продолжение следует|dimatorzok|корректор а\.|подписывайтесь на (?:наш )?канал|"
    r"спасибо за просмотр|ставьте лайк|amara\.org",
    re.IGNORECASE)


def _is_hallucination(seg) -> bool:
    text = seg.text.strip()
    if not text:
        return True
    if HALLUCINATION_RE.search(text) and len(text.split()) <= 10:
        return True
    if getattr(seg, "compression_ratio", 0) > 2.4:  # текст подозрительно хорошо сжимается — значит, повторы
        return True
    if getattr(seg, "no_speech_prob", 0) > 0.6 and getattr(seg, "avg_logprob", 0) < -1.0:
        return True
    return False


def make_prompt(title: str) -> str:
    """Затравка для Whisper. Модель подражает её стилю: без точек в затравке и текст выйдет без точек,
    поэтому даём аккуратную фразу с пунктуацией и названием лекции (подсказывает термины)."""
    title = title.strip().rstrip(".")
    return f"Лекция «{title}». Добрый день! Сегодня мы разберём новую тему, начнём с определений, а затем перейдём к примерам."


def transcribe(audio_path: str, duration: float, prompt: str | None = None, progress=None) -> list[dict]:
    """Возвращает слова: [{w, s, e, p}] — текст (с ведущим пробелом), начало, конец, уверенность."""
    if models.device() == "cuda":
        import torch  # noqa: F401  — на Windows подгружает DLL cuBLAS/cuDNN, нужные CTranslate2
    from faster_whisper import BatchedInferencePipeline, WhisperModel

    dev = models.device()
    compute = "float16" if dev == "cuda" else "int8"
    with models.heavy_lock:
        from . import llm
        llm.unload_local()  # освобождаем видеопамять под Whisper
        model = WhisperModel(config.ASR_MODEL, device=dev, compute_type=compute,
                             download_root=str(config.MODELS_DIR / "whisper"))
        try:
            pipe = BatchedInferencePipeline(model=model)
            segments, _info = pipe.transcribe(
                _load_wav(audio_path),
                language=config.ASR_LANGUAGE,
                batch_size=config.ASR_BATCH if dev == "cuda" else 4,
                beam_size=5,
                word_timestamps=True,
                vad_filter=True,
                initial_prompt=prompt,
                # С временными метками пакетный режим не теряет хвосты окон: на демо-лекциях WER 4.6% → 1.1%
                without_timestamps=False,
            )
            words: list[dict] = []
            for seg in segments:
                if progress and duration:
                    progress(min(1.0, seg.end / duration))
                if _is_hallucination(seg):
                    continue
                for w in seg.words or []:
                    if not w.word.strip():
                        continue
                    words.append({"w": w.word, "s": round(w.start, 2), "e": round(w.end, 2),
                                  "p": round(w.probability, 3)})
        finally:
            del model
            models.free_gpu()
    words.sort(key=lambda w: w["s"])
    return _collapse_loops(words)


def _load_wav(path: str) -> np.ndarray:
    """WAV 16 кГц моно (его готовит media.extract_audio) → float32. Читаем сами, без PyAV:
    faster-whisper 1.2 несовместим с PyAV 19, а декодировать здесь уже нечего."""
    with wave.open(path, "rb") as w:
        if w.getframerate() != 16000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError("ожидается WAV 16 кГц, моно, 16 бит")
        data = w.readframes(w.getnframes())
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0


def _collapse_loops(words: list[dict]) -> list[dict]:
    """Убирает зацикливания вида «и и и и и» и одно и то же слово 5+ раз подряд."""
    out: list[dict] = []
    run = 0
    for w in words:
        if out and w["w"].strip().lower() == out[-1]["w"].strip().lower():
            run += 1
            if run >= 2:
                continue
        else:
            run = 0
        out.append(w)
    return out
