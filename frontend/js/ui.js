// Небольшие помощники интерфейса: создание элементов, иконки, настройки, время, уведомления, тепловая полоса.

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

// Линейные иконки 24×24 (обводка цветом текста) — в стиле макета.
const ICONS = {
  home: '<path d="M3.5 10.5 12 3.5l8.5 7"/><path d="M5.5 9v11h13V9"/><path d="M10 20v-5.5h4V20"/>',
  lectures: '<rect x="4" y="3.5" width="12" height="17" rx="2"/><path d="M8 3.5v17"/><path d="M19.5 6.5v14"/>',
  notes: '<path d="M14 3.5H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8.5z"/><path d="M14 3.5v5h5"/><path d="M8.5 13h7M8.5 16.5h7M8.5 9.5h2.5"/>',
  doc: '<path d="M14 3.5H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8.5z"/><path d="M14 3.5v5h5"/><path d="M8.5 12.5h7M8.5 16h4"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
  settings: '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
  map: '<path d="M9 4.5 3.5 6.5v13l5.5-2 6 2 5.5-2v-13l-5.5 2z"/><path d="M9 4.5v13M15 6.5v13"/>',
  chart: '<path d="M4 20V11M10 20V5M16 20v-6M3 20h18"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  tag: '<path d="M3.5 12.2V4.5a1 1 0 0 1 1-1h7.7l8.3 8.3-8.7 8.7z"/><circle cx="8" cy="8" r="1.4"/>',
  chat: '<path d="M20.5 12a8.5 8.5 0 0 1-12.4 7.5L3.5 20.5l1-4.4A8.5 8.5 0 1 1 20.5 12z"/>',
  chev: '<path d="m9.5 6 6 6-6 6"/>',
  chevDown: '<path d="m6 9.5 6 6 6-6"/>',
  back: '<path d="M19 12H5.5M11 6l-6 6 6 6"/>',
  upload: '<path d="M12 15.5V4M7 8.5l5-4.5 5 4.5"/><path d="M4.5 15v3.5a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V15"/>',
  download: '<path d="M12 4v11.5M7 11l5 4.5 5-4.5"/><path d="M4.5 15v3.5a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V15"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  out: '<path d="M7 17 17 7M9 7h8v8"/>',
  trash: '<path d="M4 7h16M9 7V4.5h6V7M6.5 7l1 13h9l1-13"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/>',
  refresh: '<path d="M20 11a8 8 0 0 0-14.5-4.5L4 8M4 4v4h4M4 13a8 8 0 0 0 14.5 4.5L20 16M20 20v-4h-4"/>',
  copy: '<rect x="8.5" y="8.5" width="11" height="11" rx="2"/><path d="M15.5 8.5v-3a2 2 0 0 0-2-2h-8a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h3"/>',
  play: '<path d="M8 5.5v13l10-6.5z"/>',
  spark: '<path d="M12 3.5l1.8 5 5 1.8-5 1.8-1.8 5-1.8-5-5-1.8 5-1.8z"/><path d="M19 16.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/>',
  user: '<circle cx="12" cy="8.5" r="3.5"/><path d="M5 20a7 7 0 0 1 14 0"/>',
  video: '<rect x="3.5" y="6" width="12.5" height="12" rx="2"/><path d="m16 10.5 4.5-2.5v8l-4.5-2.5z"/>',
  layers: '<path d="m12 3.5 8.5 4.5-8.5 4.5-8.5-4.5z"/><path d="m3.5 12 8.5 4.5 8.5-4.5"/><path d="m3.5 16 8.5 4.5 8.5-4.5"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4 6 18M18 6l1.4-1.4"/>',
  moon: '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
  info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 8h.01"/>',
  cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2.5V6M15 2.5V6M9 18v3.5M15 18v3.5M2.5 9H6M2.5 15H6M18 9h3.5M18 15h3.5"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  quote: '<path d="M7 7h4v4c0 3-1.5 5-4 6M14 7h4v4c0 3-1.5 5-4 6"/>',
};

export function icon(name, cls = "") {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("class", `i ${cls}`.trim());
  s.setAttribute("aria-hidden", "true");
  s.innerHTML = ICONS[name] || "";
  return s;
}

// Настройки пользователя в браузере. В приватном режиме localStorage может бросать — тогда просто значения по умолчанию.
const PREF_DEFAULTS = { theme: "light", role: "teacher", follow: true, captions: true, speed: 1 };
export const prefs = {
  get(key) {
    try {
      const raw = localStorage.getItem(`sut.${key}`);
      return raw === null ? PREF_DEFAULTS[key] : JSON.parse(raw);
    } catch (_) { return PREF_DEFAULTS[key]; }
  },
  set(key, value) {
    try { localStorage.setItem(`sut.${key}`, JSON.stringify(value)); } catch (_) { /* хранилище недоступно */ }
    window.dispatchEvent(new CustomEvent("prefs", { detail: { key, value } }));
  },
};
export const canManage = () => prefs.get("role") === "teacher";

