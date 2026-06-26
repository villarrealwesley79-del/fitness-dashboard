// Fitness Dashboard Service Worker

const CACHE_NAME = 'fitness-dashboard-v20260626-fit236-active-draft';

// Install - take control immediately, but do not precache the app shell.
// The workout screen is gym-critical, and stale cached HTML/JS can strand the
// user on placeholders even while the backend has a valid plan.
self.addEventListener('install', event => {
    event.waitUntil(self.skipWaiting());
});

self.addEventListener('message', event => {
    if (event.data && event.data.type === 'SKIP_WAITING') {
        event.waitUntil(self.skipWaiting());
    }
});

// Activate - clean every old app cache. The service worker no longer owns an
// offline app shell cache; workout data and local queues live outside this cache.
self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys()
            .then(keys => Promise.all(keys.map(key => caches.delete(key))))
            .then(() => self.clients.claim())
    );
});

// Web Push - display low-stakes coaching notifications from the server.
self.addEventListener('push', event => {
    let payload = {};
    if (event.data) {
        try {
            payload = event.data.json();
        } catch {
            payload = { body: event.data.text() };
        }
    }
    const title = payload.title || 'Fitness Dashboard';
    const options = {
        body: payload.body || 'Open Fitness Dashboard for the latest coaching reminder.',
        tag: payload.tag || 'fitness-dashboard-reminder',
        data: {
            url: payload.url || '/',
            safety_critical: payload.safety_critical === true,
        },
        icon: '/static/icons/icon-192.png',
        badge: '/static/icons/icon-192.png',
    };
    event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', event => {
    event.notification.close();
    const targetUrl = new URL((event.notification.data && event.notification.data.url) || '/', self.location.origin).href;
    event.waitUntil(
        self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clientList => {
            for (const client of clientList) {
                if ('focus' in client && client.url === targetUrl) return client.focus();
            }
            if (self.clients.openWindow) return self.clients.openWindow(targetUrl);
            return undefined;
        })
    );
});

// Fetch - network first for every app-owned route. Do not store HTML, JS, CSS,
// or API responses in Cache Storage; a failed network should fail visibly
// instead of serving a stale workout shell.
self.addEventListener('fetch', event => {
    // Skip non-GET requests
    if (event.request.method !== 'GET') return;

    // API requests - network only (don't cache dynamic data)
    if (event.request.url.includes('/api/')) {
        event.respondWith(
            fetch(event.request)
                .catch(() => new Response(JSON.stringify({ error: 'Offline' }), {
                    status: 503,
                    headers: { 'Content-Type': 'application/json' }
                }))
        );
        return;
    }

    // Navigation/app-shell requests must be network first. This prevents an
    // installed PWA from pinning a broken dashboard shell during live repair.
    if (event.request.mode === 'navigate') {
        event.respondWith(
            fetch(event.request).catch(() => new Response(
                '<!doctype html><title>Offline</title><body>Fitness Dashboard is offline. Reconnect and refresh.</body>',
                { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } }
            ))
        );
        return;
    }

    // Static JS/CSS must also be network first so urgent app fixes take effect
    // on the next open instead of the second reload.
    const url = new URL(event.request.url);
    if (url.pathname.endsWith('.js') || url.pathname.endsWith('.css') || url.pathname === '/sw.js') {
        event.respondWith(fetch(event.request));
        return;
    }

    event.respondWith(fetch(event.request));
});
