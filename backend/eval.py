"""Оценка качества: поиск (Recall@k, MRR, отказ на «пустых» запросах), разбиение на разделы (Pk, WindowDiff),
распознавание (WER). Сравнивает конфигурации — от голого BM25 до полной системы — и, по желанию,
базовую линию «весь транскрипт в LLM».

Запуск:  python -m backend.eval            (результат — data/eval/results.json и страница «Качество»)
         python -m backend.eval --llm      (+ базовая линия с LLM, если она подключена)
Формат data/eval/queries.json:
  {"queries": [{"query": "...", "answers": [{"lecture": "Название", "start": 12.0, "end": 30.5}]}],
   "sections": {"Название": [0, 95.2, 210.0]},             — эталонные начала разделов (необязательно)
   "asr": [{"lecture": "Название", "start": 0, "end": 300, "reference": "текст"}]}   — эталон для WER (необязательно)"""
import argparse
import json
import sys
import re
import statistics
import time
from pathlib import Path

import numpy as np

from . import config, db, storage
from .ml import llm, models, search, text
from .ml.index import INDEX

TOLERANCE = 10.0


def _hit(r: dict, answers: list[dict], ids: dict[str, str]) -> bool:
    return any(ids.get(a["lecture"]) == r["lecture_id"] and a["start"] - TOLERANCE <= r["t"] <= a["end"] + TOLERANCE
               for a in answers)


def _metrics(rows: list[dict]) -> dict:
    pos = [r for r in rows if r["answers"]]
    neg = [r for r in rows if not r["answers"]]
    ranks = [r["rank"] for r in pos]
    out = {
        "recall_1": float(np.mean([0 < k <= 1 for k in ranks])) if pos else None,
        "recall_5": float(np.mean([0 < k <= 5 for k in ranks])) if pos else None,
        "mrr": float(np.mean([1 / k if 0 < k <= 10 else 0 for k in ranks])) if pos else None,
        "latency_p50": statistics.median([r["ms"] for r in rows]) if rows else None,
        "refusal": None,
    }
    if neg and rows and rows[0].get("can_refuse"):
        out["refusal"] = float(np.mean([r["refused"] for r in neg]))
    return out


def _swap_noctx():
    """Векторы фрагментов без контекста раздела — для абляции «помогает ли контекстное обогащение»."""
    emb = models.embedder()
    _, chunks, _, _, _, _ = INDEX.snapshot()
    vecs = emb.passages([c["text"] for c in chunks])
    with INDEX.lock:
        old = INDEX.chunk_vecs
        INDEX.chunk_vecs = vecs
    return old


def run_search_eval(queries: list[dict], ids: dict[str, str]) -> tuple[list[dict], dict]:
    configs = [("bm25", "bm25"), ("dense", "dense"), ("hybrid", "hybrid"), ("rerank_noctx", "rerank"), ("rerank", "rerank")]
    results, per_query = [], {}
    for name, mode in configs:
        old = _swap_noctx() if name == "rerank_noctx" else None
        rows = []
        try:
            for q in queries:
                t0 = time.perf_counter()
                res = search.search(q["query"], top_k=10, mode=mode, with_heat=False)
                ms = (time.perf_counter() - t0) * 1000
                rank = next((i + 1 for i, r in enumerate(res["results"]) if _hit(r, q["answers"], ids)), 0)
                top_score = res["results"][0]["score"] if res["results"] else (res["maybe"][0]["score"] if res["maybe"] else 0)
                rows.append({"query": q["query"], "answers": q["answers"], "rank": rank, "ms": ms, "set": q.get("set", "demo"),
                             "refused": not res["found"], "can_refuse": res["reranked"], "top_score": top_score,
                             "top": [(r["lecture_title"], r["t"]) for r in res["results"][:3]]})
        finally:
            if old is not None:
                with INDEX.lock:
                    INDEX.chunk_vecs = old
        m = _metrics(rows)
        m["name"] = name
        results.append(m)
        per_query[name] = rows
        print(f"{name:14s} R@1={_f(m['recall_1'])} R@5={_f(m['recall_5'])} MRR={_f(m['mrr'])} "
              f"отказ={_f(m['refusal'])} p50={m['latency_p50']:.0f} мс")
    return results, per_query


def suggest_threshold(rows: list[dict]) -> float | None:
    """Порог «не найдено», при котором лучше всего разделяются вопросы с ответом и без."""
    if not rows or not any(not r["answers"] for r in rows):
        return None
    best_thr, best_acc = None, -1.0
    for thr in np.linspace(0.01, 0.6, 60):
        acc = np.mean([(r["top_score"] >= thr) == bool(r["answers"] and r["rank"] == 1) if r["answers"]
                       else r["top_score"] < thr for r in rows])
        if acc > best_acc:
            best_thr, best_acc = float(thr), float(acc)
    return round(best_thr, 3)


