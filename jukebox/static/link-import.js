(() => {
  "use strict";

  const model = {
    screen: "paste",
    url: "",
    error: "",
    inspection: null,
    selected: new Set(),
    format: "mp3",
    quality: "best",
    artwork: true,
    destinationType: "",
    destinationName: "",
    destinationSlug: "",
    query: "",
    jobs: [],
    queueOpen: false,
    queueMinimized: false,
    activeJobId: "",
    refreshAfterJob: new Set()
  };

  const modalHost = document.createElement("div");
  modalHost.id = "linkImportModal";
  modalHost.hidden = true;
  document.body.appendChild(modalHost);

  const queueHost = document.createElement("aside");
  queueHost.id = "linkImportQueue";
  queueHost.className = "jbi-queue";
  queueHost.hidden = true;
  queueHost.setAttribute("aria-label", "Downloads");
  document.body.appendChild(queueHost);

  const strip = document.createElement("button");
  strip.id = "linkImportStrip";
  strip.className = "jbi-strip";
  strip.type = "button";
  strip.hidden = true;
  strip.setAttribute("aria-label", "Open downloads");
  document.body.appendChild(strip);

  const icon = path => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${path}"/></svg>`;
  const icons = {
    link: "M10 13a5 5 0 0 0 7.5.5l2-2a5 5 0 0 0-7-7l-1.1 1.1M14 11a5 5 0 0 0-7.5-.5l-2 2a5 5 0 0 0 7 7l1.1-1.1",
    download: "M12 3v12m-5-5 5 5 5-5M5 20h14",
    search: "M21 21l-4.4-4.4M19 11a8 8 0 1 1-16 0 8 8 0 0 1 16 0"
  };

  const importButton = document.createElement("button");
  importButton.id = "importLinkBtn";
  importButton.type = "button";
  importButton.innerHTML = `${icon(icons.link)}Import`;
  const folderButton = qs("uploadFolderBtn");
  folderButton.insertAdjacentElement("afterend", importButton);

  function esc(value) {
    return escapeHtml(String(value == null ? "" : value));
  }

  function image(url, className = "") {
    return url ? `<img class="${className}" src="${esc(url)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : `<span class="${className} jbi-art-fallback" aria-hidden="true"></span>`;
  }

  function button(label, variant = "secondary", attrs = "") {
    return `<button class="jbi-btn jbi-btn--${variant}" type="button" ${attrs}>${label}</button>`;
  }

  function box(on, mixed = false) {
    return `<span class="jbi-box${on ? " jbi-box--on" : ""}${mixed ? " jbi-box--mixed" : ""}">${on && !mixed ? "✓" : ""}</span>`;
  }

  function contextDestination() {
    if (state.view && state.view.type === "playlist") {
      const playlist = playlistBySlug(state.view.slug);
      return { type: "playlist_existing", slug: state.view.slug, name: playlist ? playlist.name : "Current playlist" };
    }
    if (state.view && state.view.type === "album") {
      return { type: "album", slug: "", name: state.view.name || "Current album" };
    }
    if (model.inspection && model.inspection.source_type === "playlist") {
      return { type: "playlist_new", slug: "", name: model.inspection.title || "Imported playlist" };
    }
    return { type: "detected_album", slug: "", name: "" };
  }

  function resetImport() {
    model.screen = "paste";
    model.error = "";
    model.inspection = null;
    model.selected = new Set();
    model.format = "mp3";
    model.quality = "best";
    model.artwork = true;
    model.query = "";
    const destination = contextDestination();
    model.destinationType = destination.type;
    model.destinationName = destination.name;
    model.destinationSlug = destination.slug;
  }

  function openImport() {
    resetImport();
    modalHost.hidden = false;
    renderModal();
    window.setTimeout(() => modalHost.querySelector("textarea")?.focus(), 0);
  }

  function closeImport() {
    modalHost.hidden = true;
    modalHost.innerHTML = "";
  }

  function shell(title, body, foot, large = false) {
    return `<div class="jbi-scrim" data-close-scrim>
      <section class="jbi-modal${large ? " jbi-modal--playlist" : ""}" role="dialog" aria-modal="true" aria-labelledby="jbiTitle">
        <header class="jbi-head"><div class="jbi-head-copy"><span>Link import</span><h2 id="jbiTitle">${esc(title)}</h2></div><button class="jbi-x" type="button" data-close aria-label="Close">×</button></header>
        <div class="jbi-body">${body}</div>
        <footer class="jbi-foot">${foot}</footer>
      </section>
    </div>`;
  }

  function pasteView(inspecting = false) {
    const error = model.error ? `<div class="jbi-error" role="alert"><span>!</span><span><b>${esc(model.error)}</b>${model.error.includes("private") ? "Ask the owner to make it unlisted or public, then try again." : "The pasted link has been kept so you can correct it."}</span></div>` : `<p class="jbi-help">Supported: YouTube and YouTube Music videos, tracks, albums, and playlists</p>`;
    const inspect = inspecting ? `<div class="jbi-inspecting" role="status" aria-live="polite"><span class="jbi-spin"></span><div><b>Inspecting link…</b><span>Reading tracks and available formats.</span></div></div>` : error;
    const body = `<div class="jbi-paste-intro"><span class="jbi-paste-icon">${icon(icons.link)}</span><div><h3>Add music or a music video</h3><p>Paste one public YouTube or YouTube Music URL. Jukebox will inspect it before anything downloads.</p></div></div>
      <label class="jbi-url-label"><span>Source URL</span><textarea class="jbi-url" rows="2" data-url aria-label="Music link" placeholder="https://youtube.com/watch?v=…" aria-invalid="${model.error ? "true" : "false"}" ${inspecting ? "readonly" : ""}>${esc(model.url)}</textarea></label>${inspect}
      <div class="jbi-capabilities"><span>MP3 audio</span><span>MP4 + audio copy</span><span>HLS streaming</span></div>`;
    const foot = `<p class="jbi-note">Public links only · no YouTube account required</p><span class="jbi-spacer"></span>${button("Cancel", "ghost", "data-close")}${button(`${inspecting ? '<span class="jbi-spin" style="width:14px;height:14px"></span>' : ""}Inspect link`, "primary", `data-inspect ${inspecting ? "disabled" : ""}`)}`;
    return shell("Import from YouTube", body, foot);
  }

  function hero(data) {
    const subtitle = data.source_type === "playlist"
      ? `<b>${esc(data.creator || "YouTube")}</b><br>${data.count} tracks • ${esc(data.duration_text || "Duration unavailable")}`
      : `<b>${esc(data.items[0]?.artist || data.creator || "Unknown artist")}</b><br>${esc(data.items[0]?.album || "Single")} • ${esc(data.items[0]?.duration_text || "")}`;
    return `<div class="jbi-hero">${image(data.thumbnail)}<div><h3>${esc(data.title)}</h3><p>${subtitle}</p></div></div>`;
  }

  function formatControls() {
    return `<div class="jbi-field"><div class="jbi-label">Format</div><div class="jbi-formats">
      <button class="jbi-format${model.format === "mp3" ? " jbi-format--active" : ""}" type="button" data-format="mp3" aria-pressed="${model.format === "mp3"}"><b>Audio only</b><span>MP3 · compact · saved for offline listening</span></button>
      <button class="jbi-format${model.format === "mp4" ? " jbi-format--active" : ""}" type="button" data-format="mp4" aria-pressed="${model.format === "mp4"}"><b>Video + audio copy</b><span>MP4 video, separate MP3, and HLS stream</span></button>
    </div></div>`;
  }

  function settingsControls() {
    const playlists = (state.playlists || []).map(item => `<option value="${esc(item.slug)}" ${model.destinationSlug === item.slug ? "selected" : ""}>${esc(item.name)}</option>`).join("");
    const radio = (type, label, hint = "") => `<button type="button" class="jbi-radio${model.destinationType === type ? " jbi-radio--active" : ""}" data-destination="${type}"><span class="jbi-radio-dot"></span><span><b>${esc(label)}</b>${hint ? `<small>${esc(hint)}</small>` : ""}</span></button>`;
    const qualityOptions = model.format === "mp4"
      ? `<option value="best">Best up to 1080p</option><option value="1080">1080p</option><option value="720">720p</option><option value="480">480p</option><option value="360">360p</option>`
      : `<option value="best">Best available</option><option value="320">320 kbps</option><option value="256">256 kbps</option><option value="192">192 kbps</option><option value="128">128 kbps</option>`;
    return `<div class="jbi-grid2">
      <div class="jbi-field"><div class="jbi-label">${model.format === "mp4" ? "Video quality" : "Audio quality"}</div><select class="jbi-select" data-quality>${qualityOptions}</select></div>
      <div class="jbi-field"><div class="jbi-label">Artwork</div><button class="jbi-check" type="button" data-artwork>${box(model.artwork)}<span><b>${model.format === "mp4" ? "Save companion artwork" : "Save and embed artwork"}</b><small>Playlist artwork is not forced onto every track.</small></span></button></div>
    </div>
    <div class="jbi-field"><div class="jbi-label">Destination</div><div class="jbi-radios">
      ${radio("playlist_new", `Create playlist “${model.inspection.title}”`, "Tracks keep their detected artist and album.")}
      ${radio("detected_album", "Organize using detected artist and album")}
      ${radio("album", "Put everything in one album folder")}
      ${radio("playlist_existing", "Add to an existing Jukebox playlist")}
    </div>
    ${model.destinationType === "playlist_existing" ? `<select class="jbi-select" data-existing style="margin-top:9px"><option value="">Choose playlist…</option>${playlists}</select>` : ""}
    ${model.destinationType === "album" ? `<input class="jbi-select" data-destination-name value="${esc(model.destinationName || model.inspection.title)}" aria-label="Album folder name" style="margin-top:9px">` : ""}
    </div>`;
  }

  function trackRow(item) {
    const checked = model.selected.has(String(item.id));
    return `<button class="jbi-track" type="button" data-track-id="${esc(item.id)}" role="checkbox" aria-checked="${checked}" ${item.unavailable ? "disabled" : ""}>
      ${box(checked)}${image(item.thumbnail, "jbi-track-art")}<span class="jbi-track-copy"><b>${esc(item.title)}</b><span>${esc(item.artist)}</span>${item.unavailable ? `<span class="jbi-unavailable">${esc(item.unavailable_reason || "Unavailable")}</span>` : ""}</span><span class="jbi-duration">${esc(item.duration_text || "—")}</span>
    </button>`;
  }

  function previewView() {
    const data = model.inspection;
    const isPlaylist = data.source_type === "playlist" || data.items.length > 1;
    if (!isPlaylist) {
      const body = `${hero(data)}${formatControls()}${settingsControls()}<p class="jbi-help">If the requested quality is unavailable, Jukebox chooses the best lower compatible quality.</p>`;
      const foot = `<span class="jbi-spacer"></span>${button("Back", "ghost", "data-back")}${button(`${icon(icons.download)}Download`, "primary", "data-download")}`;
      return shell("Import track", body, foot);
    }
    const available = data.items.filter(item => !item.unavailable);
    const filtered = data.items.filter(item => !model.query || `${item.title} ${item.artist}`.toLowerCase().includes(model.query.toLowerCase()));
    const all = available.length > 0 && available.every(item => model.selected.has(String(item.id)));
    const mixed = !all && model.selected.size > 0;
    const count = model.selected.size;
    const body = `${hero(data)}
      <label class="jbi-search">${icon(icons.search)}<input data-search value="${esc(model.query)}" placeholder="Search within playlist…" aria-label="Search within playlist"></label>
      <div class="jbi-selectbar"><button class="jbi-check" type="button" data-select-all role="checkbox" aria-checked="${all ? "true" : mixed ? "mixed" : "false"}">${box(all || mixed, mixed)}<b>Select all available</b></button><span class="jbi-selectcount">${count} selected</span></div>
      <div class="jbi-list">${filtered.map(trackRow).join("")}</div>${formatControls()}${settingsControls()}`;
    const size = Math.round(count * (model.format === "mp4" ? 40 : 7.2));
    const foot = `<p class="jbi-note">${count ? `Estimated size: approximately ${size} MB` : "Select at least one track"}</p><span class="jbi-spacer"></span>${button("Back", "ghost", "data-back")}${button(`${icon(icons.download)}${count ? `Download ${count} track${count === 1 ? "" : "s"}` : "Download"}`, "primary", `data-download ${count ? "" : "disabled"}`)}`;
    return shell("Import playlist", body, foot, true);
  }

  function renderModal() {
    if (modalHost.hidden) return;
    modalHost.innerHTML = model.screen === "inspecting" ? pasteView(true) : model.screen === "preview" ? previewView() : pasteView(false);
    const quality = modalHost.querySelector("[data-quality]");
    if (quality) quality.value = model.quality;
  }

  async function inspectLink() {
    const field = modalHost.querySelector("[data-url]");
    model.url = String(field?.value || model.url).trim();
    model.error = "";
    model.screen = "inspecting";
    renderModal();
    try {
      const response = await api("/api/import/inspect", { method: "POST", body: JSON.stringify({ url: model.url }) });
      model.inspection = response.data;
      model.selected = new Set(response.data.items.filter(item => !item.unavailable).map(item => String(item.id)));
      const destination = contextDestination();
      model.destinationType = destination.type;
      model.destinationName = destination.name;
      model.destinationSlug = destination.slug;
      model.screen = "preview";
    } catch (error) {
      model.error = error.message || "Jukebox could not inspect this link.";
      model.screen = "paste";
    }
    renderModal();
  }

  function destinationPayload() {
    return { type: model.destinationType, name: model.destinationName || model.inspection.title, slug: model.destinationSlug };
  }

  async function startDownload() {
    if (!model.inspection || !model.selected.size) return;
    const control = modalHost.querySelector("[data-download]");
    if (control) control.disabled = true;
    try {
      const response = await api("/api/import/jobs", {
        method: "POST",
        body: JSON.stringify({
          inspection_id: model.inspection.inspection_id,
          item_ids: [...model.selected],
          format: model.format,
          quality: model.quality,
          artwork: model.artwork,
          destination: destinationPayload()
        })
      });
      model.activeJobId = response.data.id;
      model.queueOpen = true;
      model.queueMinimized = false;
      closeImport();
      await pollJobs();
      msg(`Downloading ${response.data.total} track${response.data.total === 1 ? "" : "s"}`);
    } catch (error) {
      model.error = error.message || "The import could not be started.";
      model.screen = "preview";
      renderModal();
      const body = modalHost.querySelector(".jbi-body");
      body?.insertAdjacentHTML("afterbegin", `<div class="jbi-error" role="alert"><span>!</span><span><b>${esc(model.error)}</b>Your selection has been kept.</span></div>`);
    }
  }

  function fmtBytes(bytes) {
    if (!bytes) return "";
    return fmtSize(Number(bytes));
  }

  function jobItem(item, job) {
    const progress = item.progress == null ? "" : `<div class="jbi-progress" role="progressbar" aria-valuenow="${item.progress}" aria-valuemin="0" aria-valuemax="100"><i style="width:${item.progress}%"></i></div>`;
    const bytes = item.total_bytes ? `<div class="jbi-bytes">${fmtBytes(item.downloaded_bytes)} of ${fmtBytes(item.total_bytes)} · ${item.progress || 0}%</div>` : "";
    const action = !["complete", "failed", "cancelled"].includes(item.status) ? `<button class="jbi-link" type="button" data-cancel-job="${esc(job.id)}">Cancel</button>` : "";
    return `<div class="jbi-job">${image(item.thumbnail, "jbi-job-art")}<div class="jbi-job-copy"><b>${esc(item.title)}</b><div class="jbi-stage jbi-stage--${esc(item.status)}">${esc(item.stage || "Waiting")}</div>${progress}${bytes}${item.error ? `<div class="jbi-bytes" style="color:var(--jbi-danger)">${esc(item.error)}</div>` : ""}</div><div>${action}</div></div>`;
  }

  function renderQueue() {
    const jobs = model.jobs;
    const visibleJobs = jobs.slice(0, 8);
    const active = visibleJobs.find(job => !["complete", "partial", "failed", "cancelled"].includes(job.status));
    const focus = active || visibleJobs[0];
    if (!focus) {
      queueHost.hidden = true;
      strip.hidden = true;
      return;
    }
    const done = focus.completed + focus.failed + focus.cancelled;
    const summary = focus.status === "complete" ? "All downloads complete" : focus.status === "partial" ? "Import complete with problems" : focus.status === "failed" ? "Import failed" : `Downloading · ${Math.min(done + 1, focus.total)} of ${focus.total}`;
    queueHost.classList.toggle("jbi-queue--min", model.queueMinimized);
    queueHost.hidden = !model.queueOpen;
    queueHost.innerHTML = `<div class="jbi-queue-head"><b>Downloads</b><div class="jbi-queue-actions"><button type="button" data-minimize aria-label="${model.queueMinimized ? "Expand" : "Minimize"}">${model.queueMinimized ? "▣" : "—"}</button><button type="button" data-hide-queue aria-label="Close downloads">×</button></div></div>
      <div class="jbi-queue-summary"><div class="jbi-summary-line"><b>${esc(summary)}</b><span>${focus.progress}%</span></div><div class="jbi-progress"><i style="width:${focus.progress}%"></i></div></div>
      <div class="jbi-jobs">${visibleJobs.flatMap(job => job.items.map(item => jobItem(item, job))).join("")}</div>
      <div class="jbi-queue-foot">${active ? button("Cancel remaining", "ghost", `data-cancel-job="${esc(active.id)}"`) : button("View library", "secondary", "data-view-library")}<span class="jbi-spacer"></span><button class="jbi-link" type="button" data-clear-finished>Clear finished</button></div>`;
    strip.hidden = model.queueOpen || !active;
    strip.innerHTML = `<b>${esc(summary)}</b><span>${focus.progress}% · tap to open ↑</span>`;
  }

  async function pollJobs() {
    try {
      const response = await api("/api/import/jobs");
      model.jobs = response.data || [];
      for (const job of model.jobs) {
        if (["complete", "partial"].includes(job.status) && !model.refreshAfterJob.has(job.id)) {
          model.refreshAfterJob.add(job.id);
          refresh({ keepMessage: true }).catch(() => {});
        }
      }
      renderQueue();
    } catch (_) {
      // Existing app authentication handling owns fatal session failures.
    }
  }

  async function cancelJob(jobId) {
    if (!jobId) return;
    const job = model.jobs.find(value => value.id === jobId);
    if (job && job.total > 1 && !confirm("Cancel remaining downloads?\n\nCompleted tracks will stay in Jukebox. The active partial download will be removed.")) return;
    await api(`/api/import/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST", body: "{}" });
    await pollJobs();
  }

  modalHost.addEventListener("click", event => {
    const target = event.target.closest("button,[data-close-scrim]");
    if (!target) return;
    if (target.matches("[data-close]") || (target.matches("[data-close-scrim]") && event.target === target)) closeImport();
    else if (target.matches("[data-inspect]")) inspectLink();
    else if (target.matches("[data-back]")) { model.screen = "paste"; renderModal(); }
    else if (target.matches("[data-download]")) startDownload();
    else if (target.matches("[data-format]")) { model.format = target.dataset.format; model.quality = "best"; renderModal(); }
    else if (target.matches("[data-artwork]")) { model.artwork = !model.artwork; renderModal(); }
    else if (target.matches("[data-track-id]")) {
      const id = target.dataset.trackId;
      model.selected.has(id) ? model.selected.delete(id) : model.selected.add(id);
      renderModal();
    } else if (target.matches("[data-select-all]")) {
      const available = model.inspection.items.filter(item => !item.unavailable);
      const all = available.every(item => model.selected.has(String(item.id)));
      model.selected = all ? new Set() : new Set(available.map(item => String(item.id)));
      renderModal();
    } else if (target.matches("[data-destination]")) {
      model.destinationType = target.dataset.destination;
      if (model.destinationType === "playlist_new") model.destinationName = model.inspection.title;
      renderModal();
    }
  });

  modalHost.addEventListener("input", event => {
    if (event.target.matches("[data-url]")) model.url = event.target.value;
    if (event.target.matches("[data-search]")) { model.query = event.target.value; renderModal(); modalHost.querySelector("[data-search]")?.focus(); }
    if (event.target.matches("[data-quality]")) model.quality = event.target.value;
    if (event.target.matches("[data-existing]")) model.destinationSlug = event.target.value;
    if (event.target.matches("[data-destination-name]")) model.destinationName = event.target.value;
  });

  modalHost.addEventListener("keydown", event => {
    if (event.key === "Escape") { event.preventDefault(); closeImport(); }
    if (event.key === "Enter" && model.screen === "paste" && !event.shiftKey) { event.preventDefault(); inspectLink(); }
  });

  queueHost.addEventListener("click", async event => {
    const target = event.target.closest("button");
    if (!target) return;
    if (target.matches("[data-minimize]")) { model.queueMinimized = !model.queueMinimized; renderQueue(); }
    else if (target.matches("[data-hide-queue]")) { model.queueOpen = false; renderQueue(); }
    else if (target.matches("[data-cancel-job]")) await cancelJob(target.dataset.cancelJob);
    else if (target.matches("[data-clear-finished]")) { await api("/api/import/jobs/clear", { method: "POST", body: "{}" }); await pollJobs(); }
    else if (target.matches("[data-view-library]")) { model.queueOpen = false; setView({ type: "root" }); renderQueue(); }
  });

  strip.addEventListener("click", () => { model.queueOpen = true; model.queueMinimized = false; renderQueue(); });
  importButton.addEventListener("click", openImport);
  window.setInterval(pollJobs, 1500);
  pollJobs();
})();
