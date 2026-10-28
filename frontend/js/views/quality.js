// Качество поиска: результаты backend.eval — сравнение конфигураций на наборе запросов с эталонными таймкодами.
import { api } from "../api.js";
import { h, icon } from "../ui.js";

const NAMES = {
  bm25: "BM25 (только слова)",
  dense: "Векторы e5 (только смысл)",
  hybrid: "Гибрид: векторы + BM25 (RRF)",
  rerank: "Гибрид + реранкер (наша система)",
  rerank_noctx: "Гибрид + реранкер без контекста разделов",
  llm: "Распознавание + LLM по всему тексту",
};

export async function renderQuality(root) {
  root.append(h("div", { class: "loader" }));
  const [ev, status] = await Promise.all([api.evalResults(), api.status().catch(() => null)]);
  root.innerHTML = "";
  const top = h("div", { class: "surface" }, h("div", { class: "page-head", style: { marginBottom: "18px" } }, h("div", {}, h("h1", {}, "Качество поиска"),
    h("p", {}, "Мы не показываем пару удачных запросов, а меряем. Для каждого запроса из набора известен правильный момент лекции. " +
      "Попадание — когда таймкод результата лежит в эталонном интервале (±10 с). Запросы сформулированы другими словами, чем в лекции, " +
      "а часть — про то, чего в лекциях нет: система должна честно ответить «не найдено»."))));
  root.append(top);

  if (status) {
    top.append(h("div", { class: "kpis" },
      kpi(status.gpu || status.device, "устройство"),
      kpi(`Whisper ${status.asr_model}`, "распознавание речи"),
      kpi(status.embed_model.split("/").pop(), "векторы"),
      kpi(status.rerank_model ? status.rerank_model.split("/").pop() : "нет", "реранкер"),
      kpi(status.llm.available ? status.llm.name : "не подключена", "LLM (только формулировки)")));
  }
  if (!ev.available) {
    root.append(h("div", { class: "surface" }, h("div", { class: "card-title" }, icon("chart"), "Оценка ещё не запускалась"),
      h("p", { class: "muted" }, "Заполните data/eval/queries.json и запустите:"),
      h("pre", { class: "code" }, "python -m backend.eval")));
    return null;
  }
  const rows = ev.configs;
  const bestMrr = Math.max(...rows.map((r) => r.mrr || 0));
  const table = h("table", { class: "metrics-table" },
    h("thead", {}, h("tr", {}, h("th", {}, "Конфигурация"), h("th", {}, "Recall@1"), h("th", {}, "Recall@5"), h("th", {}, "MRR@10"),
      h("th", {}, "Отказ на «пустых»"), h("th", {}, "Время, мс (p50)"))),
    h("tbody", {}, rows.map((r) => h("tr", { class: r.mrr === bestMrr ? "best" : "" },
      h("td", {}, NAMES[r.name] || r.name, r.llm_model ? h("span", { class: "dim small" }, ` (${r.llm_model})`) : null),
      h("td", { class: "num" }, pct(r.recall_1)),
      h("td", { class: "num" }, pct(r.recall_5), h("span", { class: "mbar", style: { width: `${Math.round((r.recall_5 || 0) * 80)}px` } })),
      h("td", { class: "num" }, (r.mrr ?? 0).toFixed(3)),
      h("td", { class: "num" }, r.refusal === null || r.refusal === undefined ? "—" : pct(r.refusal)),
      h("td", { class: "num" }, r.latency_p50 !== undefined ? Math.round(r.latency_p50) : "—")))));
  root.append(h("div", { class: "surface" }, h("div", { class: "card-title" }, icon("search"), `Поиск: ${ev.n_queries} запросов по ${ev.n_lectures} лекциям`), h("div", { style: { overflowX: "auto" } }, table),
    h("p", { class: "muted small" }, `Запуск: ${new Date(ev.created_at * 1000).toLocaleString("ru-RU")}. Порог «не найдено»: ${ev.threshold}.`)));
  if (ev.by_set && ev.by_set.real) {
    // живая речь с Rutube отдельно от синтезированных демо-лекций: на ней видно, как система работает в жизни
    const SETS = { real: "Живые лекции (Rutube, запись с паузами и оговорками)", demo: "Демо-лекции (синтез речи)" };
    const order = ["bm25", "dense", "hybrid", "rerank"];
    const rowsBy = [];
    for (const [set, title] of Object.entries(SETS)) {
      const ms = ev.by_set[set];
      if (!ms) continue;
      const n = (ms.rerank || {}).n;
      rowsBy.push(h("tr", {}, h("td", { colspan: 5, style: { fontWeight: 700, background: "var(--card-soft)" } }, `${title}: ${n} запросов`)));
      order.filter((k) => ms[k]).forEach((k) => rowsBy.push(h("tr", { class: k === "rerank" ? "best" : "" },
        h("td", {}, NAMES[k]), h("td", { class: "num" }, pct(ms[k].recall_1)), h("td", { class: "num" }, pct(ms[k].recall_5)),
        h("td", { class: "num" }, (ms[k].mrr ?? 0).toFixed(3)), h("td", { class: "num" }, ms[k].refusal == null ? "—" : pct(ms[k].refusal)))));
    }
    root.append(h("div", { class: "surface" }, h("div", { class: "card-title" }, icon("video"), "Отдельно: живая речь и синтез"),
      h("div", { style: { overflowX: "auto" } }, h("table", { class: "metrics-table" },
        h("thead", {}, h("tr", {}, h("th", {}, "Конфигурация"), h("th", {}, "Recall@1"), h("th", {}, "Recall@5"), h("th", {}, "MRR@10"), h("th", {}, "Отказ на «пустых»"))),
        h("tbody", {}, rowsBy)))));
  }
  if (ev.segmentation) {
    root.append(h("div", { class: "surface" }, h("div", { class: "card-title" }, icon("layers"), "Разбиение на разделы"),
      h("p", {}, `Pk = ${ev.segmentation.pk.toFixed(3)}, WindowDiff = ${ev.segmentation.windowdiff.toFixed(3)} (меньше — лучше; случайное разбиение ≈ 0.5)`)));
  }
  if (ev.asr) {
    root.append(h("div", { class: "surface" }, h("div", { class: "card-title" }, icon("chat"), "Распознавание речи"),
      h("p", {}, `WER = ${pct(ev.asr.wer)} на выверенном вручную фрагменте (${ev.asr.words} слов)`)));
  }
  return null;
}

function kpi(value, label) {
  return h("div", { class: "kpi" }, h("b", { style: { fontSize: "17px" } }, value), h("span", {}, label));
}

function pct(x) {
  return x === null || x === undefined ? "—" : `${Math.round(x * 1000) / 10}%`;
}
