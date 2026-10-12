/* Join-only Web Cast sender. Never launches a receiver or loads/stops media. */
class CastDeckPhoneCast {
  constructor({ getContext, refresh, onChange, onError }) {
    Object.assign(this, { getContext, refresh, onChange, onError });
    this.pending = false;
    this.session = null;
    this.generation = 0;
    this.message = '';
    this.defaultAppId = 'CC1AD845';
  }

  eligibility(context = this.getContext()) {
    if (window.location.protocol !== 'https:') return 'Open the trusted HTTPS controller to connect this phone to Cast.';
    if (context.status && context.status.demo || String(context.id || '').startsWith('demo-')) return 'Phone Cast joining is disabled in demo mode.';
    if (!context.id) return 'Select a receiver first.';
    const receiver = context.status && context.status.receiver;
    if (!receiver || !receiver.sessionId) return 'There is no active Cast session to join. Start a file when ready, then join it.';
    if (receiver.appId !== this.defaultAppId) return `This trial supports the Default Media Receiver. ${receiver.appName || 'The current Cast app'} is controlled through the PC.`;
    return '';
  }

  async loadSdk() {
    if (this.sdkReady) return;
    if (this.sdkPromise) return this.sdkPromise;
    this.sdkPromise = new Promise((resolve, reject) => {
      let complete = false;
      const timeout = setTimeout(() => finish(new Error('The Cast SDK did not load. Check internet access and use Android Chrome.')), 12000);
      const finish = (error) => {
        if (complete) return;
        complete = true;
        clearTimeout(timeout);
        if (error) reject(error); else { this.sdkReady = true; resolve(); }
      };
      const initialize = () => {
        try {
          const sdk = window.chrome.cast;
          const config = new sdk.ApiConfig(
            new sdk.SessionRequest(this.defaultAppId),
            (session) => this.handleSession(session), () => {},
            sdk.AutoJoinPolicy.PAGE_SCOPED, sdk.DefaultActionPolicy.CAST_THIS_TAB,
          );
          // PAGE_SCOPED disables automatic joining; CAST_THIS_TAB disables an
          // automatic receiver launch when the SDK initializes.
          sdk.initialize(config, () => finish(), () => finish(new Error('This browser could not initialize Google Cast. Try Android Chrome.')));
        } catch (error) { finish(error); }
      };
      if (window.chrome && window.chrome.cast && window.chrome.cast.isAvailable) initialize();
      else {
        window.__onGCastApiAvailable = (available) => available ? initialize() : finish(new Error('Google Cast is unavailable in this browser.'));
        const script = document.createElement('script');
        script.src = 'https://www.gstatic.com/cv/js/sender/v1/cast_sender.js';
        script.async = true;
        script.onerror = () => finish(new Error('Could not load the Google Cast SDK. Check internet access.'));
        document.head.appendChild(script);
      }
    });
    try { await this.sdkPromise; }
    finally { if (!this.sdkReady) this.sdkPromise = null; }
  }

  async join() {
    const reason = this.eligibility();
    if (reason) { this.onError(reason); return; }
    if (this.pending || this.session) return;
    const generation = ++this.generation;
    const deviceId = this.getContext().id;
    this.pending = true;
    this.message = 'Loading Google Cast…';
    this.onChange();
    try {
      await this.loadSdk();
      if (generation !== this.generation) return;
      await this.refresh();
      if (generation !== this.generation || this.getContext().id !== deviceId) return;
      const latestReason = this.eligibility();
      if (latestReason) throw new Error(latestReason);
      this.targetSessionId = this.getContext().status.receiver.sessionId;
      this.targetDeviceId = deviceId;
      this.message = 'Joining the current session…';
      this.onChange();
      this.joinTimeout = setTimeout(() => {
        if (generation !== this.generation || this.session) return;
        this.pending = false;
        this.targetSessionId = null;
        this.message = 'Chrome could not join this existing session. Playback continues through the PC.';
        this.onChange();
      }, 15000);
      // Google documents this as joining an existing session. There is no
      // receiver chooser, launch/load operation, or create-session fallback.
      window.chrome.cast.requestSessionById(this.targetSessionId);
    } catch (error) {
      if (generation !== this.generation) return;
      clearTimeout(this.joinTimeout);
      this.pending = false;
      this.targetSessionId = null;
      this.message = error.message || 'Could not join the current session.';
      this.onChange();
      this.onError(this.message);
    }
  }

  handleSession(session) {
    if (!this.targetSessionId || session.sessionId !== this.targetSessionId
        || session.appId !== this.defaultAppId || this.getContext().id !== this.targetDeviceId) {
      session.leave(() => {}, () => {});
      return;
    }
    clearTimeout(this.joinTimeout);
    this.session = session;
    this.pending = false;
    this.message = 'Phone joined the Cast session. Try its volume buttons; behavior depends on Chrome and Android.';
    session.addUpdateListener((alive) => {
      if (this.session !== session || alive) return;
      this.session = null;
      this.targetSessionId = null;
      this.message = 'The phone Cast connection ended. PC controls are still available.';
      this.onChange();
    });
    this.onChange();
  }

  update() {
    if ((this.pending || this.session) && this.targetDeviceId && this.getContext().id !== this.targetDeviceId) this.leave();
    this.onChange();
  }

  leave() {
    ++this.generation;
    clearTimeout(this.joinTimeout);
    const session = this.session;
    this.session = null;
    this.pending = false;
    this.targetSessionId = null;
    this.targetDeviceId = null;
    this.message = 'Phone detached. Playback continues through the PC.';
    // leave() detaches this sender. Never call session.stop() or endSession(true).
    if (session) session.leave(() => {}, () => {});
    this.onChange();
  }
}
window.CastDeckPhoneCast = CastDeckPhoneCast;