def run_llm_baseline(queries: list[dict], ids: dict[str, str], max_chars: int = 60000) -> dict | None:
    """Базовая линия «распознавание + LLM»: отдаём модели транскрипт с таймкодами и просим 5 таймкодов."""
    if llm.backend() is None:
        print("LLM не подключена — базовая линия пропущена")
        return None
    titles = {v: k for k, v in ids.items()}
    blocks = {}
    for lid in ids.values():
        rows = db.query("SELECT start, text FROM sentences WHERE lecture_id = ? ORDER BY idx", (lid,))
        blocks[lid] = "\n".join(f"[{text.fmt_time(r['start'])}] {r['text']}" for r in rows)
    rows = []
    for q in queries:
        lids = list(ids.values())
        corpus = "\n\n".join(f"### Лекция «{titles[l]}»\n{blocks[l]}" for l in lids)
        if len(corpus) > max_chars and q["answers"]:  # не влезает — даём только нужную лекцию (это фора для LLM)
            lids = [ids[q["answers"][0]["lecture"]]]
            corpus = f"### Лекция «{titles[lids[0]]}»\n{blocks[lids[0]]}"[:max_chars]
        prompt = (f"{corpus}\n\nВопрос: {q['query']}\nНайди в транскриптах до 5 моментов, которые лучше всего отвечают на вопрос. "
                  'Ответ — только JSON: {"hits": [{"lecture": "название лекции", "time": "мм:сс"}]}. '
                  'Если ответа нет — {"hits": []}.')
        t0 = time.perf_counter()
        try:
            data = llm.chat_json(prompt, max_tokens=300)
        except llm.LLMError:
            data = {"hits": []}
        ms = (time.perf_counter() - t0) * 1000
        hits = []
        for hh in (data.get("hits") or [])[:5] if isinstance(data, dict) else []:
            m = re.match(r"(?:(\d+):)?(\d+):(\d+)", str(hh.get("time", "")))
            lid = ids.get(str(hh.get("lecture", "")).strip("«» "), lids[0] if len(lids) == 1 else None)
            if m and lid:
                t = int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
                hits.append({"lecture_id": lid, "t": t})
        rank = next((i + 1 for i, r in enumerate(hits) if _hit(r, q["answers"], ids)), 0)
        rows.append({"query": q["query"], "answers": q["answers"], "rank": rank, "ms": ms, "refused": not hits,
                     "can_refuse": True, "chars": len(corpus)})
    m = _metrics(rows)
    m["name"] = "llm"
    m["llm_model"] = llm.backend().name
    m["avg_prompt_chars"] = int(np.mean([r["chars"] for r in rows]))
    print(f"{'llm':14s} R@1={_f(m['recall_1'])} R@5={_f(m['recall_5'])} MRR={_f(m['mrr'])} "
          f"отказ={_f(m['refusal'])} p50={m['latency_p50']:.0f} мс, ~{m['avg_prompt_chars']} символов в промпте")
    return m


def pk_windowdiff(ref: list[float], hyp: list[float], duration: float) -> tuple[float, float]:
    """Pk и WindowDiff по секундной сетке: доля окон, где эталон и система расходятся в «есть ли граница»."""
    n = int(duration)
    if n < 4:
        return 0.0, 0.0

    def labels(starts):
        lab, seg = np.zeros(n, dtype=int), 0
        bounds = sorted(int(s) for s in starts if s > 0)
        for t in range(n):
            while seg < len(bounds) and t >= bounds[seg]:
                seg += 1
            lab[t] = seg
        return lab

    r, hy = labels(ref), labels(hyp)
    k = max(2, int(round(n / (len(ref) or 1) / 2)))
    pk = np.mean([(r[i] == r[i + k]) != (hy[i] == hy[i + k]) for i in range(n - k)])
    wd = np.mean([(r[i + k] - r[i]) != (hy[i + k] - hy[i]) for i in range(n - k)])
    return float(pk), float(wd)


def _norm_words(s: str) -> str:
    return " ".join(re.findall(r"[a-zа-я0-9]+", s.lower().replace("ё", "е")))


def run_asr_eval(items: list[dict], ids: dict[str, str]) -> dict | None:
    import jiwer
    refs, hyps = [], []
    for it in items:
        lid = ids.get(it["lecture"])
        words = storage.load_json(lid, "words.json") if lid else None
        if not words:
            continue
        hyp = "".join(w["w"] for w in words if it["start"] - 1 <= w["s"] <= it["end"] + 1)
        refs.append(_norm_words(it["reference"]))
        hyps.append(_norm_words(hyp))
    if not refs:
        return None
    return {"wer": float(jiwer.wer(refs, hyps)), "words": sum(len(r.split()) for r in refs)}


