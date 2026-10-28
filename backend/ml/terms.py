"""Ключевые термины и глоссарий.

1. Кандидаты — именные группы по частям речи: «квадрат гипотенузы», «теорема Пифагора», «градиентный спуск».
2. Оценка = частота × специфичность (насколько редко встречается в других лекциях) × близость вектора термина
   к смыслу лекции. Так «пример» и «вопрос» не становятся терминами, а «гипотенуза» становится.
3. MMR убирает почти одинаковые термины.
4. Для каждого термина ищем момент, где его определяют («называется», «— это»), и все упоминания."""
import math
import re
from collections import defaultdict

import numpy as np

from . import text

GENERIC = set("""
раз время вопрос случай человек дело минута секунда слово вещь пример лекция тема часть вид место сторона год день
смысл образ момент штука ситуация способ результат вариант проблема картинка слайд презентация студент друг конец
начало сегодня понятие определение вещь кусок история жизнь мир страна город глава пункт номер шаг этап задание
рисунок экран доска мел ответ работа помощь целое сила суть цель итог неделя занятие курс семинар группа процесс
число количество размер порядок оценка значение проверка конструкция реализация уровень выражение пояснение вычисление
анализ операция линия цвет счёт счет тысяча сотня единица двойка тройка четвёрка четверка пятёрка пятерка шестёрка
шестерка десятка точка элемент связь форма формат вывод состояние действие видео канал комментарий лайк подписка
""".split())
DEF_RE = re.compile(r"\bназыва(ется|ют|ем)\b|\bопредел(ение|им|яется)\b|\bпод \w+ понима|\s— это\b|\bэто есть\b|\bназовём\b|\bназовем\b",
                    re.IGNORECASE)
TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9]*(?:-[A-Za-zА-Яа-яЁё0-9]+)*|\d+[A-Za-zА-Яа-я]*")


# Прилагательные-местоимения и оценки не делают группу термином: «каждая вершина», «первый способ».
WEAK_ADJ = set("""
каждый любой весь другой этот такой следующий первый второй третий новый нужный данный разный простой важный
основной главный общий сам свой определенный целый большой маленький хороший плохой некоторый последний самый один
обычный быстрый медленный правильный нужный конкретный сложный легкий старый равный дальнейший дополнительный
соответствующий определённый необходимый возможный известный математический небольшой огромный
""".split())
# Устойчивые обороты речи, а не термины: «с точки зрения», «для дальнейшей работы».
IDIOMS = {("точка", "зрение"), ("дальнейший", "работа"), ("общий", "случай"), ("друг", "друг")}
LINK_PREPS = {"в", "на", "по", "с", "без", "для", "от", "из", "к"}  # «поиск в ширину», «обучение с учителем»
PROPER = ("Name", "Surn", "Patr", "Geox", "Orgn", "Trad", "Abbr")


# Существительные-«оболочки»: «число рёбер», «время работы» — не термины, хотя и устойчивые сочетания.
GENERIC_HEADS = {"число", "время", "количество", "размер", "способ", "вид", "тип", "часть", "набор", "порядок",
                 "случай", "пример", "вопрос", "результат", "сумма", "свойство", "идея", "смысл", "работа",
                 "значение", "реализация", "проверка", "использование", "рамка", "помощь"}
EMPTY_NOUNS = GENERIC_HEADS | {"сторона", "вещь", "штука", "момент", "дело", "место", "образ", "вариант"}


def _parse(word: str):
    return text._morph().parse(word)[0]


def _can_be_gent_noun(word: str) -> bool:
    """«смежности» — первым разбором pymorphy может дать предложный падеж; смотрим все разборы."""
    return any(str(p.tag.POS or "") == "NOUN" and "gent" in p.tag for p in text._morph().parse(word))


