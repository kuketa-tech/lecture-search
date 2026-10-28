"""Текст: сборка предложений из слов Whisper, лемматизация для BM25, чистка для конспекта, субтитры."""
import re
from functools import lru_cache

WORD_RE = re.compile(r"[a-zа-яё0-9]+(?:[-'][a-zа-яё0-9]+)*", re.IGNORECASE)
CYR_RE = re.compile(r"[а-яё]", re.IGNORECASE)
SENT_END_RE = re.compile(r"[.!?…]+[»\"')\]]*$")

# Служебные слова и паразиты устной речи (в леммах): в поиске по словам они только шумят.
STOPWORDS = set("""
и в во не что он на я с со как а то все весь она так его но да ты к у же вы за бы по только ее мне было вот от меня
еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь опять уж вам ведь там
потом себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе
под будет ж тогда кто этот того потому этого какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были
куда зачем всех никогда можно при наконец два об другой хоть после над больше тот через эти нас про всего них
какая много разве три эту моя впрочем хорошо свою этой перед иногда лучше чуть том нельзя такой им более всегда
конечно всю между это свой который мочь сказать говорить очень вообще значит типа короче просто собственно давать
давайте давай например кстати ага угу э эм ээ эээ ой хм ладно итак также либо наш ваш оно они мы вот какой-то
что-то как-то где-то кто-то вот-вот ещё сегодня смотреть посмотреть смотрите видеть получаться получается
""".split())

LEADING_FILLERS_RE = re.compile(
    r"^(?:(?:ну|вот|так|значит|короче|в общем|собственно|ладно|давайте|смотрите|слушайте|э+|эм+|м+|итак)[,.\s]+)+",
    re.IGNORECASE)
REPEAT_RE = re.compile(r"\b(\w+)(?:[\s,]+\1\b)+", re.IGNORECASE)


@lru_cache(maxsize=1)
def _morph():
    import pymorphy3
    return pymorphy3.MorphAnalyzer()


@lru_cache(maxsize=300_000)
def lemma(word: str) -> str:
    word = word.lower().replace("ё", "е")
    if not CYR_RE.search(word):
        return word
    return _morph().parse(word)[0].normal_form.replace("ё", "е")


def tokenize(text: str) -> list[str]:
    return [t.lower().replace("ё", "е") for t in WORD_RE.findall(text)]


def lemmas(text: str, keep_stopwords: bool = False) -> list[str]:
    out = []
    for tok in tokenize(text):
        lm = lemma(tok)
        if not keep_stopwords and (lm in STOPWORDS or (len(lm) < 2 and not lm.isdigit())):
            continue
        out.append(lm)
    return out


def _join(words: list[dict]) -> str:
    return re.sub(r"\s+", " ", "".join(w["w"] for w in words)).strip()


def build_sentences(words: list[dict], max_words: int = 45, max_sec: float = 25.0, pause: float = 1.2) -> list[dict]:
    """Собирает слова Whisper в предложения по знакам препинания и паузам.
    Whisper режет сегменты по паузам, а не по смыслу — для поиска и субтитров нужны настоящие предложения."""
    sents: list[list[dict]] = []
    cur: list[dict] = []
    for i, w in enumerate(words):
        if cur and w["s"] - cur[-1]["e"] > pause * 2.5:
            sents.append(cur)
            cur = []
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (nxt["s"] - w["e"]) if nxt else 0.0
        if SENT_END_RE.search(w["w"].strip()) and len(cur) >= 3:
            sents.append(cur)
            cur = []
        elif len(cur) >= max_words or cur[-1]["e"] - cur[0]["s"] >= max_sec:
            cut = next((k for k in range(len(cur) - 2, len(cur) // 3, -1) if cur[k]["w"].strip().endswith(",")), None)
            if cut is None:
                sents.append(cur)
                cur = []
            else:
                sents.append(cur[:cut + 1])
                cur = cur[cut + 1:]
        elif gap > pause and len(cur) >= 6:
            sents.append(cur)
            cur = []
    if cur:
        sents.append(cur)

    # Обрывки из 1–2 слов приклеиваем к соседу, иначе они засоряют поиск и субтитры.
    merged: list[list[dict]] = []
    for s in sents:
        if merged and len(s) < 3 and s[0]["s"] - merged[-1][-1]["e"] < 1.5:
            merged[-1].extend(s)
        else:
            merged.append(s)
    if len(merged) > 1 and len(merged[0]) < 3:
        merged[1] = merged[0] + merged[1]
        merged.pop(0)

    return [{"idx": i, "start": round(s[0]["s"], 2), "end": round(s[-1]["e"], 2), "text": _join(s)}
            for i, s in enumerate(merged) if _join(s)]


def clean_for_summary(text: str) -> str:
    """Убирает из устной фразы паразиты в начале и повторы слов — для конспекта без LLM."""
    t = LEADING_FILLERS_RE.sub("", text.strip())
    t = REPEAT_RE.sub(r"\1", t)
    t = re.sub(r"\s+", " ", t).strip(" ,")
    if not t:
        return text.strip()
    t = t[0].upper() + t[1:]
    if not re.search(r"[.!?…]$", t):
        t += "."
    return t


def fmt_time(sec: float) -> str:
    sec = int(max(0, sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _vtt_ts(sec: float) -> str:
    ms = int(round(max(0.0, sec) * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def make_vtt(words: list[dict], max_chars: int = 84, max_sec: float = 6.0) -> str:
    """Субтитры WebVTT из слов: короткие реплики по 1–2 строки, граница — конец предложения или пауза."""
    cues, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        text = _join(cur)
        nxt = words[i + 1] if i + 1 < len(words) else None
        end = (nxt is None or len(text) >= max_chars or cur[-1]["e"] - cur[0]["s"] >= max_sec
               or SENT_END_RE.search(w["w"].strip()) and len(text) > 25
               or (nxt and nxt["s"] - w["e"] > 1.0))
        if end:
            cues.append((cur[0]["s"], cur[-1]["e"], text))
            cur = []
    out = ["WEBVTT", ""]
    for n, (s, e, text) in enumerate(cues, 1):
        if len(text) > 42:  # перенос примерно посередине по пробелу
            mid = text.rfind(" ", 0, len(text) // 2 + 8)
            if mid > 0:
                text = text[:mid] + "\n" + text[mid + 1:]
        out += [str(n), f"{_vtt_ts(s)} --> {_vtt_ts(max(e, s + 0.5))}", text, ""]
    return "\n".join(out)
