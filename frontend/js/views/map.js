// Карта курса: каждый раздел каждой лекции — точка; близкие по смыслу темы оказываются рядом.
import { api } from "../api.js";
import { h, icon, side, fmtTime } from "../ui.js";

const PALETTE = ["#1fa6ab", "#7a55d3", "#e07b39", "#23955f", "#d14d8a", "#2f78c0", "#b97c07", "#5f7c86", "#9c4fd1", "#0f7f6c"];
const SHAPES = ["circle", "rect", "diamond", "triangle"];

export async function renderMap(root) {
  root.append(h("div", { class: "loader" }));
  const data = await api.map();
  root.innerHTML = "";
  const head = h("div", { class: "page-head", style: { marginBottom: "10px" } }, h("div", {}, h("h1", {}, "Карта курса"),
    h("p", {}, "Каждая точка — раздел лекции. Близость точек — близость тем по смыслу, даже если их разбирали в разных лекциях. " +
      "Цвет — группа тем (когда лекций достаточно, чтобы темы выделились), форма — лекция. Нажмите на точку, чтобы перейти к этому месту видео.")));
  const surface = h("div", { class: "surface" }, head);
  root.append(surface);
  if (!data.points.length) {
    surface.append(h("div", { class: "empty" }, icon("map"), h("div", {}, "Карта появится, когда будет обработана хотя бы одна лекция.")));
    return null;
  }

  const css = getComputedStyle(document.documentElement);
  const textColor = css.getPropertyValue("--text").trim();
  const cardColor = css.getPropertyValue("--card").trim();
  const W = 1200, H = 620, pad = 60;
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("class", "map-svg");
  const X = (x) => pad + x * (W - 2 * pad);
  const Y = (y) => pad + y * (H - 2 * pad);
  const lectureShape = Object.fromEntries(data.lectures.map((l, i) => [l.id, SHAPES[i % SHAPES.length]]));
  const lectureIdx = Object.fromEntries(data.lectures.map((l, i) => [l.id, i]));
  // если устойчивых групп тем нет (мало лекций), цвет — лекция, а близость тем видна по расположению точек
  const byLecture = data.clusters.length <= 1;
  const colorOf = (p) => PALETTE[(byLecture ? lectureIdx[p.lecture_id] : p.cluster) % PALETTE.length];
  const tip = h("div", { class: "map-tip" });
  const wrap = h("div", { class: "card map-wrap" }, svg, tip);

  const el = (tag, attrs) => {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    return e;
  };
  if (!byLecture) data.clusters.forEach((c) => {
    svg.append(el("circle", { cx: X(c.x), cy: Y(c.y), r: 40 + 9 * Math.sqrt(c.size), fill: PALETTE[c.id % PALETTE.length], opacity: 0.1 }));
  });
  data.points.forEach((p) => {
    const color = colorOf(p);
    const cx = X(p.x), cy = Y(p.y), r = 9;
    let shape;
    const kind = lectureShape[p.lecture_id];
    if (kind === "rect") shape = el("rect", { x: cx - r, y: cy - r, width: 2 * r, height: 2 * r, rx: 3 });
    else if (kind === "diamond") shape = el("polygon", { points: `${cx},${cy - r - 2} ${cx + r + 2},${cy} ${cx},${cy + r + 2} ${cx - r - 2},${cy}` });
    else if (kind === "triangle") shape = el("polygon", { points: `${cx},${cy - r - 2} ${cx + r + 2},${cy + r} ${cx - r - 2},${cy + r}` });
    else shape = el("circle", { cx, cy, r });
    shape.setAttribute("fill", color);
    shape.setAttribute("stroke", cardColor);
    shape.setAttribute("stroke-width", "2");
    shape.style.cursor = "pointer";
    shape.addEventListener("mouseenter", () => {
      tip.innerHTML = "";
      tip.append(h("div", { style: { fontWeight: 700 } }, p.title || "Без названия"), h("div", { class: "muted" }, `${p.lecture_title} · ${fmtTime(p.t)}`));
      tip.style.display = "block";
      const rect = svg.getBoundingClientRect();
      tip.style.left = `${(cx / W) * rect.width + 24}px`;
      tip.style.top = `${(cy / H) * rect.height + 24}px`;
    });
    shape.addEventListener("mouseleave", () => { tip.style.display = "none"; });
    shape.addEventListener("click", () => { location.hash = `#/lecture/${p.lecture_id}?t=${Math.floor(p.t)}`; });
    svg.append(shape);
  });
  if (!byLecture) data.clusters.forEach((c) => {
    const t = el("text", { x: X(c.x), y: Y(c.y) - 26 - 6 * Math.sqrt(c.size), "text-anchor": "middle", fill: textColor, "font-size": "15", "font-weight": "700" });
    t.textContent = c.title.length > 34 ? c.title.slice(0, 33) + "…" : c.title;
    t.style.paintOrder = "stroke";
    t.setAttribute("stroke", cardColor);
    t.setAttribute("stroke-width", "5");
    svg.append(t);
  });
  surface.append(wrap);

  side(h("div", { class: "side-card" },
    h("h3", {}, icon("lectures"), "Лекции"),
    h("div", { class: "legend", style: { flexDirection: "column", gap: "10px" } }, data.lectures.map((l) =>
      h("a", { href: `#/lecture/${l.id}`, class: "row", style: { flexWrap: "nowrap", alignItems: "flex-start" } },
        legendShape(lectureShape[l.id], byLecture ? PALETTE[lectureIdx[l.id] % PALETTE.length] : textColor),
        h("span", {}, l.title)))),
    h("div", { class: "muted small" }, "Расстояния — по выравниванию фраз разделов: две темы близки, если для фраз одной находятся почти такие же фразы в другой.")));
  return null;
}

function legendShape(kind, color) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 14 14");
  svg.setAttribute("width", "14");
  svg.setAttribute("height", "14");
  svg.style.flex = "none";
  svg.style.marginTop = "3px";
  const shapes = {
    circle: '<circle cx="7" cy="7" r="5.5"/>',
    rect: '<rect x="1.5" y="1.5" width="11" height="11" rx="2"/>',
    diamond: '<polygon points="7,0.5 13.5,7 7,13.5 0.5,7"/>',
    triangle: '<polygon points="7,1 13.5,13 0.5,13"/>',
  };
  svg.innerHTML = shapes[kind] || shapes.circle;
  svg.setAttribute("fill", color);
  return svg;
}
