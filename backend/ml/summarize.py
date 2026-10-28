"""Конспект и названия разделов: векторы выбирают → LLM формулирует → векторы проверяют.

1. ЧТО важно, решают векторы: суть раздела — задача покрытия (essence.py), главное лекции — то же покрытие
   по фразам-сути всех разделов.
2. КАК сказать, решает LLM, но в узких рамках: по одному разделу, дать название и переписать каждую
   отобранную фразу одним коротким предложением (один к одному).
3. Каждая переформулировка сверяется векторами со СВОЕЙ исходной фразой. Если смысл уплыл —
   вместо пересказа остаётся сама фраза лекции. Поэтому даже слабая LLM не может испортить конспект.
Без LLM конспект — это фразы-суть, очищенные от слов-паразитов; названия — ключевые фразы разделов."""
import math
import re

import numpy as np

from . import essence, llm, terms, text

PARAPHRASE_MIN_SIM = 0.86  # e5: пересказ той же мысли ≈ 0.9+, другая мысль ≈ 0.8
NEG_RE = re.compile(r"(?<![а-яё])(?:не|нет|ни|нельзя|без)(?![а-яё])|(?<![а-яё])не(?:отриц|ориент|возможн|верн|правильн|достаточн|обходим)",
                    re.IGNORECASE)
NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _faithful(paraphrase: str, source: str) -> bool:
    """Векторы почти не замечают «не»: «с отрицательными весами» и «с неотрицательными» для них одно и то же.
    Поэтому дополнительно сверяем полярность (есть ли отрицание) и числа: в пересказе — только числа источника."""
    if bool(NEG_RE.search(paraphrase)) != bool(NEG_RE.search(source)):
        return False
    src_nums = set(NUM_RE.findall(source))
    return all(n in src_nums for n in NUM_RE.findall(paraphrase))


def _point(sent_text: str, t: float) -> dict:
    return {"text": text.clean_for_summary(sent_text), "t": t, "support": 1.0, "verified": True, "extractive": True}