def _f(x) -> str:
    return "  —  " if x is None else f"{x:.3f}"


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # консоль Windows в cp1251
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true", help="добавить базовую линию «транскрипт в LLM»")
    ap.add_argument("--file", default=str(config.EVAL_DIR / "queries.json"))
    ap.add_argument("--extra", default=str(config.ROOT / "eval" / "real_lectures.json"),
                    help="дополнительный эталон (реальные лекции); его запросы добавляются к основным")
    args = ap.parse_args()

    db.init()
    INDEX.reload()
    spec = json.loads(open(args.file, encoding="utf-8").read()) if Path(args.file).exists() else {"queries": []}
    if args.extra and Path(args.extra).exists():
        extra = json.loads(open(args.extra, encoding="utf-8").read())
        # ответы на старые запросы, которые нашлись и в новых лекциях («шаг обучения слишком большой» есть в обеих)
        for q in spec["queries"]:
            q["answers"] = q["answers"] + extra.get("extra_answers", {}).get(q["query"], [])
        spec["queries"] = spec["queries"] + [{**q, "set": "real"} for q in extra.get("queries", [])]
    ids = {r["title"]: r["id"] for r in db.query("SELECT id, title FROM lectures WHERE searchable = 1")}
    # Ответы из лекций, которых нет в индексе, отбрасываем; запрос без единого доступного ответа пропускаем.
    queries = []
    for q in spec["queries"]:
        answers = [a for a in q["answers"] if a["lecture"] in ids]
        if q["answers"] and not answers:
            continue
        queries.append({**q, "answers": answers})
    skipped = len(spec["queries"]) - len(queries)
    print(f"Запросов: {len(queries)} (пропущено {skipped} — их лекций нет в индексе), лекций: {len(ids)}")
    models.embedder().query("прогрев")
    if models.reranker():
        models.reranker().score("прогрев", ["прогрев"])

    configs, per_query = run_search_eval(queries, ids)
    # отдельно — синтетические демо-лекции и живые лекции с Rutube
    by_set = {}
    for name, rows in per_query.items():
        for set_name in ("demo", "real"):
            sub = [r for r in rows if r["set"] == set_name]
            if sub:
                m = _metrics(sub)
                m["n"] = len(sub)
                by_set.setdefault(set_name, {})[name] = m
    for set_name, ms in by_set.items():
        m = ms.get("rerank")
        if m:
            print(f"[{set_name}] rerank: {m['n']} запросов, R@1={_f(m['recall_1'])} R@5={_f(m['recall_5'])} "
                  f"MRR={_f(m['mrr'])} отказ={_f(m['refusal'])}")
    thr = suggest_threshold(per_query.get("rerank", []))
    if thr is not None:
        print(f"Рекомендуемый порог RERANK_THRESHOLD = {thr} (сейчас {config.RERANK_THRESHOLD})")
    if args.llm:
        m = run_llm_baseline(queries, ids)
        if m:
            configs.append(m)

    seg = None
    if spec.get("sections"):
        pks, wds = [], []
        for title, ref in spec["sections"].items():
            if title not in ids:
                continue
            hyp = [r["start"] for r in db.query("SELECT start FROM sections WHERE lecture_id = ? AND level = 1 ORDER BY idx", (ids[title],))]
            dur = db.one("SELECT duration FROM lectures WHERE id = ?", (ids[title],))["duration"]
            pk, wd = pk_windowdiff(ref, hyp, dur)
            pks.append(pk)
            wds.append(wd)
            print(f"Разделы «{title}»: эталон {len(ref)}, найдено {len(hyp)}, Pk={pk:.3f}, WindowDiff={wd:.3f}")
        if pks:
            seg = {"pk": float(np.mean(pks)), "windowdiff": float(np.mean(wds))}
    asr = run_asr_eval(spec.get("asr", []), ids)
    if asr:
        print(f"WER распознавания: {asr['wer']:.3f} на {asr['words']} словах")

    out = {"created_at": time.time(), "n_queries": len(queries), "n_lectures": len(ids), "threshold": config.RERANK_THRESHOLD,
           "suggested_threshold": thr, "configs": configs, "by_set": by_set, "segmentation": seg, "asr": asr,
           "per_query": per_query.get("rerank", [])}
    (config.EVAL_DIR / "results.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Сохранено: {config.EVAL_DIR / 'results.json'}")


if __name__ == "__main__":
    main()
