const lectures = [];
let selectedLecture = null;
let selectedVideo = -1;
let selectedAudio = -1;
const $ = (selector) => document.querySelector(selector);

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(showToast.timeout);
  showToast.timeout = setTimeout(() => toast.classList.remove("show"), 2800);
}

function renderQueue(jobs) {
  const activeJobs = jobs.filter((job) => !["Completed"].includes(job.status));
  $("#queueCount").textContent = activeJobs.length ? `${activeJobs.length} active` : "No active jobs";
  $("#queueList").innerHTML = jobs.length
    ? jobs.slice().reverse().map((job) => `<div class="queue-job"><strong>${job.title}</strong><span>${job.status}${job.progress != null ? ` · ${job.progress}%` : ""}${job.speed ? ` · ${job.speed}` : ""}</span></div>`).join("")
    : `<p class="empty-state">Started downloads appear here.</p>`;
}

async function loadJobs() {
  try {
    const response = await fetch("/api/jobs");
    const data = await response.json();
    renderQueue(data.jobs || []);
  } catch {
    // The queue is supplementary; keep the rest of the interface usable.
  }
}

function renderLectures() {
  const query = $("#searchInput").value.toLowerCase();
  const visible = lectures.filter((lecture) => `${lecture.title} ${lecture.course}`.toLowerCase().includes(query));
  $("#resultCount").textContent = `${visible.length} lecture${visible.length === 1 ? "" : "s"}`;
  $("#lectureList").innerHTML = visible.map((lecture) => `
    <article class="lecture-item ${selectedLecture?.id === lecture.id ? "selected" : ""}" data-id="${lecture.id}">
      <div class="lecture-details"><span class="course-label">${lecture.course}</span><h3>${lecture.title}</h3><p>${lecture.meta}</p></div><span class="item-chevron">›</span>
    </article>`).join("") || `<p class="empty-state">No lectures match this search.</p>`;
  document.querySelectorAll(".lecture-item").forEach((item) => item.addEventListener("click", () => selectLecture(item.dataset.id)));
}

function renderSources() {
  if (!selectedLecture) {
    $("#sourceCount").textContent = "0";
    $("#sourceList").innerHTML = `<p class="empty-state">No lecture selected.</p>`;
    return;
  }
  const videos = selectedLecture.sources
    .map((source, index) => ({ source, index }))
    .filter(({ source }) => source.type === "Video" || source.type === "Video + audio");
  const audios = selectedLecture.sources
    .map((source, index) => ({ source, index }))
    .filter(({ source }) => source.type === "Audio" || source.type === "Video + audio");
  $("#sourceCount").textContent = `${selectedLecture.sources.length} sources`;
  if (!selectedLecture.sources.length) {
    $("#sourceList").innerHTML = `<p class="empty-state">Loading available streams...</p>`;
    return;
  }
  const audioOptions = [
    `<option value="-1">No audio</option>`,
    ...audios.map(({ source, index }) => `<option value="${index}" ${index === selectedAudio ? "selected" : ""}>${source.filename}</option>`)
  ].join("");
  const videoRows = videos.map(({ source, index }) => {
    const thumbnailStyle = source.thumbnail
      ? `background-image:url("${source.thumbnail}");background-position:center;background-size:cover`
      : `background:${selectedLecture.thumb}`;
    return `<div class="source-row">
      <button class="source-choice ${index === selectedVideo ? "selected" : ""}" data-video="${index}" type="button">
        <div class="source-thumb" style='${thumbnailStyle}'><span>▶</span></div>
        <span><strong>${source.filename}</strong><small>${source.type}</small></span>
      </button>
      <label class="audio-choice">
        <span class="sr-only">Audio for ${source.filename}</span>
        <select data-audio="${index}">${audioOptions}</select>
      </label>
    </div>`;
  }).join("");
  const audioOnly = !videos.length && audios.length
    ? `<div class="source-row no-video-row"><span>No video sources</span><label class="audio-choice"><span class="sr-only">Audio source</span><select data-audio="audio-only">${audioOptions}</select></label></div>`
    : "";
  $("#sourceList").innerHTML = `<div class="source-table-heading"><span>Video</span><span>Audio</span></div>${videoRows}${audioOnly}` ||
    `<p class="empty-state">No selectable sources.</p>`;
  document.querySelectorAll("[data-video]").forEach((button) => button.addEventListener("click", () => {
    const index = Number(button.dataset.video);
    selectedVideo = selectedVideo === index ? -1 : index;
    renderSources();
  }));
  document.querySelectorAll("[data-audio]").forEach((select) => select.addEventListener("change", () => {
    selectedAudio = Number(select.value);
    renderSources();
  }));
}

