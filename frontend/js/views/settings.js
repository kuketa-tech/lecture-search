// Настройки: оформление, роль, плеер — хранятся в браузере; состояние системы и подключение LLM — справочно.
import { api } from "../api.js";
import { h, icon, side, prefs, applyTheme, toast } from "../ui.js";

export async function renderSettings(root, rerender) {
  const status = await api.status().catch(() => null);

  const seg = (key, options, after) => h("div", { class: "seg" }, options.map(([value, label]) => h("button", {
    class: prefs.get(key) === value ? "on" : "",
    onclick: () => { prefs.set(key, value); if (after) after(value); rerender(); },
  }, label)));
  const sw = (key, label) => h("label", { class: "switch" },
    h("input", { type: "checkbox", checked: prefs.get(key), onchange: (e) => prefs.set(key, e.target.checked) }), label);
  const setting = (title, text, control) => h("div", { class: "setting" }, h("div", { class: "lbl" }, h("b", {}, title), h("span", {}, text)), control);

  const speed = h("select", { class: "input", style: { width: "auto" }, onchange: (e) => prefs.set("speed", Number(e.target.value)) },
    [0.75, 1, 1.25, 1.5, 1.75, 2].map((v) => h("option", { value: v, selected: prefs.get("speed") === v }, `${v}×`)));

  root.append(h("div", { class: "surface" },
    h("div", { class: "page-head", style: { marginBottom: "10px" } }, h("div", {}, h("h1", {}, "Настройки"),
      h("p", {}, "Оформление и плеер запоминаются в этом браузере."))),
    h("div", { class: "grid-2", style: { marginTop: "18px" } },
      h("div", { class: "card" }, h("div", { class: "card-title" }, icon("sun"), "Оформление и роль"),
        setting("Тема", "Светлая — как в макете; тёмная удобна вечером", seg("theme", [["light", "Светлая"], ["dark", "Тёмная"], ["system", "Как в системе"]], applyTheme)),
        setting("Роль", "Преподаватель загружает и удаляет лекции, студент — смотрит и ищет", seg("role", [["student", "Студент"], ["teacher", "Преподаватель"]]))),
      h("div", { class: "card" }, h("div", { class: "card-title" }, icon("video"), "Плеер"),
        setting("Субтитры", "Показывать распознанный текст поверх видео", sw("captions", "включены")),
        setting("Текст за видео", "Вкладка «Текст» прокручивается к звучащей фразе", sw("follow", "следовать")),
        setting("Скорость", "Скорость воспроизведения по умолчанию", speed),
        setting("История просмотра", "Где вы остановились в каждой лекции", h("button", { class: "btn btn-sm", onclick: () => {
          try { localStorage.removeItem("sut.watch"); } catch (_) { /* недоступно */ }
          toast("История просмотра очищена");
        } }, icon("trash"), "Очистить"))))));

  if (status) {
    const llm = status.llm || {};
    root.append(h("div", { class: "surface" },
      h("div", { class: "card-title" }, icon("cpu"), "Система"),
      h("dl", { class: "kv" },
        h("dt", {}, "Устройство"), h("dd", {}, status.gpu ? `${status.gpu} (CUDA)` : "CPU"),
        h("dt", {}, "Распознавание речи"), h("dd", {}, `faster-whisper ${status.asr_model}`),
        h("dt", {}, "Векторы фраз"), h("dd", {}, status.embed_model),
        h("dt", {}, "Реранкер"), h("dd", {}, status.rerank_model || "не используется"),
        h("dt", {}, "LLM"), h("dd", {}, llm.available ? `${llm.name} (${llm.kind === "local" ? "локально" : "по API"})` : "не подключена — конспект собирается из фраз лекции"),
        h("dt", {}, "Порог «не найдено»"), h("dd", {}, status.rerank_threshold),
        h("dt", {}, "ffmpeg"), h("dd", {}, status.tools && status.tools.ffmpeg ? "найден" : "не найден"),
        h("dt", {}, "Индекс"), h("dd", {}, `${status.lectures} лекций, ${status.chunks} фрагментов`)),
      h("div", { class: "card-title", style: { marginTop: "22px" } }, icon("spark"), "Подключить другую LLM"),
      h("p", { class: "muted", style: { margin: 0 } }, "LLM нужна только для формулировок конспекта и ответов в чате — поиск и структура работают без неё. " +
        "Укажите сервер в файле .env и перезапустите программу:"),
      h("pre", { class: "code" }, "# Ollama на этом компьютере\nLLM_BACKEND=openai\nLLM_BASE_URL=http://127.0.0.1:11434/v1\nLLM_MODEL=qwen2.5:7b\n\n" +
        "# или DeepSeek по API\nLLM_BACKEND=openai\nLLM_BASE_URL=https://api.deepseek.com/v1\nLLM_MODEL=deepseek-chat\nLLM_API_KEY=…")));
  }

  side(h("div", { class: "side-card" },
    h("h3", {}, icon("info"), "О проекте"),
    h("div", { class: "muted small", style: { lineHeight: 1.6 } },
      "«Суть» — семантический поиск по видеолекциям. Распознавание, поиск и структура работают локально, без интернета: " +
      "все модели лежат рядом с программой. Кейс «Киберрывок 2026»."),
    h("a", { class: "btn btn-sm", href: "#/quality" }, icon("chart"), "Качество поиска")));
  return null;
}
