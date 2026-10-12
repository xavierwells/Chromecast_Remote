/* Opt-in browser media session for a remote controlled by the PC.
 * A silent audio element may help Android retain notification controls. It
 * does not expose system volume keys or guarantee background execution.
 */
class CastDeckBackgroundRemote {
  constructor({ getContext, send, refresh, onChange, onError }) {
    this.getContext = getContext;
    this.send = send;
    this.refresh = refresh;
    this.onChange = onChange;
    this.onError = onError;
    this.supported = Boolean(navigator.mediaSession && window.MediaMetadata);
    this.enabled = false;
    this.pending = false;
    this.suspended = false;
    this.generation = 0;
    this.handlers = ['play', 'pause', 'seekbackward', 'seekforward', 'seekto', 'nexttrack', 'stop'];
  }

  static silentTrack() {
    // 30 seconds of PCM silence, generated locally with no server requests.
    const sampleRate = 8000;
    const bytes = new ArrayBuffer(44 + sampleRate * 30 * 2);
    const header = new DataView(bytes);
    const text = (offset, value) => [...value].forEach((char, index) => header.setUint8(offset + index, char.charCodeAt(0)));
    text(0, 'RIFF'); header.setUint32(4, bytes.byteLength - 8, true);
    text(8, 'WAVE'); text(12, 'fmt '); header.setUint32(16, 16, true);
    header.setUint16(20, 1, true); header.setUint16(22, 1, true);
    header.setUint32(24, sampleRate, true); header.setUint32(28, sampleRate * 2, true);
    header.setUint16(32, 2, true); header.setUint16(34, 16, true);
    text(36, 'data'); header.setUint32(40, bytes.byteLength - 44, true);
    return new Blob([bytes], { type: 'audio/wav' });
  }

  setHandler(action, handler) {
    try { navigator.mediaSession.setActionHandler(action, handler); } catch (_) { /* Browser-specific actions are optional. */ }
  }

  async enable() {
    const context = this.getContext();
    if (!this.supported || !context.id || this.pending) return;
    const generation = ++this.generation;
    this.deviceId = context.id;
    this.enabled = true;
    this.pending = true;
    this.onChange();
    try {
      if (!this.audio) {
        this.audioUrl = URL.createObjectURL(CastDeckBackgroundRemote.silentTrack());
        const audio = new Audio(this.audioUrl);
        audio.loop = true;
        audio.preload = 'auto';
        // Keep the element unmuted: the WAV itself contains only zero samples.
        this.audio = audio;
        audio.addEventListener('pause', () => {
          if (!this.enabled || this.audio !== audio) return;
          this.suspended = true;
          this.update();
          this.onChange();
        });
        audio.addEventListener('playing', () => {
          if (!this.enabled || this.audio !== audio) return;
          this.suspended = false;
          this.update();
          this.onChange();
        });
      }
      // Must start inside the button's user gesture; never starts a Cast item.
      await this.audio.play();
      if (!this.enabled || generation !== this.generation) return;
      this.suspended = false;
      this.setHandler('play', () => {
        if (!this.currentContext()) return;
        this.audio.play().catch(() => {});
        void this.dispatch('play');
      });
      this.setHandler('pause', () => { void this.dispatch('pause'); });
      this.setHandler('seekbackward', (details) => { void this.seek(-Math.abs(details.seekOffset || 15)); });
      this.setHandler('seekforward', (details) => { void this.seek(Math.abs(details.seekOffset || 15)); });
      this.setHandler('seekto', (details) => { void this.seek(details.seekTime, true); });
      // Dismissing phone controls must not terminate the TV's casting session.
      this.setHandler('stop', () => this.disable());
      this.update();
    } catch (_) {
      if (generation !== this.generation) return;
      this.disable();
      this.onError('This browser could not start background controls. Try Chrome and tap Enable again.');
    } finally {
      if (generation === this.generation) {
        this.pending = false;
        this.onChange();
      }
    }
  }

  currentContext() {
    const context = this.getContext();
    return this.enabled && context.id === this.deviceId ? context : null;
  }

  async dispatch(action, body = {}) {
    const context = this.currentContext();
    if (!context) return;
    await this.send(action, body, this.deviceId);
    this.update();
  }

  position(context) {
    const media = context.status && context.status.media;
    const duration = Math.max(0, Number(media && media.duration) || 0);
    const advance = context.status && context.status.playerState === 'PLAYING' && context.statusAt
      ? Math.max(0, (Date.now() - context.statusAt) / 1000) : 0;
    const position = Math.max(0, (Number(media && media.currentTime) || 0) + advance);
    return { duration, position: Math.min(duration || Infinity, position) };
  }

  async seek(value, absolute = false) {
    if (!this.currentContext() || !Number.isFinite(value)) return;
    const generation = this.generation;
    // Get the receiver's position again: the phone may have slept for minutes.
    await this.refresh();
    const context = this.currentContext();
    if (!context || generation !== this.generation) return;
    const { duration, position } = this.position(context);
    if (!duration) return;
    await this.dispatch('seek', { time: Math.max(0, Math.min(duration, absolute ? value : position + value)) });
  }

  update() {
    if (!this.enabled) return;
    const context = this.currentContext();
    if (!context) { this.disable(); return; }
    const status = context.status || {};
    const media = status.media;
    const metadataKey = JSON.stringify([context.deviceName, media && media.title]);
    if (metadataKey !== this.metadataKey) {
      this.metadataKey = metadataKey;
      navigator.mediaSession.metadata = new MediaMetadata({
        title: media && media.title || 'CastDeck remote',
        artist: context.deviceName || 'Chromecast',
        album: 'CastDeck',
        artwork: [{ src: new URL('/icon-512.png', window.location.href).href, sizes: '512x512', type: 'image/png' }],
      });
    }
    navigator.mediaSession.playbackState = this.suspended || status.playerState === 'PAUSED'
      ? 'paused' : ['PLAYING', 'BUFFERING'].includes(status.playerState) ? 'playing' : 'none';
    this.setHandler('nexttrack', status.queue && status.queue.length ? () => { void this.dispatch('queue-next'); } : null);
    if (navigator.mediaSession.setPositionState) {
      const { duration, position } = this.position(context);
      try {
        if (duration && Number.isFinite(duration)) navigator.mediaSession.setPositionState({ duration, position, playbackRate: 1 });
        else navigator.mediaSession.setPositionState();
      } catch (_) { /* Some browsers do not expose remote timelines. */ }
    }
  }

  disable() {
    this.enabled = false;
    this.pending = false;
    this.suspended = false;
    ++this.generation;
    if (this.audio) {
      this.audio.pause();
      this.audio.removeAttribute('src');
      this.audio.load();
      this.audio = null;
      URL.revokeObjectURL(this.audioUrl);
      this.audioUrl = null;
    }
    if (this.supported) {
      this.handlers.forEach((action) => this.setHandler(action, null));
      navigator.mediaSession.metadata = null;
      navigator.mediaSession.playbackState = 'none';
      if (navigator.mediaSession.setPositionState) {
        try { navigator.mediaSession.setPositionState(); } catch (_) { /* Optional. */ }
      }
    }
    this.metadataKey = null;
    this.deviceId = null;
    this.onChange();
  }
}

window.CastDeckBackgroundRemote = CastDeckBackgroundRemote;
