// Fitness Dashboard Service Worker

const CACHE_NAME = 'fitness-dashboard-v20260525-fit181';
const STATIC_ASSETS = [
    '/',
    '/static/css/style.css',
    '/static/js/app.js',
    '/manifest.json',
    'https://cdn.jsdelivr.net/npm/chart.js'
];

// Install - cache static assets
self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => cache.addAll(STATIC_ASSETS))
            .then(() => self.skipWaiting())
    );
});

// Activate - clean old caches
self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(keys => {
            return Promise.all(
                keys.filter(key => key !== CACHE_NAME)
                    .map(key => caches.delete(key))
            );
        }).then(() => self.clients.claim())
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

// Fetch - network first, fallback to cache
self.addEventListener('fetch', event => {
    // Skip non-GET requests
    if (event.request.method !== 'GET') return;

    // API requests - network only (don't cache dynamic data)
    if (event.request.url.includes('/api/')) {
        event.respondWith(
            fetch(event.request)
                .catch(() => new Response(JSON.stringify({ error: 'Offline' }), {
                    headers: { 'Content-Type': 'application/json' }
                }))
        );
        return;
    }

    // Static assets - cache first, network fallback
    event.respondWith(
        caches.match(event.request)
            .then(cachedResponse => {
                if (cachedResponse) {
                    // Update cache in background
                    fetch(event.request).then(response => {
                        if (response.ok) {
                            caches.open(CACHE_NAME).then(cache => {
                                cache.put(event.request, response);
                            });
                        }
                    });
                    return cachedResponse;
                }

                return fetch(event.request).then(response => {
                    if (response.ok) {
                        const responseClone = response.clone();
                        caches.open(CACHE_NAME).then(cache => {
                            cache.put(event.request, responseClone);
                        });
                    }
                    return response;
                });
            })
    );
});
