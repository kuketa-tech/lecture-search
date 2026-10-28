// Каркас и маршрутизация по хэшу: #/ — главная, #/lectures — мои лекции, #/notes — конспекты, #/search?q= — поиск,
// #/lecture/<id>?t=&q=&tab= — лекция, #/map — карта курса, #/quality — качество, #/settings — настройки.
import { api } from "./api.js";
import { h, icon, prefs, applyTheme } from "./ui.js";
import { renderHome } from "./views/home.js";
import { renderLibrary } from "./views/library.js";
import { renderNotes } from "./views/notes.js";
import { renderSearch } from "./views/search.js";
import { renderLecture } from "./views/lecture.js";
import { renderMap } from "./views/map.js";
import { renderQuality } from "./views/quality.js";
import { renderSettings } from "./views/settings.js";

const NAV = [
  ["home", "#/", "home", "Главная"],
  ["lectures", "#/lectures", "lectures", "Мои лекции"],
  ["notes", "#/notes", "notes", "Конспекты"],
  ["search", "#/search", "search", "Поиск"],
  null,
  ["map", "#/map", "map", "Карта курса"],
  ["quality", "#/quality", "chart", "Качество поиска"],
  ["settings", "#/settings", "settings", "Настройки"],
];
const ROLES = {
  student: ["Студент", "смотрю лекции, ищу и задаю вопросы"],
  teacher: ["Преподаватель", "загружаю лекции и управляю курсом"],
};

const view = document.getElementById("view");
const sideEl = document.getElementById("side");
let cleanup = null;

document.getElementById("nav").append(...NAV.map((n) => (n
  ? h("a", { href: n[1], "data-nav": n[0] }, icon(n[2]), h("span", {}, n[3]))
  : h("div", { class: "nav-sep" }))));

function parseHash() {
  const raw = location.hash.replace(/^#/, "") || "/";
  const [path, qs] = raw.split("?");
  return { parts: path.split("/").filter(Boolean), params: new URLSearchParams(qs || "") };
}

async function route() {
  if (cleanup) { try { cleanup(); } catch (_) { /* игнорируем */ } cleanup = null; }
  const { parts, params } = parseHash();
  view.innerHTML = "";
  sideEl.innerHTML = "";
  const page = parts[0] || "home";
  const navKey = page === "lecture" ? "lectures" : page;
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === navKey));
  window.scrollTo(0, 0);
  try {
    if (page === "home") cleanup = await renderHome(view);
    else if (page === "lectures") cleanup = await renderLibrary(view);
    else if (page === "notes") cleanup = await renderNotes(view);
    else if (page === "search") cleanup = await renderSearch(view, params);
    else if (page === "lecture" && parts[1]) cleanup = await renderLecture(view, parts[1], params);
    else if (page === "map") cleanup = await renderMap(view);
    else if (page === "quality") cleanup = await renderQuality(view);
    else if (page === "settings") cleanup = await renderSettings(view, route);
    else location.hash = "#/";
  } catch (e) {
    console.error(e);
    view.innerHTML = "";
    view.append(h("div", { class: "surface empty" }, icon("info"), h("div", {}, `Не удалось открыть страницу: ${e.message}`)));
  }
}

// ---------- подвал: роль пользователя, состояние системы, тема ----------
const footer = document.getElementById("footer");
const roleName = h("span");
const menu = h("div", { class: "menu" });
const user = h("div", { class: "user", onclick: (e) => { e.stopPropagation(); menu.classList.toggle("open"); } },
  h("div", { class: "avatar" }, icon("user")), h("div", { class: "user-name" }, roleName, icon("chevDown")), menu);
document.addEventListener("click", () => menu.classList.remove("open"));
function paintRole() {
  const role = prefs.get("role");
  roleName.textContent = ROLES[role][0];
  menu.innerHTML = "";
  for (const [key, [title, sub]] of Object.entries(ROLES)) {
    menu.append(h("button", { class: key === role ? "on" : "", onclick: (e) => {
      e.stopPropagation();
      menu.classList.remove("open");
      if (key !== prefs.get("role")) { prefs.set("role", key); paintRole(); route(); }
    } }, icon(key === "teacher" ? "lectures" : "user"), h("span", {}, title, h("small", {}, sub))));
  }
}
const sys = h("div", { class: "sys" }, h("span", { class: "dot" }), h("span", {}, "Проверяю систему…"));
const themeBtn = h("button", { class: "btn btn-ghost btn-sm", title: "Сменить тему", onclick: () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  prefs.set("theme", next);
  applyTheme(next);
  paintTheme();
  route();
} });
function paintTheme() {
  themeBtn.innerHTML = "";
  const dark = document.documentElement.dataset.theme === "dark";
  themeBtn.append(icon(dark ? "sun" : "moon"), dark ? "Светлая тема" : "Тёмная тема");
}
footer.append(user, h("span", { class: "spacer" }), sys, themeBtn);
paintRole();
paintTheme();

async function refreshStatus() {
  try {
    const s = await api.status();
    const llm = s.llm.available ? `LLM: ${s.llm.name}` : "LLM не подключена — конспект по векторам";
    sys.innerHTML = "";
    sys.append(h("span", { class: `dot ${s.device === "cuda" ? "ok" : "warn"}` }),
      h("span", {}, `${s.gpu || "CPU"} · Whisper ${s.asr_model} · ${llm} · в индексе ${s.lectures} лекц.`));
  } catch (_) {
    sys.innerHTML = "";
    sys.append(h("span", { class: "dot" }), h("span", {}, "Сервер недоступен"));
  }
}

window.addEventListener("hashchange", route);
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (prefs.get("theme") === "system") applyTheme(); });
route();
refreshStatus();
setInterval(refreshStatus, 30000);
