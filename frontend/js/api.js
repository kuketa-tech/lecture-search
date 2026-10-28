// Обёртка над HTTP API бэкенда.

async function request(method, url, body) {
  const opts = { method, headers: {} };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(url, opts);
  if (!r.ok) {
    let msg = `${r.status}`;
    try { msg = (await r.json()).detail || msg; } catch (_) { /* не JSON */ }
    throw new Error(msg);
  }
  return r.json();
}

export const api = {
  lectures: () => request("GET", "/api/lectures"),
  lecture: (id) => request("GET", `/api/lectures/${id}`),
  transcript: (id) => request("GET", `/api/lectures/${id}/transcript`),
  links: (id) => request("GET", `/api/lectures/${id}/links`),
  heat: (id, q, bins = 240) => request("GET", `/api/lectures/${id}/heat?q=${encodeURIComponent(q)}&bins=${bins}`),
  addUrl: (url, title) => request("POST", "/api/lectures/url", { url, title }),
  rename: (id, title) => request("PATCH", `/api/lectures/${id}`, { title }),
  remove: (id) => request("DELETE", `/api/lectures/${id}`),
  reprocess: (id, from_stage) => request("POST", `/api/lectures/${id}/reprocess`, { from_stage }),
  search: ({ q, lecture, types = [], k = 10, mode = "rerank" }) => {
    const p = new URLSearchParams({ q, k, mode });
    if (lecture) p.set("lecture", lecture);
    if (types.length) p.set("types", types.join(","));
    return request("GET", `/api/search?${p}`);
  },
  ask: (question, lecture_id, history) => request("POST", "/api/ask", { question, lecture_id, history }),
  map: () => request("GET", "/api/map"),
  status: () => request("GET", "/api/status"),
  evalResults: () => request("GET", "/api/eval"),

  // Загрузка файла с прогрессом (fetch не умеет прогресс отправки).
  upload(file, title, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      const fd = new FormData();
      fd.append("file", file);
      fd.append("title", title || "");
      xhr.open("POST", "/api/lectures");
      xhr.upload.onprogress = (e) => e.lengthComputable && onProgress && onProgress(e.loaded / e.total);
      xhr.onload = () => {
        let data = {};
        try { data = JSON.parse(xhr.responseText); } catch (_) { /* пусто */ }
        if (xhr.status >= 200 && xhr.status < 300) resolve(data);
        else reject(new Error(data.detail || `Ошибка ${xhr.status}`));
      };
      xhr.onerror = () => reject(new Error("Сеть недоступна"));
      xhr.send(fd);
    });
  },
};
