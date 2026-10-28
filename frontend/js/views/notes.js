// «Конспекты»: краткое содержание и главное по каждой лекции курса — с переходом в нужную секунду и выгрузкой в Markdown.
import { api } from "../api.js";
import { h, icon, side, fmtTime, fmtClock, plural } from "../ui.js";

export async function renderNotes(root) {
  root.append(h("div", { class: "loader" }));
  const lectures = (await api.lectures().catch(() => [])).filter((l) => l.searchable);
  const details = (await Promise.all(lectures.map((l) => api.lecture(l.id).catch(() => null)))).filter(Boolean);
  root.innerHTML = "";

  const filter = h("input", { type: "search", placeholder: "Фильтр по названию или тексту конспекта" });
  const list = h("div", { style: { display: "flex", flexDirection: "column", gap: "18px" } });
  root.append(h("div", { class: "surface" },
    h("div", { class: "page-head", style: { marginBottom: "18px" } },
      h("div", {}, h("h1", {}, "Конспекты"),
        h("p", {}, "Главное из каждой лекции. Каждый пункт — с таймкодом: нажмите, чтобы услышать, как это объяснял преподаватель."))),
    h("form", { class: "search-box", onsubmit: (e) => e.preventDefault() }, icon("search"), filter)),
  list);

  function paint() {
    const q = filter.value.trim().toLowerCase();
    list.innerHTML = "";
    const shown = details.filter((d) => !q || d.title.toLowerCase().includes(q) ||
      (d.summary && (d.summary.overview.toLowerCase().includes(q) || d.summary.key_points.some((b) => b.text.toLowerCase().includes(q)))));
    if (!shown.length) {
      list.append(h("div", { class: "surface empty" }, icon("notes"), h("div", {}, details.length ? "Ничего не нашлось." : "Конспекты появятся, когда будет обработана хотя бы одна лекция.")));
      return;
    }
    shown.forEach((d) => list.append(noteCard(d)));
  }
  filter.addEventListener("input", paint);
  paint();

  side(h("div", { class: "side-card" },
    h("h3", {}, icon("spark"), "Как собран конспект"),
    h("div", { class: "steps" },
      step("Суть — векторами", "в каждом разделе выбираются фразы, на которые похожи все остальные: так покрываются все подтемы, а не только «средняя»."),
      step("Формулировки — LLM", "модель переписывает только выбранные фразы, по одной, и не может добавить новых фактов."),
      step("Проверка", "пересказ сверяется с исходной фразой по смыслу, отрицаниям и числам; не прошёл — остаётся цитата.")),
    h("div", { class: "muted small" }, "Знак ≈ у пункта — пересказ; наведите, чтобы увидеть исходную фразу.")));
  return null;
}

function noteCard(d) {
  const go = (t) => { location.hash = `#/lecture/${d.id}?t=${Math.floor(t)}`; };
  const s = d.summary;
  const level1 = d.sections.filter((x) => x.level === 1);
  return h("div", { class: "surface", style: { padding: "22px 24px" } },
    h("div", { class: "row", style: { alignItems: "flex-start", flexWrap: "nowrap", gap: "18px" } },
      h("a", { class: "lecture-thumb", href: `#/lecture/${d.id}`, style: { width: "170px", flex: "none" } },
        d.thumb ? h("img", { src: d.thumb, alt: "" }) : null),
      h("div", { style: { minWidth: 0, flex: 1 } },
        h("a", { href: `#/lecture/${d.id}`, style: { fontWeight: 700, fontSize: "19px" } }, d.title),
        h("div", { class: "row muted small", style: { margin: "6px 0 10px" } },
          h("span", { class: "row", style: { gap: "5px" } }, icon("clock"), fmtClock(d.duration)),
          h("span", {}, `${level1.length} ${plural(level1.length, "раздел", "раздела", "разделов")}`),
          s ? h("span", {}, `покрытие смысла ${Math.round((s.coverage || 0) * 100)}%`) : null,
          s && s.method !== "vectors" ? h("span", {}, `формулировки: ${s.method}`) : null),
        s ? h("div", { class: "summary-overview", style: { fontSize: "15px" } }, s.overview) : h("div", { class: "muted" }, "Конспект готовится…"))),
    s && s.key_points.length ? h("ul", { class: "bullets", style: { marginTop: "16px" } }, s.key_points.map((b) => h("li", {},
      h("span", { class: "chip chip-time", onclick: () => go(b.t) }, fmtTime(b.t)), h("span", {}, b.text)))) : null,
    h("div", { class: "row", style: { marginTop: "16px" } },
      h("a", { class: "btn btn-sm btn-primary", href: `#/lecture/${d.id}?tab=summary` }, icon("notes"), "Полный конспект"),
      h("a", { class: "btn btn-sm", href: `/api/lectures/${d.id}/export.md`, download: "" }, icon("download"), "Скачать .md"),
      h("span", { class: "spacer" }),
      level1.slice(0, 6).map((x) => h("span", { class: "chip chip-toggle", title: `${fmtTime(x.start)} · ${x.title}`, onclick: () => go(x.start) }, x.title))));
}

function step(title, text) {
  return h("div", { class: "step" }, h("div", { class: "step-n" }, icon("check")), h("div", {}, h("b", {}, title), h("span", {}, text)));
}
