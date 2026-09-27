/* Anam service worker — PWA install + offline caching + push notifications */

const ASSET_VERSION = '__ANAM_ASSET_VERSION__';
const STATIC_CACHE = `anam-static-${ASSET_VERSION}`;
const RUNTIME_CACHE = 'anam-runtime-v3';
const versioned = (path) => `${path}?v=${ASSET_VERSION}`;
const PRECACHE = [
    '/',
    '/hub',
    '/settings',
    '/gameroom',
    '/diagnostics',
    versioned('/static/manifest.json'),
    versioned('/static/css/main.css'),
    versioned('/static/css/anam.css'),
    versioned('/static/css/hub.css'),
    versioned('/static/css/settings.css'),
    versioned('/static/css/gameroom.css'),
    versioned('/static/css/diagnostics.css'),
    versioned('/static/js/theme-boot.js'),
    versioned('/static/js/utils.js'),
    versioned('/static/js/markdown.js'),
    versioned('/static/js/websocket.js'),
    versioned('/static/js/voice.js'),
    versioned('/static/js/voice-orb.js'),
    versioned('/static/js/canvas.js'),
    versioned('/static/js/chat.js'),
    versioned('/static/js/sidebar.js'),
    versioned('/static/js/sanctuary-viewer.js'),
    versioned('/static/js/story-state.js'),
    versioned('/static/js/app.js'),
    versioned('/static/js/hub.js'),
    versioned('/static/js/settings.js'),
    versioned('/static/js/gameroom.js'),
    versioned('/static/js/diagnostics.js'),
    versioned('/static/assets/icons/app_icon.png'),
];

function isCacheable(response) {
    return response && response.ok && response.type !== 'opaque';
}

async function cacheFirst(request, cacheName) {
    const cache = await caches.open(cacheName);
    const cached = await cache.match(request);
    if (cached) return cached;
    const response = await fetch(request);
    if (isCacheable(response)) {
        await cache.put(request, response.clone());
    }
    return response;
}

async function staleWhileRevalidate(request, cacheName) {
    const cache = await caches.open(cacheName);
    const cached = await cache.match(request);
    const fetched = fetch(request).then(async (response) => {
        if (isCacheable(response)) {
            await cache.put(request, response.clone());
        }
        return response;
    });
    return cached || fetched;
}

async function notifyClients(message) {
    const windowClients = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const client of windowClients) {
        client.postMessage(message);
    }
}

async function networkFirst(request, cacheName, fallbackUrl = null, fallbackMessage = null) {
    const cache = await caches.open(cacheName);
    try {
        const response = await fetch(request);
        if (isCacheable(response)) {
            await cache.put(request, response.clone());
        }
        return response;
    } catch (err) {
        const cached = await cache.match(request);
        if (cached) {
            if (fallbackMessage) {
                await notifyClients(fallbackMessage);
            }
            return cached;
        }
        if (fallbackUrl) {
            const fallback = await cache.match(fallbackUrl);
            if (fallback) {
                if (fallbackMessage) {
                    await notifyClients(fallbackMessage);
                }
                return fallback;
            }
        }
        throw err;
    }
}

self.addEventListener('install', (event) => {
    event.waitUntil((async () => {
        const cache = await caches.open(STATIC_CACHE);
        await Promise.allSettled(
            PRECACHE.map(async (url) => {
                const request = new Request(url, { cache: 'reload' });
                try {
                    const response = await fetch(request);
                    if (!isCacheable(response)) {
                        throw new Error(`non-cacheable response for ${url}`);
                    }
                    await cache.put(request, response.clone());
                } catch (err) {
                    console.warn('[SW] Precache skipped:', url, err);
                }
            })
        );
    })());
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) =>
            Promise.all(
                keys
                    .filter((key) => key.startsWith('anam-') && ![STATIC_CACHE, RUNTIME_CACHE].includes(key))
                    .map((key) => caches.delete(key))
            )
        )
    );
    self.clients.claim();
});

self.addEventListener('fetch', (event) => {
    // Skip non-GET requests entirely — lets uploads & POSTs go straight
    // to the network without the SW consuming the request body stream.
    if (event.request.method !== 'GET') return;
    // Honor bounded/fresh API reads requested by the page.
    if (event.request.cache === 'no-store') return;
    const url = new URL(event.request.url);
    if (url.origin !== self.location.origin) return;
    if (url.pathname.startsWith('/ws')) return;

    if (event.request.mode === 'navigate') {
        event.respondWith(networkFirst(
            event.request,
            STATIC_CACHE,
            '/',
            {
                type: 'sw_navigation_fallback',
                path: url.pathname,
            }
        ));
        return;
    }

    // Hub API — stale-while-revalidate (show cached data instantly, refresh in background)
    if (url.pathname.startsWith('/api/hub/')) {
        event.respondWith(staleWhileRevalidate(event.request, RUNTIME_CACHE));
        return;
    }

    // Image files — cache-first (filenames are unique/immutable)
    if (url.pathname.startsWith('/api/images/file/') || url.pathname.startsWith('/api/documents/file/')) {
        event.respondWith(cacheFirst(event.request, RUNTIME_CACHE));
        return;
    }

    // Skip other API calls and WebSocket — no caching needed
    if (url.pathname.startsWith('/api/')) return;

    // Network-first with cache fallback for static assets
    if (url.pathname.startsWith('/static/')) {
        event.respondWith(staleWhileRevalidate(event.request, STATIC_CACHE));
        return;
    }

    event.respondWith(networkFirst(event.request, STATIC_CACHE, '/'));
});

// ── Push notifications ──

self.addEventListener('push', (event) => {
    if (!event.data) return;

    let data;
    try {
        data = event.data.json();
    } catch (e) {
        data = { title: 'Anam', body: event.data.text() };
    }

    const options = {
        body: data.body || '',
        icon: data.icon || '/static/assets/icons/app_icon.png',
        badge: '/static/assets/icons/app_icon.png',
        data: { url: data.url || '/' },
        vibrate: [200, 100, 200],
        tag: 'anam-notification',
        renotify: true,
    };

    event.waitUntil(
        self.registration.showNotification(data.title || 'Anam', options)
    );
});

self.addEventListener('notificationclick', (event) => {
    event.notification.close();

    const url = event.notification.data?.url || '/';

    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
            // Focus existing Anam tab if open
            for (const client of windowClients) {
                if (client.url.includes(self.location.origin) && 'focus' in client) {
                    return client.focus();
                }
            }
            // Otherwise open a new tab
            return clients.openWindow(url);
        })
    );
});