def build(title: str, sentences: list[dict], sent_vecs: np.ndarray, sections: list[dict], cands: dict,
          term_vecs: dict, top_terms: list[dict], embedder, progress=None) -> tuple[list[dict], dict]:
    texts = [s["text"] for s in sentences]
    types = [s.get("type", "explanation") for s in sentences]
    starts = [s["start"] for s in sentences]
    sim_full, _ = essence.similarity(sent_vecs)
    weights, eligible = essence.sentence_weights(texts, types)
    level1 = [s for s in sections if s["level"] == 1]
    ranges = [(s["s_from"], s["s_to"]) for s in level1]

    # 1. Суть каждого раздела и подраздела — векторами; названия — ключевыми фразами.
    for s in sections:
        a, b = s["s_from"], s["s_to"]
        max_k = int(np.clip(round((b - a + 1) / 6), 2, 5)) if s["level"] == 1 else 2
        chosen, cov = essence.select(sim_full[a:b + 1, a:b + 1], weights[a:b + 1], eligible[a:b + 1], max_k)
        s["essence"] = [a + c for c in chosen]
        s["coverage"] = round(cov, 3)
        sec_vec = sent_vecs[a:b + 1].mean(axis=0)
        sec_vec /= np.linalg.norm(sec_vec) + 1e-9
        s["keyphrases"] = [k for k in terms.section_keyphrases(cands, term_vecs, a, b, sec_vec, ranges, k=8) if _well_formed(k)][:6]
        if s["keyphrases"]:
            s["title"] = s["keyphrases"][0]
        elif s["level"] == 2 and s["parent"] is not None:
            s["title"] = f"{sections[s['parent']]['title']} (продолжение)"
        else:
            s["title"] = _first_words(texts, s["essence"], a)
        s["service"] = False
        service_share = sum(types[i] in ("org", "recap") for i in range(a, b + 1)) / (b - a + 1)
        opens_recap = s is level1[-1] and "recap" in types[a:min(a + 2, b + 1)]  # «Итак, подведём итог…»
        edge = s is level1[0] or s is level1[-1]  # вступление и итоги обычно по краям лекции
        if s["level"] == 1 and (opens_recap or service_share >= (0.4 if edge else 0.6)):
            # раздел из приветствий, итогов и объявлений — называем по сути, а не случайной ключевой фразой
            s["title"] = "Вступление" if s is level1[0] else "Итоги" if s is level1[-1] else "Организационные моменты"
            s["service"] = True
        s["bullets"] = [_point(texts[i], starts[i]) for i in s["essence"]] if s["level"] == 1 else []
    _dedupe_titles(sections)
    if progress:
        progress(0.2)

    # 2. Главное во всей лекции: покрытие всей лекции, кандидаты — только фразы-суть разделов.
    pool = np.zeros(len(texts), dtype=bool)
    for s in level1:
        pool[s["essence"]] = True
    key_idx, cov_all = essence.select(sim_full, weights, pool, int(np.clip(len(level1) + 2, 4, 8)), target=0.9)
    term_names = ", ".join(t["term"] for t in top_terms[:5])
    overview = (f"Лекция «{title}» состоит из {len(level1)} {_plural(len(level1), 'раздела', 'разделов', 'разделов')}: "
                + "; ".join(s["title"] for s in level1 if s["title"]) + "."
                + (f" Ключевые понятия: {term_names}." if term_names else ""))
    summary = {"overview": overview, "key_points": [_point(texts[i], starts[i]) for i in key_idx], "method": "vectors",
               "coverage": round(cov_all, 3)}

    # 3. LLM формулирует, векторы проверяют.
    backend = llm.backend()
    if backend is None:
        return sections, summary
    used_llm = False
    sec_vecs = []  # векторы содержательных фраз каждого раздела (без приветствий и «до встречи»)
    for s in level1:
        idx = [i for i in range(s["s_from"], s["s_to"] + 1) if types[i] != "org"] or [s["s_from"]]
        sec_vecs.append(sent_vecs[idx])
    taken: set[str] = set()
    for k, s in enumerate(level1):
        try:
            used_llm |= _rewrite_section(title, s, texts, starts, sent_vecs, embedder, sec_vecs, k, taken)
        except Exception as e:  # раздел остаётся с фразами-сутью и названием по ключевой фразе
            print(f"[summary] раздел {k + 1}: LLM не справилась ({type(e).__name__}: {e})")
        taken.add(s["title"].lower())
        subs = [x for x in sections if x["level"] == 2 and x["parent"] == s["idx"]]
        if len(subs) >= 2 and not s.get("service"):
            try:
                used_llm |= _title_subsections(title, s, subs, texts)
            except Exception as e:
                print(f"[summary] подразделы раздела {k + 1}: LLM не справилась ({type(e).__name__}: {e})")
        if progress:
            progress(0.2 + 0.65 * (k + 1) / max(len(level1), 1))
    try:
        rewritten = _rewrite_points([texts[i] for i in key_idx], embedder, sent_vecs[key_idx])
        summary["key_points"] = [_merge(p, texts[i], starts[i]) for p, i in zip(rewritten, key_idx)]
        used_llm |= any(not p["extractive"] for p in summary["key_points"])
        ov = _overview(title, level1, sent_vecs, embedder, texts)
        if ov:
            summary["overview"] = ov
            used_llm = True
    except Exception as e:
        print(f"[summary] обзор: LLM не справилась ({type(e).__name__}: {e})")
    if used_llm:
        summary["method"] = backend.name
    return sections, summary


def _grounded(title: str, sec_text: str) -> bool:
    """Название опирается на текст раздела: его содержательные слова (по леммам) звучат в этом разделе.
    Так LLM не может назвать раздел темой, о которой в нём не говорили."""
    words = [w for w in text.lemmas(title.replace("-", " ")) if not w.isdigit()]
    if not words:
        return False
    present = set(text.lemmas(sec_text))
    return sum(w in present for w in words) >= max(1, math.ceil(0.6 * len(words)))


def _normalize_title(t: str, sec_text: str) -> str:
    """Маленькая модель склеивает слова и пишет «Как В Заголовках»: «ОбходГрафа» → «Обход графа»,
    «DFSvsBFS» → «DFS и BFS». Заглавные оставляем у сокращений, латиницы и имён, которые так звучат в лекции."""
    t = re.sub(r"(?<=[а-яёa-z])(?=[А-ЯЁA-Z])", " ", t)
    t = re.sub(r"(?<=[A-Z]{2})(?=[a-z]{2})", " ", t)
    t = re.sub(r"\bvs\.?(?=\s|$)", "и", t)
    words = t.split()
    out = words[:1]
    for w in words[1:]:
        keep = (w.isupper() and len(w) > 1) or re.search(r"[A-Za-z]", w) or \
            re.search(r"[^.!?\s]\s+" + re.escape(w) + r"\b", sec_text)  # имя собственное в середине фразы
        out.append(w if keep else w.lower())
    return " ".join(out)


