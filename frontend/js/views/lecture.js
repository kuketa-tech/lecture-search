// Страница лекции по макету: оглавление-таймлайн слева, плеер с субтитрами, вкладки
// «Текст / Конспект / Таймкоды / Термины / Вопросы» и панель поиска справа под навигацией.
import { api } from "../api.js";
import { h, icon, side, prefs, progress, fmtTime, fmtClock, toast, timeChip, heatStrip, plural, canManage, TYPE_LABELS } from "../ui.js";

const PLYR_RU = {
  restart: "Сначала", rewind: "Назад {seektime} с", play: "Играть", pause: "Пауза", fastForward: "Вперёд {seektime} с",
  seek: "Перемотка", seekLabel: "{currentTime} из {duration}", played: "Просмотрено", buffered: "Загружено",
  currentTime: "Текущее время", duration: "Длительность", volume: "Громкость", mute: "Без звука", unmute: "Со звуком",
  enableCaptions: "Включить субтитры", disableCaptions: "Выключить субтитры", enterFullscreen: "На весь экран",
  exitFullscreen: "Выйти из полноэкранного режима", frameTitle: "Плеер", captions: "Субтитры", settings: "Настройки",
  speed: "Скорость", normal: "Обычная", quality: "Качество", loop: "Повтор", disabled: "Выключены", enabled: "Включены",
};
const TABS = [["text", "doc", "Текст"], ["summary", "notes", "Конспект"], ["moments", "clock", "Таймкоды"], ["terms", "tag", "Термины"], ["chat", "chat", "Вопросы"]];
const MOMENT_TYPES = ["definition", "example", "proof", "recap"];
const MOMENT_LABELS = { sections: "Разделы", definition: "Определения", example: "Примеры", proof: "Выводы", recap: "Итоги" };

