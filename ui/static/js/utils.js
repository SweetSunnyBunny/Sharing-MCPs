/* Helpers — date formatting, etc. */
/* ANAM GUIDE: SHARED PAGE HELPERS
   What: Little helper functions every page shares — apiFetch() for talking to the server, escapeHtml() for safe text, time/date formatting, and the CDN URL resolver.
   Loaded by: Every page (index, hub, settings, gameroom, diagnostics) — always loaded first so other scripts can use it.
   Edit here when: You need a small utility that more than one page will use. Keep this file dependency-free; everything else assumes it loaded first. */

/**
 * Resolve the API base URL. Same-origin is the default, so this normally
 * returns '' and the app uses relative paths on its current host.
 * A non-empty value is reserved for explicit public-origin overrides.
 */
function anamApiUrl() {
    if (typeof window === 'undefined') return '';
    const raw = (window.__anamApiUrl || '').trim();
    // Placeholder hasn't been replaced yet (local dev without server injection)
    if (!raw || raw === '##ANAM_API_URL##') return '';
    return raw.replace(/\/+$/, '');
}

/** Prepend the app base to a relative path. Absolute URLs pass through. */
function apiPath(path) {
    if (!path) return path;
    if (/^https?:\/\//i.test(path)) return path;
    return anamApiUrl() + path;
}

function apiCredentialsForUrl(url) {
    const resolvedUrl = apiPath(url);
    const apiBase = anamApiUrl();
    const isOurApi = !apiBase || resolvedUrl.startsWith(apiBase) || resolvedUrl.startsWith('/');
    return (apiBase && isOurApi) ? 'include' : 'same-origin';
}

function apiFetch(url, options = {}) {
    const resolvedUrl = apiPath(url);
    const credentials = apiCredentialsForUrl(url);
    return fetch(resolvedUrl, {
        credentials,
        ...options,
    });
}

function formatTime(isoString) {
    if (!isoString) return '';
    const d = new Date(isoString);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function debounce(fn, ms) {
    let timer;
    return (...args) => {
        clearTimeout(timer);
        timer = setTimeout(() => fn(...args), ms);
    };
}

async function fetchWithTimeout(url, options = {}) {
    const {
        timeoutMs = 15000,
        retries = 0,
        retryDelayMs = 400,
        ...fetchOptions
    } = options;

    const resolvedUrl = apiPath(url);
    const credentials = apiCredentialsForUrl(url);

    for (let attempt = 0; attempt <= retries; attempt++) {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), timeoutMs);

        try {
            const response = await fetch(resolvedUrl, {
                credentials,
                ...fetchOptions,
                signal: controller.signal,
            });
            clearTimeout(timeout);
            return response;
        } catch (err) {
            clearTimeout(timeout);
            const isLastAttempt = attempt === retries;
            if (isLastAttempt) throw err;
            await new Promise((resolve) => setTimeout(resolve, retryDelayMs * (attempt + 1)));
        }
    }
}

async function fetchJson(url, options = {}) {
    const response = await fetchWithTimeout(url, options);
    if (!response.ok) {
        let message = `Request failed (${response.status})`;
        try {
            const body = await response.json();
            message = body.error || body.message || message;
        } catch (_err) {
            // Ignore body parse failures.
        }
        throw new Error(message);
    }
    return response.json();
}

async function sendJson(url, method, body, options = {}) {
    return fetchJson(url, {
        method,
        headers: {
            'Content-Type': 'application/json',
            ...(options.headers || {}),
        },
        body: JSON.stringify(body ?? {}),
        ...options,
    });
}

