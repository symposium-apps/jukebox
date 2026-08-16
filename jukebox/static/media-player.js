(() => {
  "use strict";

  const audio = qs("webAudio");
  const playerbar = document.querySelector(".playerbar");
  const stage = document.createElement("section");
  stage.id = "videoStage";
  stage.className = "video-stage";
  stage.hidden = true;
  stage.setAttribute("aria-label", "Video player");
  stage.innerHTML = `
    <header class="video-stage-head">
      <div class="video-stage-copy"><b id="videoTitle">Video</b><span id="videoMeta">Preparing player</span></div>
      <div class="video-stage-actions">
        <button id="audioOnlyBtn" type="button">Audio only</button>
        <button id="videoFullscreenBtn" type="button">Full screen</button>
        <button id="videoHideBtn" type="button">Hide</button>
      </div>
    </header>
    <div class="video-frame">
      <video id="webVideo" preload="metadata" playsinline muted></video>
      <span id="videoStreamBadge" class="video-frame-overlay"><i></i><span>Preparing stream</span></span>
    </div>`;
  playerbar.insertAdjacentElement("beforebegin", stage);

  const video = qs("webVideo");
  const extra = document.querySelector(".player-extra");
  const streamState = document.createElement("span");
  streamState.id = "streamState";
  streamState.className = "stream-state";
  streamState.dataset.state = "streaming";
  streamState.textContent = "Nothing loaded";
  extra.insertAdjacentElement("afterbegin", streamState);
  const showVideo = document.createElement("button");
  showVideo.id = "showVideoBtn";
  showVideo.className = "cache-button";
  showVideo.type = "button";
  showVideo.textContent = "Show video";
  showVideo.hidden = true;
  streamState.insertAdjacentElement("afterend", showVideo);

  let stageTrackId = "";
  let stageCollapsed = false;
  let streamPoll = 0;
  let pendingSeekFrame = 0;
  let lastStreamCheck = 0;
  let streamDetails = null;
  let refreshInFlight = false;
  const audioCopyStarted = new Set();
  const audioCopyRefreshed = new Set();

  function currentTrack() {
    return state.status?.current_track || null;
  }

  function isVideo(track) {
    return !!track && (track.media_kind === "video" || String(track.extension || "").toLowerCase() === ".mp4");
  }

  function ticketed(path) {
    if (!path) return "";
    const separator = path.includes("?") ? "&" : "?";
    return `${path}${separator}ticket=${encodeURIComponent(state.streamTicket)}&generation=${encodeURIComponent(state.cacheGeneration)}`;
  }

  function matchingAudio(track) {
    if (!track) return null;
    const name = String(track.name || "").trim().toLowerCase();
    const album = String(track.album || "").trim().toLowerCase();
    return state.tracks.find(candidate => String(candidate.id) !== String(track.id)
      && String(candidate.extension || "").toLowerCase() === ".mp3"
      && String(candidate.name || "").trim().toLowerCase() === name
      && String(candidate.album || "").trim().toLowerCase() === album) || null;
  }

  function cachedState(track) {
    if (!track || isVideo(track)) return { state: "streaming", label: isVideo(track) ? "Streaming video" : "Nothing loaded" };
    const id = String(track.id);
    if (serviceWorkerAudioCache.trackIds.has(id) || audioCache.entries.has(trackCacheKey(track))) return { state: "cached", label: "Saved on this device" };
    if (audioCache.pending.has(trackCacheKey(track)) || audioCache.active?.key === trackCacheKey(track)) return { state: "preparing", label: "Saving for offline play" };
    return { state: "streaming", label: "Streaming audio" };
  }

  function renderStreamState() {
    const value = cachedState(currentTrack());
    streamState.dataset.state = value.state;
    streamState.textContent = value.label;
  }

  function setVideoSource(track, details) {
    const canNativeHls = !!video.canPlayType("application/vnd.apple.mpegurl");
    const selected = details?.ready && details.hls_url && canNativeHls
      ? ticketed(details.hls_url)
      : mediaUrl(track.id);
    const mode = details?.ready ? (canNativeHls ? "HLS stream ready" : "HLS ready · direct playback") : "Preparing HLS stream · direct playback";
    const badge = qs("videoStreamBadge");
    badge.classList.toggle("ready", !!details?.ready);
    badge.querySelector("span").textContent = mode;
    qs("videoMeta").textContent = `${track.artist || "Unknown artist"} · ${mode}`;
    if (video.dataset.source !== selected) {
      const position = Number.isFinite(audio.currentTime) ? audio.currentTime : 0;
      video.dataset.source = selected;
      video.src = selected;
      video.load();
      video.addEventListener("loadedmetadata", () => {
        if (position > 0 && Number.isFinite(video.duration)) video.currentTime = Math.min(position, Math.max(0, video.duration - 0.05));
        if (!audio.paused) video.play().catch(() => {});
      }, { once: true });
    }
  }

  async function refreshVideoSurface(force = false) {
    const track = currentTrack();
    if (!isVideo(track)) {
      stage.hidden = true;
      showVideo.hidden = true;
      stageTrackId = "";
      video.pause();
      video.removeAttribute("src");
      video.dataset.source = "";
      video.load();
      streamDetails = null;
      lastStreamCheck = 0;
      renderStreamState();
      return;
    }
    if (stageTrackId !== String(track.id)) {
      stageTrackId = String(track.id);
      stageCollapsed = false;
      streamDetails = null;
      lastStreamCheck = 0;
    }
    stage.hidden = stageCollapsed;
    showVideo.hidden = !stageCollapsed;
    qs("videoTitle").textContent = track.name || track.filename || "Video";
    const audioCopy = matchingAudio(track);
    qs("audioOnlyBtn").disabled = !audioCopy;
    qs("audioOnlyBtn").textContent = audioCopy ? "Audio only" : "Audio copy preparing";
    renderStreamState();
    if (!force && streamDetails && Date.now() - lastStreamCheck < 1200) {
      setVideoSource(track, streamDetails);
      return;
    }
    if (refreshInFlight) return;
    refreshInFlight = true;
    try {
      const details = await api(`/api/streaming/${encodeURIComponent(track.id)}`);
      if (String(currentTrack()?.id || "") !== String(track.id)) return;
      streamDetails = details;
      lastStreamCheck = Date.now();
      setVideoSource(track, details);
      const copy = await api(`/api/audio-copy/${encodeURIComponent(track.id)}`);
      if (!copy.ready && copy.state !== "preparing" && !audioCopyStarted.has(String(track.id))) {
        audioCopyStarted.add(String(track.id));
        await api(`/api/audio-copy/${encodeURIComponent(track.id)}`, { method: "POST", body: "{}" });
      } else if (copy.ready && !matchingAudio(track) && !audioCopyRefreshed.has(String(track.id))) {
        audioCopyRefreshed.add(String(track.id));
        await refresh({ keepMessage: true });
      }
      if (!details.ready) {
        clearTimeout(streamPoll);
        streamPoll = window.setTimeout(() => refreshVideoSurface(true), 1500);
      } else if (!copy.ready) {
        clearTimeout(streamPoll);
        streamPoll = window.setTimeout(() => refreshVideoSurface(true), 1500);
      }
    } catch (_) {
      setVideoSource(track, { ready: false });
    } finally {
      refreshInFlight = false;
    }
  }

  function syncVideoClock(force = false) {
    if (!isVideo(currentTrack()) || !video.currentSrc || !Number.isFinite(audio.currentTime)) return;
    if (force || !Number.isFinite(video.currentTime) || Math.abs(video.currentTime - audio.currentTime) > 0.35) {
      try { video.currentTime = audio.currentTime; } catch (_) {}
    }
    if (audio.paused) video.pause();
    else if (video.paused) video.play().catch(() => {});
  }

  const slider = qs("scrubSlider");
  function renderSeekFill() {
    slider.style.setProperty("--seek", `${Math.max(0, Math.min(100, Number(slider.value || 0) / 10))}%`);
  }
  slider.addEventListener("pointerdown", () => { state.scrubbing = true; });
  slider.addEventListener("input", event => {
    renderSeekFill();
    const duration = Number.isFinite(audio.duration) ? audio.duration : 0;
    if (!duration) return;
    const target = duration * (Number(event.target.value) / 1000);
    cancelAnimationFrame(pendingSeekFrame);
    pendingSeekFrame = requestAnimationFrame(() => {
      try {
        if (typeof audio.fastSeek === "function") audio.fastSeek(target);
        else audio.currentTime = target;
      } catch (_) {}
      state.playback.position = target;
      syncVideoClock(true);
    });
  });
  slider.addEventListener("pointerup", () => {
    state.scrubbing = false;
    state.playback.position = Number(audio.currentTime || 0);
    saveBrowserPlayback(true);
    updateScrubber();
    renderSeekFill();
  });
  slider.addEventListener("change", renderSeekFill);

  ["loadedmetadata", "durationchange", "timeupdate", "seeking", "seeked", "play", "pause", "ended"].forEach(eventName => {
    audio.addEventListener(eventName, () => {
      if (eventName === "timeupdate" || eventName === "seeking" || eventName === "seeked") syncVideoClock(eventName !== "timeupdate");
      if (eventName === "play" || eventName === "pause") syncVideoClock();
      renderSeekFill();
      renderStreamState();
      refreshVideoSurface().catch(() => {});
    });
  });

  video.addEventListener("click", () => qs("playPauseBtn").click());
  qs("videoHideBtn").addEventListener("click", () => {
    stageCollapsed = true;
    stage.hidden = true;
    showVideo.hidden = false;
  });
  showVideo.addEventListener("click", () => {
    stageCollapsed = false;
    stage.hidden = false;
    showVideo.hidden = true;
  });
  qs("videoFullscreenBtn").addEventListener("click", () => {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    else stage.requestFullscreen?.().catch(() => {});
  });
  qs("audioOnlyBtn").addEventListener("click", () => {
    const audioCopy = matchingAudio(currentTrack());
    if (audioCopy) playTrack(audioCopy.id, currentQueueIds(), state.status?.state?.queue_name || "Queue").catch(error => msg(error.message));
  });

  const originalRender = render;
  render = function renderWithMediaPlayer() {
    originalRender();
    renderStreamState();
    refreshVideoSurface().catch(() => {});
    document.querySelectorAll(".track-kind").forEach(badge => badge.classList.toggle("video", badge.textContent.trim().toLowerCase() === "mp4"));
  };

  window.setInterval(() => {
    renderStreamState();
    if (isVideo(currentTrack())) syncVideoClock();
  }, 500);
  renderSeekFill();
})();