def _is_proper(word: str, p, mid_cap: bool = False) -> bool:
    """Имя собственное: так считает словарь, или это латиница/аббревиатура, или слово с заглавной не в начале фразы
    («алгоритм Дейкстры» — фамилии нет в словаре pymorphy)."""
    return any(g in p.tag for g in PROPER) or "LATN" in p.tag or (word.isupper() and len(word) > 1) or mid_cap


def _nominative(words: list[str], parses, mid_caps: list[bool] | None = None) -> str:
    """Ставит именную группу в именительный падеж: «квадрата гипотенузы» → «квадрат гипотенузы».
    Заглавная буква остаётся только у имён собственных и аббревиатур (а не у слова в начале предложения)."""
    head = next((i for i, p in enumerate(parses) if str(p.tag.POS or "") == "NOUN"), None)
    if head is None:
        return " ".join(words)
    mid_caps = mid_caps or [False] * len(words)
    head_p = parses[head]
    head_nom = head_p.inflect({"nomn"}) or head_p
    number = head_nom.tag.number or "sing"
    gender = head_nom.tag.gender
    out = []
    for i, (w, p) in enumerate(zip(words, parses)):
        if i < head and str(p.tag.POS or "") in ("ADJF", "PRTF"):
            feats = {"nomn", number} if number == "plur" or not gender else {"nomn", gender, "sing"}
            f = p.inflect(feats)
            out.append(f.word if f else w.lower())
        elif i == head:
            out.append(head_nom.word.capitalize() if _is_proper(w, head_p, mid_caps[i]) else head_nom.word)
        else:
            out.append(w if _is_proper(w, p, mid_caps[i]) else w.lower())
    return " ".join(out)


def candidates(sentences: list[str]) -> dict[tuple, dict]:
    """Ключ — кортеж лемм. Значение: формы написания, номера предложений с упоминаниями."""
    cands: dict[tuple, dict] = {}
    for si, sent in enumerate(sentences):
        words = TOKEN_RE.findall(sent)
        parses = [_parse(w) for w in words]
        # str(): атрибуты тегов pymorphy при сравнении проверяют граммему и падают на «LATN»
        pos = ["LATN" if "LATN" in p.tag else str(p.tag.POS or "") for p in parses]
        lem = [p.normal_form.replace("ё", "е") for p in parses]
        n = len(words)
        mid_cap = [k > 0 and w[0].isupper() for k, w in enumerate(words)]
        spans = []
        for i, ps in enumerate(pos):
            if ps == "LATN" and len(words[i]) >= 2:
                spans.append((i, i))
            if ps != "NOUN":
                continue
            j = i
            while j - 1 >= 0 and pos[j - 1] in ("ADJF", "PRTF") and lem[j - 1] not in WEAK_ADJ and i - (j - 1) <= 2:
                j -= 1
            ends = {i}
            if i + 1 < n and pos[i + 1] == "NOUN" and _can_be_gent_noun(words[i + 1]):
                ends.add(i + 1)
            if i + 2 < n and pos[i + 1] == "PREP" and lem[i + 1] in LINK_PREPS:
                if pos[i + 2] == "NOUN":
                    ends.add(i + 2)
                elif i + 3 < n and pos[i + 2] in ("ADJF", "PRTF") and lem[i + 2] not in WEAK_ADJ and pos[i + 3] == "NOUN":
                    ends.add(i + 3)
            for a in {i, j}:
                for b in ends:
                    if b - a + 1 <= 4:
                        spans.append((a, b))
        for a, b in spans:
            key = tuple(lem[a:b + 1])
            if len(key) == 1 and (key[0] in GENERIC or key[0] in text.STOPWORDS or len(key[0]) < 3):
                continue
            if any(k in text.STOPWORDS for k, ps in zip(key, pos[a:b + 1]) if ps != "PREP"):
                continue
            if len(key) > 1 and pos[a] == "NOUN" and key[0] in GENERIC_HEADS:
                continue
            # «математическая сторона», «дальнейшая работа»: прилагательное при пустом существительном
            if len(key) > 1 and pos[a] in ("ADJF", "PRTF") and key[-1] in EMPTY_NOUNS:
                continue
            if any(key[k:k + 2] in IDIOMS for k in range(len(key) - 1)):
                continue
            c = cands.setdefault(key, {"forms": defaultdict(int), "sents": [], "count": 0})
            c["forms"][_nominative(words[a:b + 1], parses[a:b + 1], mid_cap[a:b + 1])] += 1
            c["count"] += 1
            if not c["sents"] or c["sents"][-1] != si:
                c["sents"].append(si)
    return cands