function locationDisplayName(loc) {
    if (!loc) return 'Unknown';
    return loc.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function formatRelativeTime(isoString) {
    if (!isoString) return '';
    const now = Date.now();
    const then = new Date(isoString).getTime();
    const diff = now - then;
    if (diff < 0) return 'now';
    const secs = Math.floor(diff / 1000);
    if (secs < 60) return 'now';
    const mins = Math.floor(secs / 60);
    if (mins < 60) return `${mins}m`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h`;
    const days = Math.floor(hours / 24);
    if (days < 7) return `${days}d`;
    const d = new Date(isoString);
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

function convertImagePath(url) {
    // Already a serve URL or external link — use as-is
    if (url.startsWith('/api/')) {
        return apiPath(url);
    }
    if (url.startsWith('http://') || url.startsWith('https://')) {
        return url;
    }
    // Windows file path containing \images\ or /images/ — extract filename
    const match = url.match(/[\\\/]images[\\\/]([^\\\/]+)$/);
    if (match) {
        return apiPath(`/api/images/file/${match[1]}`);
    }
    // Local file path (Windows or Unix) — can't be rendered in browser.
    // Backend rewrites these on stream_end; during streaming show a placeholder.
    if (/^[A-Za-z]:[\\\/]/.test(url) || (url.startsWith('/') && !url.startsWith('//'))) {
        return 'data:image/svg+xml,' + encodeURIComponent(
            '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="120">' +
            '<rect width="200" height="120" rx="8" fill="%23f0f0f0"/>' +
            '<text x="100" y="65" text-anchor="middle" fill="%23999" font-size="13">Loading image...</text>' +
            '</svg>'
        );
    }
    return url;
}

function convertDocumentPath(url) {
    // Already a serve URL or external link — use as-is
    if (url.startsWith('/api/')) {
        return apiPath(url);
    }
    if (url.startsWith('http://') || url.startsWith('https://')) {
        return url;
    }
    // Windows file path containing \documents\ or /documents/ — extract filename
    const dmatch = url.match(/[\\\/]documents[\\\/]([^\\\/]+)$/);
    if (dmatch) {
        return apiPath(`/api/documents/file/${dmatch[1]}`);
    }
    return url;
}

/* ── Focus trap for modals ── */
function resolveAnamCdnUrl(path = '') {
    const rawBase = typeof window !== 'undefined' ? (window.__anamCdnUrl || '') : '';
    const trimmedBase = String(rawBase).trim();
    const placeholderBase = trimmedBase === '##ANAM_CDN_URL##' ? '' : trimmedBase;

    let base = placeholderBase.replace(/\/+$/, '');
    if (!base && typeof window !== 'undefined') {
        const { protocol, hostname } = window.location;
        if (hostname && hostname.startsWith('anam.')) {
            base = `${protocol}//cdn.${hostname.slice('anam.'.length)}`;
        }
    }

    if (!path) return base;
    if (/^https?:\/\//i.test(path)) return path;
    if (!base) return path;
    return path.startsWith('/') ? `${base}${path}` : `${base}/${path}`;
}

const FocusTrap = {
    _previousFocus: null,
    _handler: null,
    _activeModal: null,

    trapFocus(modalElement) {
        this._previousFocus = document.activeElement;
        this._activeModal = modalElement;

        const focusable = this._getFocusable(modalElement);
        if (focusable.length > 0) focusable[0].focus();

        this._handler = (e) => {
            if (e.key !== 'Tab') return;
            const elements = this._getFocusable(modalElement);
            if (elements.length === 0) return;

            const first = elements[0];
            const last = elements[elements.length - 1];

            if (e.shiftKey) {
                if (document.activeElement === first) {
                    e.preventDefault();
                    last.focus();
                }
            } else {
                if (document.activeElement === last) {
                    e.preventDefault();
                    first.focus();
                }
            }
        };
        document.addEventListener('keydown', this._handler);
    },

    releaseFocus() {
        if (this._handler) {
            document.removeEventListener('keydown', this._handler);
            this._handler = null;
        }
        if (this._previousFocus && this._previousFocus.focus) {
            this._previousFocus.focus();
        }
        this._previousFocus = null;
        this._activeModal = null;
    },

    _getFocusable(container) {
        return Array.from(container.querySelectorAll(
            'input:not([disabled]), button:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"]), a[href]'
        )).filter(el => el.offsetParent !== null);
    },
};

function logClientEvent(eventType, detail = {}, options = {}) {
    const payload = JSON.stringify({
        event_type: eventType,
        source: options.source || 'web',
        detail,
    });

    const eventUrl = apiPath('/api/settings/client-event');

    try {
        if (navigator.sendBeacon) {
            const blob = new Blob([payload], { type: 'application/json' });
            navigator.sendBeacon(eventUrl, blob);
            return;
        }
    } catch (_err) {
        // Fall through to fetch.
    }

    fetch(eventUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: anamApiUrl() ? 'include' : 'same-origin',
        body: payload,
        keepalive: true,
    }).catch(() => {
        // Diagnostics logging is best-effort only.
    });
}
