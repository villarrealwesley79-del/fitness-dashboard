import fs from 'node:fs';
import vm from 'node:vm';

const appSource = fs.readFileSync(new URL('../../../static/js/app.js', import.meta.url), 'utf8');
const workerSource = fs.readFileSync(new URL('../../../static/js/sw.js', import.meta.url), 'utf8');

function between(source, start, end) {
  const startAt = source.indexOf(start);
  const endAt = source.indexOf(end, startAt);
  if (startAt < 0 || endAt < 0) throw new Error(`Missing source boundary: ${start} -> ${end}`);
  return source.slice(startAt, endAt);
}

const detectionSource = between(appSource, 'function _pushSupported()', 'function _pushApplyChip');
const wiringSource = between(appSource, 'function _wirePushButtons()', 'async function _pushGetVapidKey()');
const enableSource = between(appSource, 'async function enablePush()', 'async function disablePush()');
const deliverySource = between(appSource, 'async function sendPushTest()', '// ── FIT-16');

async function detect({ supported = true, ios = false, standalone = false, permission = 'default', serverSubs = [], endpointHash = null }) {
  const context = {
    module: { exports: {} },
    navigator: {
      userAgent: ios ? 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)' : 'Mozilla/5.0 Chrome',
      standalone,
      ...(supported ? { serviceWorker: {} } : {}),
    },
    window: {
      matchMedia: () => ({ matches: standalone }),
      ...(supported ? { PushManager: function PushManager() {}, Notification: {} } : {}),
    },
    Notification: { permission },
    api: async () => ({ subscriptions: serverSubs }),
    _pushCurrentEndpointHash: async () => endpointHash,
  };
  vm.runInNewContext(`${detectionSource}\nmodule.exports = { _pushDetectState };`, context);
  return (await context.module.exports._pushDetectState()).name;
}

async function verifyDelivery() {
  const resultMessages = [];
  const toasts = [];
  let request = null;
  const context = {
    module: { exports: {} },
    console,
    CSRF_HEADER_NAME: 'X-CSRF-Token',
    CSRF_HEADER_VALUE: 'fixture-token',
    $: () => ({ disabled: false }),
    _pushSetTestResult: (message) => resultMessages.push(message),
    _pushDetectState: async () => ({ name: 'granted_active', subs: [{ endpoint_hash: 'device-endpoint-hash' }] }),
    _pushCurrentEndpointHash: async () => 'device-endpoint-hash',
    fetch: async (url, options) => {
      request = { url, options };
      return { ok: true, status: 200, json: async () => ({ status: 'delivered', delivered: true }) };
    },
    toast: (message) => toasts.push(message),
    renderPushSection: async () => {},
    _pushResponseMessage: () => 'unexpected',
  };
  vm.runInNewContext(`${deliverySource}\nmodule.exports = { sendPushTest };`, context);
  await context.module.exports.sendPushTest();
  return {
    request_url: request.url,
    request_method: request.options.method,
    request_credentials: request.options.credentials,
    request_csrf: request.options.headers['X-CSRF-Token'],
    request_endpoint_hash: JSON.parse(request.options.body).endpoint_hash,
    result: resultMessages.at(-1),
    toast: toasts.at(-1),
  };
}

async function verifySetupFlow() {
  const messages = [];
  let permissionRequested = false;
  let subscribed = false;
  let subscriptionOptions = null;
  let serverRequest = null;
  const buttonListeners = {};
  const button = {
    dataset: {},
    disabled: false,
    addEventListener(name, callback) { buttonListeners[name] = callback; },
  };
  const registration = {
    pushManager: {
      subscribe: async (options) => {
        subscribed = true;
        subscriptionOptions = options;
        return { toJSON: () => ({ endpoint: 'https://push.test/device' }) };
      },
    },
  };
  const context = {
    module: { exports: {} },
    console,
    navigator: { serviceWorker: { ready: Promise.resolve(registration) } },
    Notification: {
      requestPermission: async () => {
        permissionRequested = true;
        return 'granted';
      },
    },
    $: (id) => id === 'btn-push-enable' ? button : null,
    _pushSupported: () => true,
    _pushIsIOS: () => false,
    _pushIsStandalone: () => true,
    _pushTimeout: () => new Promise(() => {}),
    _pushGetVapidKey: async () => ({ ok: true, publicKey: 'fixture-key' }),
    _pushUrlBase64ToUint8: () => new Uint8Array([1, 2, 3]),
    _pushSetTestResult: (message) => messages.push(message),
    _pushSetSetupResult: (message) => messages.push(message),
    api: async (path, options) => { serverRequest = { path, options }; },
    apiErrorMessage: () => 'server save failed',
    renderPushSection: async () => {},
  };
  vm.runInNewContext(`${wiringSource}\n${enableSource}\nlet setupCompletion;\nconst realEnablePush = enablePush;\nenablePush = () => (setupCompletion = realEnablePush());\n_wirePushButtons();\nmodule.exports = { click: () => { buttonListeners.click(); return setupCompletion; } };`, { ...context, buttonListeners });
  await context.module.exports.click();
  return {
    permission_requested: permissionRequested,
    subscribed,
    server_path: serverRequest.path,
    server_method: serverRequest.options.method,
    wired_event: buttonListeners.click ? 'click' : null,
    subscription_options: {
      user_visible_only: subscriptionOptions.userVisibleOnly,
      application_server_key: Array.from(subscriptionOptions.applicationServerKey),
    },
    persisted_payload: JSON.parse(serverRequest.options.body),
    result: messages.at(-1),
    button_reenabled: button.disabled === false,
  };
}

async function clickScenario(existingUrl = null) {
  const listeners = {};
  const openedUrls = [];
  let focusedUrl = null;
  let closed = false;
  const clients = existingUrl ? [{ url: existingUrl, focus: async () => { focusedUrl = existingUrl; } }] : [];
  const self = {
    location: { origin: 'https://fitness.test' },
    registration: { showNotification: async () => {} },
    clients: {
      claim: async () => {},
      matchAll: async () => clients,
      openWindow: async (url) => { openedUrls.push(url); },
    },
    skipWaiting: async () => {},
    addEventListener: (name, callback) => { listeners[name] = callback; },
  };
  vm.runInNewContext(workerSource, { self, caches: { keys: async () => [] }, fetch: async () => {}, Response, URL });
  let completion;
  listeners.notificationclick({
    notification: {
      data: { url: '/settings?from=push' },
      close: () => { closed = true; },
    },
    waitUntil: (promise) => { completion = promise; },
  });
  await completion;
  return { closed, focused_url: focusedUrl, opened_urls: openedUrls };
}

const target = 'https://fitness.test/settings?from=push';
const evidence = {
  setup_states: {
    unsupported: await detect({ supported: false }),
    denied: await detect({ permission: 'denied' }),
    ios_not_installed: await detect({ ios: true, standalone: false }),
    granted_inactive: await detect({ permission: 'granted' }),
    granted_active: await detect({
      permission: 'granted',
      serverSubs: [{ endpoint_hash: 'device-endpoint-hash' }],
      endpointHash: 'device-endpoint-hash',
    }),
  },
  setup_flow: await verifySetupFlow(),
  test_delivery: await verifyDelivery(),
  notification_click: {
    focus: await clickScenario(target),
    open: await clickScenario(),
  },
};

process.stdout.write(`${JSON.stringify(evidence)}\n`);
