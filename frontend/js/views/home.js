// Главная: поиск по смыслу во всём курсе, цифры курса, «продолжить просмотр» и последние лекции.
import { api } from "../api.js";
import { h, icon, side, progress, fmtTime, plural, canManage } from "../ui.js";
import { lectureCard, uploadCard } from "./library.js";

export async function renderHome(root) {
  const [lectures, status] = await Promise.all([api.lectures().catch(() => []), api.status().catch(() => null)]);
  const ready = lectures.filter((l) => l.searchable);

  // примеры запросов — из терминов уже обработанных лекций, чтобы они точно что-то находили
  const details = await Promise.all(ready.slice(0, 4).map((l) => api.lecture(l.id).catch(() => null)));
  const examples = [];
  details.filter(Boolean).forEach((d) => { if (d.terms[0]) examples.push(`что такое ${d.terms[0].term}`); });

  const input = h("input", { type: "search", placeholder: "Спросите своими словами: «зачем нужна матрица смежности»" });
  const go = (q) => { if (q.trim()) location.hash = `#/search?q=${encodeURIComponent(q.trim())}`; };
  const hero = h("div", { class: "surface hero" },
    h("div", { class: "eyebrow" }, "Видеолекции с поиском по смыслу"),
    h("h1", { style: { marginTop: "8px" } }, "Найдите нужный момент лекции ", h("span", {}, "за секунды")),
    h("p", {}, "Система распознаёт речь, делит лекцию на разделы и выделяет суть. Ищите своими словами — " +
      "даже если преподаватель сказал иначе, вы попадёте в нужную секунду видео."),
    h("form", { class: "search-box big", onsubmit: (e) => { e.preventDefault(); go(input.value); } }, icon("search"), input,
      h("button", { class: "btn btn-primary", type: "submit" }, "Найти")),
    examples.length ? h("div", { class: "examples" }, h("span", { class: "muted small" }, "Например:"),
      examples.slice(0, 4).map((q) => h("span", { class: "chip chip-toggle", onclick: () => go(q) }, q))) : null);

  const hours = lectures.reduce((a, l) => a + (l.duration || 0), 0) / 3600;
  const sections = ready.reduce((a, l) => a + (l.sections || 0), 0);
  const stats = h("div", { class: "stats" },
    stat(ready.length, plural(ready.length, "лекция в курсе", "лекции в курсе", "лекций в курсе")),
    stat(hours >= 1 ? `${hours.toFixed(1).replace(".", ",")} ч` : `${Math.round(hours * 60)} мин`, "видео распознано"),
    stat(sections, plural(sections, "раздел в оглавлениях", "раздела в оглавлениях", "разделов в оглавлениях")),
    stat(status ? status.chunks : "—", "фрагментов в поисковом индексе"));

  root.append(hero, stats);

  // продолжить просмотр
  const watched = Object.entries(progress.all())
    .map(([id, w]) => ({ ...w, lec: ready.find((l) => l.id === id) }))
    .filter((w) => w.lec && w.t > 5)
    .sort((a, b) => b.at - a.at);
  if (watched.length) {
    const w = watched[0];
    const href = `#/lecture/${w.lec.id}?t=${Math.floor(w.t)}`;
    root.append(h("div", { class: "surface" },
      h("div", { class: "card-title" }, icon("play"), "Продолжить просмотр"),
      h("div", { class: "continue" },
        h("a", { class: "thumb", href }, w.lec.thumb ? h("img", { src: w.lec.thumb, alt: "" }) : null,
          h("div", { style: { position: "absolute", left: 0, bottom: 0, height: "5px", background: "var(--accent)", width: `${Math.min(100, (w.t / (w.lec.duration || 1)) * 100)}%` } })),
        h("div", {},
          h("div", { style: { fontWeight: 700, fontSize: "18px" } }, w.lec.title),
          h("div", { class: "muted", style: { margin: "6px 0 14px" } }, `Вы остановились на ${fmtTime(w.t)} из ${fmtTime(w.lec.duration)}`),
          h("a", { class: "btn btn-primary", href }, icon("play"), "Продолжить")))));
  }

  // последние лекции
  const list = h("div", { class: "lectures" });
  lectures.slice(0, 6).forEach((l) => list.append(lectureCard(l, { compact: true })));
  root.append(h("div", { class: "surface" },
    h("div", { class: "row", style: { marginBottom: "18px" } }, h("h2", {}, "Лекции курса"), h("span", { class: "spacer" }),
      h("a", { class: "btn btn-sm", href: "#/lectures" }, "Все лекции", icon("chev"))),
    lectures.length ? list : h("div", { class: "empty" }, icon("video"), h("div", {}, "Лекций пока нет."))));

  // справа: как это работает (+ загрузка для преподавателя)
  side(h("div", { class: "side-card" },
    h("h3", {}, icon("spark"), "Как это работает"),
    h("div", { class: "steps" },
      step(1, "Речь → текст", "Whisper распознаёт речь со временем каждого слова"),
      step(2, "Текст → смысл", "каждая фраза превращается в вектор; поиск сравнивает смысл, а не буквы"),
      step(3, "Смысл → структура", "разделы — где смысл резко меняется; суть — фразы, покрывающие раздел"),
      step(4, "Проверка", "LLM только переформулирует, каждый пункт сверяется с лекцией"))));
  if (canManage()) side(uploadCard(() => window.dispatchEvent(new HashChangeEvent("hashchange"))));
  return null;
}

function stat(value, label) {
  return h("div", { class: "stat" }, h("b", {}, value), h("span", {}, label));
}

function step(n, title, text) {
  return h("div", { class: "step" }, h("div", { class: "step-n" }, n), h("div", {}, h("b", {}, title), h("span", {}, text)));
}