export function applyTheme(theme = prefs.get("theme")) {
  const dark = theme === "dark" || (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}

// Где остановился зритель — для «Продолжить просмотр».
export const progress = {
  all() {
    try { return JSON.parse(localStorage.getItem("sut.watch") || "{}"); } catch (_) { return {}; }
  },
  get(id) { return this.all()[id]; },
  save(id, t, duration) {
    const all = this.all();
    all[id] = { t, duration, at: Date.now() };
    try { localStorage.setItem("sut.watch", JSON.stringify(all)); } catch (_) { /* недоступно */ }
  },
};

// Правая колонка под навигацией: страницы кладут сюда свою панель (поиск по лекции, загрузку и т. п.).
export function side(...nodes) {
  document.getElementById("side").append(...nodes.flat().filter(Boolean));
}

export function fmtTime(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const hh = Math.floor(sec / 3600), mm = Math.floor((sec % 3600) / 60), ss = sec % 60;
  const p = (n) => String(n).padStart(2, "0");
  return hh ? `${hh}:${p(mm)}:${p(ss)}` : `${p(mm)}:${p(ss)}`;
}

export function fmtClock(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const hh = Math.floor(sec / 3600), mm = Math.floor((sec % 3600) / 60), ss = sec % 60;
  const p = (n) => String(n).padStart(2, "0");
  return `${hh}:${p(mm)}:${p(ss)}`;
}

export function fmtDuration(sec) {
  if (!sec) return "";
  const m = Math.round(sec / 60);
  if (m < 60) return `${m} мин`;
  return `${Math.floor(m / 60)} ч ${m % 60} мин`;
}

export function toast(text, kind = "") {
  const el = h("div", { class: `toast ${kind}` }, text);
  document.getElementById("toasts").append(el);
  setTimeout(() => el.remove(), 4500);
}

export function timeChip(t, onClick) {
  return h("span", { class: "chip chip-time", title: "Перейти к этому моменту", onclick: (e) => { e.stopPropagation(); onClick(t); } }, fmtTime(t));
}

export const TYPE_LABELS = {
  definition: "Определение", example: "Пример", proof: "Вывод", recap: "Итог", org: "Организационное", explanation: "Объяснение",
};

export function typeChip(t) {
  return h("span", { class: `chip t-${t}` }, TYPE_LABELS[t] || t);
}

export function plural(n, one, few, many) {
  const a = n % 10, b = n % 100;
  if (a === 1 && b !== 11) return one;
  if (a >= 2 && a <= 4 && (b < 12 || b > 14)) return few;
  return many;
}

// Тепловая полоса: где в лекции говорится о запросе. bins — значения 0..1 по времени.
export function heatStrip({ bins, duration, marks = [], onSeek, tall = false, title = "" }) {
  const canvas = h("canvas", { width: 600, height: 20 });
  const wrap = h("div", { class: `heat${tall ? " heat-tall" : ""}`, title }, canvas);
  const ctx = canvas.getContext("2d");
  const rgb = getComputedStyle(document.documentElement).getPropertyValue("--heat-rgb").trim() || "31, 166, 171";
  const w = canvas.width, hgt = canvas.height, n = bins.length || 1;
  ctx.clearRect(0, 0, w, hgt);
  for (let i = 0; i < n; i++) {
    const v = bins[i];
    if (v <= 0.01) continue;
    ctx.fillStyle = `rgba(${rgb}, ${0.12 + 0.88 * v})`;
    ctx.fillRect((i / n) * w, 0, Math.ceil(w / n) + 1, hgt);
  }
  for (const t of marks) {
    wrap.append(h("div", { class: "mark", style: { left: `${Math.min(100, (t / duration) * 100)}%` } }));
  }
  if (onSeek) {
    wrap.addEventListener("click", (e) => {
      const rect = wrap.getBoundingClientRect();
      onSeek(((e.clientX - rect.left) / rect.width) * duration);
    });
  }
  wrap.setCursor = (t) => {
    let c = wrap.querySelector(".cursor");
    if (!c) { c = h("div", { class: "cursor" }); wrap.append(c); }
    c.style.left = `${Math.min(100, (t / duration) * 100)}%`;
  };
  return wrap;
}

// Кусочки текста с подсветкой слов запроса: [[текст, 1|0], ...]
export function piecesToNodes(pieces) {
  return pieces.map(([t, hl]) => (hl ? h("mark", {}, t) : document.createTextNode(t)));
}

export function debounce(fn, ms) {
  let id;
  return (...a) => { clearTimeout(id); id = setTimeout(() => fn(...a), ms); };
}
