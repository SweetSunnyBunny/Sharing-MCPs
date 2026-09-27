                                                                                    
                                             
                                                                                                                                                                                
                                                                                                                  
                                                                                                    

const LiveCall = {
    button: null,
    session: null,
    status: null,
    identity: null,
    startedAt: 0,
    timer: null,
    maxTimer: null,
    muted: false,
    stopping: false,
    clientPromise: null,

    async init(button) {
        this.button = button;
        window.addEventListener('anam:identity-changed', () => {
            if (this.isActive() && App.currentIdentity !== this.identity) {
                this.stop({ message: 'Call ended when you switched boys.' });
            }
            this._applyAvailability();
        });
        try {
            const response = await apiFetch('/api/voice/live/status');
            if (response.ok) {
                this.status = await response.json();
            }
        } catch (err) {
            console.warn('[LiveCall] Availability check failed:', err);
        }
        this._applyAvailability();
    },

    _applyAvailability() {
        if (!this.button) return;
        const configured = this.status && this.status.configured_identities || [];
        const ready = !!(this.status && this.status.available
            && configured.includes(App.currentIdentity));
        this.button.disabled = !ready;
        this.button.classList.toggle('unavailable', !ready);
        this.button.title = ready
            ? `Start a live call with ${App.currentIdentity} (uses ElevenLabs minutes)`
            : `Live Call is not configured for ${App.currentIdentity} yet`;
    },

    isActive() {
        return !!this.session || !!(typeof Chat !== 'undefined' && Chat._liveCallMode);
    },

    async toggle() {
        if (this.isActive()) await this.stop();
        else await this.start();
    },

    async start() {
        if (this.isActive() || !this.button || this.button.disabled) return;
        try {
            await this._ensureClient();
        } catch (err) {
            console.error('[LiveCall] Client load failed:', err);
            App._showToast('Live Call could not load. Refresh and try once more.');
            return;
        }
        this.identity = App.currentIdentity;
        const callIdentity = this.identity;
        this.stopping = false;
        this.muted = false;
        if (Chat._voiceConversationMode) Chat._disableVoiceConversationMode();
        Chat._enableLiveCallMode();
        Chat._setVoiceConversationPhase('idle', 'Connecting securely to ElevenLabs…');

        try {
            const response = await apiFetch('/api/voice/live/token', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    identity: this.identity,
                    conversation_id: App.conversationId,
                }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(payload.error || 'Could not create call token');
            if (payload.conversation_id) {
                App.conversationId = payload.conversation_id;
                App.saveState();
            }

            const createdSession = await ElevenLabsClient.Conversation.startSession({
                conversationToken: payload.token,
                connectionType: 'webrtc',
                useWakeLock: true,
                onConnect: () => this._onConnected(payload.max_duration_seconds),
                onDisconnect: () => this._onDisconnected(),
                onError: (message) => this._onError(message),
                onModeChange: ({ mode }) => this._onModeChange(mode),
                onInterruption: () => {
                    if (!Chat.isStreaming) {
                        Chat._setVoiceConversationPhase('listening', 'I heard you—go ahead.');
                    }
                },
            });
            if (!Chat._liveCallMode || this.identity !== callIdentity) {
                await createdSession.endSession();
                return;
            }
            this.session = createdSession;
        } catch (err) {
            console.error('[LiveCall] Start failed:', err);
            App._showToast(err.message || 'Live Call could not start.');
            await this.stop({ silent: true });
        }
    },

    _ensureClient() {
        if (window.ElevenLabsClient && ElevenLabsClient.Conversation) {
            return Promise.resolve();
        }
        if (this.clientPromise) return this.clientPromise;
        this.clientPromise = new Promise((resolve, reject) => {
            const script = document.createElement('script');
            const version = window.__ANAM_ASSET_VERSION__ || '1';
            script.src = `/static/vendor/elevenlabs-client-1.14.1.iife.js?v=${version}`;
            script.onload = () => resolve();
            script.onerror = () => reject(new Error('ElevenLabs client failed to load'));
            document.head.appendChild(script);
        });
        return this.clientPromise;
    },

    _onConnected(maxDurationSeconds) {
        if (!Chat._liveCallMode || !this.identity) return;
        this.startedAt = Date.now();
        this._updateTimer();
        this.timer = window.setInterval(() => this._updateTimer(), 1000);
        const maxSeconds = Number(maxDurationSeconds
            || (this.status && this.status.max_duration_seconds)
            || 1800);
        this.maxTimer = window.setTimeout(() => {
            this.stop({ message: 'Thirty-minute call limit reached—ready for a fresh call whenever you are.' });
        }, maxSeconds * 1000);
        Chat._setVoiceConversationPhase('listening', 'Speak naturally. You can interrupt anytime.');
    },

    _onModeChange(mode) {
        if (!this.isActive()) return;
        if (mode === 'speaking') {
            Chat._setVoiceConversationPhase('speaking', 'Just speak to interrupt naturally.');
        } else if (Chat.isStreaming) {
            Chat._setVoiceConversationPhase('thinking', 'Your conversation is still live.');
        } else if (!this.muted) {
            Chat._setVoiceConversationPhase('listening', 'Speak naturally. You can interrupt anytime.');
        }
    },

    _onError(message) {
        console.error('[LiveCall] ElevenLabs error:', message);
        App._showToast(typeof message === 'string' ? message : 'The live call hit a snag.');
    },

    _onDisconnected() {
        if (!this.stopping) this.stop({ silent: true });
    },

    async toggleMute() {
        if (!this.session) return;
        this.muted = !this.muted;
        this.session.setMicMuted(this.muted);
        Chat._setVoiceConversationPhase(
            this.muted ? 'idle' : 'listening',
            this.muted ? 'Mic paused. Tap the orb to unmute.' : 'Mic is live. Speak naturally.'
        );
        if (Chat._voiceConversationOrb) {
            Chat._voiceConversationOrb.classList.toggle('mic-muted', this.muted);
        }
    },

    async stop(options = {}) {
        if (this.stopping) return;
        this.stopping = true;
        const session = this.session;
        this.session = null;
        window.clearInterval(this.timer);
        window.clearTimeout(this.maxTimer);
        this.timer = null;
        this.maxTimer = null;
        if (session) {
            try { await session.endSession(); } catch (err) {
                console.warn('[LiveCall] End session failed:', err);
            }
        }
        if (typeof Chat !== 'undefined') Chat._disableLiveCallMode();
        if (options.message) App._showToast(options.message);
        this.identity = null;
        this.startedAt = 0;
        this.muted = false;
        this.stopping = false;
    },

    _updateTimer() {
        const el = Chat._voiceConversationOrb
            && Chat._voiceConversationOrb.querySelector('.voice-live-timer');
        if (!el || !this.startedAt) return;
        const seconds = Math.max(0, Math.floor((Date.now() - this.startedAt) / 1000));
        const minutes = Math.floor(seconds / 60);
        el.textContent = `${minutes}:${String(seconds % 60).padStart(2, '0')} · ElevenLabs minutes`;
    },
};
