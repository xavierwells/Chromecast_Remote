const state = { devices: [], selectedId: localStorage.getItem('castdeck-selected'), status: null, statusAt: 0, mediaFiles: [], busy: false, seekOffset: 0, seekBase: 0, seekTimer: null };

const els = Object.fromEntries([
  'deviceList','deviceCount','emptyState','controller','selectedName','selectedMeta','mediaTitle','mediaSubtitle','playbackLabel','artwork','seekSlider','elapsedTime','durationTime','playPauseButton','stopButton','seekStack','volumeSlider','volumeValue','muteButton','castForm','mediaUrl','mediaTitleInput','contentType','mediaRefreshButton','localMediaList','refreshButton','toast'
].map((id) => [id, document.getElementById(id)]));

function selectedDevice() { return state.devices.find((device) => device.id === state.selectedId); }
function escapeHtml(value) { const node = document.createElement('div'); node.textContent = String(value || ''); return node.innerHTML; }
function formatTime(seconds) { const safe = Math.max(0, Number(seconds) || 0); const mins = Math.floor(safe / 60); return `${mins}:${String(Math.floor(safe % 60)).padStart(2, '0')}`; }
function formatBytes(bytes) { const value = Number(bytes) || 0; if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} KB`; return `${(value / 1024 / 1024).toFixed(value > 1024 * 1024 * 100 ? 0 : 1)} MB`; }
function setRangeFill(input) { const min = Number(input.min || 0); const max = Number(input.max || 100); const fill = ((Number(input.value) - min) / Math.max(1, max - min)) * 100; input.style.setProperty('--fill', `${fill}%`); }
function showToast(message, error = false) { els.toast.textContent = message; els.toast.className = `toast show${error ? ' error' : ''}`; clearTimeout(showToast.timer); showToast.timer = setTimeout(() => els.toast.className = 'toast', 2800); }
function setStatus(status) { state.status = status; state.statusAt = Date.now(); renderStatus(); }

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || 'Request failed');
  return data;
}

function renderDevices() {
  els.deviceCount.textContent = state.devices.length;
  if (!state.devices.length) {
    els.deviceList.innerHTML = '<div class="scan-state"><div class="scan-ring"></div>Looking for Cast devices…<br>Make sure this computer is on the same Wi‑Fi.</div>';
    state.selectedId = null;
    renderSelection();
    return;
  }
  if (state.selectedId && !selectedDevice()) { state.selectedId = null; localStorage.removeItem('castdeck-selected'); }
  els.deviceList.innerHTML = state.devices.map((device) => `
    <button class="device-card${device.id === state.selectedId ? ' active' : ''}" data-id="${escapeHtml(device.id)}">
      <span class="device-icon">▣</span>
      <span class="device-copy"><strong>${escapeHtml(device.name)}</strong><span>${escapeHtml(device.model)}</span></span>
      <i class="device-online"></i>
    </button>`).join('');
  els.deviceList.querySelectorAll('.device-card').forEach((button) => button.addEventListener('click', () => selectDevice(button.dataset.id)));
}

function renderMedia() {
  if (!state.mediaFiles.length) {
    els.localMediaList.innerHTML = '<div class="media-empty"><strong>No local media yet.</strong><br>Copy MP4, WebM, MP3, M4A, AAC, WAV, FLAC, OGG, or HLS files into<br>D:\\Chromecast_Remote\\media, then refresh.</div>';
    return;
  }
  els.localMediaList.innerHTML = state.mediaFiles.map((item, index) => {
    const kind = item.fileName.includes('.') ? item.fileName.split('.').pop() : 'media';
    return `<div class="media-item">
      <span class="media-kind">${escapeHtml(kind)}</span>
      <span class="media-copy"><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.fileName)} · ${formatBytes(item.size)}</span></span>
      <button class="media-cast" type="button" data-media-index="${index}">CAST ›</button>
    </div>`;
  }).join('');
  els.localMediaList.querySelectorAll('.media-cast').forEach((button) => button.addEventListener('click', () => {
    const item = state.mediaFiles[Number(button.dataset.mediaIndex)];
    castPayload({ url: item.castUrl, title: item.name, contentType: item.contentType }, button);
  }));
}

function renderSelection() {
  const device = selectedDevice();
  els.emptyState.classList.toggle('hidden', Boolean(device));
  els.controller.classList.toggle('hidden', !device);
  if (!device) return;
  els.selectedName.textContent = device.name;
  els.selectedMeta.textContent = `${device.model} · ${device.host}`;
  renderStatus();
}

function renderStatus() {
  const status = state.status || { volume: { level: 0, muted: false }, playerState: 'IDLE', media: null };
  const media = status.media;
  const playing = status.playerState === 'PLAYING';
  els.playbackLabel.textContent = playing ? 'NOW PLAYING' : status.playerState === 'PAUSED' ? 'PAUSED' : 'READY TO CAST';
  els.mediaTitle.textContent = media ? media.title : 'Nothing playing';
  els.mediaSubtitle.textContent = media ? (media.subtitle || 'Casting now') : 'Send a media URL below to begin';
  els.playPauseButton.textContent = playing ? 'Ⅱ' : '▶';
  els.playPauseButton.setAttribute('aria-label', playing ? 'Pause' : 'Play');
  const duration = Math.max(0, Number(media && media.duration) || 0);
  const liveAdvance = playing && state.statusAt ? (Date.now() - state.statusAt) / 1000 : 0;
  const elapsed = Math.min(duration || Infinity, Math.max(0, (Number(media && media.currentTime) || 0) + liveAdvance));
  els.seekSlider.max = duration || 100;
  els.seekSlider.value = duration ? elapsed : 0;
  els.seekSlider.disabled = !duration;
  els.elapsedTime.textContent = formatTime(elapsed);
  els.durationTime.textContent = formatTime(duration);
  setRangeFill(els.seekSlider);
  const level = Math.round(Math.max(0, Math.min(1, Number(status.volume && status.volume.level) || 0)) * 100);
  els.volumeSlider.value = level;
  els.volumeValue.textContent = `${level}%`;
  els.muteButton.classList.toggle('active', Boolean(status.volume && status.volume.muted));
  els.muteButton.textContent = status.volume && status.volume.muted ? '×' : '◖';
  setRangeFill(els.volumeSlider);
  if (media && media.image) {
    els.artwork.style.backgroundImage = `linear-gradient(rgba(0,0,0,.08),rgba(0,0,0,.08)), url("${media.image.replace(/["\\]/g, '')}")`;
  } else {
    els.artwork.style.backgroundImage = '';
  }
}

