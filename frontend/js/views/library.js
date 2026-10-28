// «Мои лекции»: сетка лекций со статусом обработки; справа — загрузка файла или ссылки (для преподавателя).
import { api } from "../api.js";
import { h, icon, side, fmtDuration, fmtClock, toast, plural, canManage, progress } from "../ui.js";

export function lectureCard(l, { onChange, compact = false } = {}) {
  const processing = l.status === "queued" || l.status === "processing";
  const href = l.searchable ? `#/lecture/${l.id}` : `#/lecture/${l.id}`;
  const watched = progress.get(l.id);
  const thumb = h("a", { class: "lecture-thumb", href },
    l.thumb ? h("img", { src: l.thumb, alt: "", loading: "lazy" }) : h("div", { class: "ph" }, icon("video")),
    l.duration ? h("span", { class: "dur" }, icon("clock"), fmtClock(l.duration)) : null,
    l.searchable ? h("div", { class: "play" }, h("span", {}, icon("play"))) : null,
    watched && l.duration ? h("div", { style: { position: "absolute", left: 0, bottom: 0, height: "4px", background: "var(--accent)",
      width: `${Math.min(100, (watched.t / l.duration) * 100)}%` } }) : null);
  let status;
  if (l.status === "error") status = h("div", { class: "status-line err" }, "⚠ ", l.error || "Ошибка обработки");
  else if (processing) {
    const stage = l.status === "queued" ? (l.queue > 1 ? `В очереди: ${l.queue}-я` : "В очереди") : l.stage;
    const queued = l.status === "queued";
    status = h("div", {},
      h("div", { class: "status-line" }, h("span", { class: "pulse" }), stage, h("span", { class: "spacer" }), queued ? "" : `${Math.round(l.progress * 100)}%`),
      queued ? null : h("div", { class: "progress", style: { marginTop: "6px" } }, h("div", { style: { width: `${Math.max(3, l.progress * 100)}%` } })),
      l.searchable ? h("div", { class: "status-line", style: { marginTop: "6px" } }, "✓ уже можно искать, конспект готовится") : null);
  } else {
    status = h("div", { class: "status-line" }, `${l.sections} ${plural(l.sections, "раздел", "раздела", "разделов")}`,
      l.llm ? h("span", { class: "dim" }, `· конспект: ${l.llm === "vectors" ? "по векторам" : l.llm}`) : null);
  }
  const manage = canManage() && !compact;
  const actions = h("div", { class: "row", style: { marginTop: "auto", gap: "6px" } },
    l.searchable ? h("a", { class: "btn btn-sm btn-primary", href }, "Открыть") : null,
    manage && l.status === "error" ? h("button", { class: "btn btn-sm", onclick: async () => {
      try { await api.reprocess(l.id, "prepare"); onChange && onChange(); } catch (e) { toast(e.message, "err"); }
    } }, icon("refresh"), "Повторить") : null,
    h("span", { class: "spacer" }),
    manage ? h("button", { class: "btn btn-sm btn-ghost", title: "Переименовать", onclick: async () => {
      const t = prompt("Название лекции", l.title);
      if (t && t.trim()) { await api.rename(l.id, t.trim()); onChange && onChange(); }
    } }, icon("edit")) : null,
    manage ? h("button", { class: "btn btn-sm btn-ghost btn-danger", title: "Удалить", onclick: async () => {
      if (!confirm(`Удалить лекцию «${l.title}»?`)) return;
      await api.remove(l.id);
      toast("Лекция удалена");
      onChange && onChange();
    } }, icon("trash")) : null);
  return h("div", { class: "lecture-card" }, thumb,
    h("div", { class: "lecture-body" }, h("a", { class: "lecture-title", href }, l.title), status, actions));
}

export function uploadCard(onDone) {
  const fileInput = h("input", { type: "file", accept: "video/*,audio/*,.mkv", style: { display: "none" } });
  const titleInput = h("input", { class: "input", placeholder: "Название (необязательно)" });
  const urlInput = h("input", { class: "input", placeholder: "Ссылка: Rutube, VK, YouTube…" });
  const bar = h("div", { class: "upload-bar" }, h("div"));
  const drop = h("div", { class: "dropzone", onclick: () => fileInput.click() },
    icon("upload"),
    h("div", { class: "big" }, "Перетащите видео"),
    h("div", { class: "muted small" }, "или нажмите · MP4, WebM, MKV, MP3"));

  async function uploadFile(file) {
    if (!file) return;
    bar.style.display = "block";
    try {
      await api.upload(file, titleInput.value.trim(), (p) => { bar.firstChild.style.width = `${Math.round(p * 100)}%`; });
      toast(`«${file.name}» загружено — начинаю обработку`);
      titleInput.value = "";
      onDone && onDone();
    } catch (e) {
      toast(`Не удалось загрузить: ${e.message}`, "err");
    } finally {
      bar.style.display = "none";
      bar.firstChild.style.width = "0";
      fileInput.value = "";
    }
  }
  fileInput.addEventListener("change", () => uploadFile(fileInput.files[0]));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("drag"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("drag"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("drag"); uploadFile(e.dataTransfer.files[0]); });

  const urlForm = h("form", { class: "upload", onsubmit: async (e) => {
    e.preventDefault();
    const url = urlInput.value.trim();
    if (!url) return;
    try {
      await api.addUrl(url, titleInput.value.trim());
      toast("Ссылка добавлена — скачиваю видео");
      urlInput.value = "";
      titleInput.value = "";
      onDone && onDone();
    } catch (err) { toast(err.message, "err"); }
  } }, urlInput, h("button", { class: "btn", type: "submit" }, icon("link"), "Добавить по ссылке"));

  return h("div", { class: "side-card upload" }, h("h3", {}, icon("upload"), "Добавить лекцию"), drop, fileInput, bar,
    titleInput, h("div", { class: "or" }, "или"), urlForm);
}

export async function renderLibrary(root) {
  let timer = null;
  let destroyed = false;
  const list = h("div", { class: "lectures" });
  const counter = h("span", { class: "muted" });

  root.append(h("div", { class: "surface" },
    h("div", { class: "page-head", style: { marginBottom: "22px" } },
      h("div", {}, h("h1", {}, "Мои лекции"), h("p", {}, canManage()
        ? "Загрузите видео — система распознает речь, разобьёт лекцию на разделы, выделит суть и термины. Искать можно сразу после распознавания."
        : "Лекции курса. Откройте любую — с оглавлением, субтитрами, конспектом и поиском по смыслу.")),
      h("span", { class: "spacer" }), counter),
    list));
  if (canManage()) side(uploadCard(() => refresh()));

  async function refresh() {
    if (destroyed) return;
    let lectures = [];
    try { lectures = await api.lectures(); } catch (e) { toast(`Сервер недоступен: ${e.message}`, "err"); }
    list.innerHTML = "";
    counter.textContent = lectures.length ? `${lectures.length} ${plural(lectures.length, "лекция", "лекции", "лекций")}` : "";
    if (!lectures.length) {
      list.append(h("div", { class: "empty", style: { gridColumn: "1/-1" } }, icon("video"), h("div", {},
        canManage() ? "Пока нет ни одной лекции. Загрузите видео или вставьте ссылку справа." : "Преподаватель ещё не загрузил лекции.")));
    }
    lectures.forEach((l) => list.append(lectureCard(l, { onChange: refresh })));
    clearTimeout(timer);
    if (lectures.some((l) => l.status === "queued" || l.status === "processing")) timer = setTimeout(refresh, 2000);
  }

  await refresh();
  return () => { destroyed = true; clearTimeout(timer); };
}
