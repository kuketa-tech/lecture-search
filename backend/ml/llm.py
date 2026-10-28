"""LLM — только для формулировок. Что важно в лекции, решают векторы; LLM получает уже отобранный текст.

Бэкенды:
- openai — любой OpenAI-совместимый сервер: Ollama (http://127.0.0.1:11434/v1), LM Studio, DeepSeek, GigaChat-прокси;
- local  — небольшая модель прямо в процессе через transformers (офлайн, без внешних программ);
- none   — без LLM: конспект и названия собираются из векторов, чат показывает найденные фрагменты."""
import json
import re
import threading
import time

import httpx

from .. import config
from . import models

THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
SYSTEM = ("Ты помогаешь студентам работать с видеолекциями. Пиши по-русски, кратко и грамотно. "
          "Используй только факты из переданного текста лекции, ничего не придумывай от себя.")


class LLMError(RuntimeError):
    pass


class OpenAIBackend:
    def __init__(self, base_url: str, model: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._ok_at = 0.0
        self._ok = False

    @property
    def name(self) -> str:
        return self.model

    def available(self) -> bool:
        if time.time() - self._ok_at < 30:
            return self._ok
        try:
            r = httpx.get(f"{self.base_url}/models", headers=self.headers, timeout=2.5)
            self._ok = r.status_code == 200
        except httpx.HTTPError:
            self._ok = False
        self._ok_at = time.time()
        return self._ok

    def chat(self, messages: list[dict], max_tokens: int = 900, temperature: float = 0.2) -> str:
        body = {"model": self.model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature,
                "stream": False}
        try:
            r = httpx.post(f"{self.base_url}/chat/completions", json=body, headers=self.headers, timeout=300)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"] or ""
        except (httpx.HTTPError, KeyError, ValueError) as e:
            self._ok_at = 0
            raise LLMError(f"LLM недоступна: {e}") from e


class LocalBackend:
    """Модель в процессе. Загружается при первом запросе, выгружается перед распознаванием речи."""

    def __init__(self, model_name: str):
        self.model_name = model_name
        self.model = None
        self.tok = None
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self.model_name.split("/")[-1]

    def available(self) -> bool:
        if self.model is not None:
            return True
        try:
            from huggingface_hub import try_to_load_from_cache
            return isinstance(try_to_load_from_cache(self.model_name, "config.json"), str)
        except Exception:
            return False

    def _load(self):
        if self.model is None:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
            self.tok = AutoTokenizer.from_pretrained(self.model_name)
            dtype = torch.float16 if models.device() == "cuda" else torch.float32
            self.model = AutoModelForCausalLM.from_pretrained(self.model_name, dtype=dtype).to(models.device())
            self.model.eval()

    def unload(self):
        with self._lock:
            if self.model is not None:
                self.model = None
                self.tok = None
                models.free_gpu()

    def chat(self, messages: list[dict], max_tokens: int = 900, temperature: float = 0.2) -> str:
        import torch
        try:
            with models.heavy_lock, self._lock:
                self._load()
                enc = self.tok.apply_chat_template(messages, add_generation_prompt=True, return_tensors="pt",
                                                   return_dict=True)
                enc = {k: v.to(self.model.device) for k, v in enc.items()}
                n_in = enc["input_ids"].shape[1]
                gen = {"do_sample": True, "temperature": temperature, "top_p": 0.9} if temperature > 0 else {"do_sample": False}
                with torch.inference_mode():
                    out = self.model.generate(**enc, max_new_tokens=max_tokens, repetition_penalty=1.05,
                                              pad_token_id=self.tok.eos_token_id, **gen)
                return self.tok.decode(out[0, n_in:], skip_special_tokens=True)
        except Exception as e:  # локальная модель не должна ронять обработку лекции
            raise LLMError(f"локальная LLM: {type(e).__name__}: {e}") from e


_openai = OpenAIBackend(config.LLM_BASE_URL, config.LLM_MODEL, config.LLM_API_KEY)
_local = LocalBackend(config.LOCAL_LLM_MODEL)


def backend():
    """Текущий бэкенд или None. В режиме auto сервер приоритетнее локальной модели."""
    mode = config.LLM_BACKEND
    if mode == "none":
        return None
    if mode in ("openai", "auto") and _openai.available():
        return _openai
    if mode in ("local", "auto") and _local.available():
        return _local
    return None


def unload_local() -> None:
    _local.unload()


def status() -> dict:
    b = backend()
    return {"mode": config.LLM_BACKEND, "available": b is not None, "name": b.name if b else None,
            "kind": "server" if b is _openai else "local" if b is _local else None}


def chat(prompt: str, max_tokens: int = 900, temperature: float = 0.2, system: str = SYSTEM) -> str:
    b = backend()
    if b is None:
        raise LLMError("LLM не подключена")
    text = b.chat([{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                  max_tokens=max_tokens, temperature=temperature)
    return THINK_RE.sub("", text).strip()


def parse_json(text: str):
    """Достаёт JSON из ответа модели, даже если он обёрнут в ```json ... ``` или окружён пояснениями."""
    text = THINK_RE.sub("", text)
    text = re.sub(r"```(?:json)?", "", text)
    for open_c, close_c in (("{", "}"), ("[", "]")):
        a, b = text.find(open_c), text.rfind(close_c)
        if a != -1 and b > a:
            try:
                return json.loads(text[a:b + 1])
            except ValueError:
                continue
    return None


def chat_json(prompt: str, max_tokens: int = 1500, retries: int = 1):
    last = ""
    for attempt in range(retries + 1):
        # температура 0: конспект воспроизводим — одна и та же лекция даёт одни и те же формулировки
        last = chat(prompt if attempt == 0 else prompt + "\n\nОтветь строго валидным JSON без пояснений.",
                    max_tokens=max_tokens, temperature=0.0)
        data = parse_json(last)
        if data is not None:
            return data
    raise LLMError(f"LLM вернула не JSON: {last[:200]}")