async function loadDevices() {
  try { state.devices = await api('/api/devices'); renderDevices(); renderSelection(); if (state.selectedId) await loadStatus(true); }
  catch (error) { showToast(error.message, true); }
}

async function loadMedia() {
  try { state.mediaFiles = await api('/api/media'); renderMedia(); }
  catch (error) { showToast(error.message, true); }
}

async function selectDevice(id) {
  resetSeekQueue();
  state.selectedId = id;
  localStorage.setItem('castdeck-selected', id);
  state.status = null;
  state.statusAt = 0;
  renderDevices();
  renderSelection();
  await loadStatus();
}

async function loadStatus(silent = false) {
  if (!state.selectedId || state.busy || state.seekTimer) return;
  try { setStatus(await api(`/api/devices/${encodeURIComponent(state.selectedId)}/status`)); }
  catch (error) { if (!silent) showToast(error.message, true); }
}

async function control(action, body = {}) {
  if (!state.selectedId || state.busy) return;
  state.busy = true;
  try {
    const result = await api(`/api/devices/${encodeURIComponent(state.selectedId)}/${action}`, { method: 'POST', body: JSON.stringify(body) });
    if (result && result.volume) setStatus(result);
    await loadStatus(true);
  } catch (error) { showToast(error.message, true); }
  finally { state.busy = false; }
}

els.playPauseButton.addEventListener('click', () => control(state.status && state.status.playerState === 'PLAYING' ? 'pause' : 'play'));
els.stopButton.addEventListener('click', () => control('stop'));

function resetSeekQueue() {
  clearTimeout(state.seekTimer);
  state.seekTimer = null;
  state.seekOffset = 0;
  state.seekBase = 0;
  els.seekStack.textContent = '';
  els.seekStack.classList.remove('show');
}

