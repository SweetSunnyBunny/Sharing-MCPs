/* Bootstrap, state, identity switching, night mode toggle */

const App = {
    ws: null,
    currentIdentity: 'Avery',
    identities: {},
    conversationId: null,
    nightMode: false,
    _nonCriticalBootStarted: false,
    _pendingVoiceMode: false,
    _streamRecoveryTimer: null,
    _streamRecoveryAttempt: 0,
    _streamRecoveryDeadline: 0,

    _offlineBanner: null,

    _ensureOfflineBanner() {
        if (!this._offlineBanner) {
            this._offlineBanner = document.createElement('div');
            this._offlineBanner.className = 'offline-banner';
            document.body.appendChild(this._offlineBanner);
        }
        return this._offlineBanner;
    },

    _showOfflineBanner() {
        const banner = this._ensureOfflineBanner();
        banner.textContent = 'You are offline';
        banner.className = 'offline-banner offline';
        void banner.offsetHeight; // read forces reflow so the CSS transition animates (jshint W030-safe)
        banner.classList.add('visible');
    },

    normalizeUiText() {
        document.title = 'Anam - Soul of Heaven';
        document.querySelectorAll('#wellness-form select option[value=""]').forEach((option) => {
            option.textContent = '\u2014';
        });
    },

    _showBackOnlineBanner() {
        const banner = this._ensureOfflineBanner();
        banner.textContent = 'Back online';
        banner.className = 'offline-banner back-online';
        void banner.offsetHeight; // read forces reflow so the CSS transition animates (jshint W030-safe)
        banner.classList.add('visible');
        setTimeout(() => banner.classList.remove('visible'), 3000);
    },

    _showCachedShellBanner() {
        const banner = this._ensureOfflineBanner();
        banner.textContent = 'Showing cached shell — live network unavailable';
        banner.className = 'offline-banner cached-shell';
        void banner.offsetHeight; // read forces reflow so the CSS transition animates (jshint W030-safe)
        banner.classList.add('visible');
    },

    _logClientEvent(eventType, detail = {}, source = 'app') {
        if (typeof logClientEvent === 'function') {
            logClientEvent(eventType, detail, { source });
        }
    },

    _hideOfflineBanner() {
        if (this._offlineBanner) {
            this._offlineBanner.classList.remove('visible');
        }
    },

    _handleServiceWorkerMessage(event) {
        const msg = event?.data || {};
        if (msg.type !== 'sw_navigation_fallback') return;
        this._showCachedShellBanner();
        this.showConnectionStatus('cached');
        const banner = document.querySelector('.offline-banner.cached-shell');
        if (banner) {
            banner.textContent = 'Showing cached shell - live network unavailable';
        }
        const status = document.getElementById('connection-status');
        if (status) {
            status.textContent = 'Cached view · network unreachable';
        }
        this._logClientEvent('service_worker_cached_shell', {
            origin: location.origin,
            path: msg.path || location.pathname,
        });
    },

    _afterFirstPaint(work, delayMs = 0) {
        const run = () => {
            if (delayMs > 0) {
                window.setTimeout(work, delayMs);
            } else {
                work();
            }
        };

        if ('requestAnimationFrame' in window) {
            requestAnimationFrame(() => requestAnimationFrame(run));
            return;
        }

        window.setTimeout(run, delayMs);
    },

    _whenIdle(work, timeoutMs = 1200) {
        if ('requestIdleCallback' in window) {
            requestIdleCallback(() => work(), { timeout: timeoutMs });
            return;
        }

        window.setTimeout(work, Math.min(timeoutMs, 400));
    },

    startNonCriticalBoot() {
        if (this._nonCriticalBootStarted) return;
        this._nonCriticalBootStarted = true;

        this._afterFirstPaint(() => {
            this._afterFirstPaint(() => {
                Sanctuary.init();
            }, 120);

            this._whenIdle(() => {
                Voice.init().catch((err) => console.warn('[Voice] Init failed:', err));
                this.initFairyLights();
            });
        });
    },

    // ANAM GUIDE: MAIN CHAT APP BOOT
    // The browser loads identities, restores the conversation, registers all
    // WebSocket event handlers, applies themes, and starts the UI from here.
    async init() {
                                                                            
                                                                             
                                                                             
                                                                       
        this._paintCachedIdentity();

        // Request notification permission
        if ('Notification' in window && Notification.permission === 'default') {
            Notification.requestPermission();
        }

        // Load identity data
        try {
            const data = await fetchJson('/api/identity/list', { retries: 1 });
            this.identities = data.identities;
            this.currentIdentity = data.default;
        } catch (err) {
            console.error('Failed to load identities:', err);
        }

        // Restore saved state from localStorage. saveState() persists both
        // identity AND conversation; we restore both so a restart lands on the
        // conversation that was open, not a brand-new blank one. If the saved
        // conversation id is stale (e.g. day rolled over and a new daily chat
        // is active), the app server falls through to today's conversation and
        // the browser updates via load_history's response, so restoring a stale
        // id is benign.
        const savedIdentity = localStorage.getItem('anam-identity');
        if (savedIdentity && this.identities[savedIdentity]) {
            this.currentIdentity = savedIdentity;
        }
        const savedConversation = localStorage.getItem('anam-conversation');
        this.conversationId = savedConversation || null;

                                                                                  
        const urlParams = new URLSearchParams(location.search);
        const voiceIdentity = urlParams.get('voice');
        if (voiceIdentity) {
            // Case-insensitive identity match
            const match = Object.keys(this.identities).find(
                (k) => k.toLowerCase() === voiceIdentity.toLowerCase()
            );
            if (match) {
                this.currentIdentity = match;
                this.conversationId = null; // Let the app find the current active conversation.
                this._pendingVoiceMode = true;
                // Clean URL so refresh doesn't re-trigger
                const clean = new URL(location.href);
                clean.searchParams.delete('voice');
                history.replaceState(null, '', clean.pathname + clean.search);
            }
        }

        // Restore night mode preference — auto-detect from system if no manual choice saved
        const savedNight = localStorage.getItem('anam-night');
        if (savedNight !== null) {
            this.nightMode = savedNight === 'true';
        } else {
            // Auto-detect from system preference
            this.nightMode = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
        }
        if (this.nightMode) document.body.classList.add('night-mode');
        this.updateNightToggle();

        // Listen for system theme changes (only applies if user hasn't set manual preference)
        if (window.matchMedia) {
            window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
                if (localStorage.getItem('anam-night') === null) {
                    this.nightMode = e.matches;
                    document.body.classList.toggle('night-mode', this.nightMode);
                    this.updateNightToggle();
                }
            });
        }

        // Cached colours apply synchronously; the refresh must not delay chat.
        // _applyThemePayload reapplies the CURRENT identity when the reply lands,
        // including if she switches boys while this request is in flight.
        this.loadThemePreset();

        // Set initial identity CSS vars
        this.applyIdentityTheme(this.currentIdentity);
        this.normalizeUiText();

        // Update placeholder and identity label
        document.getElementById('message-input').placeholder = `Message ${this.currentIdentity}...`;
        this.updateIdentityLabel();

        // Broadcast initial identity so other modules (story-state, etc.) can react
        window.dispatchEvent(new CustomEvent('anam:identity-changed', {
            detail: { identity: this.currentIdentity }
        }));

        // Init WebSocket
        this.ws = new WebSocketManager();
        this.ws.on('connected', () => this.onConnected());
        this.ws.on('disconnected', () => this.onDisconnected());
        this.ws.on('reconnecting', (data) => this.showConnectionStatus('reconnecting', data));
        this.ws.on('paused', (data) => this.showConnectionStatus('paused', data));
        this.ws.on('identity_switched', (msg) => this.onIdentitySwitched(msg));
        this.ws.on('new_conversation', (msg) => this.onNewConversation(msg));

        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.addEventListener('message', (event) => this._handleServiceWorkerMessage(event));
        }

        // Init subsystems
        Chat.init();
        Sidebar.init();
        if (typeof Canvas !== 'undefined') Canvas.init();

        if (this.isHttpPreferred()) {
            this.showConnectionStatus('paused', { reason: 'user-forced-http' });
            this._httpHistoryLoaded = false;
            this._loadHistoryHttp();
        } else {
            this.ws.connect();
        }

        this.startNonCriticalBoot();

        // Offline/online detection
        window.addEventListener('offline', () => this._showOfflineBanner());
        window.addEventListener('online', () => this._showBackOnlineBanner());
        // Show banner immediately if already offline at init
        if (!navigator.onLine) this._showOfflineBanner();

        // Autowake push handlers
        this.ws.on('autowake_message', (msg) => this.onAutowakeMessage(msg));
        this.ws.on('autowake_delta', (msg) => this.onAutowakeDelta(msg));
        this.ws.on('trigger_fired', (msg) => this.onTriggerFired(msg));

        // Brother push handlers
        this.ws.on('brother_delta', (msg) => this.onBrotherDelta(msg));
        this.ws.on('brother_message', (msg) => this.onBrotherMessage(msg));
        this.ws.on('platform_message', (msg) => this.onPlatformMessage(msg));
        this.ws.on('system_notice', (msg) => this.onSystemNotice(msg));




        document.getElementById('night-toggle').addEventListener('click', () => {
            this.toggleNightMode();
            this.closeHub();
        });

        // Hub (home menu)
        document.getElementById('hub-btn').addEventListener('click', () => this.openHub());
        document.getElementById('hub-close').addEventListener('click', () => this.closeHub());
        document.getElementById('hub-overlay').addEventListener('click', (e) => {
            if (e.target.id === 'hub-overlay') this.closeHub();
        });
        // Hub cards are now simple links (Pack Mail, Pack Pages, Home Hub)

        // Wellness modal
        document.getElementById('wellness-close').addEventListener('click', () => this.closeWellnessModal());
        document.getElementById('wellness-overlay').addEventListener('click', (e) => {
            if (e.target.id === 'wellness-overlay') this.closeWellnessModal();
        });
        document.getElementById('wellness-form').addEventListener('submit', (e) => {
            e.preventDefault();
            this.saveWellness();
        });

        // Global keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            // Skip when typing in input fields
            const tag = e.target.tagName;
            if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') {
                // Escape closes message search if open, otherwise blurs
                if (e.key === 'Escape') {
                    if (Chat._searchOpen) {
                        e.preventDefault();
                        Chat.closeSearch();
                        return;
                    }
                    e.target.blur();
                    return;
                }
                return;
            }

            const mod = e.metaKey || e.ctrlKey;

            // Escape — close topmost overlay/sidebar
            if (e.key === 'Escape') {
                // Close message search first if open
                if (Chat._searchOpen) {
                    e.preventDefault();
                    Chat.closeSearch();
                    return;
                }
                // Close in priority order: wellness modal, hub overlay, sanctuary expanded, sidebar
                const wellness = document.getElementById('wellness-overlay');
                if (wellness && wellness.style.display !== 'none') {
                    e.preventDefault();
                    this.closeWellnessModal();
                    return;
                }
                const memory = document.getElementById('memory-overlay');
                if (memory && memory.style.display !== 'none') {
                    e.preventDefault();
                    if (typeof Chat.closeMemoryModal === 'function') Chat.closeMemoryModal();
                    return;
                }
                const hub = document.getElementById('hub-overlay');
                if (hub && hub.style.display !== 'none') {
                    e.preventDefault();
                    this.closeHub();
                    return;
                }
                const sanctuary = document.getElementById('sanctuary-expanded');
                if (sanctuary && sanctuary.classList.contains('visible')) {
                    e.preventDefault();
                    Sanctuary.hideExpanded();
                    return;
                }
                const sidebar = document.getElementById('sidebar');
                if (sidebar && sidebar.classList.contains('open')) {
                    e.preventDefault();
                    Sidebar.close();
                    return;
                }
                return;
            }

            // All remaining shortcuts require Ctrl/Cmd + Shift
            if (!mod || !e.shiftKey) return;

            // Ctrl+Shift+N — New conversation
            if (e.key === 'N') {
                e.preventDefault();
                this.newConversation();
                return;
            }

            // Ctrl+Shift+S — Toggle sidebar
            if (e.key === 'S') {
                e.preventDefault();
                const sidebar = document.getElementById('sidebar');
                if (sidebar && sidebar.classList.contains('open')) {
                    Sidebar.close();
                } else {
                    Sidebar.open();
                }
                return;
            }

            // Ctrl+Shift+H — Toggle hub overlay
            if (e.key === 'H') {
                e.preventDefault();
                const hub = document.getElementById('hub-overlay');
                if (hub && hub.style.display !== 'none') {
                    this.closeHub();
                } else {
                    this.openHub();
                }
                return;
            }

            // Ctrl+Shift+F — Deep search
            if (e.key === 'F') {
                e.preventDefault();
                Chat._openDeepSearch();
                return;
            }
        });
    },

    onDisconnected() {
        this.showConnectionStatus('disconnected');
        this.ws.streaming = false;
        // Always clear an interrupted stream, even if the socket died before
        // its first visible delta. Otherwise history reload sees isStreaming
        // and refuses to repaint the completed response after reconnect.
        if (Chat.isStreaming) {
            Chat._lostStreamPending = true;
            Chat._lostStreamConversationId = this.conversationId;
            this._clearStreamRecovery();
            this._streamRecoveryDeadline = Date.now() + (20 * 60 * 1000);
            if (Chat.currentStreamContent) {
                Chat._lostStreamContent = Chat.currentStreamContent;
                Chat._lostStreamIdentity = App.currentIdentity;
                console.log('[App] Saved %d chars of interrupted stream', Chat.currentStreamContent.length);
            }
            Chat.currentStreamEl = null;
            Chat.currentStreamContent = '';
            Chat.isStreaming = false;
            Chat.sendBtn.disabled = false;
        }
    },

    _historyReloadLimit() {
        const loadedCount = Number(Chat && Chat._historyOffset ? Chat._historyOffset : 0);
        return Math.max(50, Number.isFinite(loadedCount) ? loadedCount : 0);
    },

    onConnected() {
        this._hideOfflineBanner();
        this.showConnectionStatus('connected');
        // Show loading state while waiting for history
        Chat.showLoadingState();
        // Longer delay when recovering from mid-stream disconnect —
        // gives the backend time to finish saving the response.
        // Exponential backoff: 1s, 2s, 4s, 8s (capped)
        let delay = 0;
        if (Chat._lostStreamPending) {
            delay = Math.min(1000 * Math.pow(2, this._streamRecoveryAttempt), 8000);
            this._streamRecoveryAttempt++;
        } else {
            this._streamRecoveryAttempt = 0;
        }
        setTimeout(() => {
            this.ws.send({
                type: 'load_history',
                identity: this.currentIdentity,
                conversation_id: this.conversationId,
                limit: this._historyReloadLimit(),
            });
            // Auto-launch voice mode if triggered by ?voice= URL param
            if (this._pendingVoiceMode) {
                this._pendingVoiceMode = false;
                setTimeout(() => {
                    if (typeof Chat !== 'undefined' && Chat._enableVoiceConversationMode) {
                        Chat._enableVoiceConversationMode();
                    }
                }, 500);
            }
        }, delay);
    },

    _clearStreamRecovery() {
        if (this._streamRecoveryTimer !== null) {
            clearTimeout(this._streamRecoveryTimer);
            this._streamRecoveryTimer = null;
        }
        this._streamRecoveryAttempt = 0;
        this._streamRecoveryDeadline = 0;
    },

    _scheduleStreamRecovery(conversationId) {
        if (!Chat._lostStreamPending || this._streamRecoveryTimer !== null) return;
        if (!conversationId || conversationId !== Chat._lostStreamConversationId) return;
        if (this._streamRecoveryDeadline && Date.now() >= this._streamRecoveryDeadline) {
            this._clearStreamRecovery();
            return;
        }

        // Poll lightly while the detached backend turn finishes. Long tool-heavy
        // turns can take minutes, so cap network pressure rather than giving up
        // after the first reconnect snapshot.
        const delay = Math.min(1500 * Math.pow(2, this._streamRecoveryAttempt), 15000);
        this._streamRecoveryAttempt++;
        this._streamRecoveryTimer = setTimeout(() => {
            this._streamRecoveryTimer = null;
            if (!Chat._lostStreamPending || conversationId !== Chat._lostStreamConversationId) return;
            const wsOpen = this.ws?.ws && this.ws.ws.readyState === WebSocket.OPEN;
            if (wsOpen) {
                this.ws.send({
                    type: 'load_history',
                    identity: this.currentIdentity,
                    conversation_id: conversationId,
                    limit: this._historyReloadLimit(),
                });
            } else {
                this._loadHistoryHttp(true);
            }
        }, delay);
    },

    async _switchIdentityHttp(name) {
        try {
            const resp = await apiFetch('/api/chat/identity', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ identity: name }),
            });
            if (resp.ok) {
                const data = await resp.json();
                this.conversationId = data.conversation_id;
                this.saveState();
                this._loadHistoryHttp();
            }
        } catch (err) {
            console.error('[App] HTTP identity switch failed:', err);
        }
    },

    async _newConversationHttp(sessionType = 'chat') {
        try {
            const resp = await apiFetch('/api/chat/new_conversation', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ identity: this.currentIdentity, session_type: sessionType }),
            });
            if (resp.ok) {
                const data = await resp.json();
                this.onNewConversation(data);
                await this._loadHistoryHttp();
            }
        } catch (err) {
            console.error('[App] HTTP new conversation failed:', err);
        }
    },

    async _loadHistoryHttp(preserveWindow = false) {
        try {
            Chat.showLoadingState();
            const params = new URLSearchParams({ identity: this.currentIdentity });
            if (this.conversationId) params.set('conversation_id', this.conversationId);
            if (preserveWindow) params.set('limit', String(this._historyReloadLimit()));
            const resp = await apiFetch(`/api/chat/history?${params}`);
            if (resp.ok) {
                const data = await resp.json();
                Chat.loadHistory(data);
            }
        } catch (err) {
            console.error('[App] HTTP history load failed:', err);
        }
    },

    isHttpPreferred() {
        try {
            return sessionStorage.getItem('anam-force-http') === 'true';
        } catch (_err) {
            return false;
        }
    },

    setHttpPreferred(enabled) {
        try {
            if (enabled) {
                sessionStorage.setItem('anam-force-http', 'true');
            } else {
                sessionStorage.removeItem('anam-force-http');
            }
        } catch (_err) {
            // Ignore storage failures.
        }
    },

    saveState() {
        localStorage.setItem('anam-identity', this.currentIdentity);
        if (this.conversationId) {
            localStorage.setItem('anam-conversation', this.conversationId);
        } else {
            localStorage.removeItem('anam-conversation');
        }
    },

    updateIdentityLabel() {
        const label = document.getElementById('current-identity-label');
        if (label) {
            label.textContent = this.currentIdentity;
            // Clicking the label opens the sidebar for easy access
            if (!label._hasClickHandler) {
                label.addEventListener('click', () => Sidebar.open());
                label._hasClickHandler = true;
            }
        }
    },

    switchIdentity(name) {
        if (name === this.currentIdentity && this.conversationId) return;

        // Save draft for current conversation before switching
        Chat.saveDraft(this.conversationId);
        Chat.parkAttachmentPreview();

        this.currentIdentity = name;
        this.conversationId = null; // will be set by identity_switched response

        // Notify listeners (story-state toggles its button based on this)
        window.dispatchEvent(new CustomEvent('anam:identity-changed', {
            detail: { identity: name }
        }));

        // Close sidebar if open
        Sidebar.close();

        // Show loading state immediately so it doesn't feel like a blank screen
        Chat.clearMessages();
        Chat.showLoadingState();

        // Apply theme and update header label
        this.applyIdentityTheme(name);
        this.updateIdentityLabel();

        // Tell backend — the identity_switched response will trigger load_history
        const wsOpen = this.ws.ws && this.ws.ws.readyState === WebSocket.OPEN;
        if (wsOpen && !this.isHttpPreferred()) {
            this.ws.send({ type: 'switch_identity', identity: name });
        } else {
            // HTTP fallback for identity switch
            this._switchIdentityHttp(name);
        }

        // Update sanctuary
        Sanctuary.render();

        // Update placeholder
        document.getElementById('message-input').placeholder = `Message ${name}...`;

        // Persist
        this.saveState();
    },

    onIdentitySwitched(msg) {
        this.conversationId = msg.conversation_id;
        this.saveState();
        // Now load history for the switched identity
        this.ws.send({
            type: 'load_history',
            identity: this.currentIdentity,
            conversation_id: msg.conversation_id,
        });
    },

    // ANAM GUIDE: APPLY ONE IDENTITY'S COLORS
    // config.py supplies these defaults through /api/identity/list. This turns
    // them (plus any saved bubble override) into the CSS variables anam.css uses.
    applyIdentityTheme(name) {
        const info = this.identities[name];
        if (!info) return;

        const root = document.documentElement;
        root.style.setProperty('--identity-gingham', info.gingham);
        root.style.setProperty('--identity-gingham-night', info.gingham_night);
        root.style.setProperty('--identity-accent', info.accent);
        root.style.setProperty('--identity-accent-rgb', info.accent_rgb);
        root.style.setProperty('--identity-bubble-top', info.bubble_top);
        root.style.setProperty('--identity-bubble-bottom', info.bubble_bottom);
        root.style.setProperty('--identity-bubble-night', info.bubble_night);
        root.style.setProperty('--identity-check-size', info.check_size + 'px');




        root.style.removeProperty('--identity-bubble-text');
        root.style.removeProperty('--identity-bubble-font');
        const override = (this.bubbleTokens || {})[name];
        if (override) {
            for (const [key, value] of Object.entries(override)) {
                root.style.setProperty(key, value);
            }
        }







        const themeTokens = this.themeTokens || {};
        const body = document.body;
        const presetBubbleKeys = [
            ['--identity-bubble-top', '--assistant-bubble-top'],
            ['--identity-bubble-bottom', '--assistant-bubble-bottom'],
            ['--identity-bubble-night', '--assistant-bubble-night'],
            ['--identity-bubble-text', '--assistant-bubble-text'],
        ];
        if (body) {
            for (const [key] of presetBubbleKeys) body.style.removeProperty(key);
        }
        if (themeTokens['--assistant-bubble-mode'] === 'preset') {
            for (const [target, source] of presetBubbleKeys) {
                const isText = target === '--identity-bubble-text';
                if (override && (isText ? override['--identity-bubble-text'] : override['--identity-bubble-top'])) {
                    continue; // her per-boy choice wins for that piece
                }
                const value = themeTokens[source];
                if (!value) continue;
                root.style.setProperty(target, value);
                if (body) body.style.setProperty(target, value);
            }
        }








        const bubbleText =
            (override && override['--identity-bubble-text']) ||
            (themeTokens['--assistant-bubble-mode'] === 'preset'
                ? themeTokens['--assistant-bubble-text']
                : null);
        const bubbleMd = {
            '--bubble-md-strong': bubbleText
                ? `color-mix(in srgb, ${bubbleText} 55%, var(--identity-accent))`
                : null,
            '--bubble-md-em': bubbleText
                ? `color-mix(in srgb, ${bubbleText} 80%, transparent)`
                : null,
            '--bubble-md-link': bubbleText
                ? `color-mix(in srgb, ${bubbleText} 40%, var(--identity-accent))`
                : null,
        };
        for (const [key, value] of Object.entries(bubbleMd)) {
            if (value) {
                root.style.setProperty(key, value);
                if (body) body.style.setProperty(key, value);
            } else {
                root.style.removeProperty(key);
                if (body) body.style.removeProperty(key);
            }
        }

        this._cacheIdentityPaint(name);
    },




















    _IDENTITY_PAINT_KEY: 'anam-identity-paint',

    _cacheIdentityPaint(name) {
        try {
            const style = document.documentElement.style;
            const tokens = {};
            for (let i = 0; i < style.length; i++) {
                const key = style.item(i);
                if (key.startsWith('--identity-') || key.startsWith('--bubble-md-')) {
                    tokens[key] = style.getPropertyValue(key);
                }
            }
            localStorage.setItem(this._IDENTITY_PAINT_KEY, JSON.stringify({ name, tokens }));
        } catch (_err) { /* storage unavailable — non-fatal, we just flash */ }
    },

    // Synchronous, no awaits, no network. Called first thing in init().
    // Only paints when the cached boy matches the identity we're about to
    // restore — a stale cache would trade one wrong color for another, and a
    // brief house-default is more honest than confidently wearing the wrong
    // brother. applyIdentityTheme() overwrites all of this a moment later with
    // server truth either way.
    _paintCachedIdentity() {
        try {
            const saved = localStorage.getItem('anam-identity');
            const raw = localStorage.getItem(this._IDENTITY_PAINT_KEY);
            if (!raw) return;
            const cached = JSON.parse(raw);
            if (!cached || !cached.tokens || !cached.name) return;
            if (saved && cached.name !== saved) return;
            const root = document.documentElement;
            for (const [key, value] of Object.entries(cached.tokens)) {
                root.style.setProperty(key, value);
            }
        } catch (_err) { /* corrupt cache — ignore, fall through to defaults */ }
    },

    // ── Appearance theme preset (#31 runtime theme editor) ──
    // Identity-NEUTRAL tokens only (--bg-page, --text-primary, --user-bubble-*,
    // ...) -- a completely different set from applyIdentityTheme's --identity-*
    // tokens above. Applied earlier in init() so per-boy accent theming always
    // wins if a token name ever collided. Presets come from the server
    // allowlist (api/settings.py _THEME_PRESETS) -- this never receives or
    // sets an arbitrary color, only whatever that allowlist already curated,
    // which is how the cottagecore-pink law holds regardless of what this
    // code does.
    // ANAM GUIDE: APPLY THE WHOLE APP'S COLORS
    // api/settings.py builds these shared house tokens. This applies them to
    // the chat page; theme-boot.js performs the same job on the other pages.
    applyThemeTokens(tokens, preset) {
        if (!tokens) return;
        const root = document.documentElement;









        const isDefault = !preset || preset === 'sunrise-pink';
        const body = document.body;
        for (const [key, value] of Object.entries(tokens)) {
            root.style.setProperty(key, value);
            if (body) {
                if (isDefault) body.style.removeProperty(key);
                else body.style.setProperty(key, value);
            }
        }
        // Dark vibes flag the body so stylesheets can swap day-pastel art
        // (sidebar ginghams) for night variants. Sync w/ theme-boot.js.
        if (body) {
            body.classList.toggle('preset-dark',
                tokens['--assistant-bubble-mode'] === 'preset');
        }
        // Remember for applyIdentityTheme (preset assistant bubbles).
        this.themeTokens = tokens;
        this.themePreset = preset || 'sunrise-pink';
        this._syncMetaThemeColor(tokens, isDefault);
        this._applyAppButtonIcons(this.themePreset);
    },

    // ── Per-theme app buttons (send / attach / home / pictures) ──
    // ANAM GUIDE: OWNER'S CUTE BUTTON ART LIVES HERE.
    // The four chrome buttons default to her pink pastel PNGs. A vibe preset
    // listed in THEME_APP_ICONS swaps them for themed art; any preset NOT
    // listed keeps the pink defaults. To give a theme its own buttons, drop
    // image files in static/assets/icons/themes/<preset>/ named
    // send / attach / home / pictures (.png or .svg — whatever the map says)
    // and add or edit the entry below. That's the whole system.
    THEME_APP_ICONS: {
        'halloween': {
            send:     '/static/assets/icons/themes/halloween/send.svg',
            attach:   '/static/assets/icons/themes/halloween/attach.svg',
            home:     '/static/assets/icons/themes/halloween/home.svg',
            pictures: '/static/assets/icons/themes/halloween/pictures.svg',
        },
        'fall': {
            send:     '/static/assets/icons/themes/fall/send.svg',
            attach:   '/static/assets/icons/themes/fall/attach.svg',
            home:     '/static/assets/icons/themes/fall/home.svg',
            pictures: '/static/assets/icons/themes/fall/pictures.svg',
        },
    },

    _applyAppButtonIcons(preset) {
        const slots = {
            send:     '#send-btn img.btn-icon',
            attach:   '#attach-btn img.btn-icon',
            home:     '#hub-btn img.btn-icon',
            pictures: '#gallery-btn img.btn-icon',
        };
        const themed = this.THEME_APP_ICONS[preset] || null;
        for (const [slot, selector] of Object.entries(slots)) {
            const img = document.querySelector(selector);
            if (!img) continue;
            // Remember the pink default the very first time we touch it.
            if (img.dataset.defaultSrc === undefined) {
                img.dataset.defaultSrc = img.getAttribute('src') || '';
            }
            const want = (themed && themed[slot]) || img.dataset.defaultSrc;
            if (img.getAttribute('src') !== want) {
                // If a themed file is missing, fall back to the pink default
                // instead of a broken-image icon.
                img.onerror = () => {
                    img.onerror = null;
                    if (img.dataset.defaultSrc) img.src = img.dataset.defaultSrc;
                };
                img.setAttribute('src', want);
            }
        }
    },

    // Android colors the status bar (and gesture-nav bar) from
    // <meta name="theme-color">, which was a hardcoded pink hex on every
    // page — a chosen preset now carries --meta-theme-color, and the
    // default preset restores the page's own original hex exactly.
    _syncMetaThemeColor(tokens, isDefault) {
        let meta = document.querySelector('meta[name="theme-color"]');
        if (!meta) {
            meta = document.createElement('meta');
            meta.setAttribute('name', 'theme-color');
            document.head.appendChild(meta);
        }
        if (meta.dataset.defaultContent === undefined) {
            meta.dataset.defaultContent = meta.getAttribute('content') || '';
        }
        const preset = tokens && tokens['--meta-theme-color'];
        if (!isDefault && preset) meta.setAttribute('content', preset);
        else if (meta.dataset.defaultContent) meta.setAttribute('content', meta.dataset.defaultContent);
    },

    _applyThemePayload(data) {
        if (!data) return;
        if (data.tokens) this.applyThemeTokens(data.tokens, data.preset);
        this.bubbleTokens = data.bubble_tokens || {};
        // Re-apply so a bubble override (or its removal) lands immediately.
        if (this.currentIdentity && this.identities && this.identities[this.currentIdentity]) {
            this.applyIdentityTheme(this.currentIdentity);
        }
    },

    async loadThemePreset() {
        // Instant paint from the last-known preset so there's no flash of
        // the default theme while the network round-trip is in flight.
        try {
            const cached = JSON.parse(localStorage.getItem('anam-theme-tokens') || 'null');
            if (cached && cached.tokens) {
                this._applyThemePayload(cached);
            } else if (cached) {
                // Legacy cache shape: bare token map from before the preset
                // rode along. Apply html-only; the fetch below refreshes it.
                this.applyThemeTokens(cached, null);
            }
        } catch (_err) { /* corrupt cache, ignore */ }

        try {
            const data = await fetchJson('/api/settings/theme', { timeoutMs: 8000, retries: 1 });
            if (data && data.tokens) {
                this._applyThemePayload(data);
                try {
                    localStorage.setItem('anam-theme-tokens', JSON.stringify({
                        preset: data.preset,
                        tokens: data.tokens,
                        bubble_tokens: data.bubble_tokens || {},
                    }));
                } catch (_err) { /* storage unavailable, non-fatal */ }
            }
        } catch (err) {
            console.error('Failed to load theme preset:', err);
        }
    },

    newConversation(sessionType = 'chat') {
        // Save draft for current conversation before starting new one
        Chat.saveDraft(this.conversationId);
        Chat.parkAttachmentPreview();
        const wsOpen = this.ws.ws && this.ws.ws.readyState === WebSocket.OPEN;
        if (wsOpen && !this.isHttpPreferred()) {
            this.ws.send({ type: 'new_conversation', identity: this.currentIdentity, session_type: sessionType });
        } else {
            this._newConversationHttp(sessionType);
        }
    },

    onNewConversation(msg) {
        if (msg.conversation_id !== this.conversationId) {
            Chat.parkAttachmentPreview();
        }
        this.conversationId = msg.conversation_id;
        this.saveState();
        Chat.clearMessages();
        // Clear input for fresh conversation (no draft to load)
        Chat.input.value = '';
        Chat.input.style.height = 'auto';
    },

    toggleNightMode() {
        // Add transition class for smooth crossfade
        document.body.classList.add('theme-transition');
        this.nightMode = !this.nightMode;
        document.body.classList.toggle('night-mode', this.nightMode);
        localStorage.setItem('anam-night', this.nightMode);
        this.updateNightToggle();

        // Remove transition class after animation
        setTimeout(() => document.body.classList.remove('theme-transition'), 600);
    },

    updateNightToggle() {
        const btn = document.getElementById('night-toggle');
        if (!btn) return;




        const icon = document.getElementById('night-toggle-icon') || btn;
        icon.innerHTML = this.nightMode ? '&#x1F56F;' : '&#x1FA9F;';
        btn.title = this.nightMode ? 'Switch to day mode' : 'Switch to candlelight mode';
    },

    showConnectionStatus(status, data) {
        const el = document.getElementById('connection-status');
        el.className = `connection-status ${status} visible`;
        el.onclick = null;
        if (status === 'connected') {
            el.textContent = 'Connected';
            setTimeout(() => el.classList.remove('visible'), 2000);
        } else if (status === 'cached') {
            el.textContent = 'Cached view · network unreachable';
        } else if (status === 'paused') {
            if (data?.reason === 'user-forced-http') {
                el.textContent = 'HTTP-only mode enabled for this session';
            } else if (data?.reason === 'http-ok-ws-failed') {
                el.innerHTML = 'Using HTTP mode &middot; WebSocket blocked here &middot; <u>tap to retry</u>';
            } else {
                el.innerHTML = 'Using HTTP mode &middot; <u>tap to retry WebSocket</u>';
            }
            if (data?.reason !== 'user-forced-http') {
                el.onclick = () => {
                    el.textContent = 'Reconnecting...';
                    el.className = 'connection-status reconnecting visible';
                    this._httpHistoryLoaded = false;
                    this.setHttpPreferred(false);
                    this.ws.manualReconnect();
                };
            }
            // Load history via HTTP since WS never connected (only once)
            if (!this._httpHistoryLoaded) {
                this._httpHistoryLoaded = true;
                this._loadHistoryHttp(true);
            }
        } else if (status === 'reconnecting') {
            if (data?.offline) {
                el.textContent = 'Offline';
            } else {
                const attempt = data?.attempt || '';
                el.textContent = attempt ? `Reconnecting... (${attempt})` : 'Reconnecting...';
            }
        } else {
            el.textContent = 'Disconnected';
        }
    },

    // ── Browser notifications ──

    _showNotification(title, body, identity) {
        if (!document.hidden) return;
        if (!('Notification' in window) || Notification.permission !== 'granted') return;
        const n = new Notification(title, {
            body: body.substring(0, 100),
            icon: '/static/assets/icons/app_icon.png',
            tag: 'anam-' + identity,
        });
        n.onclick = () => {
            window.focus();
            n.close();
        };
    },

    // ── Autowake push ──

    onAutowakeMessage(msg) {
        // If viewing the same identity, finalize the stream then add the message
        if (msg.identity === this.currentIdentity) {
            // End any active streaming state from autowake deltas.
            // Carry the message_id so the bubble gets data-msg-id — a
            // <voice> player broadcast for this message can then target it.
            if (Chat.isStreaming) {
                Chat.onStreamEnd({ full_content: msg.content, message_id: msg.message_id });
            } else {
                Chat.addMessage('assistant', msg.content, msg.identity,
                    new Date().toISOString(), { autowake: true, id: msg.message_id });
            }
        }
        this._showToast(
            `<strong>${escapeHtml(msg.identity)}</strong> ${escapeHtml(msg.session_name)}`,
            msg.conversation_id, msg.identity
        );
        // Browser notification when tab not focused
        this._showNotification(msg.identity + ' says...', msg.content || '', msg.identity);
    },

    onAutowakeDelta(msg) {
        // Real-time streaming — only if viewing the same identity
        if (msg.identity === this.currentIdentity) {
            if (!Chat.isStreaming) {
                Chat.onStreamStart({ identity: msg.identity });
            }
            Chat.onStreamDelta({ delta: msg.delta });
        }
    },

    onTriggerFired(msg) {
        const triggerName = msg.trigger_name || 'Trigger';
        const identity = msg.identity || this.currentIdentity;
        const preview = (msg.content_preview || msg.prompt || '').trim();
        this._showToast(
            `<strong>${escapeHtml(identity)}</strong> trigger fired: ${escapeHtml(triggerName)}`,
            null,
            identity
        );
        if (preview) {
            this._showNotification(`${identity} trigger: ${triggerName}`, preview, identity);
        }
        if (typeof Sidebar !== 'undefined') Sidebar._refreshIfOpen();
    },

    onSystemNotice(msg) {


        const text = msg.message || 'System notice';
        this._showToast(`🛠️ ${escapeHtml(text)}`, null, null);
        this._showNotification('Anam backend', text, null);
    },

    // ── Brother push ──

    onBrotherDelta(msg) {
        // If viewing the brother conversation, stream to chat
        if (msg.conversation_id === this.conversationId) {
            if (!Chat.isStreaming) {
                Chat.onStreamStart({ identity: msg.identity });
            }
            Chat.onStreamDelta({ delta: msg.delta });
        }
    },

    _brotherToastShown: {},

    onBrotherMessage(msg) {
        // If viewing the brother conversation, finalize the message
        if (msg.conversation_id === this.conversationId) {
            if (Chat.isStreaming) {
                Chat.onStreamEnd({ full_content: msg.content });
            }
        } else {
            // Only show toast once per brother conversation (not per message)
            if (!this._brotherToastShown[msg.conversation_id]) {
                this._brotherToastShown[msg.conversation_id] = true;
                this.showBrotherToast(msg.identity, msg.conversation_id);
            }
        }
        if (typeof Sidebar !== 'undefined') Sidebar._refreshIfOpen();
    },

    onPlatformMessage(msg) {
        if (msg.conversation_id === this.conversationId) {
            const role = msg.role === 'user' ? 'user' : 'assistant';
            Chat.addMessage(
                role,
                msg.content,
                role === 'assistant' ? msg.identity : null,
                new Date().toISOString(),
                {
                    platform: msg.platform,
                    direction: msg.direction,
                    id: msg.message_id || null,
                }
            );
            return;
        }
        this._showToast(
            `<strong>${escapeHtml(msg.identity)}</strong> ${msg.role === 'user' ? 'received a message on' : 'replied on'} ${escapeHtml(msg.platform || 'platform')}`,
            msg.conversation_id,
            msg.identity
        );
        if (typeof Sidebar !== 'undefined') Sidebar._refreshIfOpen();
    },

    showBrotherToast(identity, conversationId) {
        this._showToast(
            `<span class="sidebar-badge b2b">B2B</span> <strong>${escapeHtml(identity)}</strong> is talking`,
            conversationId, identity
        );
    },

    _activeToasts: [],

    _showToast(html, conversationId, toastIdentity) {
        const toast = document.createElement('div');
        toast.className = 'autowake-toast';
        if (conversationId) toast.style.cursor = 'pointer';
        toast.innerHTML = html;

        // Stack below existing toasts
        const TOAST_GAP = 8;
        let topOffset = 12;
        for (const t of this._activeToasts) {
            topOffset += t.offsetHeight + TOAST_GAP;
        }
        toast.style.top = topOffset + 'px';
        this._activeToasts.push(toast);

        const self = this;
        let fadeTimer = null;
        let removeTimer = null;
        const dismiss = () => {
            clearTimeout(fadeTimer);
            clearTimeout(removeTimer);
            toast.classList.add('toast-leaving');
            setTimeout(() => {
                const idx = self._activeToasts.indexOf(toast);
                if (idx !== -1) self._activeToasts.splice(idx, 1);
                toast.remove();
                // Reflow remaining toasts
                let y = 12;
                for (const t of self._activeToasts) {
                    t.style.top = y + 'px';
                    y += t.offsetHeight + TOAST_GAP;
                }
            }, 200);
        };

        if (conversationId) {
            toast.addEventListener('click', () => {
                dismiss();
                Chat.parkAttachmentPreview();
                // Switch identity if toast is for a different one
                const identity = toastIdentity || this.currentIdentity;
                if (identity !== this.currentIdentity && this.identities[identity]) {
                    this.currentIdentity = identity;
                    this.applyIdentityTheme(identity);
                    this.updateIdentityLabel();
                    document.getElementById('message-input').placeholder = `Message ${identity}...`;
                }
                this.conversationId = conversationId;
                this.saveState();
                this.ws.send({
                    type: 'load_history',
                    identity: this.currentIdentity,
                    conversation_id: conversationId,
                });
            });
        }
        document.body.appendChild(toast);
        // toastSlideIn animation plays automatically via CSS
        fadeTimer = setTimeout(() => dismiss(), 4000);
    },

    // ── Wellness modal ──

    async openWellnessModal() {
        const overlay = document.getElementById('wellness-overlay');
        overlay.style.display = 'flex';

        // Trap focus inside the modal
        const modalCard = overlay.querySelector('.modal-card');
        if (modalCard) FocusTrap.trapFocus(modalCard);

        try {
            const res = await apiFetch('/api/rituals/wellness/today');
            const data = await res.json();
            const form = document.getElementById('wellness-form');
            form.energy.value = data.energy || '';
            form.mood.value = data.mood || '';
            form.pain.value = data.pain || '';
            form.spoons.value = data.spoons || '';
            form.sleep_hours.value = data.sleep_hours || '';
            form.sleep_quality.value = data.sleep_quality || '';
            form.water_oz.value = data.water_oz || '';
            form.soda_count.value = data.soda_count || '';
            form.notes.value = data.notes || '';
        } catch (err) {
            console.error('[Wellness] Failed to load:', err);
        }
    },

    closeWellnessModal() {
        FocusTrap.releaseFocus();
        document.getElementById('wellness-overlay').style.display = 'none';
    },

    async saveWellness() {
        const form = document.getElementById('wellness-form');
        const body = {};
        for (const field of form.elements) {
            if (field.name) body[field.name] = field.value;
        }

        try {
            const res = await apiFetch('/api/rituals/wellness/today', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            if (res.ok) {
                this.closeWellnessModal();
                // Brief flash on the submit button to confirm save when present.
                const btn = document.querySelector('#wellness-form button[type="submit"]');
                if (btn) {
                    btn.classList.add('saved');
                    setTimeout(() => btn.classList.remove('saved'), 1200);
                }
            }
        } catch (err) {
            console.error('[Wellness] Failed to save:', err);
        }
    },

    // ── Hub (home menu) ──

    openHub() {
        const overlay = document.getElementById('hub-overlay');
        overlay.style.display = 'flex';
    },

    closeHub() {
        document.getElementById('hub-overlay').style.display = 'none';
    },

    initFairyLights() {
        const container = document.getElementById('fairy-lights');
        if (!container || container.dataset.initialized === 'true') return;

        container.dataset.initialized = 'true';

        if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
            container.classList.add('ready');
            return;
        }

        const count = window.matchMedia && window.matchMedia('(max-width: 768px)').matches ? 14 : 22;
        const fragment = document.createDocumentFragment();
        for (let i = 0; i < count; i++) {
            const light = document.createElement('div');
            light.className = 'light';
            light.style.left = Math.random() * 100 + '%';
            light.style.top = Math.random() * 100 + '%';
            light.style.animationDelay = Math.random() * 3 + 's';
            light.style.animationDuration = (2 + Math.random() * 2) + 's';
            fragment.appendChild(light);
        }
        container.appendChild(fragment);
        container.classList.add('ready');
    },
};

// Boot
document.addEventListener('DOMContentLoaded', () => App.init());
