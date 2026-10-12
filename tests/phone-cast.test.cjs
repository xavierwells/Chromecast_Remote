const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function fixture() {
  const requested = [];
  const timers = new Map();
  let nextTimer = 0;
  let config;
  let context = { id: 'living-room', status: { receiver: { appId: 'CC1AD845', sessionId: 'existing-id', appName: 'Default Media Receiver' } } };
  const sdk = {
    isAvailable: true,
    AutoJoinPolicy: { PAGE_SCOPED: 'page' }, DefaultActionPolicy: { CAST_THIS_TAB: 'tab' },
    SessionRequest: class { constructor(appId) { this.appId = appId; } },
    ApiConfig: class { constructor(request, listener, availability, policy, action) { Object.assign(this, { request, listener, policy, action }); } },
    initialize: (settings, success) => { config = settings; success(); },
    requestSessionById: (id) => requested.push(id),
    requestSession: () => { throw new Error('Creating a session must never be requested'); },
  };
  const sandbox = {
    window: { location: { protocol: 'https:' }, chrome: { cast: sdk } },
    setTimeout: (callback) => { const id = ++nextTimer; timers.set(id, callback); return id; },
    clearTimeout: (id) => timers.delete(id),
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../public/phone-cast.js'), 'utf8'), sandbox);
  const phone = new sandbox.window.CastDeckPhoneCast({
    getContext: () => context, refresh: async () => {}, onChange() {}, onError() {},
  });
  let leaves = 0;
  const session = {
    appId: 'CC1AD845', sessionId: 'existing-id',
    leave: (success) => { leaves++; success(); }, addUpdateListener() {},
    stop: () => { throw new Error('Stopping a cast is forbidden'); },
    loadMedia: () => { throw new Error('Reloading current media is forbidden'); },
  };
  return { phone, requested, session, timers, getConfig: () => config, leaves: () => leaves,
    setContext: (next) => { context = next; }, sandbox };
}

test('joining requests only the existing id with automatic session creation disabled', async () => {
  const f = fixture();
  await f.phone.join();
  assert.deepEqual(f.requested, ['existing-id']);
  assert.equal(f.getConfig().policy, 'page');
  assert.equal(f.getConfig().action, 'tab');
  f.phone.handleSession(f.session);
  assert.equal(f.phone.session, f.session);
  assert.equal(f.phone.pending, false);
  f.phone.leave();
  assert.equal(f.leaves(), 1);
  assert.equal(f.phone.session, null);
});

test('HTTP, demo devices, missing sessions, and other receiver apps cannot join', async () => {
  for (const condition of ['http', 'demo', 'idle', 'other-app']) {
    const f = fixture();
    if (condition === 'http') f.sandbox.window.location.protocol = 'http:';
    if (condition === 'demo') f.setContext({ id: 'demo-living-room', status: {} });
    if (condition === 'idle') f.setContext({ id: 'living-room', status: { receiver: {} } });
    if (condition === 'other-app') f.setContext({ id: 'living-room', status: { receiver: { appId: 'Netflix', sessionId: 'exists' } } });
    await f.phone.join();
    assert.equal(f.requested.length, 0, condition);
  }
});

test('a mismatched or cancelled late session is detached without stopping it', async () => {
  const f = fixture();
  await f.phone.join();
  f.phone.handleSession({ ...f.session, sessionId: 'different-id' });
  assert.equal(f.leaves(), 1);
  assert.equal(f.phone.session, null);
  f.phone.leave();
  f.phone.handleSession(f.session);
  assert.equal(f.leaves(), 2);
  assert.equal(f.phone.session, null);
});

test('a timeout never falls back to creating a Cast session', async () => {
  const f = fixture();
  await f.phone.join();
  [...f.timers.values()].forEach((callback) => callback());
  assert.equal(f.phone.pending, false);
  assert.deepEqual(f.requested, ['existing-id']);
  f.phone.handleSession(f.session);
  assert.equal(f.phone.session, null);
  assert.equal(f.leaves(), 1);
});

test('the session id is refreshed before joining', async () => {
  const f = fixture();
  f.phone.refresh = async () => f.setContext({ id: 'living-room', status: { receiver: { appId: 'CC1AD845', sessionId: 'new-active-id' } } });
  await f.phone.join();
  assert.deepEqual(f.requested, ['new-active-id']);
  f.phone.leave();
});