def _well_formed(title: str) -> bool:
    """Грамматика названия по словарю pymorphy: маленькая LLM склеивает ключевые слова без согласования
    («Регрессия цена», «Старое значения», «Холм-Точки») и делает опечатки («Поиск вширину»)."""
    morph = text._morph()
    words = re.findall(r"[А-Яа-яЁё]+(?:-[А-Яа-яЁё]+)*|[A-Za-z][A-Za-z0-9+#]*", title)
    if not words:
        return False
    parses = []
    for w in words:
        if re.match(r"[A-Za-z]", w):
            parses.append(None)  # латиница: BFS, Python
            continue
        known = morph.word_is_known(w.lower())
        if not known and (not w[0].isupper() or "-" in w):
            return False  # незнакомое слово со строчной — опечатка, «Холм-Точки» — склейка; с заглавной — может быть фамилией
        parses.append((w, morph.parse(w)))
    first = parses[0]
    if first is not None and str(first[1][0].tag.POS) not in ("NOUN", "ADJF", "PRTF"):
        return False  # название — именная группа, а не «Найти после этого…»
    for a1, a2 in zip(parses, parses[1:]):
        if a1 is None or a2 is None:
            continue
        (w1, p1), (_, p2) = a1, a2
        pos1, pos2 = str(p1[0].tag.POS), str(p2[0].tag.POS)
        nouns2 = [p for p in p2 if str(p.tag.POS) == "NOUN"]
        # «старое» словарь считает и существительным, поэтому прилагательное узнаём ещё и по окончанию
        if pos1 in ("ADJF", "PRTF") or (re.search(r"(?:ый|ий|ой|ая|яя|ое|ее|ые|ие)$", w1.lower())
                                        and any(str(p.tag.POS) in ("ADJF", "PRTF") for p in p1)):
            pos1 = "ADJF"
        if pos1 == "ADJF" and pos2 == "NOUN":
            adj = [p for p in p1 if str(p.tag.POS) in ("ADJF", "PRTF")]
            if not any(a.tag.case == n.tag.case and a.tag.number == n.tag.number and
                       (a.tag.number == "plur" or a.tag.gender == n.tag.gender) for a in adj for n in nouns2):
                return False
        elif pos1 == "NOUN" and re.search(r"(?:ый|ий|ой|ая|яя|ое|ее|ые|ие)$", a2[0].lower()) and \
                any(str(p.tag.POS) in ("ADJF", "PRTF") for p in p2):
            return False  # «Алгоритм новый»: прилагательное после существительного в названии — калька
        elif pos1 == "NOUN" and pos2 == "NOUN":
            # два существительных подряд: второе — в родительном или творительном («обход графа», «работа очередью»)
            if not any("gent" in n.tag or "ablt" in n.tag for n in nouns2):
                return False
    return True


def _accept_title(cand, lecture_title: str, sec_text: str, taken: set[str], sim_ok) -> str:
    """Проверки названия от LLM. Возвращает принятое название или пустую строку."""
    if not isinstance(cand, str) or not 1 <= len(cand.split()) <= 8:
        return ""
    cand = _normalize_title(cand.strip().strip("«»\"'.").strip(), sec_text)
    if any(re.search(r"[А-Яа-яЁё]", w) and re.search(r"[A-Za-z]", w) for w in cand.split()):
        print(f"[summary] название «{cand}» отклонено: смесь кириллицы и латиницы в одном слове")
        return ""
    if not cand or cand.lower() == lecture_title.lower() or cand.lower() in taken:
        return ""
    if not _well_formed(cand):
        print(f"[summary] название «{cand}» отклонено: несогласованные слова или опечатка")
        return ""
    if any(ab not in sec_text for ab in re.findall(r"\b[А-ЯЁA-Z]{2,}\b", cand)):
        print(f"[summary] название «{cand}» отклонено: сокращение, которого нет в разделе")
        return ""
    if not (_grounded(cand, sec_text) or sim_ok(cand)):
        print(f"[summary] название «{cand}» отклонено: его слов нет в разделе и по смыслу оно ближе к другому")
        return ""
    return cand[0].upper() + cand[1:]


