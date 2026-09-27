/* WebSocket manager with auto-reconnect + heartbeat keepalive */
/* ANAM GUIDE: LIVE CONNECTION KEEPER
   What: keeps the live line to the server open — auto-reconnects when the phone sleeps or wifi blips, queues messages sent while offline, and falls back to plain HTTP if the line won't stay up.
   Loaded by: index.html (the main chat page); chat.js and app.js send/receive everything through the WS object it creates.
   Edit here when: chat stops updating after the phone was locked, reconnects feel too slow/eager, or offline messages get lost — timing knobs are the _ms values at the top. */

class WebSocketManager {
    constructor() {
        this.ws = null;
        this.url = this._buildUrl();
        this.handlers = {};
        this.reconnectDelay = 1000;
        this.maxReconnectDelay = 30000;
        this.reconnectAttempts = 0;
        this.maxAutoRetries = 5;
        this.paused = false;
        this.intentionalClose = false;
        this.streaming = false;
        this._pingInterval = null;
        this._pongTimeout = null;
        this._connectTimeout = null;
        this._reconnectTimer = null;
        this._pendingQueue = [];
        this._maxQueueSize = 50;
        this._queueStorageKey = 'anam-ws-pending';
        this._lastActivityAt = Date.now();
        this._hiddenAt = document.hidden ? Date.now() : null;
        this._lastResumeCheckAt = 0;
        this._resumeCheckThrottleMs = 4000;
        this._resumeProbeTimeoutMs = 12000;
        this._staleAfterIdleMs = 45000;
        this._rateLimitedUntil = 0;
        this._rateLimitRetryMs = 65000;
        this._redirectThreshold = 4;
        this._publicBaseUrl = window.__ANAM_PUBLIC_BASE_URL__ || '';
        this._touchDevice = 'ontouchstart' in window || navigator.maxTouchPoints > 0;
                                                                               
                                                                                
                                                                             
                                                                             
        this._httpFallbackThreshold = this._touchDevice ? this.maxAutoRetries : Number.POSITIVE_INFINITY;
        this.httpFallback = false;

        this._loadQueue();
        window.addEventListener('online', () => {
            if (!this.intentionalClose) {
                // Network just came back — reset attempts and try fresh
                this.reconnectAttempts = 0;
                this.reconnectDelay = 1000;
                this.paused = false;
                this._handleResume('online');
            }
        });
        window.addEventListener('focus', () => {
            if (!this.intentionalClose && !document.hidden) this._handleResume('focus');
        });
        window.addEventListener('pageshow', () => {
            if (!this.intentionalClose && !document.hidden) this._handleResume('pageshow');
        });
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) {
                this._hiddenAt = Date.now();
                return;
            }
            this._handleResume('visibility');
        });
    }

    _buildUrl() {
        const base = (typeof anamApiUrl === 'function') ? anamApiUrl() : '';
        if (base) {
            // Explicit public-origin override: derive ws(s) URL from the app origin.
            return base.replace(/^http/, 'ws') + '/ws/chat';
        }
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        return `${proto}//${location.host}/ws/chat`;
    }

    _loadQueue() {
        try {
            const raw = sessionStorage.getItem(this._queueStorageKey);
            this._pendingQueue = raw ? JSON.parse(raw) : [];
        } catch (_err) {
            this._pendingQueue = [];
        }
    }

    _persistQueue() {
        try {
            if (this._pendingQueue.length > 0) {
                sessionStorage.setItem(this._queueStorageKey, JSON.stringify(this._pendingQueue));
            } else {
                sessionStorage.removeItem(this._queueStorageKey);
            }
        } catch (_err) {
            // Ignore storage failures.
        }
    }

    on(event, handler) {
        if (!this.handlers[event]) this.handlers[event] = [];
        this.handlers[event].push(handler);
    }

    _emit(event, data) {
        (this.handlers[event] || []).forEach(h => h(data));
    }

    connect() {
        if (this.intentionalClose) return;

        const rateLimitWait = this._rateLimitedUntil - Date.now();
        if (rateLimitWait > 0) {
            if (!this._reconnectTimer) {
                this._emit('reconnecting', {
                    attempt: this.reconnectAttempts + 1,
                    delay: rateLimitWait,
                    rateLimited: true,
                });
                this._reconnectTimer = setTimeout(() => {
                    this._reconnectTimer = null;
                    this.connect();
                }, rateLimitWait);
            }
            return;
        }

        if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
            return;
        }

        if (this._reconnectTimer) {
            clearTimeout(this._reconnectTimer);
            this._reconnectTimer = null;
        }

        this.intentionalClose = false;
        this.paused = false;
        const socket = new WebSocket(this.url);
        this.ws = socket;
        const connectTimeout = setTimeout(() => {
            if (socket.readyState === WebSocket.CONNECTING) {
                console.warn('[WS] Connect timeout');
                socket.close();
            }
        }, 25000);
        this._connectTimeout = connectTimeout;

        socket.onopen = () => {
            if (this.ws !== socket) {
                socket.close(1000, 'Superseded connection');
                return;
            }
            console.log('[WS] Connected');
            clearTimeout(connectTimeout);
            if (this._connectTimeout === connectTimeout) this._connectTimeout = null;
            this._lastActivityAt = Date.now();
            this.reconnectAttempts = 0;
            this.reconnectDelay = 1000;
            this._rateLimitedUntil = 0;
            this.httpFallback = false;
            if (this._fallbackProbeTimer) {
                clearInterval(this._fallbackProbeTimer);
                this._fallbackProbeTimer = null;
            }
            this._startHeartbeat();
            this._emit('connected');
            setTimeout(() => this._drainQueue(), 400);
        };

        socket.onmessage = (event) => {
            if (this.ws !== socket) return;
            try {
                const msg = JSON.parse(event.data);
                this._lastActivityAt = Date.now();
                // Any incoming message proves connection is alive — clear pong timeout
                this._clearPongTimeout();
                // Handle pong and keepalive silently
                if (msg.type === 'pong' || msg.type === 'keepalive') {
                    return;
                }
                this._emit('message', msg);
                this._emit(msg.type, msg);
            } catch (err) {
                console.error('[WS] Parse error:', err);
            }
        };

        socket.onclose = (event) => {
            clearTimeout(connectTimeout);
            if (this._connectTimeout === connectTimeout) this._connectTimeout = null;
            // A replacement socket may already be live. A late callback from
            // the retired Android connection must not clear or reconnect it.
            if (this.ws !== socket) return;
            console.log('[WS] Closed:', event.code, event.reason);
            this._stopHeartbeat();
            this.ws = null;
            this._emit('disconnected');
            // Auth failure: redirect to login on the app origin, don't reconnect.
            if (event.code === 4001) {
                const apiBase = (typeof anamApiUrl === 'function') ? anamApiUrl() : '';
                window.location.href = apiBase + '/auth/login';
                return;
            }
            if (event.code === 4029) {
                this._rateLimitedUntil = Date.now() + this._rateLimitRetryMs;
                this.connect();
                return;
            }
            if (!this.intentionalClose && !this.paused) {
                this._reconnect();
            }
        };

        socket.onerror = (err) => {
            if (this.ws !== socket) return;
            console.error('[WS] Error:', err);
            this._emit('error', err);
        };
    }

    _startHeartbeat() {
        this._stopHeartbeat();
                                                               
          
                                                                          
                                                                        
                                                                       
                                                                          
                                                                             
                                                                              
                                                                          
                                                                              
          
                                                       
                                                                             
                                                                            
                                                             
        this._pingInterval = setInterval(() => {
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                if (this.streaming) {
                    this._sendPing(30000, 'heartbeat-streaming');
                } else {
                    this._sendPing(10000, 'heartbeat');
                }
            }
        }, 20000);
    }

    _stopHeartbeat() {
        if (this._pingInterval) {
            clearInterval(this._pingInterval);
            this._pingInterval = null;
        }
        this._clearPongTimeout();
    }

    _clearPongTimeout() {
        if (this._pongTimeout) {
            clearTimeout(this._pongTimeout);
            this._pongTimeout = null;
        }
    }

    _armPongTimeout(timeoutMs, reason, socket, replaceOnTimeout = false) {
        this._clearPongTimeout();
        this._pongTimeout = setTimeout(() => {
            console.warn(`[WS] Pong timeout (${reason}) — connection dead`);
            if (this.ws !== socket) return;
            if (replaceOnTimeout) {
                this._replaceConnection(reason);
            } else {
                socket.close();
            }
        }, timeoutMs);
    }

    _sendPing(timeoutMs = 10000, reason = 'heartbeat', replaceOnTimeout = false) {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
            return false;
        }
        const socket = this.ws;
        try {
            socket.send(JSON.stringify({ type: 'ping' }));
            this._armPongTimeout(timeoutMs, reason, socket, replaceOnTimeout);
            return true;
        } catch (err) {
            console.warn(`[WS] Ping failed (${reason})`, err);
            return false;
        }
    }

    _handleResume(reason) {
        if (this.intentionalClose) return;

        const now = Date.now();
        const mustHandleTransition = (
            this._hiddenAt !== null
            || this.paused
            || this.httpFallback
            || reason === 'online'
        );
        if (!mustHandleTransition && now - this._lastResumeCheckAt < this._resumeCheckThrottleMs) {
            return;
        }
        this._lastResumeCheckAt = now;

        const wasHidden = this._hiddenAt !== null;
        const hiddenFor = wasHidden ? now - this._hiddenAt : 0;
        this._hiddenAt = null;
        const idleFor = now - this._lastActivityAt;

        if (!navigator.onLine) {
            this._emit('reconnecting', { attempt: this.reconnectAttempts + 1, delay: null, offline: true });
            return;
        }

        // HTTP fallback is useful while the network is genuinely bad, but it
        // must not become sticky across a later foreground/online transition.
        if (this.paused || this.httpFallback) {
            this.reconnectAttempts = 0;
            this.reconnectDelay = 1000;
            this.paused = false;
            this.httpFallback = false;
            this._replaceConnection(`resume-${reason}`);
            return;
        }

        if (!this.ws || this.ws.readyState === WebSocket.CLOSED || this.ws.readyState === WebSocket.CLOSING) {
            this.connect();
            return;
        }
        if (this.ws.readyState === WebSocket.CONNECTING) {
            return;
        }

        const shouldReplace = (
            reason === 'online'
            || reason === 'pageshow'
            || wasHidden
            || idleFor >= this._staleAfterIdleMs
        );

        if (!shouldReplace) return;

        // Keep an in-progress answer on its existing socket if it still answers
        // promptly. Replacing a healthy streaming socket would strand the rest
        // of that reply on the retired connection.
        if (this.streaming) {
            console.log(`[WS] Probing active stream after ${reason} (hidden ${hiddenFor}ms, idle ${idleFor}ms)`);
            if (!this._sendPing(this._resumeProbeTimeoutMs, `resume-streaming-${reason}`, true)) {
                this._replaceConnection(`resume-streaming-${reason}`);
            }
            return;
        }

        // Android can leave readyState at OPEN after the underlying radio or
        // browser process has discarded the connection. Replacing it is both
        // faster and more reliable than waiting for a zombie socket to time out.
        console.log(`[WS] Replacing connection after ${reason} (hidden ${hiddenFor}ms, idle ${idleFor}ms)`);
        this._replaceConnection(`resume-${reason}`);
    }

    _replaceConnection(reason) {
        const oldSocket = this.ws;
        const wasStreaming = this.streaming;
        this.ws = null;
        this._stopHeartbeat();
        clearTimeout(this._connectTimeout);
        this._connectTimeout = null;
        // Let Chat preserve any partial text and clear its streaming state
        // before the replacement socket asks for authoritative history.
        if (wasStreaming) this._emit('disconnected');
        if (oldSocket) {
            try {
                oldSocket.close(1000, reason);
            } catch (_err) {
                // CONNECTING sockets can reject close(); the identity guard on
                // their callbacks still prevents them from touching the new one.
            }
        }
        this.connect();
    }

    _isPrivateHost(hostname) {
        if (!hostname) return false;
        const host = hostname.toLowerCase();
        if (host === 'localhost' || host === '127.0.0.1' || host === '::1') {
            return false;
        }
        if (/^10\./.test(host) || /^192\.168\./.test(host)) {
            return true;
        }
        if (/^172\.(1[6-9]|2\d|3[0-1])\./.test(host)) {
            return true;
        }
        if (host.endsWith('.local')) {
            return true;
        }
        return !host.includes('.');
    }

    _maybeRedirectToPublicOrigin(reason) {
        if (!this._publicBaseUrl || !navigator.onLine) {
            return false;
        }
        if (!this._isPrivateHost(location.hostname)) {
            return false;
        }
        try {
            const target = new URL(this._publicBaseUrl);
            if (target.origin === location.origin) {
                return false;
            }
            const nextUrl = `${target.origin}${location.pathname}${location.search}${location.hash}`;
            console.warn(`[WS] Redirecting from private origin to public origin after ${reason}: ${nextUrl}`);
            window.location.replace(nextUrl);
            return true;
        } catch (err) {
            console.warn('[WS] Public-origin redirect failed', err);
            return false;
        }
    }

    _enterHttpFallback(reason, extra = {}) {
        if (this.paused && this.httpFallback) return;
        if (this._reconnectTimer) {
            clearTimeout(this._reconnectTimer);
            this._reconnectTimer = null;
        }
        this.paused = true;
        this.httpFallback = true;








        if (this._fallbackProbeTimer) clearInterval(this._fallbackProbeTimer);
        const probe = () => {
            if (this.intentionalClose) return;
            if (!this.paused && !this.httpFallback) return;
            if (!navigator.onLine) return;
            console.log('[WS] Fallback self-heal probe');
            this.reconnectAttempts = 0;
            this.reconnectDelay = 1000;
            this.paused = false;
            this.connect();
        };
        // First retry fast (a tunnel edge blip lasts ~10s), then steady 30s.
        setTimeout(probe, 8000);
        this._fallbackProbeTimer = setInterval(probe, 30000);
        console.warn(`[WS] Entering HTTP fallback (${reason})`);
        if (typeof logClientEvent === 'function') {
            logClientEvent('transport_http_fallback', {
                reason,
                attempts: this.reconnectAttempts,
                reachable: extra.reachable === true,
                host: location.host,
            }, { source: 'websocket' });
        }
        this._emit('paused', {
            attempts: this.reconnectAttempts,
            reason,
            ...extra,
        });
    }

    _reconnect() {
        if (this.intentionalClose || this.paused) return;
        if (this._reconnectTimer) return;
        if (!navigator.onLine) {
            this._emit('reconnecting', { attempt: this.reconnectAttempts + 1, delay: null, offline: true });
            return;
        }

        this.reconnectAttempts++;

        // Try private→public redirect after threshold
        if (this.reconnectAttempts >= this._redirectThreshold && this._maybeRedirectToPublicOrigin(`reconnect-${this.reconnectAttempts}`)) {
            return;
        }

        // After max retries, stop auto-reconnecting — let user tap to retry
        if (this.reconnectAttempts > this.maxAutoRetries) {
            this._enterHttpFallback('max-retries');
            return;
        }

        const baseDelay = Math.min(
            this.reconnectDelay * Math.pow(1.5, this.reconnectAttempts - 1),
            this.maxReconnectDelay
        );
        const jitter = Math.round(baseDelay * 0.2 * Math.random());
        const delay = baseDelay + jitter;
        console.log(`[WS] Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts}/${this.maxAutoRetries})`);
        this._emit('reconnecting', { attempt: this.reconnectAttempts, delay });
        this._reconnectTimer = setTimeout(async () => {
            this._reconnectTimer = null;
            // Quick health check — don't waste a WebSocket attempt if server unreachable
            const reachable = await this._healthCheck();
            if (this.intentionalClose || this.paused) return;
            if (reachable) {
                if (this.reconnectAttempts >= this._httpFallbackThreshold) {
                    this._enterHttpFallback('http-ok-ws-failed', { reachable: true });
                    return;
                }
                this.connect();
            } else {
                console.warn('[WS] Server unreachable — will retry after backoff');
                this._reconnect();
            }
        }, delay);
    }

    /** Manual reconnect — resets counters and tries immediately */
    manualReconnect() {
        this.reconnectAttempts = 0;
        this.reconnectDelay = 1000;
        this._rateLimitedUntil = 0;
        this.paused = false;
        this.httpFallback = false;
        this.intentionalClose = false;
        if (this._reconnectTimer) {
            clearTimeout(this._reconnectTimer);
            this._reconnectTimer = null;
        }
        this._emit('reconnecting', { attempt: 1, delay: 0 });
        this._replaceConnection('manual-reconnect');
    }

    /** Lightweight HTTP check to see if the server is reachable */
    async _healthCheck() {
        try {
            const url = (typeof apiPath === 'function') ? apiPath('/health') : '/health';
            const resp = await fetch(url, {
                method: 'GET',
                cache: 'no-store',
                signal: AbortSignal.timeout(8000),
            });
            return resp.ok;
        } catch (_err) {
            return false;
        }
    }

    send(data) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(data));
            this._lastActivityAt = Date.now();
            return true;
        }
        // Queue chat messages for delivery on reconnect
        if (data && data.type === 'message') {
            if (this._pendingQueue.length >= this._maxQueueSize) {
                this._pendingQueue.shift();
            }
            this._pendingQueue.push(data);
            this._persistQueue();
            console.warn('[WS] Not connected — message queued (%d pending)', this._pendingQueue.length);
            return 'queued';
        }
        console.warn('[WS] Not connected, cannot send');
        return false;
    }

    _drainQueue() {
        while (this._pendingQueue.length > 0 && this.ws && this.ws.readyState === WebSocket.OPEN) {
            const msg = this._pendingQueue.shift();
            this.ws.send(JSON.stringify(msg));
            console.log('[WS] Drained queued message');
        }
        this._persistQueue();
    }

    close() {
        this.intentionalClose = true;
        if (this._reconnectTimer) {
            clearTimeout(this._reconnectTimer);
            this._reconnectTimer = null;
        }
        if (this._fallbackProbeTimer) {
            clearInterval(this._fallbackProbeTimer);
            this._fallbackProbeTimer = null;
        }
        clearTimeout(this._connectTimeout);
        this._connectTimeout = null;
        this._stopHeartbeat();
        if (this.ws) this.ws.close();
    }
}