export async function renderLecture(root, id, params) {
  let lec = await api.lecture(id);
  const timers = [];
  let destroyed = false;
  const back = h("a", { class: "back", href: "#/lectures" }, icon("back"), "Назад к лекциям");

  if (!lec.searchable) {
    const box = h("div", { style: { maxWidth: "640px" } });
    root.append(h("div", { class: "surface" }, back, h("div", { style: { height: "18px" } }), box));
    const paint = () => {
      box.innerHTML = "";
      box.append(h("h1", {}, lec.title),
        lec.status === "error"
          ? h("div", { class: "status-line err", style: { marginTop: "14px" } }, "⚠ ", lec.error)
          : [h("div", { class: "status-line", style: { margin: "16px 0 8px" } }, h("span", { class: "pulse" }), lec.stage || "В очереди",
            h("span", { class: "spacer" }), `${Math.round(lec.progress * 100)}%`),
          h("div", { class: "progress" }, h("div", { style: { width: `${Math.max(3, lec.progress * 100)}%` } })),
          h("p", { class: "muted small" }, "Страница откроется сама, как только лекцию можно будет смотреть и искать: " +
            "поиск заработает сразу после распознавания, конспект дозреет следом.")]);
    };
    paint();
    const poll = async () => {
      if (destroyed) return;
      lec = await api.lecture(id).catch(() => lec);
      if (lec.searchable) { window.dispatchEvent(new HashChangeEvent("hashchange")); return; }
      paint();
      if (lec.status !== "error") timers.push(setTimeout(poll, 2000));
    };
    timers.push(setTimeout(poll, 2000));
    return () => { destroyed = true; timers.forEach(clearTimeout); };
  }

  const [tr, links, status] = await Promise.all([api.transcript(id), api.links(id).catch(() => ({})), api.status().catch(() => ({ llm: {} }))]);
  const sents = tr.sentences;
  const starts = sents.map((s) => s.start);
  const duration = lec.duration || (sents.length ? sents[sents.length - 1].end : 0);

  // ---------- плеер ----------
  const ext = (lec.video || "").split(".").pop();
  const video = h("video", { playsinline: true, controls: true, preload: "metadata" },
    h("source", { src: lec.video, type: ext === "webm" ? "video/webm" : "video/mp4" }),
    h("track", { kind: "captions", label: "Русский", srclang: "ru", src: lec.subtitles, default: true }));
  const videoBox = h("div", { class: "video-box" }, video);
  let player = null;

  function seek(t, play = true) {
    const apply = () => {
      video.currentTime = Math.max(0, t);
      if (play) video.play().catch(() => {});
    };
    if (video.readyState >= 1) apply();
    else video.addEventListener("loadedmetadata", apply, { once: true });
    flashSentence(t);
    resumeBox.remove();
  }

  // ---------- шапка ----------
  const level1 = lec.sections.filter((s) => s.level === 1);
  const chapters = h("div", { class: "chapters" }, level1.map((s) => h("div", {
    style: { flex: `${Math.max(1, s.end - s.start)}` }, title: `${fmtTime(s.start)} · ${s.title}`, onclick: () => seek(s.start),
  })));
  const head = h("div", {},
    h("div", { class: "lec-head" }, h("h1", { class: lec.title.length > 60 ? "long" : "" }, h("span", { class: "lbl" }, "Лекция: "), h("span", { class: "name" }, lec.title))),
    h("div", { class: "lec-meta", style: { marginTop: "10px" } },
      h("span", { class: "it" }, icon("layers"), `${level1.length} ${plural(level1.length, "раздел", "раздела", "разделов")}`),
      h("span", { class: "it" }, icon("doc"), `${sents.length} ${plural(sents.length, "фраза", "фразы", "фраз")}`),
      h("a", { class: "it", href: `/api/lectures/${id}/export.md`, download: "", title: "Конспект, оглавление и термины в Markdown" }, icon("download"), "Конспект .md"),
      h("span", { class: "it", title: "Длительность" }, icon("clock"), fmtClock(duration))));

  const saved = progress.get(id);
  const resumeBox = saved && saved.t > 20 && saved.t < duration - 20 && !params.get("t")
    ? h("div", { class: "resume" }, icon("play"), `Вы остановились на ${fmtTime(saved.t)}`, h("span", { class: "spacer" }),
      h("button", { class: "btn btn-sm btn-primary", onclick: () => seek(saved.t) }, "Продолжить"))
    : h("span");

  // ---------- вкладки ----------
  const bodies = {};
  const tabBtns = {};
  const tabsBar = h("div", { class: "tabs" });
  let followOn = prefs.get("follow");
  const textTools = h("div", { class: "tab-tools" },
    h("label", { class: "switch" }, h("input", { type: "checkbox", checked: followOn, onchange: (e) => { followOn = e.target.checked; prefs.set("follow", followOn); } }), "следовать за видео"),
    h("span", { class: "spacer" }),
    h("span", { style: { textDecoration: "underline", textDecorationColor: "var(--t-definition)", textDecorationThickness: "2px", textUnderlineOffset: "4px" } }, "определение"),
    h("span", { style: { textDecoration: "underline dotted", textDecorationColor: "var(--t-example)", textDecorationThickness: "2px", textUnderlineOffset: "4px" } }, "пример"));
  const tabsCard = h("div", { class: "tabs-card" }, tabsBar, textTools);
  for (const [key, ic, label] of TABS) {
    tabBtns[key] = h("button", { class: "tab", onclick: () => showTab(key) }, icon(ic), label);
    tabsBar.append(tabBtns[key]);
    bodies[key] = h("div", { class: "tab-body", style: { display: "none" } });
    tabsCard.append(bodies[key]);
  }
  let activeTab = "text";
  function showTab(key) {
    activeTab = key;
    for (const k in bodies) {
      bodies[k].style.display = k === key ? "" : "none";
      tabBtns[k].classList.toggle("active", k === key);
    }
    textTools.style.display = key === "text" ? "" : "none";
    if (key === "chat") setTimeout(() => chatInput.focus(), 50);
    if (key === "text" && lastIdx >= 0) scrollIntoPanel(spans[lastIdx], bodies.text);
  }

  // Текст лекции
  const spans = [];
  const transcript = h("div", { class: "transcript" });
  bodies.text.append(transcript);
  const headAt = new Map(level1.map((x) => [x.s_from, x]));
  const subAt = new Set(lec.sections.filter((x) => x.level === 2).map((x) => x.s_from));
  let p = null;
  sents.forEach((s, i) => {
    const sec = headAt.get(i);
    if (sec) {
      transcript.append(h("div", { class: "sec-head", onclick: () => seek(sec.start) }, timeChip(sec.start, seek), sec.title));
      p = null;
    } else if (subAt.has(i)) p = null;
    if (!p || p.childElementCount >= 6) { p = h("p"); transcript.append(p); }
    const span = h("span", { class: `sent ty-${s.type}`, title: `${fmtTime(s.start)}${s.type !== "explanation" ? " · " + TYPE_LABELS[s.type] : ""}`,
      onclick: () => seek(s.start) }, s.text);
    spans.push(span);
    p.append(span, " ");
  });

  function sentenceAt(t) {
    let lo = 0, hi = starts.length - 1, ans = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (starts[mid] <= t + 0.05) { ans = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return ans;
  }
  function scrollIntoPanel(el, body) {
    if (!el || body.style.display === "none") return;
    const top = el.offsetTop - body.clientHeight / 2 + el.offsetHeight / 2;
    body.scrollTo({ top, behavior: "smooth" });
  }
  function flashSentence(t) {
    const i = sentenceAt(t + 2.1);
    if (i >= 0) {
      spans[i].classList.remove("flash");
      void spans[i].offsetWidth;
      spans[i].classList.add("flash");
      scrollIntoPanel(spans[i], bodies.text);
    }
  }

  // ---------- оглавление-таймлайн слева ----------
  const tocPanel = h("div", { class: "toc-panel" });
  const tocItems = [];
  function paintToc() {
    tocPanel.innerHTML = "";
    tocItems.length = 0;
    tocPanel.append(h("h2", {}, "Оглавление"));
    if (!lec.sections.length) { tocPanel.append(h("div", { class: "muted small", style: { margin: "0 8px" } }, "Оглавление готовится…")); return; }
    const tl = h("div", { class: "tl" });
    level1.forEach((s) => {
      const subs = lec.sections.filter((x) => x.level === 2 && x.parent === s.idx);
      const subEls = subs.map((x) => {
        const el = h("div", { class: "tl-sub", onclick: (e) => { e.stopPropagation(); seek(x.start); } }, h("b", {}, fmtTime(x.start)), h("span", {}, x.title));
        tocItems.push([x, el]);
        return el;
      });
      const ls = links[String(s.idx)] || [];
      const item = h("div", { class: "tl-item", onclick: () => seek(s.start) },
        h("span", { class: "tl-time" }, fmtTime(s.start)),
        h("div", { class: "tl-title" }, s.title || "Без названия"),
        subEls.length ? h("div", { class: "tl-subs" }, subEls) : null,
        ls.map((l) => h("div", {
          class: "tl-link", title: `Та же тема в другой лекции: совпадают мысли (${Math.round(l.score * 100)}%) и общий термин`,
          onclick: (e) => { e.stopPropagation(); location.hash = `#/lecture/${l.lecture_id}?t=${Math.floor(l.t)}`; },
        }, icon("out"), h("span", {}, `также в «${l.lecture_title}», ${fmtTime(l.t)}`,
          l.term ? h("span", { class: "dim" }, ` · ${l.term}`) : null))));
      tocItems.push([s, item]);
      tl.append(item);
    });
    tocPanel.append(tl, h("div", { class: "toc-note" }, "Границы разделов найдены по векторам фраз — там, где смысл речи резко меняется."));
  }

  // ---------- конспект ----------
  function bulletList(items) {
    return h("ul", { class: "bullets" }, items.map((b) => h("li", {
      class: b.verified ? "" : "unverified",
      title: b.extractive ? "Фраза из лекции дословно"
        : `Пересказ LLM, сверен с исходной фразой (похожесть ${Math.round((b.support || 0) * 100)}%):\n«${b.source || ""}»`,
    }, timeChip(b.t, seek), h("span", {}, b.text, b.extractive ? null : h("sup", { class: "dim", style: { marginLeft: "3px" } }, "≈")))));
  }

  function paintSummary() {
    const b = bodies.summary;
    b.innerHTML = "";
    const s = lec.summary;
    if (!s) { b.append(h("div", { class: "muted" }, lec.status === "ready" ? "Конспекта нет." : "Конспект готовится…")); return; }
    const byVectors = s.method === "vectors";
    b.append(h("div", { class: "method" },
      icon("spark"), h("span", {}, "Суть отобрана векторами"),
      h("span", { class: "dim" }, "·"),
      byVectors ? h("span", {}, "пункты — ключевые фразы лекции (LLM не подключена)") : h("span", {}, `сформулировано ${s.method}, каждый пункт сверен с текстом`),
      h("span", { class: "dim" }, "·"),
      h("span", { title: "Какая доля смысла лекции «покрыта» выбранными фразами" }, `покрытие смысла ${Math.round((s.coverage || 0) * 100)}%`),
      byVectors && status.llm && status.llm.available && canManage() ? h("button", { class: "btn btn-sm", onclick: async () => {
        await api.reprocess(id, "structure"); toast("Переписываю конспект с LLM — это займёт минуту"); pollUntilReady();
      } }, "Переписать с LLM") : null));
    b.append(h("div", { class: "summary-overview" }, s.overview));
    if (s.key_points.length) b.append(h("h3", { style: { margin: "18px 0 4px" } }, "Главное"), bulletList(s.key_points));
    level1.forEach((sec) => {
      b.append(h("div", { class: "sum-section" },
        h("h3", {}, timeChip(sec.start, seek), sec.title),
        h("div", { class: "coverage", title: "Насколько выбранные фразы представляют весь раздел" },
          h("span", { class: "bar" }, h("div", { style: { width: `${Math.round(sec.coverage * 100)}%` } })), `передаёт ${Math.round(sec.coverage * 100)}% смысла раздела`),
        bulletList(sec.bullets)));
    });
  }

  // ---------- таймкоды: разделы и ключевые моменты по типам ----------
  let momentMode = "sections";
  function paintMoments() {
    const b = bodies.moments;
    b.innerHTML = "";
    const counts = Object.fromEntries(MOMENT_TYPES.map((t) => [t, sents.filter((s) => s.type === t).length]));
    const seg = h("div", { class: "seg" }, ["sections", ...MOMENT_TYPES].filter((m) => m === "sections" || counts[m]).map((m) =>
      h("button", { class: m === momentMode ? "on" : "", onclick: () => { momentMode = m; paintMoments(); } },
        MOMENT_LABELS[m], m === "sections" ? null : h("span", { class: "dim" }, ` ${counts[m]}`))));
    const copyBtn = h("button", { class: "btn btn-sm", title: "Список таймкодов в формате описания YouTube/Rutube — видео само разобьётся на главы",
      onclick: () => copyChapters() }, icon("copy"), "Копировать главы");
    b.append(h("div", { class: "row", style: { marginBottom: "14px" } }, seg, h("span", { class: "spacer" }), copyBtn));
    const list = h("div", { class: "moments" });
    if (momentMode === "sections") {
      lec.sections.forEach((s) => list.append(h("div", { class: "moment", style: s.level === 2 ? { paddingLeft: "34px" } : {}, onclick: () => seek(s.start) },
        timeChip(s.start, seek), h("span", {}), h("span", { class: "txt", style: { fontWeight: s.level === 1 ? 600 : 400 } }, s.title))));
    } else {
      sents.filter((s) => s.type === momentMode).forEach((s) => list.append(h("div", { class: "moment", onclick: () => seek(s.start) },
        timeChip(s.start, seek), h("span", { class: `chip t-${s.type}` }, TYPE_LABELS[s.type]), h("span", { class: "txt" }, s.text))));
      b.append(h("div", { class: "muted small", style: { marginBottom: "8px" } },
        "Тип фразы определён без обучения: вектор фразы сравнивается с эталонными формулировками и проверяется по словам-маркерам («называется», «например»…)."));
    }
    b.append(list);
  }
  async function copyChapters() {
    // YouTube требует, чтобы первая глава начиналась с 0:00
    const lines = level1.map((s, i) => {
      const t = i === 0 ? 0 : Math.floor(s.start);
      const hh = Math.floor(t / 3600), mm = Math.floor((t % 3600) / 60), ss = t % 60;
      const stamp = hh ? `${hh}:${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}` : `${mm}:${String(ss).padStart(2, "0")}`;
      return `${stamp} ${s.title}`;
    }).join("\n");
    try { await navigator.clipboard.writeText(lines); toast("Главы скопированы — вставьте их в описание видео"); }
    catch (_) { toast("Браузер не дал доступ к буферу обмена", "err"); }
  }

  // ---------- термины ----------
  function paintTerms() {
    const b = bodies.terms;
    b.innerHTML = "";
    if (!lec.terms.length) { b.append(h("div", { class: "muted" }, lec.status === "ready" ? "Терминов не найдено." : "Термины готовятся…")); return; }
    b.append(h("div", { class: "muted small", style: { marginBottom: "6px" } },
      "Термины выбраны по частоте, редкости в других лекциях и близости к смыслу этой лекции. Метки на полосе — все упоминания."));
    lec.terms.forEach((t) => {
      b.append(h("div", { class: "term" },
        h("div", { class: "term-head" }, h("span", { class: "term-name" }, t.term),
          h("span", { class: "chip" }, `${t.count} ${plural(t.count, "упоминание", "упоминания", "упоминаний")}`),
          h("span", { class: "spacer" }), h("span", { class: "small muted" }, "впервые"), timeChip(t.first_time, seek)),
        t.def_text ? h("div", { class: "term-def" }, t.def_text, " ", timeChip(t.def_time, seek)) : null,
        h("div", { class: "ticks" }, t.mentions.map((m) => h("span", { style: { left: `${(m / duration) * 100}%` }, title: fmtTime(m), onclick: () => seek(m) })))));
    });
  }

  // ---------- вопрос-ответ ----------
  const history = [];
  const chatLog = h("div", { class: "chat-log" });
  const chatInput = h("input", { class: "input", placeholder: "Задайте вопрос по лекции…" });
  const suggest = h("div", { class: "chat-suggest" });
  const chatForm = h("form", { class: "chat-form", onsubmit: (e) => { e.preventDefault(); ask(chatInput.value); } }, chatInput,
    h("button", { class: "btn btn-primary", type: "submit" }, "Спросить"));
  bodies.chat.append(h("div", { class: "chat" }, chatLog, suggest, chatForm));
  chatLog.append(h("div", { class: "msg bot" }, "Спросите что угодно по этой лекции. Я найду нужные фрагменты и отвечу со ссылками на моменты видео. " +
    "Если в лекции об этом не говорилось — так и скажу."));
  function paintSuggest() {
    suggest.innerHTML = "";
    lec.terms.slice(0, 3).forEach((t) => suggest.append(h("span", { class: "chip chip-toggle", onclick: () => ask(`Что такое ${t.term}?`) }, `Что такое ${t.term}?`)));
  }
  async function ask(text) {
    text = (text || "").trim();
    if (!text) return;
    chatInput.value = "";
    chatLog.append(h("div", { class: "msg user" }, text));
    const typing = h("div", { class: "msg bot" }, h("span", { class: "typing" }, h("i"), h("i"), h("i")));
    chatLog.append(typing);
    chatLog.scrollTop = chatLog.scrollHeight;
    try {
      const res = await api.ask(text, id, history.slice(-6));
      typing.remove();
      history.push({ role: "user", content: text }, { role: "assistant", content: res.answer });
      chatLog.append(answerNode(res));
    } catch (e) {
      typing.remove();
      chatLog.append(h("div", { class: "msg bot" }, `Ошибка: ${e.message}`));
    }
    chatLog.scrollTop = chatLog.scrollHeight;
  }
  function answerNode(res) {
    const srcByN = Object.fromEntries(res.sources.map((s) => [s.n, s]));
    const parts = res.answer.split(/(\[\d+\])/g).map((part) => {
      const m = part.match(/^\[(\d+)\]$/);
      if (m && srcByN[m[1]]) {
        const s = srcByN[m[1]];
        return h("span", { class: "cite", title: `${fmtTime(s.t)} — ${s.best}`, onclick: () => openSource(s) }, `${m[1]} · ${fmtTime(s.t)}`);
      }
      return part;
    });
    const node = h("div", { class: "msg bot" }, parts);
    const shown = res.sources.filter((s) => s.cited !== false);
    if (shown.length) {
      node.append(h("div", { class: "sources" }, shown.map((s) => h("div", { class: "src", onclick: () => openSource(s) },
        h("span", { class: "chip chip-time" }, `${s.n} · ${fmtTime(s.t)}`), h("span", {}, s.best)))));
    }
    const meta = [];
    if (res.cached) meta.push(`ответ из кэша: похожий вопрос «${res.similar_question}» уже задавали`);
    if (res.search_query && res.search_query !== history[history.length - 2]?.content) meta.push(`искал: «${res.search_query}»`);
    if (res.llm) meta.push(`сформулировано ${res.llm}`);
    if (meta.length) node.append(h("div", { class: "meta" }, meta.join(" · ")));
    return node;
  }
  function openSource(s) {
    if (s.lecture_id === id) seek(s.t);
    else location.hash = `#/lecture/${s.lecture_id}?t=${Math.floor(s.t)}`;
  }

  // ---------- поиск справа: по этой лекции или по всем ----------
  let scope = "lecture";
  const sInput = h("input", { type: "search", placeholder: "Найти по смыслу…", value: params.get("q") || "" });
  const heatBox = h("div");
  const resList = h("div", { class: "res-list" });
  const scopeSeg = h("div", { class: "seg" });
  function paintScope() {
    scopeSeg.innerHTML = "";
    [["lecture", "В этой лекции"], ["all", "Во всех"]].forEach(([k, label]) => scopeSeg.append(h("button", {
      class: k === scope ? "on" : "", type: "button", onclick: () => { scope = k; paintScope(); if (sInput.value.trim()) runSearch(); },
    }, label)));
  }
  function paintHint() {
    resList.innerHTML = "";
    resList.append(h("div", { class: "muted small" }, "Спросите своими словами — найдётся нужная секунда, даже если в лекции сказано иначе."));
    lec.terms.slice(0, 3).forEach((t) => resList.append(h("div", { class: "res-item", onclick: () => { sInput.value = t.term; runSearch(); } },
      h("div", { class: "body" }, h("span", { class: "snip" }, `например: «${t.term}»`)), icon("chev", "chev"))));
  }
  async function runSearch() {
    const q = sInput.value.trim();
    heatBox.innerHTML = "";
    if (!q) { paintHint(); return; }
    resList.innerHTML = "";
    resList.append(h("div", { class: "loader" }));
    const lectureFilter = scope === "lecture" ? id : "";
    const [res, heat] = await Promise.all([
      api.search({ q, lecture: lectureFilter, k: 8 }),
      scope === "lecture" ? api.heat(id, q, 240).catch(() => null) : null,
    ]);
    resList.innerHTML = "";
    if (heat && heat.bins && heat.bins.length) {
      const strip = heatStrip({ bins: heat.bins, duration, marks: res.results.map((r) => r.t), onSeek: (t) => seek(t), tall: true,
        title: "Где в лекции говорится о запросе. Тёмные метки — найденные фрагменты" });
      heatBox.append(strip, h("div", { class: "heat-legend" }, h("span", {}, "00:00"), h("span", {}, "ярче — ближе по смыслу"), h("span", {}, fmtTime(duration))));
      heatBox.strip = strip;
    }
    if (!res.results.length) {
      resList.append(h("div", { class: "muted small" }, scope === "lecture"
        ? "В этой лекции об этом, похоже, не говорится. Попробуйте искать во всех лекциях."
        : "В лекциях курса об этом, похоже, не говорится."));
      return;
    }
    res.results.forEach((r) => {
      const best = r.sentences.find((s) => s.best);
      const here = r.lecture_id === id;
      resList.append(h("div", { class: "res-item", onclick: () => (here ? seek(r.t) : (location.hash = `#/lecture/${r.lecture_id}?t=${Math.floor(r.t)}&q=${encodeURIComponent(q)}`)) },
        h("div", { class: "body" },
          h("div", { class: "row", style: { gap: "8px", flexWrap: "nowrap" } }, h("span", { class: "chip chip-time" }, fmtTime(r.t)),
            here ? null : h("span", { class: "where" }, r.lecture_title)),
          h("span", { class: "snip" }, best ? best.pieces.map(([t, hl]) => (hl ? h("mark", {}, t) : t)) : "")),
        icon("chev", "chev")));
    });
  }
  const searchCard = h("div", { class: "side-card grow" },
    h("h3", {}, icon("search"), "Поиск по лекции"),
    h("form", { class: "search-box", onsubmit: (e) => { e.preventDefault(); runSearch(); } }, icon("search"), sInput),
    scopeSeg, heatBox, h("div", { class: "res-title" }, "Результаты поиска"), resList);
  paintScope();
  side(searchCard);

  // ---------- сборка страницы ----------
  root.append(h("div", { class: "surface lec" },
    h("div", { class: "lec-left" }, back, tocPanel),
    h("div", { class: "lec-main" }, head, resumeBox, videoBox, chapters, tabsCard)));
  const tab0 = params.get("tab");
  let lastIdx = -1;
  showTab(TABS.some(([k]) => k === tab0) ? tab0 : "text");
  paintToc();
  paintSummary();
  paintMoments();
  paintTerms();
  paintSuggest();

  if (window.Plyr) {
    player = new window.Plyr(video, {
      captions: { active: prefs.get("captions"), language: "ru", update: false }, iconUrl: "/static/vendor/plyr.svg", blankVideo: "",
      seekTime: 10, keyboard: { focused: true, global: false }, i18n: PLYR_RU, invertTime: false,
      speed: { selected: prefs.get("speed"), options: [0.75, 1, 1.25, 1.5, 1.75, 2] },
      controls: ["play-large", "play", "rewind", "fast-forward", "progress", "current-time", "duration", "mute", "volume", "captions", "settings", "fullscreen"],
    });
    player.on("ratechange", () => prefs.set("speed", player.speed));
    player.on("captionsenabled", () => prefs.set("captions", true));
    player.on("captionsdisabled", () => prefs.set("captions", false));
  }

  // синхронизация текста, оглавления и полосы разделов с видео
  let lastSaved = 0;
  const onTime = () => {
    const t = video.currentTime;
    const i = sentenceAt(t);
    if (i !== lastIdx) {
      if (lastIdx >= 0) spans[lastIdx].classList.remove("now");
      if (i >= 0) {
        spans[i].classList.add("now");
        if (followOn && activeTab === "text" && !video.paused) scrollIntoPanel(spans[i], bodies.text);
      }
      lastIdx = i;
    }
    let cur = null;
    tocItems.forEach(([s, el]) => {
      const on = t >= s.start && t < s.end + 0.5;
      if (s.level === 1) {
        el.classList.toggle("active", on);
        el.classList.toggle("done", t >= s.end + 0.5);
        if (on) cur = s;
      } else el.classList.toggle("active", on);
    });
    [...chapters.children].forEach((c, k) => c.classList.toggle("active", level1[k] === cur));
    if (heatBox.strip) heatBox.strip.setCursor(t);
    if (Math.abs(t - lastSaved) > 5) { lastSaved = t; progress.save(id, t, duration); }
  };
  video.addEventListener("timeupdate", onTime);

  function pollUntilReady() {
    const poll = async () => {
      if (destroyed) return;
      const fresh = await api.lecture(id).catch(() => null);
      if (fresh && fresh.status === "ready") {
        lec = fresh;
        paintToc(); paintSummary(); paintMoments(); paintTerms(); paintSuggest();
        toast("Конспект обновлён");
        return;
      }
      timers.push(setTimeout(poll, 3000));
    };
    timers.push(setTimeout(poll, 3000));
  }
  if (lec.status !== "ready") pollUntilReady();

  const t0 = parseFloat(params.get("t"));
  if (!Number.isNaN(t0)) seek(t0, true);
  if (sInput.value) runSearch(); else paintHint();

  return () => {
    destroyed = true;
    timers.forEach(clearTimeout);
    video.removeEventListener("timeupdate", onTime);
    if (video.currentTime > 0) progress.save(id, video.currentTime, duration);
    try { if (player) player.destroy(); } catch (_) { /* уже удалён */ }
    video.pause();
    video.removeAttribute("src");
  };
}