def _display(c: dict) -> str:
    # при равенстве предпочитаем форму с заглавной: «Дейкстра» в начале фразы и в середине — одно имя
    return max(c["forms"].items(), key=lambda kv: kv[1] + (0.5 if any(ch.isupper() for ch in kv[0]) else 0))[0]


def extract(sentences: list[dict], sent_vecs: np.ndarray, embedder, other_docs: list[set[str]],
            max_terms: int = 20) -> tuple[list[dict], dict, dict[tuple, np.ndarray]]:
    texts = [s["text"] for s in sentences]
    cands = candidates(texts)
    cands = {k: v for k, v in cands.items() if v["count"] >= 2}
    if not cands:
        return [], {}, {}
    n_docs = len(other_docs) + 1

    def spec(key) -> float:
        df = sum(1 for d in other_docs if all(k in d for k in key))
        return 1.0 + math.log((n_docs + 1) / (df + 1))

    # Одиночное слово, которое почти всегда стоит внутри более длинного термина («смежность» внутри
    # «матрица смежности» и «список смежности»), самостоятельным термином не считаем.
    inside: dict[str, int] = defaultdict(int)
    for k, v in cands.items():
        if len(k) > 1:
            for w in set(k):
                inside[w] += v["count"]
    cands = {k: v for k, v in cands.items() if len(k) > 1 or inside[k[0]] < 0.7 * v["count"]}
    base = {k: math.log1p(v["count"]) * (1.6 if len(k) > 1 else 1.0) * spec(k) for k, v in cands.items()
            if len(k) > 1 or v["count"] >= 3}
    if not base:
        return [], cands, {}
    top = sorted(base, key=base.get, reverse=True)[:200]
    tvecs = embedder.passages([_display(cands[k]) for k in top])
    centroid = sent_vecs.mean(axis=0)
    centroid /= np.linalg.norm(centroid) + 1e-9
    sem = tvecs @ centroid
    sem_z = (sem - sem.mean()) / (sem.std() + 1e-6)
    score = np.array([base[k] for k in top]) * (1.0 + np.clip(sem_z, -0.9, 2.0) * 0.5)

    # MMR: следующий термин — важный, но не похожий на уже выбранные.
    chosen: list[int] = []
    order = list(np.argsort(-score))
    norm_score = score / (score.max() or 1)
    while order and len(chosen) < max_terms:
        best, best_val = None, -1e9
        for i in order[:60]:
            red = max((float(tvecs[i] @ tvecs[j]) for j in chosen), default=0.0)
            val = 0.75 * norm_score[i] - 0.25 * red * 1.5
            if val > best_val:
                best, best_val = i, val
        order.remove(best)
        key = top[best]
        # одиночное слово, которое почти всегда входит в уже выбранную группу, не берём
        if len(key) == 1 and any(key[0] in top[j] and len(top[j]) > 1 and
                                 cands[key]["count"] <= 1.5 * cands[top[j]]["count"] for j in chosen):
            continue
        chosen.append(best)

    terms = []
    for i in chosen:
        key = top[i]
        c = cands[key]
        mention_idx = c["sents"]
        best_def, best_val = None, 0.0
        for si in mention_idx:
            t = texts[si]
            if not _defines(t, key):
                continue
            val = 2.0 + (1.0 if sentences[si].get("type") == "definition" else 0.0)
            if 5 <= len(t.split()) <= 60:
                val -= 0.002 * si  # при прочих равных — раньше
                if val > best_val:
                    best_def, best_val = si, val
        terms.append({
            "term": _display(c),
            "lemma": " ".join(key),
            "score": round(float(score[i]), 3),
            "count": c["count"],
            "first_time": sentences[mention_idx[0]]["start"],
            "def_text": texts[best_def] if best_def is not None else None,
            "def_time": sentences[best_def]["start"] if best_def is not None else None,
            "mentions": [round(sentences[si]["start"], 1) for si in mention_idx],
        })
    term_vecs = {top[i]: tvecs[i] for i in range(len(top))}
    return terms, cands, term_vecs


