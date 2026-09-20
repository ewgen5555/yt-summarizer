const $ = (id) => document.getElementById(id);
const form = $("form"), statusEl = $("status"), resultEl = $("result"), btn = $("submit");

const STATUS_TEXT = {
  queued: "В очереди…",
  downloading: "Получаем данные видео…",
  transcribing: "Распознаём речь…",
  analyzing: "Анализируем содержание…",
  done: "Готово",
  error: "Ошибка",
};

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  resultEl.classList.add("hidden");
  setStatus("Отправляем…");
  btn.disabled = true;
  try {
    const res = await fetch("/api/process", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: $("url").value.trim() }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    const job = await res.json();
    await poll(job.id);
  } catch (err) {
    setStatus("Ошибка: " + err.message, true);
  } finally {
    btn.disabled = false;
  }
});

async function poll(id) {
  for (;;) {
    const job = await (await fetch(`/api/jobs/${id}`)).json();
    setStatus(`${STATUS_TEXT[job.status] || job.status} ${job.progress || ""}`, job.status === "error");
    if (job.status === "done") return render(job);
    if (job.status === "error") return setStatus("Ошибка: " + job.error, true);
    await new Promise((r) => setTimeout(r, 2500));
  }
}

function setStatus(text, isError = false) {
  statusEl.textContent = text;
  statusEl.classList.remove("hidden");
  statusEl.classList.toggle("error", isError);
}

function render(job) {
  const { video, transcript, analysis } = job;
  $("title").textContent = video.title;
  const mins = video.duration_sec ? Math.round(video.duration_sec / 60) + " мин · " : "";
  $("meta").textContent = `${video.channel || ""} · ${mins}источник текста: ${transcript.source}`;
  $("summary").textContent = analysis.summary;
  $("ideas").replaceChildren(...analysis.key_ideas.map((t) => el("li", t)));
  $("topic").textContent = analysis.structure.topic;
  $("sections").replaceChildren(
    ...analysis.structure.sections.map((s) => {
      const li = el("li", "");
      li.append(el("strong", s.title + ". "), document.createTextNode(s.summary));
      return li;
    })
  );
  $("transcript").textContent = transcript.text;
  resultEl.classList.remove("hidden");
}

function el(tag, text) {
  const n = document.createElement(tag);
  n.textContent = text;
  return n;
}
