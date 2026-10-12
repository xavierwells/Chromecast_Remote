const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function fixture({ rejectPlay = false } = {}) {
  const handlers = new Map();
  const sent = [];
  let context = {
    id: 'living-room', deviceName: 'Living Room', statusAt: Date.now(),
    status: { playerState: 'PAUSED', media: { title: 'Movie', duration: 120, currentTime: 10 }, queue: [{ title: 'Next' }] },
  };
  const session = { setActionHandler: (action, callback) => handlers.set(action, callback), setPositionState() {} };
  class Audio {
    constructor(src) { this.src = src; this.listeners = {}; }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    async play() {
      if (rejectPlay) throw new Error('Blocked');
      this.listeners.playing?.();
    }
    pause() { this.listeners.pause?.(); }
    removeAttribute() {}
    load() {}
  }
  class Metadata { constructor(data) { Object.assign(this, data); } }
  const sandbox = {
    navigator: { mediaSession: session }, window: { MediaMetadata: Metadata, location: { href: 'http://localhost:4173/' } },
    MediaMetadata: Metadata, Audio, URL, Blob, Date,
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../public/background-remote.js'), 'utf8'), sandbox);
  const remote = new sandbox.window.CastDeckBackgroundRemote({
    getContext: () => context,
    send: async (action, body, id) => sent.push({ action, body, id }),
    refresh: async () => { context.status.media.currentTime = 70; },
    onChange() {}, onError() {},
  });
  return { remote, sent, handlers, session, setContext: (next) => { context = next; } };
}

test('enabling, phone audio suspension, and disabling never change the receiver', async () => {
  const { remote, sent } = fixture();
  await remote.enable();
  assert.equal(remote.enabled, true);
  assert.equal(remote.suspended, false);
  assert.equal(sent.length, 0);
  remote.audio.pause();
  assert.equal(remote.suspended, true);
  assert.equal(sent.length, 0);
  await remote.enable();
  assert.equal(remote.suspended, false);
  remote.disable();
  assert.equal(remote.enabled, false);
  assert.equal(remote.audio, null);
  assert.equal(sent.length, 0);
});

test('notification pause and next target the selected receiver; stop only releases phone controls', async () => {
  const { remote, sent, handlers, session } = fixture();
  await remote.enable();
  handlers.get('pause')();
  handlers.get('nexttrack')();
  assert.deepEqual(sent.map(({ action, id }) => ({ action, id })), [
    { action: 'pause', id: 'living-room' }, { action: 'queue-next', id: 'living-room' },
  ]);
  handlers.get('stop')();
  assert.equal(remote.enabled, false);
  assert.equal(session.metadata, null);
  assert.equal(session.playbackState, 'none');
  assert.equal(sent.length, 2);
});

test('background seek refreshes receiver position before computing the jump', async () => {
  const { remote, sent } = fixture();
  await remote.enable();
  await remote.seek(15);
  assert.equal(sent[0].action, 'seek');
  assert.equal(sent[0].body.time, 85);
  remote.disable();
});

test('switching rooms cancels the phone session and stale notification handlers', async () => {
  const { remote, sent, handlers, setContext } = fixture();
  await remote.enable();
  const oldPause = handlers.get('pause');
  setContext({ id: 'bedroom', deviceName: 'Bedroom', status: {} });
  remote.update();
  oldPause();
  assert.equal(remote.enabled, false);
  assert.equal(sent.length, 0);
});

test('blocked audio cleans up without sending a Cast command', async () => {
  const { remote, sent } = fixture({ rejectPlay: true });
  await remote.enable();
  assert.equal(remote.enabled, false);
  assert.equal(remote.pending, false);
  assert.equal(remote.audio, null);
  assert.equal(sent.length, 0);
});

test('the local track is a valid 30-second WAV with only silent samples', async () => {
  const { remote } = fixture();
  const bytes = Buffer.from(await remote.constructor.silentTrack().arrayBuffer());
  assert.equal(bytes.toString('ascii', 0, 4), 'RIFF');
  assert.equal(bytes.toString('ascii', 8, 12), 'WAVE');
  assert.equal(bytes.readUInt32LE(24), 8000);
  assert.equal(bytes.readUInt32LE(40), 8000 * 30 * 2);
  assert.equal(bytes.subarray(44).some((value) => value !== 0), false);
});
