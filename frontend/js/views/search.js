// Поиск по всему курсу: фильтры по типу фрагмента и лекции, тепловые полосы, честное «не найдено».
import { api } from "../api.js";
import { h, icon, side, fmtTime, heatStrip, piecesToNodes, typeChip, TYPE_LABELS, plural } from "../ui.js";

const FILTER_TYPES = ["definition", "example", "proof", "recap"];

export async function renderSearch(root, params) {
  const q = (params.get("q") || "").trim();
  const types = new Set((params.get("types") || "").split(",").filter(Boolean));
  const lectureFilter = params.get("lecture") || "";
  const lectures = await api.lectures().catch(() => []);

  const go = (patch) => {
    const query = patch.q !== undefined ? patch.q : q;
    if (!query) return;
    const p = new URLSearchParams({ q: query });
    const t = patch.types !== undefined ? patch.types : [...types];
    const l = patch.lecture !== undefined ? patch.lecture : lectureFilter;
    if (t.length) p.set("types", t.join(","));
    if (l) p.set("lecture", l);
    location.hash = `#/search?${p}`;
  };

  const input = h("input", { type: "search", value: q, placeholder: "Что ищем? Например: «как найти кратчайший путь в графе»" });
  const select = h("select", { class: "input", onchange: (e) => go({ lecture: e.target.value }) },
    h("option", { value: "" }, "Все лекции курса"),
    lectures.filter((l) => l.searchable).map((l) => h("option", { value: l.id, selected: l.id === lectureFilter }, l.title)));
  const chips = FILTER_TYPES.map((t) => h("span", {
    class: `chip chip-toggle ${types.has(t) ? "on" : ""}`,
    onclick: () => { const n = new Set(types); n.has(t) ? n.delete(t) : n.add(t); go({ types: [...n] }); },
  }, `только ${TYPE_LABELS[t].toLowerCase()}`));
  const info = h("span", { class: "muted small" });
  const list = h("div", { class: "results" });

  root.append(h("div", { class: "surface" },
    h("div", { class: "page-head", style: { marginBottom: "18px" } },
      h("div", {}, h("h1", {}, "Поиск по курсу"), h("p", {}, "Ищет по смыслу во всех лекциях и отправляет в нужную секунду видео."))),
    h("form", { class: "search-box big", onsubmit: (e) => { e.preventDefault(); go({ q: input.value.trim() }); } }, icon("search"), input,
      h("button", { class: "btn btn-primary", type: "submit" }, "Найти")),
    h("div", { class: "filters", style: { marginTop: "14px" } }, select, ...chips, h("span", { class: "spacer" }), info)),
  list);

  side(h("div", { class: "side-card" },
    h("h3", {}, icon("info"), "Как устроен поиск"),
    h("div", { class: "steps" },
      step(1, "Два поиска сразу", "по смыслу (векторы e5) и по словам с учётом словоформ (BM25)"),
      step(2, "Объединение", "списки сливаются по рангам (RRF) — берётся лучшее из обоих"),
      step(3, "Перепроверка", "реранкер читает запрос и фрагмент вместе и оценивает, отвечает ли он"),
      step(4, "Точная секунда", "внутри фрагмента выбирается фраза-ответ; слабые совпадения — «не найдено»")),
    h("a", { class: "btn btn-sm", href: "#/quality" }, icon("chart"), "Метрики качества")));

  if (!q) {
    setTimeout(() => input.focus(), 50);
    list.append(h("div", { class: "surface empty" }, icon("search"), h("div", {}, "Введите вопрос — найду фрагменты лекций, где об этом говорится.")));
    return null;
  }

  list.append(h("div", { class: "loader" }));
  const res = await api.search({ q, lecture: lectureFilter, types: [...types], k: 12 });
  list.innerHTML = "";
  const n = res.results.length;
  info.textContent = n ? `${n} ${plural(n, "фрагмент", "фрагмента", "фрагментов")} · ${res.ms} мс` : `${res.ms} мс`;

  if (!n) {
    list.append(h("div", { class: "surface notfound" }, icon("search"),
      h("div", { style: { fontWeight: 700, fontSize: "18px", margin: "8px 0" } }, "В лекциях об этом, похоже, не говорится"),
      h("div", { class: "muted" }, "Ни один фрагмент не прошёл порог уверенности. Система не выдумывает ответ, если его нет — попробуйте сформулировать иначе.")));
    if (res.maybe.length) {
      list.append(h("div", { class: "maybe-title" }, "Самое близкое из того, что есть (низкая уверенность):"));
      res.maybe.forEach((r) => list.append(resultCard(r, res.heat[r.lecture_id], q, true)));
    }
    return null;
  }
  res.results.forEach((r) => list.append(resultCard(r, res.heat[r.lecture_id], q)));
  return null;
}

function resultCard(r, heat, q, weak = false) {
  const open = (t) => { location.hash = `#/lecture/${r.lecture_id}?t=${Math.floor(t)}&q=${encodeURIComponent(q)}`; };
  // Вероятность реранкера сильно «прижата» к нулю даже у хороших ответов (0.1 — уже уверенное попадание),
  // поэтому показываем её в логарифмической шкале: 0.001 → 0%, 0.01 → 33%, 0.1 → 67%, 1 → 100%.
  const pct = Math.round(Math.min(1, Math.max(0, (Math.log10(Math.max(r.score, 1e-4)) + 3) / 3)) * 100);
  const text = h("div", { class: "result-text" }, r.sentences.map((s) =>
    [h("span", { class: `s ${s.best ? "best" : ""}`, onclick: () => open(Math.max(0, s.t - 1)) }, piecesToNodes(s.pieces)), " "]));
  return h("div", { class: "result", style: weak ? { opacity: 0.7 } : {} },
    h("div", { class: "result-head" },
      h("span", { class: "chip chip-time", onclick: () => open(r.t) }, `▶ ${fmtTime(r.t)}`),
      h("a", { class: "result-lecture", href: `#/lecture/${r.lecture_id}?t=${Math.floor(r.t)}&q=${encodeURIComponent(q)}` }, r.lecture_title),
      r.section ? h("span", { class: "muted small" }, `· ${r.section}`) : null,
      ...r.tags.filter((t) => t !== "org").map(typeChip),
      h("span", { class: "spacer" }),
      h("span", { class: "score", title: `Уверенность реранкера, что фрагмент отвечает на запрос (вероятность ${r.score.toFixed(3)}, шкала логарифмическая)` },
        "релевантность", h("span", { class: "score-bar" }, h("div", { style: { width: `${pct}%` } })), `${pct}%`)),
    text,
    heat && heat.bins.length ? heatStrip({ bins: heat.bins, duration: heat.duration, marks: [r.t], onSeek: open,
      title: "Где ещё в этой лекции говорится о запросе — нажмите, чтобы перейти" }) : null);
}

function step(n, title, text) {
  return h("div", { class: "step" }, h("div", { class: "step-n" }, n), h("div", {}, h("b", {}, title), h("span", {}, text)));
}