def _rewrite_section(lecture_title: str, s: dict, texts, starts, sent_vecs, embedder, all_vecs: list[np.ndarray],
                     own: int, taken: set[str]) -> bool:
    phrases = [texts[i] for i in s["essence"]]
    numbered = "\n".join(f"{n}. {p}" for n, p in enumerate(phrases, 1))
    prev = f"Названия соседних разделов (не повторяй их): {', '.join(sorted(taken))}.\n" if taken else ""
    kp = f"Ключевые слова раздела: {', '.join(s['keyphrases'])}.\n" if s.get("keyphrases") else ""
    prompt = (f"Ниже ключевые фразы одного раздела лекции «{lecture_title}». Это расшифровка устной речи, "
              f"в ней возможны ошибки распознавания.\n\n{numbered}\n\n{kp}{prev}"
              "Задание:\n1) title — название раздела из 2–5 слов, отражающее его главную тему; составь его из "
              "ключевых слов раздела (без кавычек, без слова «раздел»).\n"
              f"2) points — перепиши каждую из {len(phrases)} фраз одним коротким грамотным предложением: "
              "сохрани смысл, термины и числа, убери слова-паразиты, ничего не добавляй от себя. "
              "Порядок и количество фраз сохрани.\n"
              'Ответ — только JSON: {"title": "...", "points": ["...", "..."]}')
    data = llm.chat_json(prompt, max_tokens=120 + 90 * len(phrases))
    if not isinstance(data, dict):
        raise llm.LLMError("ожидался JSON-объект")
    changed = False
    sec_text = " ".join(texts[s["s_from"]:s["s_to"] + 1])

    def sim_ok(cand: str) -> bool:
        # запасной путь: название подходит именно этому разделу лучше, чем любому другому (по двум самым
        # похожим фразам раздела, а не по среднему — итоговый раздел «похож на всё»)
        tv = embedder.passages([cand])[0]
        sims = np.array([np.sort(sv @ tv)[::-1][:2].mean() for sv in all_vecs])
        return bool(sims[own] >= sims.max() - 0.01 and sims[own] >= 0.8)

    if not s.get("service"):
        title = _accept_title(data.get("title"), lecture_title, sec_text, taken, sim_ok)
        if title:
            s["title"] = title
            changed = True
    pts = data.get("points")
    if isinstance(pts, list) and len(pts) == len(phrases):
        rewritten = _check([p if isinstance(p, str) else "" for p in pts], embedder, sent_vecs[s["essence"]])
        s["bullets"] = [_merge(p, texts[i], starts[i]) for p, i in zip(rewritten, s["essence"])]
        changed |= any(not b["extractive"] for b in s["bullets"])
    return changed


def _title_subsections(lecture_title: str, parent: dict, subs: list[dict], texts) -> bool:
    """Названия подразделов одним запросом: по две фразы-сути каждой части. Каждое название проходит
    те же проверки, что и у разделов; не прошло — остаётся ключевая фраза."""
    parts = "\n".join(f"Часть {n}: " + " ".join(texts[i] for i in x["essence"]) +
                      (f" (ключевые слова: {', '.join(x['keyphrases'][:4])})" if x.get("keyphrases") else "")
                      for n, x in enumerate(subs, 1))
    prompt = (f"Раздел «{parent['title']}» лекции «{lecture_title}» делится на {len(subs)} части. Ниже их ключевые фразы "
              f"(расшифровка устной речи).\n\n{parts}\n\nДай каждой части название из 2–4 слов, словами из её фраз, "
              "чтобы части отличались друг от друга. Не повторяй название раздела.\n"
              'Ответ — только JSON: {"titles": ["...", "..."]}')
    data = llm.chat_json(prompt, max_tokens=40 + 30 * len(subs))
    got = data.get("titles") if isinstance(data, dict) else None
    if not isinstance(got, list) or len(got) != len(subs):
        return False
    taken = {parent["title"].lower()}
    changed = False
    for x, cand in zip(subs, got):
        sec_text = " ".join(texts[x["s_from"]:x["s_to"] + 1])
        t = _accept_title(cand, lecture_title, sec_text, taken, lambda c: False)
        if t:
            x["title"] = t
            changed = True
        taken.add(x["title"].lower())
    return changed