def _defines(sentence: str, key: tuple) -> bool:
    """Определяет ли фраза именно этот термин, а не упоминает его.
    «Степенью вершины называют число рёбер»: при «называть» определяемое слово стоит в творительном падеже.
    «Граф — это …», «Под графом понимают …», «Определение графа …» — термин до «это» или после «под/определение»."""
    words = TOKEN_RE.findall(sentence)
    parses = [_parse(w) for w in words]
    lem = [p.normal_form.replace("ё", "е") for p in parses]
    head = key[0]
    pos = [i for i, l in enumerate(lem) if l == head]
    if not pos:
        return False
    low = sentence.lower()
    if "называ" in low:
        return any("ablt" in parses[i].tag for i in pos)
    m = re.search(r"\s(?:—|–|-)?\s*это\s", low)
    if m:  # «Граф — это …»: термин — подлежащее, в именительном падеже, сразу перед «это»
        cut = len(TOKEN_RE.findall(sentence[:m.start()]))
        return cut <= len(key) + 3 and any(i < cut and "nomn" in parses[i].tag for i in pos)
    if re.search(r"\bпод\s", low) and "понима" in low:
        return any(i > 0 and lem[i - 1] == "под" for i in pos)
    if re.search(r"\bопредел(?:ение|им|яется)\b", low):
        return True
    return False


def section_keyphrases(cands: dict, term_vecs: dict, s_from: int, s_to: int, sec_vec: np.ndarray,
                       all_ranges: list[tuple[int, int]], k: int = 6) -> list[str]:
    """Ключевые фразы раздела по убыванию: частые в этом разделе, редкие в остальных и близкие к его смыслу.
    Первая — название раздела без LLM; весь список — подсказка LLM, из каких слов строить название."""
    scored = []
    n_sec = max(len(all_ranges), 1)
    for key, c in cands.items():
        tf = sum(1 for si in c["sents"] if s_from <= si <= s_to)
        if not tf or key not in term_vecs:
            continue
        df = sum(1 for a, b in all_ranges if any(a <= si <= b for si in c["sents"]))
        val = math.log1p(tf) * math.log(1 + n_sec / df) * (1.3 if len(key) > 1 else 1.0)
        val *= 1.0 + 2.0 * max(0.0, float(term_vecs[key] @ sec_vec) - 0.75)
        scored.append((val, key))
    scored.sort(reverse=True)
    out = []
    morph = text._morph()
    for _, key in scored:
        title = _display(cands[key])
        # ошибки распознавания («тэтажей» вместо «тет») не годятся в названия; фамилии с заглавной — годятся
        if any(re.match(r"[а-яё]", w) and not morph.word_is_known(w) for w in title.split()):
            continue
        title = title[0].upper() + title[1:]
        if title.lower() not in (t.lower() for t in out):
            out.append(title)
        if len(out) >= k:
            break
    return out


def section_title(cands: dict, term_vecs: dict, s_from: int, s_to: int, sec_vec: np.ndarray,
                  all_ranges: list[tuple[int, int]]) -> str:
    """Название раздела без LLM: лучшая ключевая фраза раздела."""
    kp = section_keyphrases(cands, term_vecs, s_from, s_to, sec_vec, all_ranges, k=1)
    return kp[0] if kp else ""
