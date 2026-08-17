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
    </div>
    <footer class="video-transport" aria-label="Video playback controls">
      <div class="video-transport-buttons">
        <button id="videoRewindBtn" type="button" aria-label="Rewind 10 seconds" title="Rewind 10 seconds">−10</button>
        <button id="videoPlayPauseBtn" class="video-play-pause" type="button" aria-label="Pause" title="Play or pause">Pause</button>
        <button id="videoForwardBtn" type="button" aria-label="Forward 10 seconds" title="Forward 10 seconds">+10</button>
      </div>
      <div class="video-progress">
        <span id="videoElapsedText">0:00</span>
        <input id="videoScrubSlider" type="range" min="0" max="1000" value="0" step="1" aria-label="Video position" disabled>
        <span id="videoDurationText">0:00</span>
      </div>
    </footer>`;
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
  showVideo.className = "control-button video-return-control";
  showVideo.type = "button";
  showVideo.title = "Show video";
  showVideo.innerHTML = '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="5" width="17" height="12" rx="2"/><path d="M9 21h6"/><path d="M12 17v4"/></svg><span>Show video</span>';
  showVideo.hidden = true;
  qs("nextBtn").insertAdjacentElement("afterend", showVideo);

  let stageTrackId = "";
  let stageCollapsed = false;
  let streamPoll = 0;
  let pendingSeekFrame = 0;
  let lastStreamCheck = 0;
  let streamDetails = null;
  let refreshInFlight = false;
  let videoSourceIsHls = false;
  let sourceRevision = 0;
  let hlsAlignmentToken = 0;
  let hlsAligning = false;
  let resumeAfterVideoScrub = false;
  let videoScrubbing = false;
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
    const useNativeHls = !!(details?.ready && details.hls_url && canNativeHls);
    const selected = useNativeHls
      ? ticketed(details.hls_url)
      : mediaUrl(track.id);
    const mode = details?.ready ? (canNativeHls ? "HLS stream ready" : "HLS ready · direct playback") : "Preparing HLS stream · direct playback";
    const badge = qs("videoStreamBadge");
    badge.classList.toggle("ready", !!details?.ready);
    badge.querySelector("span").textContent = mode;
    qs("videoMeta").textContent = `${track.artist || "Unknown artist"} · ${mode}`;
    if (video.dataset.source !== selected) {
      const position = Number.isFinite(audio.currentTime) ? audio.currentTime : 0;
      const shouldResume = !audio.paused;
      const revision = ++sourceRevision;
      videoSourceIsHls = useNativeHls;
      if (useNativeHls && shouldResume) {
        audio.pause();
        video.pause();
      }
      video.dataset.source = selected;
      video.src = selected;
      video.load();
      video.addEventListener("loadedmetadata", () => {
        if (revision !== sourceRevision) return;
        if (useNativeHls) alignHlsPair(position, { resume: shouldResume, revision });
        else {
          if (position > 0 && Number.isFinite(video.duration)) seekVideo(position);
          if (!audio.paused) video.play().catch(() => {});
        }
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
      videoSourceIsHls = false;
      hlsAlignmentToken += 1;
      hlsAligning = false;
      resumeAfterVideoScrub = false;
      sourceRevision += 1;
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

  function seekVideo(target) {
    if (!Number.isFinite(target) || !video.currentSrc) return;
    const duration = Number.isFinite(video.duration) ? video.duration : target;
    const bounded = Math.max(0, Math.min(target, Math.max(0, duration - 0.05)));
    try {
      // Native HLS implementations can silently ignore fastSeek before their
      // seekable ranges settle. An explicit currentTime assignment is queued
      // correctly and is also what user-driven HLS seeking expects.
      if (!videoSourceIsHls && typeof video.fastSeek === "function") video.fastSeek(bounded);
      else video.currentTime = bounded;
    } catch (_) {}
  }

  function waitUntilPlayable(element, token, revision, timeout = 30000) {
    // Remote native HLS commonly remains at HAVE_CURRENT_DATA (2) until play()
    // is called. Requiring HAVE_FUTURE_DATA here deadlocks that transition.
    if (element.readyState >= 2 && !element.seeking) return Promise.resolve(true);
    return new Promise(resolve => {
      let timer = 0;
      const finish = value => {
        clearTimeout(timer);
        element.removeEventListener("canplay", check);
        element.removeEventListener("seeked", check);
        element.removeEventListener("loadeddata", check);
        resolve(value);
      };
      const check = () => {
        if (token !== hlsAlignmentToken || revision !== sourceRevision) finish(false);
        else if (element.readyState >= 2 && !element.seeking) finish(true);
      };
      element.addEventListener("canplay", check);
      element.addEventListener("seeked", check);
      element.addEventListener("loadeddata", check);
      timer = window.setTimeout(() => finish(false), timeout);
      check();
    });
  }

  async function alignHlsPair(target, { resume = false, revision = sourceRevision } = {}) {
    if (!videoSourceIsHls || revision !== sourceRevision || !Number.isFinite(target)) return false;
    const token = ++hlsAlignmentToken;
    hlsAligning = true;
    audio.pause();
    video.pause();
    try { audio.currentTime = Math.max(0, target); } catch (_) {}
    seekVideo(target);
    const videoReady = await waitUntilPlayable(video, token, revision);
    if (!videoReady || token !== hlsAlignmentToken || revision !== sourceRevision) {
      if (token === hlsAlignmentToken) hlsAligning = false;
      return false;
    }
    // Native HLS may settle on a nearby keyframe. Align the paused audio clock
    // to the frame the browser can actually render before starting either one.
    const aligned = Number.isFinite(video.currentTime) ? video.currentTime : target;
    try { audio.currentTime = Math.max(0, aligned); } catch (_) {}
    const audioReady = await waitUntilPlayable(audio, token, revision);
    if (!audioReady || token !== hlsAlignmentToken || revision !== sourceRevision) {
      if (token === hlsAlignmentToken) hlsAligning = false;
      return false;
    }
    if (resume) {
      await Promise.allSettled([audio.play(), video.play()]);
      // Keep the recovery guard active through the first successful play turn;
      // native HLS can emit `waiting` synchronously from play().
      if (token === hlsAlignmentToken) hlsAligning = false;
      return !audio.paused && !video.paused;
    }
    hlsAligning = false;
    return true;
  }

  function syncVideoClock(force = false) {
    if (!isVideo(currentTrack()) || !video.currentSrc || !Number.isFinite(audio.currentTime)) return;
    const drift = Number.isFinite(video.currentTime) ? video.currentTime - audio.currentTime : Infinity;
    if (force) {
      seekVideo(audio.currentTime);
    } else if (videoSourceIsHls) {
      // Native HLS seeks may snap to a segment/keyframe. Re-seeking every clock tick
      // traps playback in the first segment, so use gentle rate correction and reserve
      // hard synchronization for initial load and explicit user seeks.
      if (!video.seeking && video.readyState >= 2 && Number.isFinite(drift)) {
        video.playbackRate = Math.abs(drift) < 0.18 ? 1 : (drift > 0 ? 0.96 : (Math.abs(drift) > 1 ? 1.12 : 1.04));
      }
    } else if (!Number.isFinite(drift) || Math.abs(drift) > 0.75) {
      seekVideo(audio.currentTime);
    }
    if (audio.paused) {
      video.pause();
      video.playbackRate = 1;
    }
    else if (video.paused) video.play().catch(() => {});
  }

  function seekAudio(target, { commit = false } = {}) {
    const duration = Number.isFinite(audio.duration) ? audio.duration : 0;
    if (!duration) return;
    const bounded = Math.max(0, Math.min(target, duration));
    const wasPlaying = !audio.paused;
    if (videoSourceIsHls && videoScrubbing) {
      resumeAfterVideoScrub = resumeAfterVideoScrub || wasPlaying;
      hlsAlignmentToken += 1;
      audio.pause();
      video.pause();
    }
    try {
      // Chromium can leave a remote MP4 at HAVE_METADATA indefinitely after
      // fastSeek(). Assigning currentTime triggers the required byte-range fetch.
      audio.currentTime = bounded;
    } catch (_) { return; }
    state.playback.position = bounded;
    if (videoSourceIsHls && wasPlaying && !videoScrubbing) alignHlsPair(bounded, { resume: true });
    else seekVideo(bounded);
    updateVideoTransport();
    if (commit) saveBrowserPlayback(true);
  }

  function updateVideoTransport(previewTime = null) {
    const duration = Number.isFinite(audio.duration) ? audio.duration : 0;
    const current = previewTime === null ? (Number.isFinite(audio.currentTime) ? audio.currentTime : 0) : previewTime;
    const stageSlider = qs("videoScrubSlider");
    qs("videoElapsedText").textContent = fmtTime(current);
    qs("videoDurationText").textContent = fmtTime(duration);
    stageSlider.disabled = !duration;
    if (!videoScrubbing) stageSlider.value = duration ? String(Math.round((current / duration) * 1000)) : "0";
    stageSlider.style.setProperty("--seek", `${duration ? Math.max(0, Math.min(100, (current / duration) * 100)) : 0}%`);
    const paused = audio.paused;
    const playPause = qs("videoPlayPauseBtn");
    playPause.textContent = paused ? "Play" : "Pause";
    playPause.setAttribute("aria-label", paused ? "Play" : "Pause");
    qs("videoRewindBtn").disabled = !duration;
    qs("videoForwardBtn").disabled = !duration;
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
        audio.currentTime = target;
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
      updateVideoTransport();
      refreshVideoSurface().catch(() => {});
    });
  });

  video.addEventListener("click", () => qs("playPauseBtn").click());
  qs("videoPlayPauseBtn").addEventListener("click", () => {
    if (!audio.paused) qs("playPauseBtn").click();
    else if (videoSourceIsHls) alignHlsPair(audio.currentTime, { resume: true });
    else qs("playPauseBtn").click();
  });
  qs("videoRewindBtn").addEventListener("click", () => seekAudio((audio.currentTime || 0) - 10, { commit: true }));
  qs("videoForwardBtn").addEventListener("click", () => seekAudio((audio.currentTime || 0) + 10, { commit: true }));
  const videoSlider = qs("videoScrubSlider");
  videoSlider.addEventListener("pointerdown", () => { videoScrubbing = true; });
  videoSlider.addEventListener("input", event => {
    videoScrubbing = true;
    const duration = Number.isFinite(audio.duration) ? audio.duration : 0;
    if (!duration) return;
    const target = duration * (Number(event.target.value) / 1000);
    seekAudio(target);
    updateVideoTransport(target);
  });
  const commitVideoSeek = () => {
    if (!videoScrubbing) return;
    videoScrubbing = false;
    state.playback.position = Number(audio.currentTime || 0);
    saveBrowserPlayback(true);
    updateScrubber();
    updateVideoTransport();
    if (resumeAfterVideoScrub && videoSourceIsHls) {
      resumeAfterVideoScrub = false;
      alignHlsPair(audio.currentTime, { resume: true });
    }
  };
  videoSlider.addEventListener("pointerup", commitVideoSeek);
  videoSlider.addEventListener("pointercancel", commitVideoSeek);
  videoSlider.addEventListener("change", commitVideoSeek);
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
    updateVideoTransport();
    refreshVideoSurface().catch(() => {});
    document.querySelectorAll(".track-kind").forEach(badge => badge.classList.toggle("video", badge.textContent.trim().toLowerCase() === "mp4"));
  };

  window.setInterval(() => {
    renderStreamState();
    if (isVideo(currentTrack())) {
      syncVideoClock();
      updateVideoTransport();
    }
  }, 500);
  audio.addEventListener("play", () => {
    if (videoSourceIsHls && !hlsAligning && (video.seeking || video.readyState < 3)) {
      const target = Number.isFinite(audio.currentTime) ? audio.currentTime : 0;
      audio.pause();
      alignHlsPair(target, { resume: true });
    }
  });
  const recoverBufferedHlsPair = async () => {
    if (!videoSourceIsHls || hlsAligning || audio.paused || video.paused) return;
    const revision = sourceRevision;
    const token = ++hlsAlignmentToken;
    hlsAligning = true;
    audio.pause();
    video.pause();
    // Do not hard-seek the HLS element during a transient stall: doing so can
    // snap it back to the same segment. Let its buffered frame settle, move
    // only the direct audio clock to that frame, then resume the pair.
    await new Promise(resolve => window.setTimeout(resolve, 250));
    if (token !== hlsAlignmentToken || revision !== sourceRevision) return;
    const aligned = Number.isFinite(video.currentTime) ? video.currentTime : audio.currentTime;
    try { audio.currentTime = Math.max(0, aligned); } catch (_) {}
    const audioReady = await waitUntilPlayable(audio, token, revision);
    if (!audioReady || token !== hlsAlignmentToken || revision !== sourceRevision) {
      if (token === hlsAlignmentToken) hlsAligning = false;
      return;
    }
    await Promise.allSettled([audio.play(), video.play()]);
    if (token === hlsAlignmentToken) hlsAligning = false;
  };
  audio.addEventListener("waiting", recoverBufferedHlsPair);
  video.addEventListener("waiting", recoverBufferedHlsPair);
  renderSeekFill();
})();