function flushSeekQueue() {
  if (!state.seekTimer) return;
  if (state.busy) {
    state.seekTimer = setTimeout(flushSeekQueue, 200);
    return;
  }
  const target = Math.max(0, Math.min(Number(els.seekSlider.max) || Infinity, state.seekBase + state.seekOffset));
  resetSeekQueue();
  control('seek', { time: target });
}

function queueSeek(delta) {
  if (!state.selectedId || !state.status || !state.status.media) return;
  if (!state.seekTimer) state.seekBase = Number(els.seekSlider.value) || 0;
  state.seekOffset += delta;
  const preview = Math.max(0, Math.min(Number(els.seekSlider.max) || Infinity, state.seekBase + state.seekOffset));
  els.seekSlider.value = preview;
  els.elapsedTime.textContent = formatTime(preview);
  setRangeFill(els.seekSlider);
  els.seekStack.textContent = `Queued ${state.seekOffset >= 0 ? '+' : ''}${state.seekOffset}s`;
  els.seekStack.classList.add('show');
  clearTimeout(state.seekTimer);
  state.seekTimer = setTimeout(flushSeekQueue, 750);
}

document.querySelectorAll('.skip-button').forEach((button) => button.addEventListener('click', () => queueSeek(Number(button.dataset.skip))));
els.seekSlider.addEventListener('input', () => { els.elapsedTime.textContent = formatTime(els.seekSlider.value); setRangeFill(els.seekSlider); });
els.seekSlider.addEventListener('change', () => { resetSeekQueue(); control('seek', { time: Number(els.seekSlider.value) }); });
els.volumeSlider.addEventListener('input', () => { els.volumeValue.textContent = `${els.volumeSlider.value}%`; setRangeFill(els.volumeSlider); });
els.volumeSlider.addEventListener('change', () => control('volume', { level: Number(els.volumeSlider.value) / 100 }));
els.muteButton.addEventListener('click', () => control('mute', { muted: !(state.status && state.status.volume && state.status.volume.muted) }));
els.refreshButton.addEventListener('click', () => { loadDevices(); showToast('Device list refreshed'); });
els.mediaRefreshButton.addEventListener('click', () => { loadMedia(); showToast('Local media refreshed'); });

els.mediaUrl.addEventListener('input', () => {
  const url = els.mediaUrl.value.toLowerCase();
  if (url.includes('.m3u8')) els.contentType.value = 'application/x-mpegURL';
  else if (url.includes('.mp3')) els.contentType.value = 'audio/mpeg';
  else if (url.includes('.aac')) els.contentType.value = 'audio/aac';
});

async function castPayload(payload, button) {
  if (!state.selectedId || state.busy) return;
  const originalLabel = button.innerHTML;
  state.busy = true; button.disabled = true; button.textContent = 'CASTING…';
  try {
    const result = await api(`/api/devices/${encodeURIComponent(state.selectedId)}/play-url`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    if (result && result.volume) setStatus(result);
    showToast(`Casting to ${selectedDevice().name}`);
    await loadStatus(true);
  } catch (error) { showToast(error.message, true); }
  finally { state.busy = false; button.disabled = false; button.innerHTML = originalLabel; }
}

els.castForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = els.castForm.querySelector('button[type=submit]');
  await castPayload({ url: els.mediaUrl.value, title: els.mediaTitleInput.value, contentType: els.contentType.value }, button);
});

const events = new EventSource('/api/events');
events.addEventListener('devices', (event) => { state.devices = JSON.parse(event.data); renderDevices(); renderSelection(); });
events.addEventListener('status', (event) => { const update = JSON.parse(event.data); if (update.id === state.selectedId) setStatus(update.status); });
events.onerror = () => {};

loadDevices();
loadMedia();
setInterval(() => loadStatus(true), 15000);
setInterval(() => {
  if (state.status && state.status.playerState === 'PLAYING' && state.status.media && !state.seekTimer) renderStatus();
}, 1000);
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) { loadDevices(); loadMedia(); loadStatus(true); }
});
window.addEventListener('pageshow', () => { loadDevices(); loadMedia(); loadStatus(true); });