def _rewrite_points(phrases: list[str], embedder, src_vecs) -> list[dict]:
    numbered = "\n".join(f"{n}. {p}" for n, p in enumerate(phrases, 1))
    prompt = (f"Перепиши каждую фразу из расшифровки лекции одним коротким грамотным предложением: сохрани смысл, "
              f"термины и числа, убери слова-паразиты, ничего не добавляй. Порядок и количество фраз сохрани.\n\n"
              f"{numbered}\n\nОтвет — только JSON: {{\"points\": [\"...\", \"...\"]}}")
    data = llm.chat_json(prompt, max_tokens=100 * len(phrases) + 60)
    pts = data.get("points") if isinstance(data, dict) else None
    if not isinstance(pts, list) or len(pts) != len(phrases):
        return [{"text": "", "support": 0.0}] * len(phrases)
    return _check([p if isinstance(p, str) else "" for p in pts], embedder, src_vecs)


def _check(points: list[str], embedder, src_vecs: np.ndarray) -> list[dict]:
    """Сверка пересказа с исходной фразой: косинус векторов пересказа и источника."""
    clean = [p.strip() for p in points]
    vecs = embedder.passages([p or "-" for p in clean])
    sims = (vecs * src_vecs).sum(axis=1)
    return [{"text": p, "support": round(float(sm), 3)} for p, sm in zip(clean, sims)]


def _merge(p: dict, source: str, t: float) -> dict:
    """Пересказ, если он верен исходной фразе, иначе — сама фраза лекции."""
    if (p["text"] and p["support"] >= PARAPHRASE_MIN_SIM and len(p["text"]) <= max(240, len(source) * 1.3)
            and _faithful(p["text"], source)):
        return {"text": p["text"], "t": t, "support": p["support"], "verified": True, "extractive": False,
                "source": source}
    return _point(source, t)


def _overview(title: str, level1: list[dict], sent_vecs, embedder, sources: list[str]) -> str | None:
    lines = [f"{k}. {s['title']}: " + " ".join(b["text"] for b in s["bullets"]) for k, s in enumerate(level1, 1)]
    prompt = (f"Лекция «{title}». Ниже её разделы и главные мысли.\n\n" + "\n".join(lines) +
              "\n\nНапиши 2–3 предложения: о чём эта лекция и что из неё вынесет студент. Используй только "
              "то, что есть выше. Ответ — только текст, без заголовков.")
    out = llm.chat(prompt, max_tokens=220, temperature=0.2).strip()
    if not out or len(out) > 900:
        return None
    # Каждое предложение обзора должно опираться на конкретное место лекции и не переворачивать его смысл.
    parts = [p.strip() for p in re.split(r"(?<=[.!?…])\s+", out) if p.strip()]
    vecs = embedder.passages(parts)
    sims = vecs @ sent_vecs.T
    kept = []
    for k, p in enumerate(parts):
        j = int(sims[k].argmax())
        if sims[k, j] >= 0.84 and _faithful(p, sources[j]):
            kept.append(p)
    if not kept:
        return None
    # если выпало первое предложение, следующее не должно начинаться со связки «Также…»
    kept[0] = re.sub(r"^(?:Также|Кроме того|А также)[,\s]+", "", kept[0])
    kept[0] = kept[0][:1].upper() + kept[0][1:]
    return " ".join(kept)


def _dedupe_titles(sections: list[dict]) -> None:
    """Подраздел не должен называться так же, как его раздел или соседний подраздел, а разделы — друг как друг:
    берём следующую ключевую фразу. В оглавлении «Массив → Массив» ничего не говорит."""
    used_l1: set[str] = set()
    used_sub: dict[int, set[str]] = {}
    by_idx = {x["idx"]: x for x in sections}
    for x in sections:
        if x["level"] == 1:
            used = used_l1
        else:
            parent = by_idx.get(x["parent"])
            used = used_sub.setdefault(x["parent"], {parent["title"].lower()} if parent else set())
        if x["title"].lower() in used:
            alt = next((k for k in x.get("keyphrases", []) if k.lower() not in used), None)
            if alt:
                x["title"] = alt
        used.add(x["title"].lower())


def _first_words(texts, idxs, fallback_idx, n: int = 6) -> str:
    i = idxs[0] if idxs else fallback_idx
    words = text.clean_for_summary(texts[i]).rstrip(".!?…").split()
    return " ".join(words[:n]) + ("…" if len(words) > n else "")


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many