async function selectLecture(id) {
  const lecture = lectures.find((item) => item.id === id);
  if (!lecture) return;
  selectedLecture = lecture;
  $("#selectedCourse").textContent = lecture.course;
  $("#selectedTitle").textContent = lecture.title;
  $("#selectedMeta").textContent = lecture.meta;
  $("#selectedThumb").style.background = lecture.thumb;
  $("#filenamePreview").textContent = `${lecture.title.replaceAll(" ", "_").replaceAll("&", "and")}.mp4`;
  renderLectures();
  renderSources();
  if (lecture.loaded) {
    return;
  }
  lecture.loading = true;
  try {
    const response = await fetch("/api/resolve-lesson", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: lecture.url || lecture.lessonUrl })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    lecture.sources = data.formats.map((source) => ({
      ...source,
      size: source.size || "Adaptive",
      detail: source.detail || "",
      m3u: source.m3u || source.formatId
    }));
    lecture.meta = data.duration ? `${Math.round(data.duration / 60)} min` : "Lecture";
    lecture.thumb = data.thumbnail ? `url("${data.thumbnail}") center/cover` : lecture.thumb;
    lecture.loaded = true;
    if (selectedLecture !== lecture) return;
    selectedVideo = lecture.sources.findIndex((source) => source.type === "Video" || source.type === "Video + audio");
    selectedAudio = lecture.sources.findIndex((source) => source.type === "Audio" || source.type === "Video + audio");
    $("#selectedMeta").textContent = lecture.meta;
    $("#selectedThumb").style.background = lecture.thumb;
    renderLectures();
    renderSources();
  } catch (error) {
    showToast(error.message || "Could not load lecture streams");
    $("#sourceList").innerHTML = `<p class="empty-state">Could not load streams.</p>`;
  } finally {
    lecture.loading = false;
  }
}

async function loadStatus() {
  try {
    const status = await (await fetch("/api/status")).json();
    $("#connectionStatus span").textContent = status.configured ? "Brave cookies ready" : "Brave cookies not configured";
    $("#connectionStatus").classList.toggle("offline", !status.configured);
  } catch {
    $("#connectionStatus span").textContent = "Local preview mode";
  }
}

$("#searchInput").addEventListener("input", renderLectures);
$("#resolveRecording").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const url = $("#recordingUrl").value.trim();
  if (!url) return showToast("Paste an Echo360 section URL first");
  button.disabled = true;
  button.textContent = "Loading...";
  try {
    const response = await fetch("/api/resolve-section", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    lectures.splice(0, lectures.length, ...data.lectures.map((item) => ({
      ...item,
      sources: item.sources || [],
      thumb: item.thumbnail ? `url("${item.thumbnail}") center/cover` : "linear-gradient(135deg,#3b326b,#9380d3)"
    })));
    selectedLecture = null;
    selectedVideo = -1;
    selectedAudio = -1;
    $("#selectedCourse").textContent = "NO LECTURE SELECTED";
    $("#selectedTitle").textContent = "Select a lecture";
    $("#selectedMeta").textContent = "Its available streams will appear here.";
    $("#selectedThumb").style.background = "";
    $("#filenamePreview").textContent = "Waiting for lecture";
    renderSources();
    renderLectures();
    showToast(`Loaded ${data.lectures.length} lecture${data.lectures.length === 1 ? "" : "s"}`);
  } catch (error) {
    showToast(error.message || "Could not load section");
  } finally {
    button.disabled = false;
    button.innerHTML = "Load lectures <span>→</span>";
  }
});

$("#downloadBtn").addEventListener("click", async () => {
  if (!selectedLecture) return showToast("Select a lecture first");
  const video = selectedLecture.sources[selectedVideo];
  const audio = selectedLecture.sources[selectedAudio];
  if (!video && !audio) return showToast("Wait for the lecture streams to load");
  try {
    const response = await fetch("/api/download", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
      url: selectedLecture.url,
      title: selectedLecture.title,
      videoFormatId: video?.formatId,
      audioFormatId: audio?.formatId,
      videoUrl: video?.url || "",
      audioUrl: audio && audio !== video ? audio.url : ""
    }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    showToast(data.message);
    loadJobs();
  } catch (error) {
    showToast(error.message || "Could not start download");
  }
});

renderLectures();
loadStatus();
loadJobs();
setInterval(loadJobs, 1500);
