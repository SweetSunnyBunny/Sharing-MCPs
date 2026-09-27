/* Voice - dual playback system:
   - Play button on every message -> Kokoro (free, local)
   - Special <voice> messages -> ElevenLabs (premium, saved to disk)
*/
/* ANAM GUIDE: MESSAGE AUDIO PLAYER
   What: plays voice on chat messages — the little play button (free Kokoro voice) and the fancy ElevenLabs voice-note player with the waveform bars.
   Loaded by: index.html (the main chat page); chat.js calls Voice.createPlayButton / createElevenLabsPlayer when drawing messages.
   Edit here when: changing how play buttons or voice-note players look/behave, pause/resume, the fake waveform, or how text is cleaned before being spoken. */



const Voice = {
    currentAudio: null,
    currentBtn: null,
    currentWrapper: null,
    kokoroAvailable: false,
    _paused: false,
    _resumeCallback: null,
    _stateListeners: [],

    // ─── Seeded pseudo-waveform bars (#9) ──────────────────────────────
    // Real amplitude data isn't available for ElevenLabs/TTS audio without
    // paying for a decode pass on every render, so — same trick as voice-
    // memo apps everywhere — we fake a stable, good-looking waveform from a
    // seed (the message id) via a tiny LCG. Same seed always draws the same
    // bars, so they never jump around on re-render.
    _waveBarCache: new Map(),
    _WAVE_BAR_COUNT: 48,

    _getWaveBars(seed) {
        const key = String(seed || '');
        if (this._waveBarCache.has(key)) return this._waveBarCache.get(key);
        let s = 0;
        for (let i = 0; i < key.length; i++) s = (s * 31 + key.charCodeAt(i)) >>> 0;
        const bars = [];
        for (let i = 0; i < this._WAVE_BAR_COUNT; i++) {
            s = (s * 1664525 + 1013904223) >>> 0;
            const raw = (s >>> 16) / 65535; // 0..1
            const centre = Math.abs((i / (this._WAVE_BAR_COUNT - 1)) - 0.5) * 2; // 0 mid, 1 edges
            const h = 4 + Math.round((1 - centre * 0.55) * raw * 14); // 4-18px, bell-biased
            bars.push(h);
        }
        this._waveBarCache.set(key, bars);
        return bars;
    },

    async init() {
        // Check if Kokoro TTS is available on the backend
        try {
            const data = await fetchJson('/api/voice/status', { timeoutMs: 8000 });
            this.kokoroAvailable = data.kokoro === true;
        } catch {
            this.kokoroAvailable = false;
        }
    },

    onPlaybackStateChange(handler) {
        if (typeof handler === 'function') {
            this._stateListeners.push(handler);
        }
    },

    _emitPlaybackState(state, detail = {}) {
        this._stateListeners.forEach((handler) => {
            try {
                handler(state, detail);
            } catch (err) {
                console.warn('[Voice] Playback listener failed:', err);
            }
        });
    },

    /**
     * Create a play button with inline controls for any message (Kokoro — free).
     * Controls (progress bar, time, stop) appear when audio starts playing.
     */
    createPlayButton(text, identity) {
        const wrapper = document.createElement('div');
        wrapper.className = 'voice-player kokoro';

        const btn = document.createElement('button');
        btn.className = 'voice-play-btn kokoro';
        btn.title = 'Hear this message read aloud';
        btn.innerHTML = '&#9654;';

        // Controls — hidden until playback starts
        const controls = document.createElement('div');
        controls.className = 'kokoro-controls';

        const progressTrack = document.createElement('div');
        progressTrack.className = 'kokoro-progress-track';
        const progressFill = document.createElement('div');
        progressFill.className = 'kokoro-progress-fill';
        progressTrack.appendChild(progressFill);

        const time = document.createElement('span');
        time.className = 'kokoro-time';
        time.textContent = '0:00';

        const stopBtn = document.createElement('button');
        stopBtn.className = 'kokoro-stop-btn';
        stopBtn.innerHTML = '&#9724;';
        stopBtn.title = 'Stop';

        controls.appendChild(progressTrack);
        controls.appendChild(time);
        controls.appendChild(stopBtn);

        wrapper.appendChild(btn);
        wrapper.appendChild(controls);

        // Store refs on wrapper for later access
        wrapper._controls = controls;
        wrapper._progressFill = progressFill;
        wrapper._timeDisplay = time;
        wrapper._isStreaming = false;
        wrapper._streamState = null;
        wrapper._elapsedTime = 0;
        wrapper._playBtn = btn;
        wrapper._voiceText = text;
        wrapper._voiceIdentity = identity;

        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const samePlayback = this.currentWrapper === wrapper && (
                this.currentAudio || this._resumeCallback || this._paused
            );
            // If this wrapper is currently active, toggle pause/resume
            if (samePlayback) {
                if (this._paused) {
                    this.resume();
                } else {
                    this.pause();
                }
                return;
            }
            this.playKokoro(text, identity, btn, wrapper);
        });

        stopBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.stop();
        });

        // Click progress track to seek (single mode only)
        progressTrack.addEventListener('click', (e) => {
            e.stopPropagation();
            if (!wrapper._isStreaming && this.currentAudio && this.currentAudio.duration && this.currentWrapper === wrapper) {
                const rect = progressTrack.getBoundingClientRect();
                const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
                this.currentAudio.currentTime = pct * this.currentAudio.duration;
            }
        });

        return wrapper;
    },

    /**
     * Create a small v3 button that generates ElevenLabs audio on demand.
     * Audio is cached server-side by message ID — relistens are free.
     */

    /**
     * Create a special voice message player (ElevenLabs — premium).
     * Used when the identity chose to send a voice message.
     */
    createElevenLabsPlayer(audioUrl, identity, seedKey) {
        const wrapper = document.createElement('div');
        wrapper.className = 'voice-player elevenlabs';

        const btn = document.createElement('button');
        btn.className = 'voice-play-btn elevenlabs';
        btn.title = `Hear ${identity}'s voice`;
        btn.innerHTML = '&#9654;';

        const info = document.createElement('div');
        info.className = 'voice-info';

        const label = document.createElement('span');
        label.className = 'voice-label';
        label.textContent = `${identity}'s voice`;

        const sub = document.createElement('span');
        sub.className = 'voice-sublabel';
        sub.textContent = 'Tap to listen';

        info.appendChild(label);
        info.appendChild(sub);

        // Seeded pseudo-waveform (#9) — same shape every time for this
        // message (seeded on its id, falling back to the audio URL), fills
        // with the identity's accent color as playback (or a click-scrub)
        // moves through it. Doubles as the scrub control — no separate
        // linear progress bar needed.
        const bars = this._getWaveBars(seedKey || audioUrl);
        const waveEl = document.createElement('div');
        waveEl.className = 'voice-waveform';
        waveEl.setAttribute('role', 'slider');
        waveEl.setAttribute('tabindex', '0');
        waveEl.setAttribute('aria-label', 'Voice message position');
        waveEl.setAttribute('aria-valuemin', '0');
        waveEl.setAttribute('aria-valuemax', '100');
        waveEl.setAttribute('aria-valuenow', '0');
        const barEls = bars.map((h) => {
            const barEl = document.createElement('span');
            barEl.className = 'voice-wave-bar';
            barEl.style.height = h + 'px';
            waveEl.appendChild(barEl);
            return barEl;
        });

        wrapper.appendChild(btn);
        wrapper.appendChild(info);
        wrapper.appendChild(waveEl);

        wrapper._playBtn = btn;
        wrapper._subLabel = sub;
        wrapper._waveEl = waveEl;

        const setWaveProgress = (ratio) => {
            const clamped = Math.max(0, Math.min(1, ratio || 0));
            const playheadBar = Math.round(clamped * (barEls.length - 1));
            barEls.forEach((barEl, i) => barEl.classList.toggle('played', clamped > 0 && i <= playheadBar));
            waveEl.setAttribute('aria-valuenow', String(Math.round(clamped * 100)));
        };
        wrapper._setWaveProgress = setWaveProgress;

        const seekToRatio = (ratio) => {
            const audio = this.currentAudio;
            if (this.currentWrapper === wrapper && audio && audio.duration) {
                audio.currentTime = Math.max(0, Math.min(1, ratio)) * audio.duration;
                setWaveProgress(ratio);
            } else {
                // Not playing yet — start playback and seek once it's ready.
                wrapper._pendingSeekRatio = ratio;
                this.playElevenLabs(audioUrl, btn, label, sub, wrapper);
            }
        };

        waveEl.addEventListener('click', (e) => {
            e.stopPropagation();
            const rect = waveEl.getBoundingClientRect();
            seekToRatio(rect.width ? (e.clientX - rect.left) / rect.width : 0);
        });

        // Arrow-key seek (click-to-scrub's keyboard sibling)
        waveEl.addEventListener('keydown', (e) => {
            const audio = this.currentAudio;
            if (this.currentWrapper !== wrapper || !audio || !audio.duration) return;
            if (e.key === 'ArrowRight') {
                e.preventDefault();
                audio.currentTime = Math.min(audio.duration, audio.currentTime + 5);
            } else if (e.key === 'ArrowLeft') {
                e.preventDefault();
                audio.currentTime = Math.max(0, audio.currentTime - 5);
            }
        });

        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.playElevenLabs(audioUrl, btn, label, sub, wrapper);
        });

        return wrapper;
    },

    /**
     * Play ElevenLabs audio. Progress is shown by filling the seeded
     * waveform bars built in createElevenLabsPlayer (played-bars in the
     * identity accent color) rather than a separate linear progress bar.
     */
    playElevenLabs(url, btn, label, sub, wrapper) {
        if (this.currentAudio && this.currentBtn === btn) {
            this.stop();
            return;
        }
        this.stop();

        btn.classList.add('loading');
        btn.innerHTML = '&#8943;';
        sub.textContent = 'Loading...';
        this.currentBtn = btn;
        this.currentWrapper = wrapper;

        // Fetch audio as blob then play via object URL — direct Audio(url)
        // fails on Android Chrome due to service worker / security policy quirks.
        const audio = new Audio();
        audio.preload = 'auto';
        this.currentAudio = audio;

        const fetchOpts = {};
        if (/^https?:\/\//.test(url) && !url.includes(window.location.origin)) {
            audio.crossOrigin = 'anonymous';
            fetchOpts.mode = 'cors';
        }
        fetch(url, fetchOpts)
            .then(r => {
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                return r.blob();
            })
            .then(blob => {
                if (this.currentAudio !== audio) return; // user stopped before load finished
                const blobUrl = URL.createObjectURL(blob);
                audio._blobUrl = blobUrl; // store for cleanup
                audio.src = blobUrl;
            })
            .catch(err => {
                console.error('[Voice] Fetch failed:', err?.message || err, url);
                this.resetBtn(btn);
                sub.textContent = 'Failed to load';
                if (wrapper._setWaveProgress) wrapper._setWaveProgress(0);
                this.currentAudio = null;
                this.currentBtn = null;
                this.currentWrapper = null;
                this._emitPlaybackState('idle', { provider: 'elevenlabs', button: btn, url, reason: 'fetch_failed' });
            });

        audio.addEventListener('play', () => {
            btn.classList.remove('loading');
            btn.classList.add('playing');
            btn.innerHTML = '&#9724;';
            sub.textContent = 'Playing...';
            this._emitPlaybackState('playing', { provider: 'elevenlabs', button: btn, url });
        });

        audio.addEventListener('timeupdate', () => {
            if (audio.duration && wrapper._setWaveProgress) {
                wrapper._setWaveProgress(audio.currentTime / audio.duration);
            }
        });

        audio.addEventListener('ended', () => {
            this.resetBtn(btn);
            sub.textContent = 'Tap to replay';
            if (wrapper._setWaveProgress) wrapper._setWaveProgress(1);
            if (audio._blobUrl) URL.revokeObjectURL(audio._blobUrl);
            this.currentAudio = null;
            this.currentBtn = null;
            this.currentWrapper = null;
            this._emitPlaybackState('idle', { provider: 'elevenlabs', button: btn, url, reason: 'ended' });
        });

        audio.addEventListener('error', (e) => {
            const err = audio.error;
            console.error('[Voice] Audio load error:', err?.code, err?.message, url);
            this.resetBtn(btn);
            sub.textContent = 'Failed to load';
            if (audio._blobUrl) URL.revokeObjectURL(audio._blobUrl);
            this.currentAudio = null;
            this.currentBtn = null;
            this.currentWrapper = null;
            this._emitPlaybackState('idle', { provider: 'elevenlabs', button: btn, url, reason: 'error' });
        });

        // Play once the blob has loaded and audio is ready. If she clicked
        // a spot on the (not-yet-playing) waveform, honor that seek the
        // moment duration is known, before playback starts.
        audio.addEventListener('canplay', () => {
            if (this.currentAudio !== audio) return; // stale
            if (typeof wrapper._pendingSeekRatio === 'number' && audio.duration) {
                audio.currentTime = Math.max(0, Math.min(1, wrapper._pendingSeekRatio)) * audio.duration;
                wrapper._pendingSeekRatio = null;
            }
            audio.play().catch((err) => {
                console.error('[Voice] Playback failed:', err?.message || err, url);
                this.resetBtn(btn);
                sub.textContent = 'Playback failed';
                if (audio._blobUrl) URL.revokeObjectURL(audio._blobUrl);
                this.currentAudio = null;
                this.currentBtn = null;
                this.currentWrapper = null;
                this._emitPlaybackState('idle', { provider: 'elevenlabs', button: btn, url, reason: 'play_failed' });
            });
        }, { once: true });
    },

    _resetElevenLabsUi(wrapper, state = 'idle') {
        if (!wrapper) return;
        const btn = wrapper._playBtn;
        const sub = wrapper._subLabel;
        if (btn) this.resetBtn(btn);
        if (sub) {
            sub.textContent = state === 'error' ? 'Playback failed' : 'Tap to listen';
        }
        if (wrapper._setWaveProgress) wrapper._setWaveProgress(0);
    },

    /* ── Kokoro controls ── */

    _showControls(wrapper) {
        if (wrapper && wrapper._controls) {
            wrapper._controls.classList.add('visible');
        }
    },

    _hideControls(wrapper) {
        if (wrapper && wrapper._controls) {
            wrapper._controls.classList.remove('visible');
            wrapper._progressFill.style.width = '0%';
            wrapper._timeDisplay.textContent = '0:00';
            wrapper._elapsedTime = 0;
            wrapper._streamState = null;
        }
    },

    _updateProgress(wrapper, currentTime, duration) {
        if (!wrapper || !wrapper._progressFill) return;

        if (wrapper._isStreaming && wrapper._streamState) {
            const st = wrapper._streamState;
            // Estimate total if stream not done yet
            const total = st.streamDone ? st.totalChunks : Math.max(st.totalChunks, st.playedChunks + 2);
            const chunkProgress = duration > 0 ? currentTime / duration : 0;
            const overall = total > 0 ? (st.playedChunks + chunkProgress) / total : 0;
            wrapper._progressFill.style.width = Math.min(overall * 100, 100) + '%';

            const elapsed = wrapper._elapsedTime + currentTime;
            wrapper._timeDisplay.textContent = this._formatTime(elapsed);
        } else if (duration > 0) {
            wrapper._progressFill.style.width = (currentTime / duration * 100) + '%';
            wrapper._timeDisplay.textContent = this._formatTime(currentTime) + ' / ' + this._formatTime(duration);
        }
    },

    _formatTime(seconds) {
        const m = Math.floor(seconds / 60);
        const s = Math.floor(seconds % 60);
        return m + ':' + s.toString().padStart(2, '0');
    },

    /* ── Pause / Resume ── */

    pause() {
        if (!this.currentWrapper || this._paused) return;
        if (this.currentAudio) {
            this.currentAudio.pause();
        }
        this._paused = true;
        if (this.currentBtn) {
            this.currentBtn.classList.remove('playing');
            this.currentBtn.classList.add('paused');
            this.currentBtn.innerHTML = '&#9654;'; // show play icon to resume
        }
    },

    resume() {
        if (!this._paused) return;
        this._paused = false;
        if (this.currentBtn) {
            this.currentBtn.classList.remove('paused');
            this.currentBtn.classList.add('playing');
            this.currentBtn.innerHTML = '&#10074;&#10074;'; // pause bars
        }
        if (this.currentAudio && this.currentAudio.paused) {
            this.currentAudio.play();
        } else if (this._resumeCallback) {
            // Between streaming chunks — continue to next
            const cb = this._resumeCallback;
            this._resumeCallback = null;
            cb();
        }
    },

    /**
     * Flash a play button red, then restore it — the visible signal that a
     * voice request failed, so silence is never mistaken for success.
     */
    _flashBtnError(btn) {
        btn.title = 'Voice unavailable right now';
        btn.style.borderColor = '#c44';
        btn.style.color = '#c44';
        setTimeout(() => {
            btn.style.borderColor = '';
            btn.style.color = '';
            this.resetBtn(btn);
        }, 1500);
    },

    /* ── Kokoro playback ── */

    _prepareTextForTTS(text) {
        return (text || '').replace(/\s+/g, ' ').trim();
    },

    /**
     * Play arbitrary text in the identity's REAL ElevenLabs voice.
     *
     * The premium sibling of playKokoro: same button, same progress UI, one
     * whole clip instead of streamed chunks (so first audio is a beat slower,
     * and it's actually him). Used by in-chat Voice Conversation mode when
     * Owner's Settings voice switch says elevenlabs. Costs credits per call.
     * Returns true if playback started, false so the caller can fall back.
     */
    async playElevenLabsText(text, identity, btn, wrapper) {
        if (this.currentAudio && this.currentBtn === btn) {
            this.stop();
            return false;
        }
        this.stop();

        btn.classList.add('loading');
        btn.innerHTML = '&#8943;';
        this.currentBtn = btn;
        this.currentWrapper = wrapper;
        this._stopRequested = false;
        this._paused = false;
        this._resumeCallback = null;
        wrapper._elapsedTime = 0;
        wrapper._isStreaming = false;

        const ttsText = this._prepareTextForTTS(text);
        wrapper._voiceText = ttsText;

        try {
            const resp = await fetchWithTimeout('/api/voice/tts-eleven', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ identity, text: ttsText }),
                timeoutMs: 45000,
            });

            if (!resp.ok) {
                console.warn('[Voice] ElevenLabs TTS failed:', resp.status);
                this.resetBtn(btn);
                this._hideControls(wrapper);
                return false;
            }

            // A stop/barge-in while we were waiting on ElevenLabs — don't
            // start talking over whatever came next.
            if (this._stopRequested || this.currentBtn !== btn) {
                this.resetBtn(btn);
                this._hideControls(wrapper);
                return false;
            }

            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            this.playBlob(url, btn, wrapper, () => URL.revokeObjectURL(url), 'elevenlabs');
            return true;
        } catch (err) {
            console.error('[Voice] ElevenLabs playback error:', err);
            this.resetBtn(btn);
            this._hideControls(wrapper);
            return false;
        }
    },

    /**
     * Play via Kokoro — uses streaming endpoint for long text, regular for short.
     */
    async playKokoro(text, identity, btn, wrapper) {
        // If already playing this button, stop (shouldn't reach here due to pause logic, but safety)
        if (this.currentAudio && this.currentBtn === btn) {
            this.stop();
            return;
        }
        this.stop();

        btn.classList.add('loading');
        btn.innerHTML = '&#8943;';
        this.currentBtn = btn;
        this.currentWrapper = wrapper;
        this._stopRequested = false;
        this._paused = false;
        this._resumeCallback = null;
        wrapper._elapsedTime = 0;

        const ttsText = this._prepareTextForTTS(text);
        wrapper._voiceText = ttsText;

        // Use chunked streaming for longer text (>300 chars)
        if (ttsText.length > 300) {
            wrapper._isStreaming = true;
            return await this._playKokoroStreaming(ttsText, identity, btn, wrapper);
        } else {
            wrapper._isStreaming = false;
            return await this._playKokoroSingle(ttsText, identity, btn, wrapper);
        }
    },

    async _playKokoroSingle(text, identity, btn, wrapper) {
        try {
            const resp = await fetchWithTimeout('/api/voice/tts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ identity, text }),




                timeoutMs: 120000,
            });

            if (!resp.ok) {
                console.warn('[Voice] Kokoro TTS failed:', resp.status);
                this._flashBtnError(btn);
                return false;
            }

            this.kokoroAvailable = true;
            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            this.playBlob(url, btn, wrapper, () => URL.revokeObjectURL(url));
            return true;
        } catch (err) {
            console.error('[Voice] Kokoro playback error:', err);
            this.resetBtn(btn);
            this._hideControls(wrapper);
            return false;
        }
    },

    /**
     * Chunked streaming: fetch sentence-group WAV chunks via NDJSON,
     * start playing the first chunk immediately while later ones synthesize.
     */
    async _playKokoroStreaming(text, identity, btn, wrapper) {
        try {
            const resp = await fetchWithTimeout('/api/voice/tts-stream', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ identity, text }),


                timeoutMs: 120000,
            });

            if (!resp.ok) {
                console.warn('[Voice] Kokoro stream failed:', resp.status);
                this._flashBtnError(btn);
                return false;
            }

            this.kokoroAvailable = true;
            const reader = resp.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            const audioQueue = [];
            let playing = false;
            let streamDone = false;
            let receivedChunk = false;
            const self = this;

            // Stream state for progress tracking
            const streamState = {
                totalChunks: 0,
                playedChunks: 0,
                streamDone: false,
            };
            wrapper._streamState = streamState;
            wrapper._elapsedTime = 0;

            const playNext = () => {
                if (self._stopRequested) return;
                if (self._paused) {
                    // Save callback so resume() can continue
                    self._resumeCallback = playNext;
                    return;
                }
                if (audioQueue.length === 0) {
                    playing = false;
                    if (streamDone) {
                        self.resetBtn(btn);
                        self._hideControls(wrapper);
                        self._emitPlaybackState('idle', { provider: 'kokoro', button: btn, wrapper, reason: 'stream_complete' });
                    }
                    return;
                }
                playing = true;
                const blob = audioQueue.shift();
                const url = URL.createObjectURL(blob);
                const audio = new Audio(url);
                self.currentAudio = audio;

                audio.addEventListener('play', () => {
                    btn.classList.remove('loading');
                    btn.classList.add('playing');
                    btn.innerHTML = '&#10074;&#10074;';
                    self._showControls(wrapper);
                    self._emitPlaybackState('playing', { provider: 'kokoro', button: btn, wrapper });
                });

                audio.addEventListener('timeupdate', () => {
                    self._updateProgress(wrapper, audio.currentTime, audio.duration);
                });

                audio.addEventListener('ended', () => {
                    if (audio.duration) {
                        wrapper._elapsedTime += audio.duration;
                    }
                    streamState.playedChunks++;
                    URL.revokeObjectURL(url);
                    playNext();
                });
                audio.addEventListener('error', () => {
                    streamState.playedChunks++;
                    URL.revokeObjectURL(url);
                    playNext();
                });
                audio.play().catch(() => {
                    URL.revokeObjectURL(url);
                    playNext();
                });
            };

            // Read NDJSON stream
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                if (self._stopRequested) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop() || '';

                for (const line of lines) {
                    if (!line.trim()) continue;
                    try {
                        const chunk = JSON.parse(line);
                        // Decode base64 WAV
                        const binary = atob(chunk.audio);
                        const bytes = new Uint8Array(binary.length);
                        for (let i = 0; i < binary.length; i++) {
                            bytes[i] = binary.charCodeAt(i);
                        }
                        const blob = new Blob([bytes], { type: 'audio/wav' });
                        audioQueue.push(blob);
                        streamState.totalChunks++;
                        receivedChunk = true;

                        // Start playing as soon as first chunk arrives
                        if (!playing) {
                            playNext();
                        }
                    } catch (e) {
                        console.warn('[Voice] Chunk parse error:', e);
                    }
                }
            }

            streamDone = true;
            streamState.streamDone = true;
            // If nothing is playing (all chunks arrived and finished), clean up
            if (!playing && audioQueue.length === 0) {






                if (!receivedChunk) {
                    console.warn('[Voice] Kokoro stream returned no audio');
                    this._flashBtnError(btn);
                } else {
                    this.resetBtn(btn);
                }
                this._hideControls(wrapper);
                this._emitPlaybackState('idle', { provider: 'kokoro', button: btn, wrapper, reason: 'stream_empty' });
            }

            return receivedChunk;

        } catch (err) {
            console.error('[Voice] Kokoro streaming error:', err);
            this.resetBtn(btn);
            this._hideControls(wrapper);
            this._emitPlaybackState('idle', { provider: 'kokoro', button: btn, wrapper, reason: 'stream_error' });
            return false;
        }
    },

    /**
     * Shared audio playback for Kokoro single-shot (blob URLs).
     */
    playBlob(url, btn, wrapper, onCleanup, provider = 'kokoro') {
        const audio = new Audio(url);
        this.currentAudio = audio;
        this._paused = false;

        audio.addEventListener('play', () => {
            btn.classList.remove('loading');
            btn.classList.add('playing');
            btn.innerHTML = '&#10074;&#10074;'; // pause bars
            this._showControls(wrapper);
            this._emitPlaybackState('playing', { provider, button: btn, wrapper });
        });

        audio.addEventListener('timeupdate', () => {
            this._updateProgress(wrapper, audio.currentTime, audio.duration);
        });

        audio.addEventListener('ended', () => {
            this.resetBtn(btn);
            this._hideControls(wrapper);
            if (onCleanup) onCleanup();
            this._emitPlaybackState('idle', { provider, button: btn, wrapper, reason: 'ended' });
        });

        audio.addEventListener('error', () => {
            this.resetBtn(btn);
            this._hideControls(wrapper);
            if (onCleanup) onCleanup();
            this._emitPlaybackState('idle', { provider, button: btn, wrapper, reason: 'error' });
        });

        audio.play().catch(() => {
            this.resetBtn(btn);
            this._hideControls(wrapper);
            this._emitPlaybackState('idle', { provider, button: btn, wrapper, reason: 'play_failed' });
        });
    },

    stop() {
        this._stopRequested = true;
        this._resumeCallback = null;
        if (this.currentAudio) {
            this.currentAudio.pause();
            this.currentAudio.currentTime = 0;
            if (this.currentAudio._blobUrl) URL.revokeObjectURL(this.currentAudio._blobUrl);
            this.currentAudio = null;
        }
        if (this.currentBtn) {
            this.resetBtn(this.currentBtn);
        }
        if (this.currentWrapper) {
            if (this.currentWrapper.classList.contains('kokoro')) {
                this._hideControls(this.currentWrapper);
            } else if (this.currentWrapper.classList.contains('elevenlabs')) {
                this._resetElevenLabsUi(this.currentWrapper);
                // Clear a scrub-before-load seek so it can't wrongly fire on
                // a later, unrelated play of the same wrapper.
                this.currentWrapper._pendingSeekRatio = null;
            }
            this.currentWrapper = null;
        }
        this._paused = false;
        this._emitPlaybackState('idle', { reason: 'stopped' });
    },

    resetBtn(btn) {
        if (btn) {
            btn.classList.remove('loading', 'playing', 'paused');
            btn.innerHTML = '&#9654;';
        }
        this.currentAudio = null;
        this.currentBtn = null;
        this._paused = false;
    },
};
