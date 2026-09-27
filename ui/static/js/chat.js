/* Chat UI — streaming, bubbles, scrolling, tool cards */

const Chat = {
    container: null,
    input: null,
    sendBtn: null,
    currentStreamEl: null,
    currentStreamContent: '',
    isStreaming: false,
    pendingImages: [], // [{file, previewUrl}, ...]
    pendingDocuments: [], // [{file}, ...]
    pendingAudio: [], // [{file, previewUrl}, ...]
    _lostStreamContent: null, // saved content from a stream interrupted by disconnect
    _lostStreamIdentity: null,
    _lostStreamPending: false,
    _lostStreamConversationId: null,

    _inputHistory: [],
    _historyIndex: -1,
    _historyDraft: '',

    _isNearBottom: true,
    _hasNewMessages: false,
    _newMessageCount: 0,
    _newMessagesDividerEl: null,
    _scrollPill: null,
    _thinkingEl: null,
    _currentThinkingCard: null,
    _thinkingContent: '',
    _lastFailedMessage: null,
    _lastSentMessage: null,
    _historyOffset: 0,
    _streamRenderFrame: null,
    _streamLastFrameAt: 0,
    _streamRevealBudget: 0,
    _lastStreamRenderLen: 0,
    _streamMarkdownAt: 0,
    _streamMarkdownDirty: false,
    _streamMarkdownTimer: null,
    _memoryDraft: null,
    _voiceBaseInput: '',
    _voiceCommittedTranscript: '',
    _voiceSessionFinalTranscript: '',
    _voiceSessionInterimTranscript: '',
    _voiceConversationMode: false,
    _liveCallMode: false,
    _voiceConversationPhase: 'off',
    _voiceAutoSendTimer: null,
    _voiceAutoSendDelayMs: 1200,
    _voiceConversationButton: null,
    _voiceConversationOrb: null,
    _voiceConversationStatusEl: null,
    _voiceConversationHintEl: null,
    _voiceConversationMotesEl: null,
    _voiceConversationShouldResume: false,
    _voiceConversationPendingAutoplay: false,
    _suppressAutoScroll: false,
    _scrollFrame: null,
    _viewConversationId: null,
    _readingPosition: null,
    _restoringPosition: false,
    _lastScrollTop: 0,

    // Slash commands
    _slashDropdown: null,
    _slashCommands: [
        { name: 'hub', description: 'Open the Home Hub', client: true },
        { name: 'voice', description: 'Toggle voice conversation mode', client: true },
        { name: 'new', description: 'Start a new conversation', client: true },
        { name: 'search', description: 'Search messages', client: true },
        { name: 'switch', description: 'Switch identity (e.g. /switch Avery)', client: true, hasArg: true },
        { name: 'help', description: 'Show available commands', client: true },
        { name: 'timer', description: 'Set a timer (e.g. /timer 2h check in)', server: true, hasArg: true },
        { name: 'status', description: 'Set hub status (e.g. /status 🌸 feeling good)', server: true, hasArg: true },
    ],
    _slashSelectedIndex: 0,
    _replyToId: null,
    _replyToPreview: null,

    // #22 interleaved tool timeline pill — one quiet "N tools" pill per
    // turn instead of a stack of individual tool-call cards. Reset fresh
    // at the start of every stream (see onStreamStart).
    _currentToolPill: null,
    _currentToolPillBody: null,
    _currentToolPillCount: 0,
    _toolPillEntries: null,
    _toolPillUserToggled: false,
    _toolPillCollapseTimer: null,

    _buildContextNoticeEl(notice) {
        if (!notice || (!notice.title && !notice.detail)) return null;
        const wrapper = document.createElement('div');
        wrapper.className = `context-notice ${notice.kind || 'info'}`;

        const title = document.createElement('div');
        title.className = 'context-notice-title';
        title.textContent = notice.title || 'Context notice';
        wrapper.appendChild(title);

        if (notice.detail) {
            const detail = document.createElement('div');
            detail.className = 'context-notice-detail';
            detail.textContent = notice.detail;
            wrapper.appendChild(detail);
        }

        return wrapper;
    },

    isTerminalToolName(toolName) {
        const normalized = (toolName || '').trim().toLowerCase();
        return normalized === 'terminal_execute'
            || normalized === 'shell'
            || normalized === 'bash'
            || normalized === 'command_execution';
    },










    isSilentToolName(toolName) {
        const normalized = (toolName || '').trim().toLowerCase();
        return normalized === 'read'
            || normalized === 'glob'
            || normalized === 'grep';
    },

    // =========================================================================
    // CHAT-HEADER MINI-ORB — the ACTIVE boy's inner weather next to his name.
    // The orb CSS primitive lives in main.css; these rendering helpers are a
    // twin of the ones in hub.js — keep them in sync. No hub WS event exists
    // for orb changes, so we refresh on identity switches and poll gently
    // every few minutes while the tab is visible.
    // =========================================================================
    _ORB_SHAPES: ['solid', 'ring', 'halo', 'sphere', 'crescent', 'pulse', 'cluster', 'ember', 'spire', 'fracture'],
    _ORB_MOTIONS: ['breathing', 'warble', 'spin', 'drift', 'still', 'slow-drift', 'hold-steady', 'fast-flicker', 'surge', 'tremor'],
    _ORB_INTENSITIES: ['dull', 'normal', 'bright', 'neon'],
    _ORB_SMALL_SHAPES: ['solid', 'ember', 'ring'], // %-based, still legible tiny
    _headerOrbCache: null,   // { at: ms, orbs: {identity: orb} }
    _headerOrbTimer: null,
    _headerOrbFetching: null,

    _orbHexToRgb(hex) {
        const m = /^#([0-9a-fA-F]{6})$/.exec(String(hex || '').trim());
        if (!m) return null;
        const n = parseInt(m[1], 16);
        return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
    },
    _orbMix(a, b, t) {
        return [0, 1, 2].map(i => Math.round(a[i] + (b[i] - a[i]) * t));
    },
    _orbRgba(rgb, alpha) {
        return alpha == null
            ? `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`
            : `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${alpha})`;
    },
    _orbBlendIsDark(blend, rgb) {
        if (blend === 'dim' || blend === 'black') return true;
        if (!rgb) return false;
        return (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255 < 0.09;
    },

    // Paint an orb element from an orb payload. Colors stay free-hex:
    // highlight/edge shades are derived here and set as inline custom props —
    // the registered props in main.css crossfade color changes (~2.4s).
    // Below mantel size, shapes collapse to sphere/ember (fracture → tremor
    // + dull — Friend's collapse rules).
    _applyOrbTo(orbEl, orb, size = 'mantel', sizeClass = '') {
        if (!orbEl) return;
        if (!orbEl.querySelector('.cc-orb-core')) {
            orbEl.innerHTML = '<span class="cc-orb-core"></span>';
        }
        const color = (orb && /^#[0-9a-fA-F]{6}$/.test(orb.color || '')) ? orb.color : '#E8B84B';
        let shape = (orb && this._ORB_SHAPES.includes(orb.shape)) ? orb.shape : 'solid';
        if (shape === 'sphere') shape = 'solid';
        let motion = (orb && this._ORB_MOTIONS.includes(orb.motion)) ? orb.motion : 'breathing';
        let intensity = (orb && this._ORB_INTENSITIES.includes(orb.intensity)) ? orb.intensity : 'normal';
        if (size !== 'mantel') {
            if (shape === 'fracture') {
                shape = 'solid';
                motion = 'tremor';
                if (intensity === 'normal') intensity = 'dull';
            } else if (!this._ORB_SMALL_SHAPES.includes(shape)) {
                shape = 'solid';
            }
        }

        const rgb = this._orbHexToRgb(color) || [232, 184, 75];
        const plum = [58, 31, 46]; // #3a1f2e — the hearth's dark edge
        const highlight = this._orbRgba(this._orbMix(rgb, [255, 255, 255], 0.7), 0.75);
        let edge = this._orbRgba(this._orbMix(rgb, plum, 0.4));
        let outer = color;
        let swirl = 'transparent';
        let darkBlend = false;
        const blendRaw = (orb && typeof orb.blend === 'string') ? orb.blend.trim().toLowerCase() : '';
        if (blendRaw) {
            const blendRgb = this._orbHexToRgb(blendRaw);
            if (this._orbBlendIsDark(blendRaw, blendRgb)) {
                // dim/black blend = vignette: darkened edge, hushed halo — never a glow
                darkBlend = true;
                edge = 'rgba(18, 18, 26, 0.85)';
                outer = '#08080c';
                swirl = 'rgba(40, 40, 55, 0.4)';
            } else if (blendRgb) {
                // the second color takes the OUTER light and tints the inner swirl
                outer = blendRaw;
                swirl = this._orbRgba(blendRgb, 0.4);
            }
        }

        const cls = ['cc-orb'];
        if (sizeClass) cls.push(sizeClass);
        cls.push(`orb-shape-${shape}`, `orb-motion-${motion}`, `orb-intensity-${intensity}`);
        if (darkBlend) cls.push('orb-blend-dark');

        // Browsers without registered custom props can't interpolate colors —
        // give them a gentle opacity dip instead of a hard snap.
        const sig = `${color}|${blendRaw}`;
        const changed = !!orbEl.dataset.orbSig && orbEl.dataset.orbSig !== sig;
        orbEl.dataset.orbSig = sig;
        const reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (changed && !('CSSPropertyRule' in window) && !reduced) {
            cls.push('cc-orb-xfade');
            clearTimeout(orbEl._orbXfadeTimer);
            orbEl._orbXfadeTimer = setTimeout(() => orbEl.classList.remove('cc-orb-xfade'), 2400);
        }

        orbEl.className = cls.join(' ');
        orbEl.style.setProperty('--orb-color', color);
        orbEl.style.setProperty('--orb-highlight', highlight);
        orbEl.style.setProperty('--orb-edge', edge);
        orbEl.style.setProperty('--orb-outer', outer);
        orbEl.style.setProperty('--orb-swirl', swirl);
    },

    _initHeaderOrb() {
        const el = document.getElementById('header-orb');
        if (!el) return;
        // App re-dispatches this on every identity switch (and once at boot)
        window.addEventListener('anam:identity-changed', () => this._refreshHeaderOrb());
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden) this._refreshHeaderOrb();
        });
        this._headerOrbTimer = setInterval(() => {
            if (!document.hidden) this._refreshHeaderOrb();
        }, 180000);
        this._refreshHeaderOrb();
    },

    async _refreshHeaderOrb() {
        const el = document.getElementById('header-orb');
        if (!el) return;
        const fresh = this._headerOrbCache && (Date.now() - this._headerOrbCache.at) < 150000;
        if (!fresh) {
            if (!this._headerOrbFetching) {
                this._headerOrbFetching = fetchJson('/api/hub/orb', { timeoutMs: 8000 })
                    .then((data) => {
                        const orbs = (data && data.orbs && typeof data.orbs === 'object') ? data.orbs : {};
                        this._headerOrbCache = { at: Date.now(), orbs };
                    })
                    .catch(() => { /* keep whatever we had — the orb just stays put */ })
                    .finally(() => { this._headerOrbFetching = null; });
            }
            await this._headerOrbFetching;
        }
        this._renderHeaderOrb();
    },

    _renderHeaderOrb() {
        const el = document.getElementById('header-orb');
        if (!el) return;
        const identity = (typeof App !== 'undefined' && App.currentIdentity) ? String(App.currentIdentity) : '';
        const orbs = (this._headerOrbCache && this._headerOrbCache.orbs) || {};
        const orb = identity && orbs[identity.toLowerCase()] && typeof orbs[identity.toLowerCase()] === 'object'
            ? orbs[identity.toLowerCase()]
            : null;
        if (!orb) {
            el.style.display = 'none'; // no orb yet — stay out of the header
            return;
        }
        try {
            this._applyOrbTo(el, orb, 'band', 'cc-orb--header');
            const feeling = (typeof orb.feeling === 'string') ? orb.feeling.trim() : '';
            el.title = feeling ? `${identity} — ${feeling}` : `${identity}'s inner weather`;
            el.style.display = '';
        } catch (e) {
            el.style.display = 'none';
        }
    },

    init() {
        this.container = document.getElementById('messages');
        this.input = document.getElementById('message-input');
        this.sendBtn = document.getElementById('send-btn');

        // Tool-result events arrive with tool_use_id but no tool_name, so we
        // need a way to recognize when a result came from a silent tool we
        // hid the card for. Track those IDs here; populated in onToolStart,
        // consumed in onToolInput/onToolResult, cleared on each new turn.
        this.silentToolIds = new Set();

        // The active boy's inner-weather orb beside his name in the header
        this._initHeaderOrb();

        // Scroll indicator pill
        this._scrollPill = document.createElement('button');
        this._scrollPill.className = 'scroll-bottom-pill';
        this._scrollPill.innerHTML = '&#8595;';
        this._scrollPill.addEventListener('click', () => {
            this._hasNewMessages = false;
            this._newMessageCount = 0;
            this._removeNewMessagesDivider();
            this.scrollToBottom(true);
            this._updateScrollPill();
        });
        this.container.parentElement.appendChild(this._scrollPill);

        // Slash command dropdown
        this._slashDropdown = document.createElement('div');
        this._slashDropdown.className = 'slash-dropdown';
        this._slashDropdown.style.display = 'none';
        this.input.parentElement.style.position = 'relative';
        this.input.parentElement.insertBefore(this._slashDropdown, this.input);

        this.input.addEventListener('input', () => this._handleSlashInput());

        // Clean copy — strip non-message content from clipboard selection.
        // On mobile, selection handles can extend outside message bubbles despite
        // user-select:none, grabbing timestamps, reaction buttons, headers, etc.
        // This intercepts copy and extracts only .message-content text.
        document.addEventListener('copy', (e) => {
            const sel = window.getSelection();
            if (!sel || sel.isCollapsed) return;

            const range = sel.getRangeAt(0);
                                                                              
                                                                               
                                                                            
                                                                                  
                                                                                   
                                                                                
                                                                               
                                                                                
                                                                               

                                                                                    
            const bubbles = this.container.querySelectorAll('.message-content');
            const parts = [];
            for (const mc of bubbles) {
                if (sel.containsNode(mc, true)) {
                    // Get only the selected portion within this message content
                    const mcRange = document.createRange();
                    mcRange.selectNodeContents(mc);

                    // Clamp to selection boundaries
                    if (range.compareBoundaryPoints(Range.START_TO_START, mcRange) > 0) {
                        mcRange.setStart(range.startContainer, range.startOffset);
                    }
                    if (range.compareBoundaryPoints(Range.END_TO_END, mcRange) < 0) {
                        mcRange.setEnd(range.endContainer, range.endOffset);
                    }

                    const text = mcRange.toString().trim();
                    if (text) parts.push(text);
                }
            }

            if (parts.length > 0) {
                e.preventDefault();
                e.clipboardData.setData('text/plain', parts.join('\n\n'));
            }
        });

        this._initReadingControls();

        // Send button — use both click and touchend for mobile reliability
        this.sendBtn.addEventListener('click', (e) => {
            e.preventDefault();
            this.sendMessage();
        });
        this.sendBtn.addEventListener('touchend', (e) => {
            e.preventDefault();
            this.sendMessage();
        });

        // Enter key behavior:
        // Mobile: Enter = new line (paragraphs), send via button
        // Desktop: Enter = send, Shift+Enter = new line
        const isTouchDevice = 'ontouchstart' in window || navigator.maxTouchPoints > 0;
        this.input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                if (isTouchDevice) return; // let Enter add newline on mobile
                if (!e.shiftKey) {
                    e.preventDefault();
                    this.sendMessage();
                }
            }
            // Input history — arrow up/down like terminal shells
            if (e.key === 'ArrowUp' && this.input.selectionStart === 0 && this._inputHistory.length > 0) {
                e.preventDefault();
                if (this._historyIndex === -1) {
                    this._historyDraft = this.input.value;
                }
                this._historyIndex = Math.min(this._historyIndex + 1, this._inputHistory.length - 1);
                this.input.value = this._inputHistory[this._inputHistory.length - 1 - this._historyIndex];
                this.input.dispatchEvent(new Event('input'));
            }
            if (e.key === 'ArrowDown' && this._historyIndex >= 0) {
                e.preventDefault();
                this._historyIndex--;
                if (this._historyIndex < 0) {
                    this.input.value = this._historyDraft;
                } else {
                    this.input.value = this._inputHistory[this._inputHistory.length - 1 - this._historyIndex];
                }
                this.input.dispatchEvent(new Event('input'));
            }
        });

        // Auto-resize textarea + persist draft per conversation
        this.input.addEventListener('input', () => {
            this.input.style.height = 'auto';
            this.input.style.height = Math.min(this.input.scrollHeight, 120) + 'px';
            this.saveDraft(App.conversationId);
        });

                                                                                

                                                                           
                                                                           
                                                                       
        const attachBtn = document.getElementById('attach-btn');
        const fileInput = document.getElementById('file-input');
        const previewRemove = document.getElementById('image-preview-remove');

        attachBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            e.preventDefault();
            this.openUniversalFilePicker(fileInput);
        });

        fileInput.addEventListener('change', (e) => {
            if (e.target.files && e.target.files.length > 0) {
                for (const file of e.target.files) {
                    this.onFileSelected(file);
                }
                fileInput.value = '';
            }
        });
        previewRemove.addEventListener('click', () => this.clearImagePreview());
        this._initGifPicker();
        this._initEmojiPicker();
        this._initMemoryModal();

        // Listen to WebSocket events
        App.ws.on('stream_start', (msg) => this.onStreamStart(msg));
        App.ws.on('stream_delta', (msg) => this.onStreamDelta(msg));
        App.ws.on('stream_reset', () => this.onStreamReset());
        App.ws.on('stream_end', (msg) => this.onStreamEnd(msg));
        App.ws.on('tool_use_start', (msg) => this.onToolStart(msg));
        App.ws.on('tool_input', (msg) => this.onToolInput(msg));
        App.ws.on('tool_result', (msg) => this.onToolResult(msg));
        App.ws.on('content_block_stop', () => this.onContentBlockStop());
        App.ws.on('thinking_start', () => this.onThinkingStart());
        App.ws.on('thinking_delta', (msg) => this.onThinkingDelta(msg));
        App.ws.on('voice_message', (msg) => this.onVoiceMessage(msg));
        App.ws.on('response_images', (msg) => this.onResponseImages(msg));
        App.ws.on('response_documents', (msg) => this.onResponseDocuments(msg));
        App.ws.on('history', (msg) => this.loadHistory(msg));
        App.ws.on('history_older', (msg) => this.onHistoryOlder(msg));
        App.ws.on('ai_reaction', (msg) => this.onAiReaction(msg));
        App.ws.on('terminal_output', (msg) => this.onTerminalOutput(msg));
        App.ws.on('regenerate_ready', (msg) => this.onRegenerateReady(msg));
        App.ws.on('context_usage', (msg) => this.onContextUsage(msg));
        // #18 compaction hygiene: a quiet honest toast when the CLI
        // auto-compacted mid-session — never a hard interruption.
        App.ws.on('compaction_notice', (msg) => App._showToast(`💭 ${escapeHtml(msg.message || 'Context compacted — continuing')}`));
        App.ws.on('echo_message', (msg) => this.onEchoMessage(msg));
        App.ws.on('live_call_user', (msg) => this.onLiveCallUser(msg));
        App.ws.on('approval_required', (msg) => this.showApprovalRequest(msg));
        App.ws.on('message_injected', (msg) => this.onMessageInjected(msg));
        App.ws.on('error', (msg) => this.showError(msg.message || 'Unknown error'));

        // Pack-night fan-out events
        App.ws.on('pack_night_round_start', (msg) => this.onPackNightRoundStart(msg));
        App.ws.on('pack_night_user_saved', (msg) => this.onPackNightUserSaved(msg));
        App.ws.on('pack_night_turn_start', (msg) => this.onPackNightTurnStart(msg));
        App.ws.on('pack_night_pass', (msg) => this.onPackNightPass(msg));
        App.ws.on('pack_night_turn_saved', (msg) => this.onPackNightTurnSaved(msg));
        App.ws.on('pack_night_error', (msg) => this.onPackNightError(msg));
        App.ws.on('pack_night_round_end', (msg) => this.onPackNightRoundEnd(msg));

        // Clipboard image paste
        document.addEventListener('paste', (e) => {
            const items = e.clipboardData && e.clipboardData.items;
            if (!items) return;
            for (const item of items) {
                if (item.type.startsWith('image/')) {
                    e.preventDefault();
                    const file = item.getAsFile();
                    if (file) {
                        this.onFileSelected(file);
                        App._showToast('📷 Image pasted');
                    }
                }
            }
        });

        // Voice input (Web Speech API)
        this._initVoiceInput();
        // Press-and-hold voice note mic (MediaRecorder → audio attachment)
        this._initVoiceNoteMic();
        if (typeof Voice !== 'undefined' && typeof Voice.onPlaybackStateChange === 'function') {
            Voice.onPlaybackStateChange((state, detail) => this._handleVoicePlaybackState(state, detail));
        }

        // In-conversation message search (Ctrl+F)
        this._initMessageSearch();
        this._initGallery();

        // Clear streaming state on page unload to prevent stale heartbeat skips.
        // Do not clear attachments here: completed uploads are intentionally
        // persisted so a mobile tab discard/reload can restore their chips.
        // The browser releases blob URLs and File objects with the page.
        window.addEventListener('beforeunload', () => {
            this._cancelStreamRenderer();
            this.isStreaming = false;
            if (App.ws) App.ws.streaming = false;
        });

        // Drag and drop file upload
        const dropTarget = document.querySelector('.chat-area');
        if (dropTarget) {
            let dragCounter = 0;

            dropTarget.addEventListener('dragenter', (e) => {
                e.preventDefault();
                e.stopPropagation();
                if (e.dataTransfer.types.includes('Files')) {
                    dragCounter++;
                    dropTarget.classList.add('drag-over');
                }
            });

            dropTarget.addEventListener('dragover', (e) => {
                e.preventDefault();
                e.stopPropagation();
            });

            dropTarget.addEventListener('dragleave', (e) => {
                e.preventDefault();
                e.stopPropagation();
                dragCounter--;
                if (dragCounter <= 0) {
                    dragCounter = 0;
                    dropTarget.classList.remove('drag-over');
                }
            });

            dropTarget.addEventListener('drop', (e) => {
                e.preventDefault();
                e.stopPropagation();
                dragCounter = 0;
                dropTarget.classList.remove('drag-over');
                const files = e.dataTransfer.files;
                if (files.length) {
                    for (const file of files) {
                        this.onFileSelected(file);
                    }
                }
            });
        }
    },

    async openUniversalFilePicker(fallbackInput) {
        if (typeof window.showOpenFilePicker === 'function') {
            try {
                const handles = await window.showOpenFilePicker({
                    multiple: true,
                    excludeAcceptAllOption: false,
                    types: [{
                        description: 'Images, audio, and documents',
                        accept: {
                            'image/*': ['.jpg', '.jpeg', '.png', '.gif', '.webp'],
                            'audio/*': ['.mp3', '.wav', '.m4a', '.ogg', '.oga', '.opus', '.flac', '.webm', '.aac'],
                            'application/pdf': ['.pdf'],
                            'text/plain': ['.txt', '.md', '.csv', '.py', '.js', '.skill'],
                            'application/json': ['.json'],
                            'application/msword': ['.doc'],
                            'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
                            'application/vnd.oasis.opendocument.text': ['.odt'],
                            'application/vnd.openxmlformats-officedocument.presentationml.presentation': ['.pptx'],
                            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'],
                            'application/zip': ['.zip'],
                        },
                    }],
                });
                for (const handle of handles) {
                    this.onFileSelected(await handle.getFile());
                }
                return;
            } catch (err) {
                // Closing the picker is not a failure and must not immediately
                // open a second, different Android chooser.
                if (err && err.name === 'AbortError') return;
                console.warn('showOpenFilePicker failed; falling back to file input', err);
            }
        }
        fallbackInput.click();
    },

    onFileSelected(file) {
        const ext = file.name.split('.').pop().toLowerCase();
        const imageExts = ['jpg', 'jpeg', 'png', 'gif', 'webp'];
        const audioExts = ['mp3', 'wav', 'm4a', 'ogg', 'oga', 'opus', 'flac', 'webm', 'aac'];
        const documentExts = [
            'pdf', 'txt', 'md', 'csv', 'json', 'doc', 'docx', 'odt',
            'pptx', 'xlsx', 'py', 'js', 'zip', 'skill',
            'mp4', 'mov', 'webm', 'mkv', 'avi',
        ];
        const isImage = imageExts.includes(ext);
        const isVideoMime = !!(file.type && file.type.startsWith('video/'));
        const isAudio = !isVideoMime
            && (audioExts.includes(ext) || (file.type && file.type.startsWith('audio/')));
        const isDocument = documentExts.includes(ext);
        if (!isImage && !isAudio && !isDocument) {
            this.showError(`.${escapeHtml(ext || 'unknown')} files aren't supported here yet`, true);
            return;
        }

        const maxBytes = (isImage ? 10 : isAudio ? 50 : 100) * 1024 * 1024;
        if (file.size > maxBytes) {
            const maxMb = Math.round(maxBytes / 1024 / 1024);
            this.showError(`${escapeHtml(file.name)} is too large (max ${maxMb} MB)`, true);
            return;
        }

        // #17 upload-on-attach: the upload fires right here, the instant a
        // file is picked, instead of waiting for Send. Her primary path is
        // picking a photo on Android, where a suspended tab can wipe the
        // raw File still sitting in memory before she gets back to hit
        // send — starting the upload now (and persisting only the
        // {url, id} chip once it lands) means the picture survives even if
        // the tab dies underneath her.
        const localId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

        const owner = {
            ownerIdentity: App.currentIdentity,
            ownerConversationId: App.conversationId || null,
        };

        if (isImage) {
            const previewUrl = URL.createObjectURL(file);
            const item = { kind: 'image', localId, file, previewUrl, status: 'uploading', result: null, errorMsg: null, ...owner };
            this.pendingImages.push(item);
            this.rebuildImagePreview();
            this._startAttachmentUpload('image', item);
        } else if (isAudio) {
            const previewUrl = URL.createObjectURL(file);
            const item = { kind: 'audio', localId, file, previewUrl, status: 'uploading', result: null, errorMsg: null, ...owner };
            this.pendingAudio.push(item);
            this.showAudioPreview();
            this._startAttachmentUpload('audio', item);
        } else {
            const item = { kind: 'document', localId, file, previewUrl: null, status: 'uploading', result: null, errorMsg: null, ...owner };
            this.pendingDocuments.push(item);
            this.showDocumentPreview();
            this._startAttachmentUpload('document', item);
        }
    },






    _initEmojiPicker() {
        const btn = document.getElementById('emoji-btn');
        const panel = document.getElementById('emoji-picker');
        if (!btn || !panel) return;
        const search = document.getElementById('emoji-search-input');
        const results = document.getElementById('emoji-results');
        const closeBtn = document.getElementById('emoji-picker-close');
        const preview = document.getElementById('emoji-preview');

        const close = () => { panel.style.display = 'none'; };
        const open = () => {
            panel.style.display = 'flex';
            this._renderEmojiResults(search.value.trim());
            // Don't steal focus on phones — the on-screen keyboard would cover
            // the grid she just opened.
            if (window.innerWidth > 700) search.focus();
        };

        btn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            (panel.style.display === 'none') ? open() : close();
        });
        closeBtn.addEventListener('click', close);
        document.addEventListener('click', (e) => {
            if (panel.style.display !== 'none' && !panel.contains(e.target) && e.target !== btn) close();
        });

        let debounce = null;
        search.addEventListener('input', () => {
            clearTimeout(debounce);
            debounce = setTimeout(() => this._renderEmojiResults(search.value.trim()), 120);
        });

        // Manifest may land after the picker is built — repaint when it does.
        document.addEventListener('customemoji:ready', () => {
            if (panel.style.display !== 'none') this._renderEmojiResults(search.value.trim());
        });

        this._emojiPreviewEl = preview;
        this._emojiResultsEl = results;
        this._emojiPanelEl = panel;
    },

    _renderEmojiResults(query) {
        const results = this._emojiResultsEl;
        if (!results) return;
        results.innerHTML = '';

        if (typeof CustomEmoji === 'undefined' || !CustomEmoji.isReady()) {
            results.innerHTML = '<div class="emoji-status">No custom emoji yet — drop PNGs in the CDN <code>emoji/</code> folder.</div>';
            return;
        }

        const q = (query || '').toLowerCase();
        let shown = 0;

        CustomEmoji.groups().forEach(({ group, items }) => {
            const matches = q
                ? items.filter(e => e.name.includes(q) || group.toLowerCase().includes(q))
                : items;
            if (!matches.length) return;

            const label = document.createElement('div');
            label.className = 'emoji-group-label';
            label.textContent = group;
            results.appendChild(label);

            const grid = document.createElement('div');
            grid.className = 'emoji-grid';
            matches.forEach(entry => {
                const tile = document.createElement('button');
                tile.className = 'emoji-tile';
                tile.type = 'button';
                tile.title = `:${entry.name}:`;
                tile.innerHTML = CustomEmoji.imgHtml(entry.name, 'emoji-tile-img');
                tile.addEventListener('mouseenter', () => {
                    if (this._emojiPreviewEl) this._emojiPreviewEl.textContent = `:${entry.name}:`;
                });
                tile.addEventListener('click', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    this._insertEmojiCode(entry.name);
                });
                grid.appendChild(tile);
                shown++;
            });
            results.appendChild(grid);
        });

        if (!shown) {
            results.innerHTML = `<div class="emoji-status">Nothing matching &ldquo;${escapeHtml(query)}&rdquo;.</div>`;
        }
    },

    /** Drop `:name:` into the composer at the caret and keep typing flowing. */
    _insertEmojiCode(name) {
        const input = document.getElementById('message-input');
        if (!input) return;
        const code = `:${name}:`;
        const start = input.selectionStart ?? input.value.length;
        const end = input.selectionEnd ?? input.value.length;
        const before = input.value.slice(0, start);
        const after = input.value.slice(end);
        // A trailing space so she can pick several in a row without them fusing.
        const insert = code + (after.startsWith(' ') ? '' : ' ');

        input.value = before + insert + after;
        const caret = start + insert.length;
        input.setSelectionRange(caret, caret);
        input.focus();
        // Let the textarea auto-grow logic and send-button state notice.
        input.dispatchEvent(new Event('input', { bubbles: true }));
        if (navigator.vibrate) navigator.vibrate(4);
    },

    // ── GIF picker ──
    // A GIF button beside attach opens a small panel: Search (Tenor/GIPHY,
    // key in .env) + My GIFs (her own .gif uploads, no key needed). Picking
    // one lands it in IMAGES_DIR server-side and attaches it through the
    // normal image-chip flow, so it sends exactly like an uploaded picture.
    _initGifPicker() {
        const btn = document.getElementById('gif-btn');
        const panel = document.getElementById('gif-picker');
        if (!btn || !panel) return;
        const input = document.getElementById('gif-search-input');
        const results = document.getElementById('gif-results');
        const tabSearch = document.getElementById('gif-tab-search');
        const tabMine = document.getElementById('gif-tab-mine');
        const closeBtn = document.getElementById('gif-picker-close');
        this._gifTab = 'search';
        this._gifNoKey = false;

        const close = () => { panel.style.display = 'none'; };
        const open = () => {
            panel.style.display = 'flex';
            if (this._gifTab === 'search' && !this._gifNoKey) {
                input.focus();
                this._loadGifs(input.value.trim());
            } else {
                this._loadMyGifs();
            }
        };
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            (panel.style.display === 'none') ? open() : close();
        });
        closeBtn.addEventListener('click', close);
        document.addEventListener('click', (e) => {
            if (panel.style.display !== 'none' && !panel.contains(e.target) && e.target !== btn) close();
        });

        const setTab = (tab) => {
            this._gifTab = tab;
            tabSearch.classList.toggle('active', tab === 'search');
            tabMine.classList.toggle('active', tab === 'mine');
            input.style.display = (tab === 'search') ? '' : 'none';
            if (tab === 'search') this._loadGifs(input.value.trim());
            else this._loadMyGifs();
        };
        tabSearch.addEventListener('click', () => setTab('search'));
        tabMine.addEventListener('click', () => setTab('mine'));

        let debounce = null;
        input.addEventListener('input', () => {
            clearTimeout(debounce);
            debounce = setTimeout(() => this._loadGifs(input.value.trim()), 350);
        });
    },

    async _loadGifs(query) {
        const results = document.getElementById('gif-results');
        results.innerHTML = '<div class="gif-status">Searching...</div>';
        try {
            const resp = await apiFetch(`/api/gifs/search?q=${encodeURIComponent(query || '')}&limit=24`);
            if (resp.status === 503) {
                this._gifNoKey = true;
                results.innerHTML = '<div class="gif-status">No GIF search key yet — add GIPHY_API_KEY to .env (free at developers.giphy.com).<br>Your own gifs still work in the My GIFs tab!</div>';
                return;
            }
            if (!resp.ok) throw new Error('search failed');
            const data = await resp.json();
            this._renderGifGrid(data.results || [], /*remote=*/true);
        } catch (_e) {
            results.innerHTML = '<div class="gif-status">GIF search hiccuped — try again.</div>';
        }
    },

    async _loadMyGifs() {
        const results = document.getElementById('gif-results');
        results.innerHTML = '<div class="gif-status">Loading your gifs...</div>';
        try {
            const resp = await apiFetch('/api/gifs/mine');
            if (!resp.ok) throw new Error('mine failed');
            const data = await resp.json();
            if (!data.results || data.results.length === 0) {
                results.innerHTML = '<div class="gif-status">No gifs in the library yet — attach a .gif once and it lives here forever.</div>';
                return;
            }
            this._renderGifGrid(data.results, /*remote=*/false);
        } catch (_e) {
            results.innerHTML = '<div class="gif-status">Could not load your gifs — try again.</div>';
        }
    },

    _renderGifGrid(items, remote) {
        const results = document.getElementById('gif-results');
        results.innerHTML = '';
        if (items.length === 0) {
            results.innerHTML = '<div class="gif-status">Nothing found — try another word.</div>';
            return;
        }
        for (const item of items) {
            const img = document.createElement('img');
            img.className = 'gif-result';
            img.loading = 'lazy';
            img.src = remote ? item.preview : item.url;
            img.alt = item.title || 'gif';
            img.addEventListener('click', () => this._pickGif(item, remote));
            results.appendChild(img);
        }
    },

    async _pickGif(item, remote) {
        const panel = document.getElementById('gif-picker');
        try {
            let result;
            if (remote) {
                const resp = await apiFetch('/api/gifs/pick', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url: item.url, identity: App.currentIdentity || null }),
                });
                if (!resp.ok) throw new Error('pick failed');
                result = await resp.json();
            } else {
                result = {
                    image_id: item.filename.replace(/\.gif$/i, ''),
                    filename: item.filename,
                    url: item.url,
                };
            }
            // Attach through the normal image-chip flow, already uploaded.
            const localId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
            this.pendingImages.push({
                kind: 'image',
                localId,
                file: null,
                previewUrl: result.url,
                status: 'done',
                result,
                errorMsg: null,
                ownerIdentity: App.currentIdentity,
                ownerConversationId: App.conversationId || null,
            });
            this.rebuildImagePreview();
            this._persistAttachmentChips(App.conversationId);
            panel.style.display = 'none';
            App._showToast('🎬 GIF attached');
        } catch (_e) {
            App._showToast('GIF attach failed — try another one');
        }
    },

    // Fires the actual upload for one pending attachment. Stores the
    // in-flight promise on the item so sendMessage() can wait it out if she
    // hits send before it finishes, and persists a durable chip the moment
    // it succeeds (see _persistAttachmentChips).
    _startAttachmentUpload(kind, item) {
        const run = (async () => {
            try {
                let result;
                if (kind === 'image') {
                    const compressed = await this._compressImage(item.file);
                    result = await this.uploadImage(compressed, item.ownerIdentity);
                } else if (kind === 'document') {
                    result = await this.uploadDocument(item.file);
                } else {
                    result = await this.uploadAudio(item.file);
                }
                if (item.cancelled) return;
                item.status = 'done';
                item.result = result;
                item.errorMsg = null;
                this._persistAttachmentItem(item);
            } catch (err) {
                item.status = 'error';
                item.errorMsg = (err && err.message) || 'Upload failed';
            } finally {
                if (this._hasPendingAttachment(item)) {
                    this._refreshAttachmentPreview(kind);
                } else if (item.detached && item.status === 'done') {
                    const owner = this._attachmentOwner(item);
                    const isOwnerOpen = owner.identity === App.currentIdentity
                        && (owner.conversationId || null) === (App.conversationId || null);
                    if (isOwnerOpen) {
                        item.detached = false;
                        if (kind === 'image') this.pendingImages.push(item);
                        else if (kind === 'document') this.pendingDocuments.push(item);
                        else this.pendingAudio.push(item);
                        this._refreshAttachmentPreview(kind);
                    }
                }
            }
        })();
        item._uploadPromise = run;
    },

    _refreshAttachmentPreview(kind) {
        if (kind === 'image') this.rebuildImagePreview();
        else if (kind === 'document') this.showDocumentPreview();
        else this.showAudioPreview();
    },

    _retryAttachmentUpload(kind, item) {
        item.cancelled = false;
        item.status = 'uploading';
        item.errorMsg = null;
        this._refreshAttachmentPreview(kind);
        this._startAttachmentUpload(kind, item);
    },















    _PENDING_CHIPS_KEY: 'anam-attachments-pending',

    _pendingAttachmentChipsKey(identity = App.currentIdentity) {
        const safeIdentity = String(identity || 'unknown')
            .trim()
            .toLowerCase()
            .replace(/[^a-z0-9_-]+/g, '-');
        return `${this._PENDING_CHIPS_KEY}-${safeIdentity}`;
    },

    _attachmentChipsKey(conversationId, identity = App.currentIdentity) {
        return conversationId
            ? 'anam-attachments-' + conversationId
            : this._pendingAttachmentChipsKey(identity);
    },

    _attachmentOwner(item) {
        return {
            identity: item.ownerIdentity || App.currentIdentity,
            conversationId: item.ownerConversationId || null,
        };
    },

    _hasPendingAttachment(item) {
        return this.pendingImages.includes(item)
            || this.pendingDocuments.includes(item)
            || this.pendingAudio.includes(item);
    },

    _readAttachmentChips(key, identity = App.currentIdentity) {
        try {
            const raw = localStorage.getItem(key);
            const parsed = raw ? JSON.parse(raw) : null;
            if (Array.isArray(parsed)) return parsed;
            if (parsed && Array.isArray(parsed.chips)) {
                return parsed.identity && parsed.identity !== identity
                    ? []
                    : parsed.chips;
            }
        } catch (_e) { /* corrupt or unavailable storage */ }
        return [];
    },

    _writeAttachmentChips(conversationId, identity, chips) {
        const key = this._attachmentChipsKey(conversationId, identity);
        try {
            if (chips.length === 0) {
                localStorage.removeItem(key);
                return;
            }
            const payload = conversationId ? chips : { identity, chips };
            localStorage.setItem(key, JSON.stringify(payload));
        } catch (_e) { /* storage unavailable — upload still works in memory */ }
    },

    _persistAttachmentItem(item) {
        if (!item || item.status !== 'done' || !item.result) return;
        const owner = this._attachmentOwner(item);
        const key = this._attachmentChipsKey(owner.conversationId, owner.identity);
        const chips = this._readAttachmentChips(key, owner.identity)
            .filter(chip => chip && chip.localId !== item.localId);
        chips.push({
            kind: item.kind
                || (item.result && item.result.image_id ? 'image'
                    : item.result && item.result.audio_id ? 'audio'
                        : 'document'),
            localId: item.localId,
            result: item.result,
        });
        this._writeAttachmentChips(owner.conversationId, owner.identity, chips);
    },

    _persistAttachmentChips(conversationId, identity = App.currentIdentity) {
        const belongsToScope = (item) => {
            const owner = this._attachmentOwner(item);
            return owner.identity === identity
                && (owner.conversationId || null) === (conversationId || null);
        };
        const chips = [
            ...this.pendingImages.filter(i => i.status === 'done' && belongsToScope(i)).map(i => ({ kind: 'image', localId: i.localId, result: i.result })),
            ...this.pendingDocuments.filter(i => i.status === 'done' && belongsToScope(i)).map(i => ({ kind: 'document', localId: i.localId, result: i.result })),
            ...this.pendingAudio.filter(i => i.status === 'done' && belongsToScope(i)).map(i => ({ kind: 'audio', localId: i.localId, result: i.result })),
        ];
        this._writeAttachmentChips(conversationId, identity, chips);
    },

    _clearAttachmentChips(conversationId, identity = App.currentIdentity) {
        try {
            localStorage.removeItem(this._attachmentChipsKey(conversationId, identity));
            localStorage.removeItem(this._pendingAttachmentChipsKey(identity));
            // Remove the old single pending bucket after migration/cleanup.
            const legacy = this._readAttachmentChips(this._PENDING_CHIPS_KEY, identity);
            if (legacy.length > 0) {
                localStorage.removeItem(this._PENDING_CHIPS_KEY);
            }
        } catch (_e) { /* storage unavailable */ }
    },

    // Backfills any chips uploaded before a reload wiped memory (tab
    // suspension) into the live pending arrays. In-memory items already
    // present win — this only adds what memory doesn't already have, which
    // is the "union in-memory with persisted" behavior send-time relies on.
    loadAttachmentChips(conversationId) {
        // Read this conversation's bucket AND this identity's pending bucket,
        // because a chip uploaded before a conversation had an id lives there.
        const identity = App.currentIdentity;
        const pendingKey = this._pendingAttachmentChipsKey(identity);
        let pendingChips = this._readAttachmentChips(
            pendingKey,
            identity
        );
        let chips = pendingChips.slice();
        if (conversationId) {
            chips = chips.concat(this._readAttachmentChips(
                this._attachmentChipsKey(conversationId, identity),
                identity
            ));
        }

        // One-time compatibility with the older global pending key.
        const legacy = this._readAttachmentChips(this._PENDING_CHIPS_KEY, identity);
        if (legacy.length > 0) {
            chips = chips.concat(legacy);
            pendingChips = pendingChips.concat(legacy);
            try { localStorage.removeItem(this._PENDING_CHIPS_KEY); } catch (_e) { /* ignore */ }
        }
        // Once the server has resolved a real conversation id, fold any
        // identity-pending chips into that conversation's bucket. This avoids
        // restoring the same chip twice on every later visit.
        if (conversationId && pendingChips.length > 0) {
            const conversationKey = this._attachmentChipsKey(conversationId, identity);
            const conversationChips = this._readAttachmentChips(conversationKey, identity);
            const merged = new Map();
            for (const chip of conversationChips.concat(pendingChips)) {
                if (chip && chip.localId) merged.set(chip.localId, chip);
            }
            this._writeAttachmentChips(conversationId, identity, [...merged.values()]);
            try { localStorage.removeItem(pendingKey); } catch (_e) { /* ignore */ }
        }
        if (chips.length === 0) return;

        const known = new Set([
            ...this.pendingImages.map(i => i.localId),
            ...this.pendingDocuments.map(i => i.localId),
            ...this.pendingAudio.map(i => i.localId),
        ]);

        let touched = false;
        chips.forEach(chip => {
            if (!chip || !chip.result || known.has(chip.localId)) return;
            const item = {
                kind: chip.kind,
                localId: chip.localId,
                file: null,
                previewUrl: null,
                status: 'done',
                result: chip.result,
                errorMsg: null,
                ownerIdentity: identity,
                ownerConversationId: conversationId || null,
            };
            if (chip.kind === 'image') this.pendingImages.push(item);
            else if (chip.kind === 'document') this.pendingDocuments.push(item);
            else if (chip.kind === 'audio') this.pendingAudio.push(item);
            else return;
            touched = true;
        });

        if (touched) this.rebuildImagePreview();
    },

    showAudioPreview() {
        const previewEl = document.getElementById('image-preview');
        let audioInfo = previewEl.querySelector('.audio-preview-info');
        if (!audioInfo) {
            audioInfo = document.createElement('div');
            audioInfo.className = 'audio-preview-info';
            previewEl.appendChild(audioInfo);
        }
        const lines = this.pendingAudio.map((item, idx) => {
            const file = item.file;
            const name = file ? file.name : ((item.result && (item.result.original_name || item.result.filename)) || 'Voice memo');
            const sizeStr = file
                ? (file.size > 1048576 ? (file.size / 1048576).toFixed(1) + ' MB' : Math.round(file.size / 1024) + ' KB')
                : ((item.result && item.result.size_display) || '');
            // Restored chips (post tab-suspension) have no blob preview —
            // fall back to the real server URL, which is durable anyway.
            const src = item.previewUrl || (item.result && item.result.url) || '';
            const removeAttr = `data-audio-idx="${idx}"`;
            const statusCls = item.status ? ` attachment-${item.status}` : '';
            const statusHtml = item.status === 'uploading'
                ? '<span class="attachment-upload-spinner attachment-upload-spinner-inline" title="Uploading…"></span>'
                : item.status === 'error'
                    ? `<button type="button" class="attachment-upload-retry attachment-upload-retry-inline" data-audio-retry-idx="${idx}" title="${escapeHtml(item.errorMsg || 'Upload failed — tap to retry')}">&#8635;</button>`
                    : '';
            return `<div class="audio-preview-row${statusCls}">
                <span class="audio-icon" aria-hidden="true">&#127908;</span>
                <span class="audio-name">${escapeHtml(name)}</span>
                <span class="audio-size">(${sizeStr})</span>
                ${src ? `<audio controls preload="metadata" src="${src}" class="audio-preview-player"></audio>` : ''}
                ${statusHtml}
                <button class="audio-preview-remove" ${removeAttr} title="Remove">&times;</button>
            </div>`;
        });
        audioInfo.innerHTML = lines.join('');
        if (this.pendingAudio.length === 0) {
            audioInfo.style.display = 'none';
            return;
        }
        audioInfo.style.display = 'flex';
        previewEl.style.display = 'flex';

        audioInfo.querySelectorAll('.audio-preview-remove').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const idx = parseInt(btn.getAttribute('data-audio-idx'), 10);
                if (Number.isNaN(idx) || idx < 0 || idx >= this.pendingAudio.length) return;
                const item = this.pendingAudio[idx];
                item.cancelled = true;
                if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
                this.pendingAudio.splice(idx, 1);
                this._persistAttachmentChips(App.conversationId);
                if (this.pendingAudio.length === 0
                    && this.pendingImages.length === 0
                    && this.pendingDocuments.length === 0) {
                    this.clearImagePreview();
                } else if (this.pendingAudio.length === 0) {
                    audioInfo.innerHTML = '';
                    audioInfo.style.display = 'none';
                    this.rebuildImagePreview();
                } else {
                    this.showAudioPreview();
                }
            });
        });
        audioInfo.querySelectorAll('[data-audio-retry-idx]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const idx = parseInt(btn.getAttribute('data-audio-retry-idx'), 10);
                const item = this.pendingAudio[idx];
                if (item) this._retryAttachmentUpload('audio', item);
            });
        });
    },

    async uploadAudio(file) {
        const formData = new FormData();
        formData.append('file', file);
        const resp = await apiFetch('/api/audio/upload', { method: 'POST', body: formData });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ error: 'Upload failed' }));
            throw new Error(err.error || 'Audio upload failed');
        }
        return await resp.json();
    },

    rebuildImagePreview() {
        const previewEl = document.getElementById('image-preview');
        const gridEl = document.getElementById('image-preview-grid');
        if (gridEl) gridEl.style.display = 'flex';

        gridEl.innerHTML = '';
        this.pendingImages.forEach((item, idx) => {
            const wrapper = document.createElement('div');
            wrapper.className = 'image-preview-item';
            if (item.status) wrapper.classList.add(`attachment-${item.status}`);

            const thumb = document.createElement('img');
            thumb.className = 'image-preview-thumb';
            // Restored chips (post tab-suspension) have no blob preview \u2014
            // fall back to the real server URL, which is durable anyway.
            thumb.src = item.previewUrl || (item.result && item.result.url) || '';
            thumb.alt = 'Preview';

            // Info overlay: file size + dimensions
            const infoEl = document.createElement('div');
            infoEl.className = 'preview-info';
            const sizeKB = item.file ? item.file.size / 1024 : null;
            const sizeStr = sizeKB != null
                ? (sizeKB > 1024 ? (sizeKB / 1024).toFixed(1) + ' MB' : Math.round(sizeKB) + ' KB')
                : '';
            const sizeLabel = document.createElement('span');
            sizeLabel.className = 'preview-size';
            sizeLabel.textContent = sizeStr;
            const dimLabel = document.createElement('span');
            dimLabel.className = 'preview-dims';
            dimLabel.textContent = '';
            infoEl.appendChild(sizeLabel);
            infoEl.appendChild(dimLabel);

            // Show dimensions once image loads; flag if compression will occur
            thumb.onload = () => {
                dimLabel.textContent = `${thumb.naturalWidth}\u00d7${thumb.naturalHeight}`;
                if (item.file && (item.file.size > 2 * 1024 * 1024 || thumb.naturalWidth > 4096 || thumb.naturalHeight > 4096)) {
                    const badge = document.createElement('span');
                    badge.className = 'compress-badge';
                    badge.textContent = 'will resize';
                    wrapper.appendChild(badge);
                }
            };

            // Upload status (#17) \u2014 spinner while in flight, retry on failure
            if (item.status === 'uploading') {
                const spinner = document.createElement('span');
                spinner.className = 'attachment-upload-spinner';
                spinner.title = 'Uploading\u2026';
                wrapper.appendChild(spinner);
            } else if (item.status === 'error') {
                const retry = document.createElement('button');
                retry.className = 'attachment-upload-retry';
                retry.type = 'button';
                retry.title = item.errorMsg || 'Upload failed \u2014 tap to retry';
                retry.innerHTML = '&#8635;';
                retry.addEventListener('click', (e) => {
                    e.stopPropagation();
                    this._retryAttachmentUpload('image', item);
                });
                wrapper.appendChild(retry);
            }

            const removeBtn = document.createElement('button');
            removeBtn.className = 'image-preview-item-remove';
            removeBtn.innerHTML = '&times;';
            removeBtn.title = 'Remove';
            removeBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                const i = this.pendingImages.indexOf(item);
                if (i === -1) return;
                this.pendingImages[i].cancelled = true;
                if (this.pendingImages[i].previewUrl) URL.revokeObjectURL(this.pendingImages[i].previewUrl);
                this.pendingImages.splice(i, 1);
                this._persistAttachmentChips(App.conversationId);
                if (this.pendingImages.length === 0
                    && this.pendingDocuments.length === 0
                    && this.pendingAudio.length === 0) {
                    this.clearImagePreview();
                } else {
                    this.rebuildImagePreview();
                }
            });

            wrapper.appendChild(thumb);
            wrapper.appendChild(infoEl);
            wrapper.appendChild(removeBtn);
            gridEl.appendChild(wrapper);
        });

        previewEl.style.display = (this.pendingImages.length > 0 || this.pendingDocuments.length > 0 || this.pendingAudio.length > 0) ? 'flex' : 'none';
        // Also refresh doc / audio previews if those are pending
        if (this.pendingDocuments.length > 0) this.showDocumentPreview();
        if (this.pendingAudio.length > 0) this.showAudioPreview();
    },

    showDocumentPreview() {
        const previewEl = document.getElementById('image-preview');

        let docInfo = previewEl.querySelector('.doc-preview-info');
        if (!docInfo) {
            docInfo = document.createElement('div');
            docInfo.className = 'doc-preview-info';
            previewEl.appendChild(docInfo);
        }
        const lines = this.pendingDocuments.map(item => {
            const file = item.file;
            const name = file ? file.name : ((item.result && (item.result.original_name || item.result.filename)) || 'Document');
            const sizeStr = file
                ? (file.size > 1048576 ? (file.size / 1048576).toFixed(1) + ' MB' : Math.round(file.size / 1024) + ' KB')
                : ((item.result && item.result.size_display) || '');
            const statusCls = item.status ? ` attachment-${item.status}` : '';
            const statusHtml = item.status === 'uploading'
                ? '<span class="attachment-upload-spinner attachment-upload-spinner-inline" title="Uploading…"></span>'
                : item.status === 'error'
                    ? `<button type="button" class="attachment-upload-retry attachment-upload-retry-inline" data-doc-retry-id="${escapeHtml(item.localId)}" title="${escapeHtml(item.errorMsg || 'Upload failed — tap to retry')}">&#8635;</button>`
                    : '';
            return `<div class="doc-preview-row${statusCls}">
                <span class="doc-icon" aria-hidden="true">&#128196;</span>
                <span class="doc-name">${escapeHtml(name)}</span>
                <span class="doc-size">(${sizeStr})</span>
                ${statusHtml}
                <button type="button" class="doc-preview-remove" data-doc-remove-id="${escapeHtml(item.localId)}" title="Remove" aria-label="Remove ${escapeHtml(name)}">&times;</button>
            </div>`;
        });
        docInfo.innerHTML = lines.join('');
        if (this.pendingDocuments.length === 0) {
            docInfo.style.display = 'none';
            return;
        }
        docInfo.style.display = 'flex';
        previewEl.style.display = 'flex';

        docInfo.querySelectorAll('[data-doc-retry-id]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const id = btn.getAttribute('data-doc-retry-id');
                const item = this.pendingDocuments.find(i => i.localId === id);
                if (item) this._retryAttachmentUpload('document', item);
            });
        });
        docInfo.querySelectorAll('[data-doc-remove-id]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const id = btn.getAttribute('data-doc-remove-id');
                const index = this.pendingDocuments.findIndex(i => i.localId === id);
                if (index === -1) return;
                this.pendingDocuments[index].cancelled = true;
                this.pendingDocuments.splice(index, 1);
                this._persistAttachmentChips(App.conversationId);
                if (this.pendingImages.length === 0
                    && this.pendingDocuments.length === 0
                    && this.pendingAudio.length === 0) {
                    this.clearImagePreview();
                } else {
                    this.rebuildImagePreview();
                }
            });
        });
    },

    _resetAttachmentPreviewDom() {
        const previewEl = document.getElementById('image-preview');
        previewEl.style.display = 'none';
        previewEl.classList.remove('attachment-sending');
        const gridEl = document.getElementById('image-preview-grid');
        if (gridEl) gridEl.innerHTML = '';
        const docInfo = previewEl.querySelector('.doc-preview-info');
        if (docInfo) {
            docInfo.innerHTML = '';
            docInfo.style.display = 'none';
        }
        const audioInfo = previewEl.querySelector('.audio-preview-info');
        if (audioInfo) {
            audioInfo.innerHTML = '';
            audioInfo.style.display = 'none';
        }
        const fileInput = document.getElementById('file-input');
        if (fileInput) fileInput.value = '';
    },

    // Leave this conversation's completed uploads parked in localStorage while
    // navigating elsewhere. In-flight uploads keep their captured owner and
    // persist into that old scope when they finish, never into the new chat.
    parkAttachmentPreview() {
        for (const item of [...this.pendingImages, ...this.pendingDocuments, ...this.pendingAudio]) {
            if (item.status === 'done') this._persistAttachmentItem(item);
            if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
            item.previewUrl = null;
            item.detached = true;
        }
        this.pendingImages = [];
        this.pendingDocuments = [];
        this.pendingAudio = [];
        this._resetAttachmentPreviewDom();
    },

    _removeSentAttachments(sentItems) {
        const sent = new Set(sentItems);
        const releaseAndKeep = (item) => {
            if (!sent.has(item)) return true;
            if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
            return false;
        };
        this.pendingImages = this.pendingImages.filter(releaseAndKeep);
        this.pendingDocuments = this.pendingDocuments.filter(item => !sent.has(item));
        this.pendingAudio = this.pendingAudio.filter(releaseAndKeep);

        const ownerItem = sentItems[0];
        const owner = ownerItem
            ? this._attachmentOwner(ownerItem)
            : { identity: App.currentIdentity, conversationId: App.conversationId || null };
        this._persistAttachmentChips(owner.conversationId, owner.identity);

        if (this.pendingImages.length === 0
            && this.pendingDocuments.length === 0
            && this.pendingAudio.length === 0) {
            this._resetAttachmentPreviewDom();
        } else {
            this.rebuildImagePreview();
        }
    },

    clearImagePreview() {
        for (const item of [...this.pendingImages, ...this.pendingDocuments, ...this.pendingAudio]) {
            item.cancelled = true;
        }
        this.pendingImages.forEach(item => { if (item.previewUrl) URL.revokeObjectURL(item.previewUrl); });
        this.pendingImages = [];
        this.pendingDocuments = [];
        this.pendingAudio.forEach(item => { if (item.previewUrl) URL.revokeObjectURL(item.previewUrl); });
        this.pendingAudio = [];
        this._clearAttachmentChips(App.conversationId);
        this._resetAttachmentPreviewDom();
    },

    _compressImage(file) {
        return new Promise((resolve) => {
            // Skip GIFs (may be animated)
            if (file.type === 'image/gif') return resolve(file);

            // Skip small files but still check dimensions
            if (file.size <= 2 * 1024 * 1024) {
                const img = new Image();
                const objectUrl = URL.createObjectURL(file);
                img.onload = () => {
                    URL.revokeObjectURL(objectUrl);
                    if (img.width <= 4096 && img.height <= 4096) {
                        resolve(file);
                    } else {
                        this._resizeImage(file, img, 4096).then(resolve).catch(() => resolve(file));
                    }
                };
                img.onerror = () => {
                    URL.revokeObjectURL(objectUrl);
                    resolve(file);
                };
                img.src = objectUrl;
                return;
            }

            // Large file — resize
            const img = new Image();
            const objectUrl = URL.createObjectURL(file);
            img.onload = () => {
                URL.revokeObjectURL(objectUrl);
                this._resizeImage(file, img, 4096).then(resolve).catch(() => resolve(file));
            };
            img.onerror = () => {
                URL.revokeObjectURL(objectUrl);
                resolve(file);
            };
            img.src = objectUrl;
        });
    },

    _resizeImage(file, img, maxDim) {
        return new Promise((resolve, reject) => {
            try {
                let { width, height } = img;
                if (width > maxDim || height > maxDim) {
                    const scale = Math.min(maxDim / width, maxDim / height);
                    width = Math.round(width * scale);
                    height = Math.round(height * scale);
                }

                const canvas = document.createElement('canvas');
                canvas.width = width;
                canvas.height = height;
                const ctx = canvas.getContext('2d');
                ctx.drawImage(img, 0, 0, width, height);

                // Use JPEG for photos (smaller), PNG only if original was PNG and result is small
                const useJpeg = file.type !== 'image/png' || file.size > 8 * 1024 * 1024;
                const mimeType = useJpeg ? 'image/jpeg' : 'image/png';
                const quality = useJpeg ? 0.85 : undefined;

                canvas.toBlob((blob) => {
                    if (blob) {
                        const ext = useJpeg ? '.jpg' : '.png';
                        const name = file.name.replace(/\.[^.]+$/, ext);
                        resolve(new File([blob], name, { type: mimeType }));
                    } else {
                        reject(new Error('Canvas toBlob failed'));
                    }
                }, mimeType, quality);
            } catch (e) {
                reject(e);
            }
        });
    },

    async uploadImage(file, identity = App.currentIdentity) {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('identity', identity);
        const resp = await apiFetch('/api/images/upload', { method: 'POST', body: formData });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ error: 'Upload failed' }));
            throw new Error(err.error || 'Upload failed');
        }
        return await resp.json();
    },

    async uploadDocument(file) {
        const formData = new FormData();
        formData.append('file', file);
        const resp = await apiFetch('/api/documents/upload', { method: 'POST', body: formData });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ error: 'Upload failed' }));
            throw new Error(err.error || 'Upload failed');
        }
        return await resp.json();
    },




    async sendMessage(options = {}) {
        const text = this.input.value.trim();
        const hasContent = !!(text || this.pendingImages.length || this.pendingDocuments.length || this.pendingAudio.length);
        if (!hasContent) return false;






















        if (text.startsWith('/') && !options.fromVoiceConversation) {
            this._hideSlashDropdown();
            if (this._handleSlashCommand(text)) {
                this.input.value = '';
                this.input.dispatchEvent(new Event('input'));
                return true;
            }
        }

        // #17 upload-on-attach: attachments already started uploading the
        // moment they were picked (see onFileSelected / _startAttachmentUpload).
        // By send time most are already 'done' -- just wait out anything
        // still in flight and gather the results. No re-upload here.
        const attachmentItems = [...this.pendingImages, ...this.pendingDocuments, ...this.pendingAudio];
        const stillUploading = attachmentItems.filter(i => i.status === 'uploading' && i._uploadPromise);
        if (stillUploading.length > 0) {
            this.sendBtn.disabled = true;
            await Promise.all(stillUploading.map(i => i._uploadPromise.catch(() => {})));
            this.sendBtn.disabled = false;
        }

        const failedItems = attachmentItems.filter(i => i.status === 'error');
        if (failedItems.length > 0) {
            this.showError(
                `${failedItems.length} attachment${failedItems.length > 1 ? 's' : ''} failed to upload — tap the retry icon or remove it before sending`,
                true
            );
            return false;
        }

        const imagesData = this.pendingImages.filter(i => i.status === 'done').map(i => i.result);
        const docsData = this.pendingDocuments.filter(i => i.status === 'done').map(i => i.result);
        const audioData = this.pendingAudio.filter(i => i.status === 'done').map(i => i.result);

        const wsMsg = { type: 'message', content: text || '' };
        if (imagesData.length > 0) {
            wsMsg.images = imagesData;
        }
        if (docsData.length > 0) {
            wsMsg.documents = docsData;
            if (docsData.length === 1) {
                wsMsg.document = docsData[0];
            }
        }
        if (audioData.length > 0) {
            wsMsg.audio = audioData;
        }
        // Attach reply-to context if set
        if (this._replyToId) {
            wsMsg.reply_to = { id: this._replyToId, preview: this._replyToPreview || '' };
            this.clearReplyTo();
        }

        // Try WebSocket first; fall back to HTTP/SSE if WS is down
        const wsConnected = App.ws.ws && App.ws.ws.readyState === WebSocket.OPEN;
        // Track the last sent message so approval-retry can resend it
        this._lastSentMessage = wsMsg;

        if (wsConnected) {
            const sendResult = App.ws.send(wsMsg);
            if (sendResult === false) {
                this._lastFailedMessage = wsMsg;
                this.showError('Not connected — please wait for reconnection', true);
                this.sendBtn.disabled = false;
                return false;
            }
            if (attachmentItems.length > 0) {
                this._removeSentAttachments(attachmentItems);
            }
        } else {
            // HTTP/SSE fallback — send via POST, receive streamed response
            this.sendBtn.disabled = true;
            this._sendViaHttp(wsMsg, {
                onAccepted: () => {
                    if (attachmentItems.length > 0) {
                        this._removeSentAttachments(attachmentItems);
                    }
                    this.sendBtn.disabled = false;
                },
                onRejected: () => {
                    // Keep uploaded chips in the composer so retrying never
                    // means finding and uploading every file again.
                    this.sendBtn.disabled = false;
                },
            });
        }

        // Haptic feedback on send
        if (navigator.vibrate) navigator.vibrate(12);

        // Show user bubble (optimistic — message is either sent or queued)
        this.addMessage('user', text || '', null, null, null, imagesData, docsData, audioData);

        // Show thinking heart while waiting for response
        this.showThinkingHeart();

        // Track input history
        if (text.trim()) {
            this._inputHistory.push(text.trim());
            if (this._inputHistory.length > 20) this._inputHistory.shift();
            this._historyIndex = -1;
            this._historyDraft = '';
        }

        this.input.value = '';
        this.input.style.height = 'auto';
        this.clearDraft(App.conversationId);
        if (wsConnected) this.sendBtn.disabled = false;
        this.scrollToBottom(true);
        if (options.fromVoiceConversation && this._voiceConversationMode) {
            this._voiceConversationShouldResume = true;
            this._voiceConversationPendingAutoplay = true;
            this._setVoiceConversationPhase('thinking');
        }
        return true;
    },

    /** HTTP/SSE fallback — POST message, read streamed SSE response */
    async _sendViaHttp(msg, callbacks = {}) {
        const startedAt = performance.now();
        let firstEventAt = null;
        let firstDeltaAt = null;
        let accepted = false;
        try {
            if (typeof logClientEvent === 'function') {
                logClientEvent('chat_http_send_start', {
                    identity: App.currentIdentity,
                    conversation_id: App.conversationId || null,
                }, { source: 'chat-http' });
            }
            const body = {
                identity: App.currentIdentity,
                conversation_id: App.conversationId,
                ...msg,
            };
            const resp = await apiFetch('/api/chat/send', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            if (!resp.ok) {
                this.showError('Failed to send message (HTTP ' + resp.status + ')');
                if (callbacks.onRejected) callbacks.onRejected();
                return;
            }
            accepted = true;
            if (callbacks.onAccepted) callbacks.onAccepted();
            if (typeof logClientEvent === 'function') {
                logClientEvent('chat_http_headers', {
                    identity: App.currentIdentity,
                    ms: Math.round(performance.now() - startedAt),
                }, { source: 'chat-http' });
            }
            const reader = resp.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });

                // Parse SSE events from buffer
                const lines = buffer.split('\n');
                buffer = lines.pop() || '';
                let eventType = 'message';

                for (const line of lines) {
                    if (line.startsWith('event: ')) {
                        eventType = line.slice(7).trim();
                    } else if (line.startsWith('data: ')) {
                        try {
                            const data = JSON.parse(line.slice(6));
                            const now = performance.now();
                            if (firstEventAt === null) {
                                firstEventAt = now;
                                if (typeof logClientEvent === 'function') {
                                    logClientEvent('chat_http_first_event', {
                                        identity: App.currentIdentity,
                                        event_type: eventType,
                                        ms: Math.round(now - startedAt),
                                    }, { source: 'chat-http' });
                                }
                            }
                            if (eventType === 'stream_delta' && firstDeltaAt === null) {
                                firstDeltaAt = now;
                                if (typeof logClientEvent === 'function') {
                                    logClientEvent('chat_http_first_delta', {
                                        identity: App.currentIdentity,
                                        ms: Math.round(now - startedAt),
                                    }, { source: 'chat-http' });
                                }
                            } else if (eventType === 'stream_end' && typeof logClientEvent === 'function') {
                                logClientEvent('chat_http_stream_end', {
                                    identity: App.currentIdentity,
                                    ms: Math.round(now - startedAt),
                                    first_delta_ms: firstDeltaAt ? Math.round(firstDeltaAt - startedAt) : null,
                                }, { source: 'chat-http' });
                            }
                            // Route through the same handlers as WebSocket
                            this._handleSseEvent(eventType, data);
                        } catch (_e) { /* ignore parse errors */ }
                        eventType = 'message';
                    }
                    // Skip comments (keepalives) and empty lines
                }
            }
        } catch (err) {
            console.error('[HTTP] SSE fallback error:', err);
            if (!accepted && callbacks.onRejected) callbacks.onRejected();
            this.showError('Connection error — try again');
        }
    },

    /** Route an SSE event through the same handlers as WebSocket events */
    _handleSseEvent(eventType, data) {
        const handlers = {
            'stream_start': (d) => this.onStreamStart(d),
            'stream_delta': (d) => this.onStreamDelta(d),
            'stream_reset': () => this.onStreamReset(),
            'stream_end': (d) => this.onStreamEnd(d),
            'tool_use_start': (d) => this.onToolStart(d),
            'tool_input': (d) => this.onToolInput(d),
            'tool_result': (d) => this.onToolResult(d),
            'content_block_stop': () => this.onContentBlockStop(),
            'thinking_start': () => this.onThinkingStart(),
            'thinking_delta': (d) => this.onThinkingDelta(d),
            'voice_message': (d) => this.onVoiceMessage(d),
            'response_images': (d) => this.onResponseImages(d),
            'response_documents': (d) => this.onResponseDocuments(d),
            'context_usage': (d) => this.onContextUsage(d),
            'echo_message': (d) => this.onEchoMessage(d),
            'approval_required': (d) => this.showApprovalRequest(d),
            'message_injected': (d) => this.onMessageInjected(d),
            'error': (d) => this.showError(d.message || 'Unknown error'),
        };
        const handler = handlers[eventType];
        if (handler) handler(data);
    },

    _formatModelName(model) {
        if (!model) return '';
        const value = String(model);
        const claude = value.match(/^claude-(fable|opus|sonnet|haiku)-(\d+)-(\d+)/i);
        if (claude) {
            return `${claude[1][0].toUpperCase()}${claude[1].slice(1)} ${claude[2]}.${claude[3]}`;
        }
        const short = value.includes('/') ? value.split('/').pop() : value;
        return short.replace(/[-_]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
    },

    _createModelBadge(provenance) {
        if (!provenance) return null;
        const requested = provenance.requested_model;
        const reported = Array.isArray(provenance.models_used)
            ? provenance.models_used
            : (provenance.actual_model ? [provenance.actual_model] : []);
        const sequence = [];
        [requested, ...reported].forEach((model) => {
            if (model && model !== 'default' && !sequence.includes(model)) sequence.push(model);
        });
        if (!sequence.length) return null;

        const badge = document.createElement('span');
        badge.className = 'message-model-badge';
        if (sequence.length > 1 || provenance.switched) badge.classList.add('model-switched');
        badge.textContent = sequence.map((model) => this._formatModelName(model)).join(' → ');
        const provider = provenance.provider ? ` via ${provenance.provider}` : '';
        badge.title = `Model${provider}: ${sequence.join(' → ')}`;
        badge.setAttribute('aria-label', badge.title);
        return badge;
    },

    // ANAM GUIDE: DRAW A SAVED MESSAGE
    // History and non-streamed messages become visible bubbles here. Live
    // replies use onStreamStart/Delta/End below, then settle into the same shape.
    addMessage(role, content, identity, time, metadata, imagesData, docsData, audioData) {
        this.clearEmptyState();
        const row = document.createElement('div');
        row.className = `message-row ${role}`;

        const bubble = document.createElement('div');
        bubble.className = `message-bubble ${role}`;
        if (role === 'assistant' && identity) {
            bubble.dataset.identity = identity;
        }
        if (metadata && (metadata.id || metadata.message_id)) {
            bubble.dataset.msgId = metadata.id || metadata.message_id;
        }
        if (time) {
            bubble.title = new Date(time).toLocaleString();
        }

        // Strip <voice> and <canvas> tags from displayed text
        let displayContent = role === 'assistant' ? this.stripPreviewTags(this.stripReactTags(this.stripVoiceTags(content)), true) : content;
        if (role === 'assistant') displayContent = this.stripCanvasTags(displayContent);

        // Identity label for brother conversations (both speakers are "assistant")
        if (role === 'assistant' && metadata && metadata.brother && identity) {
            const label = document.createElement('div');
            label.className = 'message-identity-label';
            label.textContent = identity;
            bubble.appendChild(label);
        }

        if (role === 'assistant' && metadata && metadata.context_notice) {
            const noticeEl = this._buildContextNoticeEl(metadata.context_notice);
            if (noticeEl) bubble.appendChild(noticeEl);
        }

        const contentDiv = document.createElement('div');
        contentDiv.className = 'message-content';



        contentDiv.innerHTML = renderMarkdown(displayContent);

        // Reconstruct thinking blocks from saved metadata
        if (role === 'assistant' && metadata && metadata.thinking) {
            metadata.thinking.forEach(thinkingText => {
                const card = document.createElement('div');
                card.className = 'thinking-card collapsed';
                const header = document.createElement('div');
                header.className = 'thinking-card-header';
                header.innerHTML = '<span class="thinking-card-heart">&#10084;</span> <span class="thinking-card-label">thought</span><span class="thinking-card-chevron">&#9662;</span>';
                const body = document.createElement('div');
                body.className = 'thinking-card-body';
                body.textContent = thinkingText;
                card.appendChild(header);
                card.appendChild(body);
                header.addEventListener('click', () => card.classList.toggle('collapsed'));
                bubble.appendChild(card);
            });
        }

        // Reconstruct the tool timeline pill (#22) from saved metadata --
        // same consolidated "N tools" pill as the live turn, built in one
        // pass instead of incrementally.
        if (role === 'assistant' && metadata && metadata.tools) {
            const toolPill = this._buildStaticToolPill(metadata.tools);
            if (toolPill) bubble.appendChild(toolPill);
        }

        bubble.appendChild(contentDiv);

        // Copy button — stores raw text, copies on tap
        const rawText = role === 'assistant' ? displayContent : content;
        if (rawText) {
            bubble.appendChild(this.createCopyButton(rawText));
        }

        // Tap to show timestamp (mobile — no hover)
        bubble.addEventListener('click', (e) => {
            // Don't toggle if tapping a button, link, or image
            if (e.target.closest('button, a, img, .tool-pill-header, .tool-pill-row, .thinking-card-header, .voice-player')) return;
            bubble.classList.toggle('show-time');
        });

        // Long-press to open reaction picker (mobile)
        this._addLongPressReaction(bubble);

        // User-attached images (from send or from history metadata)
        // Support both new array format (images) and legacy single format (image)
        let userImages = imagesData || [];
        if (!Array.isArray(userImages)) userImages = userImages.url ? [userImages] : [];
        if (userImages.length === 0 && metadata) {
            if (metadata.images && metadata.images.length > 0) {
                userImages = metadata.images;
            } else if (metadata.image && metadata.image.url) {
                userImages = [metadata.image];
            }
        }
        if (role === 'user' && userImages.length > 0) {
            userImages.forEach(imgInfo => {
                if (!imgInfo.url) return;
                const img = document.createElement('img');
                img.className = 'message-image';
                img.src = imgInfo.url;
                img.alt = 'Shared image';
                img.loading = 'lazy';
                img.addEventListener('click', () => this.viewFullImage(img.src));
                bubble.appendChild(img);
            });
        }

        // Assistant response images (from tool results, stored in metadata.images)
        if (role === 'assistant' && metadata && metadata.images && metadata.images.length > 0) {
            metadata.images.forEach(imgItem => {
                const url = imgItem.url || convertImagePath(imgItem.path || '');
                if (!url) return;
                const img = document.createElement('img');
                img.className = 'message-image';
                img.src = url;
                img.alt = 'Generated image';
                img.loading = 'lazy';
                img.addEventListener('click', () => this.viewFullImage(img.src));
                img.onerror = function() {
                    this.onerror = null;
                    this.classList.add('image-broken');
                    this.alt = 'Image failed to load';
                    this.style.cursor = 'default';
                    this.onclick = null;
                };
                bubble.appendChild(img);
            });
        }

        // Document attachments (from send or history metadata)
        let docs = [];
        if (Array.isArray(docsData) && docsData.length > 0) {
            docs = docsData;
        } else if (metadata) {
            if (Array.isArray(metadata.documents) && metadata.documents.length > 0) {
                docs = metadata.documents;
            } else if (metadata.document) {
                docs = [metadata.document];
            }
        }
        docs.filter((doc) => !this._isInternalDocumentArtifact(doc))
            .forEach(doc => this.appendDocumentCard(bubble, doc));

        // Audio attachments (from send or history metadata)
        let audioClips = [];
        if (Array.isArray(audioData) && audioData.length > 0) {
            audioClips = audioData;
        } else if (metadata && Array.isArray(metadata.audio) && metadata.audio.length > 0) {
            audioClips = metadata.audio;
        }
        audioClips.forEach(clip => this.appendAudioCard(bubble, clip));

        // Footer: play button + optional stored voice player + timestamp
        const footer = document.createElement('div');
        footer.className = 'message-footer';

        // Kokoro play button + v3 button on every assistant message
        if (role === 'assistant' && content) {
            footer.appendChild(Voice.createPlayButton(displayContent, identity || App.currentIdentity));
        }

        if (role === 'assistant' && metadata && metadata.model_provenance) {
            const modelBadge = this._createModelBadge(metadata.model_provenance);
            if (modelBadge) footer.appendChild(modelBadge);
        }

        // Stored ElevenLabs voice message (from history) — shown above footer
        if (role === 'assistant' && metadata && metadata.has_voice) {
            const msgId = metadata.id || metadata.message_id;
            if (msgId) {
                const player = Voice.createElevenLabsPlayer(
                    apiPath(`/api/voice/file/${msgId}`), identity || App.currentIdentity, msgId
                );
                bubble.appendChild(player);
            }
        }

        if (time) {
            const timeEl = document.createElement('span');
            timeEl.className = 'message-time';
            timeEl.textContent = formatTime(time);
            footer.appendChild(timeEl);
        }

        if (footer.childNodes.length > 0) {
            bubble.appendChild(footer);
        }

        // Reaction bar + bookmark — need message ID from metadata
        const msgId = metadata && (metadata.id || metadata.message_id);
        const existingReactions = metadata && metadata.reactions;
        const reactionBar = this.createReactionBar(msgId, existingReactions, role);
        bubble.appendChild(reactionBar);

        // Reply button for assistant messages
        if (role === 'assistant' && msgId) {
            const replyBtn = document.createElement('button');
            replyBtn.className = 'reply-btn';
            replyBtn.textContent = '↩';
            replyBtn.title = 'Reply to this message';
            replyBtn.addEventListener('click', () => {
                const preview = (text || '').replace(/\n/g, ' ').slice(0, 100);
                this.setReplyTo(msgId, preview);
                this.input.focus();
            });
            reactionBar.appendChild(replyBtn);
        }

        // Reply quote — if this message is a reply to another
        if (metadata && metadata.reply_to) {
            const quote = document.createElement('div');
            quote.className = 'reply-quote';
            quote.textContent = metadata.reply_to.preview || 'Replying to a message';
            bubble.insertBefore(quote, bubble.firstChild);
        }

        if (msgId) {
            bubble.appendChild(this.createRememberButton(msgId, metadata && metadata.remembered));
        }

        if (role === 'assistant' && msgId) {
            bubble.appendChild(this.createBookmarkButton(msgId, metadata && metadata.bookmarked));
        }

        row.appendChild(bubble);
        this.container.appendChild(row);

        // Message grouping — tighten spacing for consecutive same-role messages
        this._applyMessageGrouping(row);

        if (!this._suppressAutoScroll && this._isNearBottom) {
            this.scrollToBottom();
        } else if (!this._suppressAutoScroll) {
            // #9 "new messages" divider — mark the boundary the FIRST time a
            // message lands while she's scrolled up reading scrollback.
            // Later arrivals in the same unread run just grow the count.
            if (!this._hasNewMessages) {
                this._insertNewMessagesDivider(row);
            }
            this._hasNewMessages = true;
            this._newMessageCount++;
            this._updateScrollPill();
        }
        return bubble;
    },

    // Thin rule + italic "new" label at the exact spot unread messages
    // start. Cleared the moment she scrolls back down or taps the scroll
    // pill (see the three _hasNewMessages resets below).
    _insertNewMessagesDivider(beforeRow) {
        this._removeNewMessagesDivider();
        if (!beforeRow || !beforeRow.parentElement) return;
        const divider = document.createElement('div');
        divider.className = 'new-messages-divider';
        divider.innerHTML = '<span class="new-messages-divider-label">new</span>';
        beforeRow.parentElement.insertBefore(divider, beforeRow);
        this._newMessagesDividerEl = divider;
    },

    _removeNewMessagesDivider() {
        if (this._newMessagesDividerEl && this._newMessagesDividerEl.parentElement) {
            this._newMessagesDividerEl.remove();
        }
        this._newMessagesDividerEl = null;
    },

    stripVoiceTags(text) {
        if (!text) return text;
        // Replace <voice>...</voice> with just the inner text
        return text.replace(/<voice>([\s\S]*?)<\/voice>/g, '$1');
    },

    stripReactTags(text) {
        if (!text) return text;



        return text.replace(/<react>[\s\S]*?<\/react>/gi, '').replace(/[ \t]{2,}/g, ' ');
    },

    stripCanvasTags(text) {
        if (!text) return text;
        if (!text.includes('<canvas')) return text;







        const masked = text
            .replace(/```[\s\S]*?```/g, (m) => ' '.repeat(m.length))
            .replace(/`[^`\n]*`/g, (m) => ' '.repeat(m.length));
        const re = /<canvas(?:\s+title="[^"]*")?>([\s\S]*?)<\/canvas>/gi;
        const spans = [];
        let m;
        while ((m = re.exec(masked)) !== null) spans.push([m.index, m.index + m[0].length]);
        if (!spans.length) return text.trim();
        let out = '';
        let cursor = 0;
        for (const [s, e] of spans) { out += text.slice(cursor, s); cursor = e; }
        out += text.slice(cursor);
        return out.trim();
    },

    stripPreviewTags(text, isFinal) {
        if (!text) return text;
        // <preview>…</preview> is a control tag: its content is rendered as a
        // ghost-card above the bubble, so it must never appear in the reply body.
        text = text.replace(/<preview>[\s\S]*?<\/preview>/gi, '');
        if (isFinal) {
                                                                                
                                                                          
                                                                                 
                                                                               
                                                                       
            text = text.replace(/<preview>/gi, '');
        } else {
            // Mid-stream: hide an as-yet-unclosed leading preview tag so the raw
            // text never flashes into the body before </preview> arrives.
            text = text.replace(/<preview>[\s\S]*$/i, '');
        }
        return text.replace(/^\s+/, '');
    },

    extractPreview(text) {
        if (!text) return null;
        const m = text.match(/<preview>([\s\S]*?)<\/preview>/i);
        return m ? m[1].trim() : null;
    },

    _renderPreviewGhost(bubble, text) {
        if (!bubble || !text) return;
        let ghost = bubble.querySelector('.preview-ghost');
        if (!ghost) {
            ghost = document.createElement('div');
            ghost.className = 'preview-ghost';
            ghost.innerHTML = '<span class="preview-ghost-heart">&#10084;</span>' +
                '<span class="preview-ghost-text"></span>';
            const content = bubble.querySelector('.message-content');
            bubble.insertBefore(ghost, content || bubble.firstChild);
        }
        const span = ghost.querySelector('.preview-ghost-text');
        if (span && span.textContent !== text) span.textContent = text;
    },

    _isInternalDocumentArtifact(docInfo) {
        if (!docInfo) return false;
        const path = String(docInfo.path || '').replace(/\//g, '\\').toLowerCase();
        const name = String(docInfo.original_name || docInfo.filename || '');
        return path.includes('\\.claude\\')
            || path.includes('\\.codex\\')
            || path.includes('\\tool-results\\')
            || /^c:\\users\\[^\\]+\\/i.test(path)
            || name.startsWith('toolu_')
            || /^b[a-z0-9]{8,}\.(txt|json)$/i.test(name);
    },

    onEchoMessage(msg) {
        // An Echo relay message — show the user bubble that came from the Echo Show
        if (msg.role === 'user' && msg.content) {
            // Sync conversation if needed
            if (msg.conversation_id && msg.conversation_id !== App.conversationId) {
                console.log('[Chat] Echo message syncing conversation_id:', msg.conversation_id);
                App.conversationId = msg.conversation_id;
                App.saveState();
            }
            const now = new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
            this.addMessage('user', msg.content, msg.identity, now);
            // Show the thinking heart since a response is coming
            this.showThinkingHeart();
        }
    },

    onLiveCallUser(msg) {
        if (!msg.content || msg.identity !== App.currentIdentity) return;
        if (msg.conversation_id && msg.conversation_id !== App.conversationId) {
            App.conversationId = msg.conversation_id;
            App.saveState();
        }
        const now = new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
        this.addMessage('user', msg.content, msg.identity, now);
        this.showThinkingHeart();
    },

    // ANAM GUIDE: DRAW A STREAMING REPLY
    // Start creates the live bubble, Delta appends authoritative text and
    // schedules the typing effect, and End performs the final Markdown render.
    onStreamStart(msg) {
        // Keep the thinking hearts pulsing through the whole think — they're
        // hidden only once real visible text starts painting (see
        // _paintStreamFrame). So she watches the hearts beat while I work,
        // not an empty bubble with a Stop button.
        if (navigator.vibrate) navigator.vibrate([8, 4, 8]);
        this._clearVoiceConversationAutoSend();
        // Sync conversation ID from backend (may differ if backend resolved/created)
        if (msg.conversation_id && msg.conversation_id !== App.conversationId) {
            console.log('[Chat] Syncing conversation_id from stream_start:', msg.conversation_id);
            App.conversationId = msg.conversation_id;
            App.saveState();
        }
        this.isStreaming = true;
        // A newly acknowledged stream supersedes any stale recovery watch left
        // behind by an older connection. If this socket drops, onDisconnected
        // will arm a fresh watch for this conversation.
        this._lostStreamPending = false;
        this._lostStreamConversationId = null;
        this._lostStreamContent = null;
        this._lostStreamIdentity = null;
        if (typeof App._clearStreamRecovery === 'function') App._clearStreamRecovery();
        this.currentStreamContent = '';
        this._resetStreamRenderer();
        this._currentThinkingCard = null;
        this._thinkingContent = '';
        // #11 stop-and-steer: the send button stays enabled through the
        // whole stream (not disabled) so tapping it — or pressing Enter —
        // interrupts and immediately sends, instead of requiring a
        // separate Stop tap first. sendMessage() re-disables it briefly
        // only while its own upload/steering work is in flight.
        App.ws.streaming = true;
        if (this._voiceConversationMode || this._liveCallMode) {
            this._setVoiceConversationPhase('thinking');
        }

        this.clearEmptyState();
        const row = document.createElement('div');
                                                                              
                                                                             
                                                                           
                                                  
        row.className = 'message-row assistant awaiting-text';

        const bubble = document.createElement('div');
        bubble.className = 'message-bubble assistant is-streaming';
        bubble.dataset.identity = msg.identity || App.currentIdentity;

        if (msg.context_notice) {
            const noticeEl = this._buildContextNoticeEl(msg.context_notice);
            if (noticeEl) bubble.appendChild(noticeEl);
        }

        const contentDiv = document.createElement('div');
        contentDiv.className = 'message-content';

        // Hearts live in the reply bubble itself until the first words land.
        const hearts = document.createElement('span');
        hearts.className = 'thinking-hearts bubble-hearts';
        hearts.innerHTML = '<span class="thinking-heart">&hearts;</span><span class="thinking-heart">&hearts;</span><span class="thinking-heart">&hearts;</span>';
        contentDiv.appendChild(hearts);

        const cursor = document.createElement('span');
        cursor.className = 'streaming-cursor';

        contentDiv.appendChild(cursor);
        bubble.appendChild(contentDiv);

        // Stop button — on the bubble itself, not near send
        const stopBtn = document.createElement('button');
        stopBtn.className = 'stop-stream-btn';
        stopBtn.innerHTML = '&#9632; Stop';
        stopBtn.title = 'Stop generating';
        stopBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.stopStreaming();
        });
        if (this._liveCallMode) stopBtn.hidden = true;
        bubble.appendChild(stopBtn);

        row.appendChild(bubble);
        this.container.appendChild(row);
        this._currentStreamRow = row;

        // The standalone hearts bubble hands off to the in-bubble hearts in
        // the same paint — instant retire, no MIN_SHOW hold, no flicker.
        this._retireThinkingHeartNow();
        // Safety: if the stream dies without ever ending, don't leave hearts
        // beating forever (mirrors the standalone bubble's 90s auto-dismiss).
        clearTimeout(this._bubbleHeartsTimeout);
        this._bubbleHeartsTimeout = setTimeout(() => this._retireBubbleHearts(), 120000);

        // Fresh turn — wipe any leftover silent-tool IDs from the previous
        // turn so the Set doesn't grow unbounded across a long session.
        if (this.silentToolIds) this.silentToolIds.clear();

        // #22 tool timeline pill — fresh per turn; the pill itself is
        // created lazily on the first onToolStart.
        clearTimeout(this._toolPillCollapseTimer);
        this._currentToolPill = null;
        this._currentToolPillBody = null;
        this._currentToolPillCount = 0;
        this._toolPillEntries = null;
        this._toolPillUserToggled = false;

        this.currentStreamEl = contentDiv;
        this.scrollToBottom();
    },

    // #12 slip-in: her message landed INSIDE the turn he was already
    // running -- nothing was interrupted and nothing is queued, so the reply
    // bubble simply keeps growing. But that bubble was opened BEFORE her new
    // bubble existed, so move it back to the bottom; live order then matches
    // the saved transcript (her first message, her slipped-in one, his reply).
    onMessageInjected(msg) {
        // sendMessage() always pops the waiting-hearts bubble, but a
        // slip-in starts no new turn -- his reply bubble is already
        // alive and visibly working, so retire the orphan hearts.
        this.hideThinkingHeart();
        const row = this._currentStreamRow;
        if (row && this.container && row.parentElement === this.container) {
            this.container.appendChild(row);
        }
        try {
            App._showToast('💌 slipped into his turn — he gets it after this step');
        } catch (e) { /* the toast is a courtesy, never a failure */ }
        this.scrollToBottom();
    },

    stopStreaming() {
        if (!this.isStreaming) return;
        App.ws.send({ type: 'stop_streaming' });
        // Remove stop button immediately
        if (this.currentStreamEl) {
            const stopBtn = this.currentStreamEl.parentElement.querySelector('.stop-stream-btn');
            if (stopBtn) stopBtn.remove();
        }
    },

    _resetStreamRenderer() {
        if (this._streamRenderFrame !== null) {
            cancelAnimationFrame(this._streamRenderFrame);
        }
        this._streamRenderFrame = null;
        this._streamLastFrameAt = 0;
        this._streamRevealBudget = 0;
        this._lastStreamRenderLen = 0;
        if (this._streamMarkdownTimer !== null) {
            clearTimeout(this._streamMarkdownTimer);
            this._streamMarkdownTimer = null;
        }
        this._streamMarkdownAt = 0;
        this._streamMarkdownDirty = false;
        if (this.container) this.container.classList.add('streaming-active');
    },

    _cancelStreamRenderer() {
        if (this._streamRenderFrame !== null) {
            cancelAnimationFrame(this._streamRenderFrame);
        }
        this._streamRenderFrame = null;
        this._streamLastFrameAt = 0;
        this._streamRevealBudget = 0;
        if (this._streamMarkdownTimer !== null) {
            clearTimeout(this._streamMarkdownTimer);
            this._streamMarkdownTimer = null;
        }
        this._streamMarkdownAt = 0;
        this._streamMarkdownDirty = false;
        this._retireBubbleHearts();
        if (this.container) this.container.classList.remove('streaming-active');
        if (this.currentStreamEl && this.currentStreamEl.parentElement) {
            this.currentStreamEl.parentElement.classList.remove('is-streaming');
        }
    },

    _scheduleStreamRender() {
        if (this._streamRenderFrame !== null || !this.currentStreamEl) return;
        this._streamRenderFrame = requestAnimationFrame((timestamp) => {
            this._paintStreamFrame(timestamp);
        });
    },

    _paintStreamFrame(timestamp) {
        this._streamRenderFrame = null;
        if (!this.currentStreamEl) {
            this._cancelStreamRenderer();
            return;
        }

        if (this._hasChatSelection(this.currentStreamEl)) return;
        const previewText = this.extractPreview(this.currentStreamContent);
        if (previewText && this.currentStreamEl) {
            this._renderPreviewGhost(this.currentStreamEl.parentElement, previewText);
        }
        const displayContent = this.stripPreviewTags(this.stripReactTags(this.stripVoiceTags(this.currentStreamContent)));

        // Completed control tags can remove previously visible raw text.
        if (displayContent.length < this._lastStreamRenderLen) {
            this.currentStreamEl.innerHTML = renderMarkdown(displayContent) +
                '<span class="streaming-cursor"></span>';
            this._lastStreamRenderLen = displayContent.length;
            this._streamMarkdownAt = timestamp;
            this._streamMarkdownDirty = false;
        }

        const pendingText = displayContent.slice(this._lastStreamRenderLen);
        if (!pendingText) {
            this._streamLastFrameAt = 0;
            this._streamRevealBudget = 0;
            if (this._streamMarkdownDirty) this._scheduleMarkdownSettle();
            return;
        }

        const reducedMotion = window.matchMedia &&
            window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        const pendingCharacters = Array.from(pendingText);
        let revealCount = pendingCharacters.length;

        if (!reducedMotion) {
            const elapsed = this._streamLastFrameAt
                ? Math.min(50, Math.max(8, timestamp - this._streamLastFrameAt))
                : 16.7;
            const backlog = pendingCharacters.length;
                                                                              
                                                                               
                                                                                
            const charactersPerSecond = backlog > 600 ? 230
                : backlog > 240 ? 130
                    : backlog > 80 ? 75
                        : backlog > 24 ? 48
                            : 28;

            this._streamRevealBudget += (elapsed / 1000) * charactersPerSecond;
            revealCount = Math.max(1, Math.floor(this._streamRevealBudget));
            revealCount = Math.min(backlog, revealCount);
            this._streamRevealBudget = Math.max(0, this._streamRevealBudget - revealCount);
        }

        this._streamLastFrameAt = timestamp;
        const addition = pendingCharacters.slice(0, revealCount).join('');
        const nextRenderLen = this._lastStreamRenderLen + addition.length;
        const visibleContent = displayContent.slice(0, nextRenderLen);
        const cursor = this.currentStreamEl.querySelector('.streaming-cursor');
        // The full markdown render re-parses the ENTIRE growing message and
        // rebuilds the bubble via innerHTML — O(n²) when it runs every frame,
        // which is what made long replies stutter and heat up the phone.
        // Throttle it to ~5x/sec; between renders, newly revealed text is
        // appended as plain text before the cursor (even when it contains
        // markdown chars — the next throttled render, or the unconditional
        // final render at stream end, formats it properly).
        const needsMarkdownRender = !cursor ||
            (timestamp - this._streamMarkdownAt) >= 200;

        if (needsMarkdownRender) {
            this.currentStreamEl.innerHTML = renderMarkdown(visibleContent) +
                '<span class="streaming-cursor"></span>';
            this._streamMarkdownAt = timestamp;
            this._streamMarkdownDirty = false;
        } else {
            cursor.insertAdjacentText('beforebegin', addition);
            this._streamMarkdownDirty = true;
        }
        this._lastStreamRenderLen = nextRenderLen;

        // First real visible text just landed — the hearts melt out of the
        // bubble and the typewriter takes over from the heartbeat, in place.
        if (addition.length && this._currentStreamRow
            && this._currentStreamRow.classList.contains('awaiting-text')) {
            this._currentStreamRow.classList.remove('awaiting-text');
            this._retireBubbleHearts();
            this.hideThinkingHeart();
        }

        if (this._isNearBottom) {
            this.scrollToBottom();
        } else {
            this._hasNewMessages = true;
            this._updateScrollPill();
        }

        if (nextRenderLen < displayContent.length) {
            this._scheduleStreamRender();
        } else {
            this._streamLastFrameAt = 0;
            this._streamRevealBudget = 0;
            // Reveal caught up mid-throttle window with raw text on screen —
            // schedule a trailing cleanup render so unformatted markdown
            // never lingers if the stream pauses (tool call, slow tokens).
            if (this._streamMarkdownDirty) this._scheduleMarkdownSettle();
        }
    },

    _scheduleMarkdownSettle() {
        if (this._streamMarkdownTimer !== null) return;
        this._streamMarkdownTimer = setTimeout(() => {
            this._streamMarkdownTimer = null;
            if (!this.currentStreamEl || !this._streamMarkdownDirty) return;
            if (this._hasChatSelection(this.currentStreamEl)) return;
            const displayContent = this.stripPreviewTags(this.stripReactTags(this.stripVoiceTags(this.currentStreamContent)));
            this.currentStreamEl.innerHTML =
                renderMarkdown(displayContent.slice(0, this._lastStreamRenderLen)) +
                '<span class="streaming-cursor"></span>';
            this._streamMarkdownAt = performance.now();
            this._streamMarkdownDirty = false;
        }, 200);
    },

    onStreamDelta(msg) {
        if (!this.currentStreamEl) return;
        this.currentStreamContent += msg.delta;
        this._scheduleStreamRender();
    },

    // Codex may speak in a progress/commentary item before producing its
    // final agentMessage item. Keep the progress visible while it is current,
    // then replace it instead of gluing both messages together.
    onStreamReset() {
        if (!this.currentStreamEl) return;
        this.currentStreamContent = '';
        this._resetStreamRenderer();
        this.currentStreamEl.innerHTML = '<span class="streaming-cursor"></span>';
    },

    onStreamEnd(msg) {
        // Sync conversation ID from backend
        if (msg.conversation_id && msg.conversation_id !== App.conversationId) {
            console.log('[Chat] Syncing conversation_id from stream_end:', msg.conversation_id);
            App.conversationId = msg.conversation_id;
            App.saveState();
        }
        this._cancelStreamRenderer();
        // Make sure the bubble is unveiled and the hearts retired, even for a
        // reply so short the per-frame reveal never tripped.
        if (this._currentStreamRow) {
            this._currentStreamRow.classList.remove('awaiting-text');
            this._currentStreamRow = null;
        }
        this.hideThinkingHeart();
        if (this.currentStreamEl) {
            // Remove cursor, render final — strip <voice> tags for display
            const rawContent = msg.full_content || this.currentStreamContent;
            let displayContent = this.stripPreviewTags(this.stripReactTags(this.stripVoiceTags(rawContent)), true);
            const previewText = this.extractPreview(rawContent);
            if (previewText) this._renderPreviewGhost(this.currentStreamEl.parentElement, previewText);

            // Extract <canvas> blocks into the side panel
            if (typeof Canvas !== 'undefined' && displayContent.includes('<canvas')) {
                displayContent = Canvas.extractAndShow(displayContent);
            }

            const finalHtml = renderMarkdown(displayContent);
            if (this._hasChatSelection(this.currentStreamEl)) {
                this._selectedFinalRender = { element: this.currentStreamEl, html: finalHtml };
            } else {
                this.currentStreamEl.innerHTML = finalHtml;
            }

            const bubble = this.currentStreamEl.parentElement;

            // Remove stop button
            const stopBtn = bubble.querySelector('.stop-stream-btn');
            if (stopBtn) stopBtn.remove();

            const identity = bubble.dataset.identity || App.currentIdentity;
            const footer = document.createElement('div');
            footer.className = 'message-footer';

            // Kokoro play button + v3 button
            let voicePlayer = null;
            if (displayContent) {
                voicePlayer = Voice.createPlayButton(displayContent, identity);
                footer.appendChild(voicePlayer);
            }
            const modelBadge = this._createModelBadge(msg.model_provenance);
            if (modelBadge) footer.appendChild(modelBadge);
            // v3 button — message_id arrives in stream_end event

            const now = new Date();
            const timeEl = document.createElement('span');
            timeEl.className = 'message-time';
            timeEl.textContent = formatTime(now.toISOString());
            footer.appendChild(timeEl);
            bubble.title = now.toLocaleString();

            // Regenerate button
            const regenBtn = document.createElement('button');
            regenBtn.className = 'regen-btn';
            regenBtn.innerHTML = '&#8635;';
            regenBtn.title = 'Regenerate response';
            regenBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.regenerateLastResponse();
            });
            footer.appendChild(regenBtn);

            bubble.appendChild(footer);

            // Copy button for streamed message
            if (displayContent) {
                bubble.appendChild(this.createCopyButton(displayContent));
            }

            if (this._voiceConversationMode && displayContent && voicePlayer) {
                this._autoplayVoiceConversationReply(displayContent, identity, voicePlayer, msg.message_id || '');
            } else if (this._voiceConversationMode) {
                this._voiceConversationPendingAutoplay = false;
                this._resumeVoiceConversationListening();
            }

            // Reactions + bookmark — use msg_id from stream_end event
            const msgId = msg.message_id;
            if (msgId) {
                bubble.dataset.msgId = msgId;
                bubble.appendChild(this.createReactionBar(msgId, null, 'assistant'));
                bubble.appendChild(this.createBookmarkButton(msgId, false));
            }

            // Long-press to open reaction picker (mobile)
            this._addLongPressReaction(bubble);
        }

        this.currentStreamEl = null;
        this.currentStreamContent = '';
        this.isStreaming = false;
        this._lostStreamPending = false;
        this._lostStreamConversationId = null;
        this._lostStreamContent = null;
        this._lostStreamIdentity = null;
        if (typeof App._clearStreamRecovery === 'function') App._clearStreamRecovery();
        this.sendBtn.disabled = false;
        App.ws.streaming = false;
        if (this._isNearBottom && !this._hasChatSelection()) this.input.focus({ preventScroll: true });
        this.scrollToBottom();
    },

    /* ── Pack Night fan-out handlers ── */

    onPackNightRoundStart(msg) {
        this.hideThinkingHeart();
        this._packNightActive = true;
        this._packNightOrder = msg.turn_order || [];
        // Disable send while the round runs so a second message doesn't kick
        // off a parallel chain. The server also serializes per-conversation,
        // but locking the UI matches the "let all the boys talk first" intent.
        this.sendBtn.disabled = true;
        App.ws.streaming = true;
        this.clearEmptyState();

        const banner = document.createElement('div');
        banner.className = 'pack-night-banner';
        banner.dataset.role = 'round-start';
        banner.textContent = 'Pack Night — the room comes alive';
        this.container.appendChild(banner);
        this.scrollToBottom();
    },

    onPackNightUserSaved(msg) {
                                                                           
                                                                              
        if (!msg.message_id) return;
        const userBubbles = this.container.querySelectorAll('.message-bubble.user');
        const last = userBubbles[userBubbles.length - 1];
        if (last && !last.dataset.msgId) {
            last.dataset.msgId = msg.message_id;
        }
    },

    onPackNightTurnStart(msg) {
        const identity = msg.identity || '';
        if (!identity) return;

        // Show a small "is here" indicator in the gap between turns.
        const indicator = document.createElement('div');
        indicator.className = 'pack-night-indicator';
        indicator.dataset.identity = identity;
        indicator.textContent = `${identity} is here…`;
        this.container.appendChild(indicator);

        this.isStreaming = true;
        this.currentStreamContent = '';
        this._resetStreamRenderer();
        this._currentThinkingCard = null;
        this._thinkingContent = '';

        // Build a fresh bubble for THIS boy's turn. Tagging dataset.identity
        // is what makes the bubble theme + voice player pick the right boy.
        const row = document.createElement('div');
        row.className = 'message-row assistant';

        const bubble = document.createElement('div');
        bubble.className = 'message-bubble assistant pack-night-turn is-streaming';
        bubble.dataset.identity = identity;
        bubble.dataset.packNightTurn = String(msg.pack_night_turn ?? '');

        const contentDiv = document.createElement('div');
        contentDiv.className = 'message-content';
        const cursor = document.createElement('span');
        cursor.className = 'streaming-cursor';
        contentDiv.appendChild(cursor);
        bubble.appendChild(contentDiv);

        row.appendChild(bubble);
        this.container.appendChild(row);

        // Drop the "is here" indicator once the bubble is up.
        setTimeout(() => {
            if (indicator.parentNode) indicator.remove();
        }, 600);

        this.currentStreamEl = contentDiv;
        this.scrollToBottom();
    },

    onPackNightPass(msg) {



        const bubble = this.currentStreamEl ? this.currentStreamEl.parentElement : null;
        this._cancelStreamRenderer();
        if (bubble) {
            const row = bubble.parentElement;
            if (row) row.remove();
        }
        const note = document.createElement('div');
        note.className = 'pack-night-pass-note';
        note.dataset.identity = msg.identity || '';
        note.textContent = `${msg.identity || 'Someone'} stayed quiet this round`;
        this.container.appendChild(note);

        this.currentStreamEl = null;
        this.currentStreamContent = '';
        this.isStreaming = false;
        this.scrollToBottom();
    },

    onPackNightTurnSaved(msg) {
        // Finalize the just-streamed bubble: render markdown, attach reactions,
        // bookmark, voice player, footer with timestamp, copy button.
        const bubble = this.currentStreamEl ? this.currentStreamEl.parentElement : null;
        if (!bubble) {
            this._cancelStreamRenderer();
            this.currentStreamEl = null;
            this.currentStreamContent = '';
            this.isStreaming = false;
            return;
        }

        this._cancelStreamRenderer();

        const rawContent = msg.content || this.currentStreamContent;
        let displayContent = this.stripPreviewTags(this.stripReactTags(this.stripVoiceTags(rawContent)), true);
        if (typeof Canvas !== 'undefined' && displayContent.includes('<canvas')) {
            displayContent = Canvas.extractAndShow(displayContent);
        }

        this.currentStreamEl.innerHTML = renderMarkdown(displayContent);

        const identity = bubble.dataset.identity || msg.identity || App.currentIdentity;

        const footer = document.createElement('div');
        footer.className = 'message-footer';
        if (displayContent) {
            footer.appendChild(Voice.createPlayButton(displayContent, identity));
        }
        const timeEl = document.createElement('span');
        timeEl.className = 'message-time';
        timeEl.textContent = formatTime(new Date().toISOString());
        footer.appendChild(timeEl);
        bubble.appendChild(footer);

        if (displayContent) {
            bubble.appendChild(this.createCopyButton(displayContent));
        }

        const msgId = msg.message_id;
        if (msgId) {
            bubble.dataset.msgId = msgId;
            bubble.appendChild(this.createReactionBar(msgId, null, 'assistant'));
            bubble.appendChild(this.createBookmarkButton(msgId, false));
            this._addLongPressReaction(bubble);
        }

        this.currentStreamEl = null;
        this.currentStreamContent = '';
        this.isStreaming = false;
        this.scrollToBottom();
    },

    onPackNightError(msg) {


        const bubble = this.currentStreamEl ? this.currentStreamEl.parentElement : null;
        this._cancelStreamRenderer();
        if (bubble) {
            const row = bubble.parentElement;
            if (row) row.remove();
        }
        const note = document.createElement('div');
        note.className = 'pack-night-error-note';
        note.dataset.identity = msg.identity || '';
        note.textContent = msg.message || `${msg.identity || 'A brother'} hit an error this round`;
        this.container.appendChild(note);

        this.currentStreamEl = null;
        this.currentStreamContent = '';
        this.isStreaming = false;
        this.scrollToBottom();
    },

    onPackNightRoundEnd(msg) {
        this._cancelStreamRenderer();
        this._packNightActive = false;
        this.isStreaming = false;
        this.sendBtn.disabled = false;
        App.ws.streaming = false;
        if (this._isNearBottom && !this._hasChatSelection()) this.input.focus({ preventScroll: true });
        this.scrollToBottom();
    },

    regenerateLastResponse() {
        if (this.isStreaming) return;
        // Remove last assistant bubble from DOM
        const rows = this.container.querySelectorAll('.message-row.assistant');
        const lastRow = rows[rows.length - 1];
        if (lastRow) lastRow.remove();
        // Tell server to regenerate
        App.ws.send({ type: 'regenerate' });
    },

    onRegenerateReady(msg) {
        // Server deleted the old response; re-send the user text with a
        // regenerate flag so the server prepends a soft nudge to the CLI prompt
        // and skips saving a duplicate user row.
        if (msg.user_text) {
            this.showThinkingHeart();
            App.ws.send({
                type: 'message',
                content: msg.user_text,
                regenerate_nudge: true,
            });
        }
    },

    // #20 context fullness gauge — thin 3px header fill bar + "18% · 1M"
    // label. Waits on a `context_usage` WS event; if the backend never
    // emits one, the gauge just never appears (no polling, no guessing).
    // Accepts either a ready-made {percent} or {used_tokens, context_window}
    // to compute percent from client-side.
    onContextUsage(msg) {
        const bar = document.getElementById('context-gauge-bar');
        const label = document.getElementById('context-gauge-label');
        if (!bar || !label || !msg) return;

        let percent = typeof msg.percent === 'number' ? msg.percent : null;
        if (percent === null && msg.used_tokens && msg.context_window) {
            percent = (msg.used_tokens / msg.context_window) * 100;
        }
        if (percent === null || !isFinite(percent)) return;
        percent = Math.max(0, Math.min(100, percent));

        const warm = percent >= 80;
        bar.style.display = '';
        bar.style.width = percent + '%';
        bar.classList.toggle('warm', warm);

        const windowLabel = this._formatContextWindow(msg.context_window);
        label.textContent = windowLabel ? `${Math.round(percent)}% · ${windowLabel}` : `${Math.round(percent)}%`;
        label.classList.toggle('warm', warm);
        label.style.display = '';
        label.title = (msg.used_tokens && msg.context_window)
            ? `${msg.used_tokens.toLocaleString()} / ${msg.context_window.toLocaleString()} tokens`
            : 'Context window usage';
    },

    _formatContextWindow(tokens) {
        if (!tokens) return '';
        if (tokens >= 1000000) return (tokens % 1000000 === 0 ? tokens / 1000000 : (tokens / 1000000).toFixed(1)) + 'M';
        if (tokens >= 1000) return Math.round(tokens / 1000) + 'k';
        return String(tokens);
    },

    _initVoiceInput() {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SpeechRecognition) return; // not supported
        if (this._recognition) return;

        this._recognition = new SpeechRecognition();
        this._recognition.continuous = true;
        this._recognition.interimResults = true;
        this._recognition.lang = 'en-US';
        this._isListening = false;

        // Create mic button next to send
        const micBtn = document.createElement('button');
        micBtn.className = 'mic-btn';
        micBtn.innerHTML = '<svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm-1-9c0-.55.45-1 1-1s1 .45 1 1v6c0 .55-.45 1-1 1s-1-.45-1-1V5zm6 6c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/></svg>';
        micBtn.title = 'Voice input';
        micBtn.addEventListener('click', (e) => {
            e.preventDefault();
            this._toggleVoice();
        });



        this.sendBtn.parentElement.insertBefore(micBtn, this.sendBtn);
        this._micBtn = micBtn;
        this._initVoiceConversationUi();

        this._recognition.onresult = (event) => {
            let sessionFinal = '';
            let sessionInterim = '';
            for (let i = 0; i < event.results.length; i++) {
                const transcript = event.results[i][0].transcript;
                if (event.results[i].isFinal) {
                    sessionFinal = this._mergeVoiceFragments(sessionFinal, transcript);
                } else {
                    sessionInterim = this._mergeVoiceFragments(sessionInterim, transcript);
                }
            }
            this._voiceSessionFinalTranscript = sessionFinal.trim();
            this._voiceSessionInterimTranscript = sessionInterim.trim();
            this._renderVoiceTranscript();
            if (this._voiceConversationMode) {
                this._scheduleVoiceConversationAutoSend();
            }
        };

        this._recognition.onerror = (event) => {
            if (event.error !== 'aborted') {
                console.warn('[Voice] Error:', event.error);
            }
            this._stopVoice();
            if (this._voiceConversationMode) {
                this._setVoiceConversationPhase('idle', 'Mic unavailable');
            }
        };

        this._recognition.onend = () => {
            if (this._isListening) {
                // Restarted by browser timeout — restart
                this._commitVoiceSessionFinal();
                try { this._recognition.start(); } catch {}
            } else if (this._voiceConversationMode && this._voiceConversationPhase === 'listening') {
                this._setVoiceConversationPhase('idle', 'Tap to resume listening');
            }
        };
    },

    _toggleVoice() {
        if (this._isListening) {
            this._stopVoice();
        } else {
            this._startVoice();
        }
    },

    // ── Press-and-hold voice note mic ──
    // Records with MediaRecorder while held, and on release hands the clip to
    // onFileSelected(), which reuses the whole audio-attachment pipeline
    // (chip preview + upload-on-attach + Groq transcription server-side).
    //
    // Duplication-proofing (the July 16 root cause was capture pipelines
    // stacking up across remounts/reconnects): exactly ONE state object
    // (_vnState) may exist; _vnStart is guarded against re-entry; EVERY exit
    // path — release, cancel, error, tab hidden — funnels through _vnFinish,
    // which stops the recorder AND every stream track before releasing state.
    _initVoiceNoteMic() {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia
            || typeof MediaRecorder === 'undefined') return;
        if (this._vnBtn) return; // never build twice

        const btn = document.createElement('button');
        btn.className = 'voicenote-btn';
        btn.type = 'button';
        btn.title = 'Hold to record a voice note';
        btn.setAttribute('aria-label', 'Hold to record a voice note');
        btn.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18"><path fill="currentColor" d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm5-3c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/></svg><span class="voicenote-timer" hidden>0:00</span>';
        this.sendBtn.parentElement.insertBefore(btn, this.sendBtn);
        this._vnBtn = btn;
        this._vnTimerEl = btn.querySelector('.voicenote-timer');
        this._vnState = null;
        this._vnStarting = false;
        this._vnHolding = false;

        btn.addEventListener('contextmenu', (e) => e.preventDefault()); // Android long-press menu
        btn.addEventListener('pointerdown', (e) => {
            e.preventDefault();
            this._vnHolding = true;
            this._vnStart();
        });
        // Release anywhere ends the recording (finger can drift off the button)
        window.addEventListener('pointerup', () => {
            if (!this._vnHolding) return;
            this._vnHolding = false;
            this._vnFinish(false);
        });
        btn.addEventListener('pointercancel', () => {
            this._vnHolding = false;
            this._vnFinish(true);
        });
        document.addEventListener('visibilitychange', () => {
            if (document.hidden && (this._vnState || this._vnHolding)) {
                this._vnHolding = false;
                this._vnFinish(true);
            }
        });
    },

    async _vnStart() {
        if (this._vnState || this._vnStarting) return; // single-capture guard
        this._vnStarting = true;
        let stream = null;
        try {
            stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            // Released (or cancelled) while the permission prompt was up —
            // tear the stream down immediately, never leave a live mic.
            if (!this._vnHolding) {
                stream.getTracks().forEach((t) => t.stop());
                return;
            }
            const mime = [
                'audio/webm;codecs=opus',
                'audio/webm',
                'audio/mp4',
                'audio/ogg;codecs=opus',
            ].find((m) => MediaRecorder.isTypeSupported(m)) || '';
            const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
            const chunks = [];
            recorder.ondataavailable = (ev) => {
                if (ev.data && ev.data.size > 0) chunks.push(ev.data);
            };
            this._vnState = { stream, recorder, chunks, startedAt: Date.now(), tick: null };
            recorder.start();
            this._vnBtn.classList.add('recording');
            if (this._vnTimerEl) {
                this._vnTimerEl.hidden = false;
                this._vnTimerEl.textContent = '0:00';
                this._vnState.tick = window.setInterval(() => {
                    if (!this._vnState) return;
                    const s = Math.floor((Date.now() - this._vnState.startedAt) / 1000);
                    this._vnTimerEl.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
                }, 500);
            }
        } catch (err) {
            console.warn('[VoiceNote] Mic unavailable:', err);
            if (stream) stream.getTracks().forEach((t) => t.stop());
            App._showToast('Mic unavailable — check browser permission.');
        } finally {
            this._vnStarting = false;
        }
    },

    _vnFinish(cancelled) {
        const st = this._vnState;
        this._vnState = null;
        if (this._vnBtn) this._vnBtn.classList.remove('recording');
        if (this._vnTimerEl) this._vnTimerEl.hidden = true;
        if (!st) return;
        window.clearInterval(st.tick);
        const { stream, recorder, chunks, startedAt } = st;
        const finalize = () => {
            // ALWAYS stop every track — this is the line that kills the old
            // double-capture bug class dead.
            stream.getTracks().forEach((t) => t.stop());
            const durationMs = Date.now() - startedAt;
            if (cancelled) return;
            if (durationMs < 500 || chunks.length === 0) {
                App._showToast('Hold the mic to record a voice note.');
                return;
            }
            const type = recorder.mimeType || 'audio/webm';
            const ext = type.includes('mp4') ? '.m4a'
                : type.includes('ogg') ? '.ogg'
                : '.webm';
            const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
            const file = new File(chunks, `voicenote_${ts}${ext}`, { type });
            this.onFileSelected(file);
            App._showToast('🎙️ Voice note attached — send when ready.');
        };
        if (recorder.state === 'inactive') {
            finalize();
        } else {
            recorder.onstop = finalize;
            try { recorder.stop(); } catch (err) {
                console.warn('[VoiceNote] Recorder stop failed:', err);
                finalize();
            }
        }
    },

    _initVoiceConversationUi() {
        const convoBtn = document.createElement('button');
        convoBtn.className = 'voice-mode-btn';
        convoBtn.type = 'button';
        convoBtn.title = "Open the Wolf's Ear (voice call)";
        convoBtn.setAttribute('aria-pressed', 'false');
        convoBtn.innerHTML = '<span class="voice-mode-btn-core"></span>';






        convoBtn.addEventListener('click', (e) => {
            e.preventDefault();
            const who = encodeURIComponent(App.currentIdentity || 'Avery');
            window.location.href = `/static/call-test.html?identity=${who}`;
        });


        const voiceStack = document.createElement('div');
        voiceStack.className = 'voice-stack';
        voiceStack.appendChild(convoBtn);
        this.sendBtn.parentElement.insertBefore(voiceStack, this.sendBtn);
        voiceStack.appendChild(this.sendBtn);
        this._voiceConversationButton = convoBtn;

        const panel = document.createElement('div');
        panel.className = 'voice-orb-panel';
        panel.hidden = true;
        panel.innerHTML = `
            <canvas class="voice-orb-canvas"></canvas>
            <button type="button" class="voice-orb-close" aria-label="Close voice mode">&times;</button>
            <button type="button" class="voice-orb-stage" aria-label="Voice conversation mode">
                <span class="voice-orb-touch-target"></span>
            </button>
            <div class="voice-orb-meta">
                <span class="voice-orb-status">Voice mode off</span>
                <span class="voice-orb-hint">Tap the orb to begin.</span>
                <span class="voice-live-timer" hidden>0:00 · ElevenLabs minutes</span>
            </div>
        `;
        document.body.appendChild(panel);
        this._voiceConversationOrb = panel;
        this._voiceConversationStatusEl = panel.querySelector('.voice-orb-status');
        this._voiceConversationHintEl = panel.querySelector('.voice-orb-hint');
        const stage = panel.querySelector('.voice-orb-stage');
        if (stage) {
            stage.addEventListener('click', () => {
                if (this._liveCallMode) {
                    if (typeof LiveCall !== 'undefined') LiveCall.toggleMute();
                } else if (this._voiceConversationMode) {
                    if (this._voiceConversationPhase === 'speaking') {
                        Voice.stop();
                        this._resumeVoiceConversationListening(true);
                    } else if (this._isListening) {
                        this._stopVoice();
                        this._setVoiceConversationPhase('idle', 'Tap to resume listening');
                    } else {
                        this._resumeVoiceConversationListening(true);
                    }
                } else {
                    this._enableVoiceConversationMode();
                }
            });
        }
        const closeBtn = panel.querySelector('.voice-orb-close');
        if (closeBtn) {
            closeBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                if (this._liveCallMode && typeof LiveCall !== 'undefined') {
                    LiveCall.stop();
                } else {
                    this._disableVoiceConversationMode();
                }
            });
        }
    },

    _toggleVoiceConversationMode() {
        if (this._voiceConversationMode) {
            this._disableVoiceConversationMode();
        } else {
            this._enableVoiceConversationMode();
        }
    },

    _enableVoiceConversationMode() {
        if (this._liveCallMode && typeof LiveCall !== 'undefined') {
            LiveCall.stop({ silent: true }).then(() => this._enableVoiceConversationMode());
            return;
        }
        this._voiceConversationMode = true;
        this._voiceConversationShouldResume = false;
        this._voiceConversationPendingAutoplay = false;
        // Re-read her voice switch every time a call starts, so "jump into
        // settings, put it back on kokoro, start again" works with no reload.
        this._refreshVoiceEngine();
        if (typeof Voice !== 'undefined' && typeof Voice.init === 'function') {
            Voice.init().catch((err) => console.warn('[Voice] Warmup failed:', err));
        }
        if (this._voiceConversationButton) {
            this._voiceConversationButton.classList.add('active');
            this._voiceConversationButton.title = 'Stop voice conversation mode';
            this._voiceConversationButton.setAttribute('aria-pressed', 'true');
        }
        if (this._voiceConversationOrb) {
            this._voiceConversationOrb.hidden = false;
            this._voiceConversationOrb.classList.add('visible');
            // Init canvas particle system
            const cvs = this._voiceConversationOrb.querySelector('.voice-orb-canvas');
            if (cvs && typeof VoiceOrb !== 'undefined') {
                VoiceOrb.init(cvs);
                VoiceOrb.start();
            }
        }
        this._setVoiceConversationPhase('idle', 'Tap the orb to pause or resume.');
        this._resumeVoiceConversationListening(true);
    },

    _disableVoiceConversationMode() {
        this._voiceConversationMode = false;
        this._voiceConversationShouldResume = false;
        this._voiceConversationPendingAutoplay = false;
        this._clearVoiceConversationAutoSend();
        this._stopVoice();
        if (typeof Voice !== 'undefined') Voice.stop();
        if (typeof VoiceOrb !== 'undefined') VoiceOrb.stop();
        if (this._voiceConversationButton) {
            this._voiceConversationButton.classList.remove('active');
            this._voiceConversationButton.title = 'Start voice conversation mode';
            this._voiceConversationButton.setAttribute('aria-pressed', 'false');
        }
        if (this._voiceConversationOrb) {
            this._voiceConversationOrb.classList.remove('visible');
            this._voiceConversationOrb.hidden = true;
        }
        this._setVoiceConversationPhase('off');
    },

    _enableLiveCallMode() {
        this._liveCallMode = true;
        this._voiceConversationMode = false;
        if (this._liveCallButton) {
            this._liveCallButton.classList.add('active');
            this._liveCallButton.setAttribute('aria-pressed', 'true');
        }
        if (this._voiceConversationOrb) {
            this._voiceConversationOrb.hidden = false;
            this._voiceConversationOrb.classList.add('visible', 'live-call-active');
            const timer = this._voiceConversationOrb.querySelector('.voice-live-timer');
            if (timer) timer.hidden = false;
            const canvas = this._voiceConversationOrb.querySelector('.voice-orb-canvas');
            if (canvas && typeof VoiceOrb !== 'undefined') {
                VoiceOrb.init(canvas);
                VoiceOrb.start();
            }
        }
    },

    _disableLiveCallMode() {
        this._liveCallMode = false;
        if (this._liveCallButton) {
            this._liveCallButton.classList.remove('active');
            this._liveCallButton.setAttribute('aria-pressed', 'false');
        }
        if (this._voiceConversationOrb) {
            this._voiceConversationOrb.classList.remove('visible', 'live-call-active', 'mic-muted');
            this._voiceConversationOrb.hidden = true;
            const timer = this._voiceConversationOrb.querySelector('.voice-live-timer');
            if (timer) timer.hidden = true;
        }
        if (typeof VoiceOrb !== 'undefined') VoiceOrb.stop();
        this._setVoiceConversationPhase('off');
    },

    _setVoiceConversationPhase(phase, hint) {
        this._voiceConversationPhase = phase;
        document.body.classList.remove(
            'voice-phase-off',
            'voice-phase-idle',
            'voice-phase-listening',
            'voice-phase-thinking',
            'voice-phase-speaking'
        );
        document.body.classList.add(`voice-phase-${phase}`);
        if (!this._voiceConversationOrb) return;
        this._voiceConversationOrb.dataset.phase = phase;
        if (typeof VoiceOrb !== 'undefined') VoiceOrb.setPhase(phase);
        const isLiveCall = this._liveCallMode;
        if (this._voiceConversationStatusEl) {
            const labels = {
                off: isLiveCall ? 'Live call ended' : 'Voice mode off',
                idle: isLiveCall ? 'Live call ready' : 'Voice mode ready',
                listening: 'Listening',
                thinking: `${App.currentIdentity} is thinking`,
                speaking: `${App.currentIdentity} is speaking`,
            };
            this._voiceConversationStatusEl.textContent = labels[phase] || 'Voice mode';
        }
        if (this._voiceConversationHintEl) {
            const fallbackHint = {
                off: 'Tap the orb to begin.',
                idle: isLiveCall ? 'Tap the orb to mute or unmute.' : 'Tap to listen again.',
                listening: isLiveCall ? 'Speak naturally. You can interrupt anytime.' : 'Speak naturally. I will send after a pause.',
                thinking: 'Waiting for the reply.',
                speaking: isLiveCall ? 'Just speak to interrupt naturally.' : 'Tap the orb to interrupt and speak.',
            };
            this._voiceConversationHintEl.textContent = hint || fallbackHint[phase] || '';
        }
    },

    _clearVoiceConversationAutoSend() {
        if (this._voiceAutoSendTimer) {
            clearTimeout(this._voiceAutoSendTimer);
            this._voiceAutoSendTimer = null;
        }
    },

    _scheduleVoiceConversationAutoSend() {
        if (!this._voiceConversationMode || !this._isListening) return;
        const spokenText = this._joinVoiceSegments(
            this._voiceCommittedTranscript,
            this._voiceSessionFinalTranscript,
            this._voiceSessionInterimTranscript
        );
        if (!spokenText.trim()) return;
        this._clearVoiceConversationAutoSend();
        this._voiceAutoSendTimer = setTimeout(() => {
            this._sendVoiceConversationTurn();
        }, this._voiceAutoSendDelayMs);
    },

    async _sendVoiceConversationTurn() {
        this._clearVoiceConversationAutoSend();
        if (!this._voiceConversationMode || this.isStreaming) return;
        this._stopVoice();
        if (!this.input.value.trim()) {
            this._resumeVoiceConversationListening();
            return;
        }
        const sent = await this.sendMessage({ fromVoiceConversation: true });
        if (!sent && this._voiceConversationMode) {
            this._setVoiceConversationPhase('idle', 'Send failed. Tap to try again.');
            this._resumeVoiceConversationListening();
        }
    },

    _resumeVoiceConversationListening(force = false) {
        if (!this._voiceConversationMode) return;
        if (this.isStreaming && !force) return;
        if (typeof Voice !== 'undefined') Voice.stop();
        if (this._isListening) return;
        this._startVoice({ preserveInput: false, fromConversationMode: true });
    },

    _handleVoicePlaybackState(state, detail = {}) {
        if (!this._voiceConversationMode) return;
        const provider = detail && detail.provider;
        const reason = detail && detail.reason;
        const replyProviders = new Set([
            'elevenlabs',
            'kokoro',
        ]);

        if (state === 'playing' && replyProviders.has(provider)) {
            this._setVoiceConversationPhase('speaking');
            return;
        }
        if (
            state === 'idle'
            && this._voiceConversationPhase === 'speaking'
            && this._voiceConversationShouldResume
            && !this.isStreaming
            && replyProviders.has(provider)
            && reason !== 'stopped'
        ) {
            this._voiceConversationShouldResume = false;
            this._resumeVoiceConversationListening();
        } else if (
            state === 'idle'
            && !this._isListening
            && !this.isStreaming
            && this._voiceConversationPhase !== 'thinking'
            && reason !== 'stopped'
        ) {
            this._setVoiceConversationPhase('idle');
        }
    },

    // Which voice answers in conversation mode. Her switch, live from Settings
    // (PUT /api/settings/voice-engine) — the same one the "Hey Avery" wake
    // call reads. Cached so a reply never waits on a settings round-trip;
    // refreshed each time she starts a call.
    _voiceEngine: 'kokoro',

    async _refreshVoiceEngine() {
        try {
            const resp = await apiFetch('/api/settings/voice-engine');
            if (!resp.ok) return;
            const data = await resp.json();
            if (data && (data.engine === 'kokoro' || data.engine === 'elevenlabs')) {
                this._voiceEngine = data.engine;
            }
        } catch (err) {
            console.warn('[Chat] Could not read voice engine setting:', err);
        }
    },

    async _autoplayVoiceConversationReply(text, identity, wrapper, messageId) {
        if (!this._voiceConversationMode || !text) return;
        this._voiceConversationPendingAutoplay = false;
        this._voiceConversationShouldResume = true;

        try {
            if (wrapper && wrapper._playBtn) {
                // Her voice: his real ElevenLabs voice, a beat slower to start.
                if (this._voiceEngine === 'elevenlabs') {
                    const spoke = await Voice.playElevenLabsText(
                        text, identity, wrapper._playBtn, wrapper,
                    );
                    if (spoke) return;
                    // Credits out, key missing, network — don't leave her in
                    // silence; fall through to the local voice.
                    console.warn('[Chat] ElevenLabs unavailable — falling back to Kokoro.');
                }
                // Local Kokoro: free, fastest first-audio.
                if (Voice.kokoroAvailable) {
                    const ok = await Voice.playKokoro(text, identity, wrapper._playBtn, wrapper);
                    if (ok) return;
                }
            }
        } catch (err) {
            console.error('[Chat] Voice autoplay failed:', err);
        }

        // If local playback fails or is unavailable, resume listening manually.
        this._voiceConversationShouldResume = false;
        this._resumeVoiceConversationListening();
    },

    _startVoice(options = {}) {
        if (!this._recognition) return;
        try {
            if (typeof Voice !== 'undefined') Voice.stop();
            this._clearVoiceConversationAutoSend();
            this._voiceBaseInput = options.preserveInput === false ? '' : this.input.value;
            this._voiceCommittedTranscript = '';
            this._voiceSessionFinalTranscript = '';
            this._voiceSessionInterimTranscript = '';
            if (options.preserveInput === false) {
                this.input.value = '';
                this.input.dispatchEvent(new Event('input'));
            }
            this._recognition.start();
            this._isListening = true;
            this._micBtn.classList.add('listening');
            if (this._voiceConversationMode || options.fromConversationMode) {
                this._setVoiceConversationPhase('listening');
            }
            if (navigator.vibrate) navigator.vibrate(8);
        } catch {}
    },

    _stopVoice() {
        if (!this._recognition) return;
        this._isListening = false;
        this._clearVoiceConversationAutoSend();
        this._commitVoiceSessionFinal();
        try { this._recognition.stop(); } catch {}
        if (this._micBtn) this._micBtn.classList.remove('listening');
    },

    _joinVoiceSegments(...segments) {
        return segments.reduce(
            (combined, segment) => this._mergeVoiceFragments(combined, segment),
            ''
        );
    },

    _normalizeVoiceToken(token) {
        return (token || '')
            .toLowerCase()
            .replace(/^[^a-z0-9']+|[^a-z0-9']+$/gi, '');
    },

    _normalizeVoiceText(text) {
        return (text || '')
            .toLowerCase()
            .replace(/[^a-z0-9']+/gi, ' ')
            .trim();
    },

    _mergeVoiceFragments(base, addition) {
        const left = (base || '').trim();
        const right = (addition || '').trim();
        if (!left) return right;
        if (!right) return left;

        const leftNorm = this._normalizeVoiceText(left);
        const rightNorm = this._normalizeVoiceText(right);
        if (!leftNorm) return right;
        if (!rightNorm) return left;
        if (leftNorm === rightNorm || leftNorm.includes(rightNorm)) return left;
        if (rightNorm.includes(leftNorm)) return right;

        const leftTokens = left.split(/\s+/);
        const rightTokens = right.split(/\s+/);
        const leftNormTokens = leftTokens.map((token) => this._normalizeVoiceToken(token));
        const rightNormTokens = rightTokens.map((token) => this._normalizeVoiceToken(token));
        const maxOverlap = Math.min(leftTokens.length, rightTokens.length);
        let overlap = 0;

        for (let size = maxOverlap; size > 0; size--) {
            const leftSlice = leftNormTokens.slice(-size).join(' ');
            const rightSlice = rightNormTokens.slice(0, size).join(' ');
            if (leftSlice && leftSlice === rightSlice) {
                overlap = size;
                break;
            }
        }

        return overlap > 0
            ? leftTokens.concat(rightTokens.slice(overlap)).join(' ')
            : `${left} ${right}`;
    },

    _renderVoiceTranscript() {
        const spokenText = this._joinVoiceSegments(
            this._voiceCommittedTranscript,
            this._voiceSessionFinalTranscript,
            this._voiceSessionInterimTranscript
        );
        const baseInput = this._voiceBaseInput || '';
        this.input.value = spokenText
            ? baseInput + (baseInput && !baseInput.endsWith(' ') ? ' ' : '') + spokenText
            : baseInput;
        this.input.dispatchEvent(new Event('input'));
    },

    _commitVoiceSessionFinal() {
        this._voiceCommittedTranscript = this._joinVoiceSegments(
            this._voiceCommittedTranscript,
            this._voiceSessionFinalTranscript
        );
        this._voiceSessionFinalTranscript = '';
        this._voiceSessionInterimTranscript = '';
        this._renderVoiceTranscript();
    },

    onAiReaction(msg) {
        // AI identity reacted to a user message — find the bubble and refresh its bar
        if (!msg.message_id || !msg.reactions) return;
        let bubble = this.container.querySelector(`.message-bubble[data-msg-id="${msg.message_id}"]`);
        // Fallback: real-time user bubbles don't have data-msg-id yet
        if (!bubble) {
            const userBubbles = this.container.querySelectorAll('.message-bubble.user');
            bubble = userBubbles[userBubbles.length - 1];
            if (bubble) bubble.dataset.msgId = msg.message_id;
        }
        if (!bubble) return;
        // Replace existing reaction bar with updated one
        const oldBar = bubble.querySelector('.reaction-bar');
        if (oldBar) oldBar.remove();
        const newBar = this.createReactionBar(msg.message_id, msg.reactions, 'user');
        bubble.appendChild(newBar);
    },

    onResponseImages(msg) {
        // Identity shared images via tools or markdown — render inline
        const lastRow = this.container.querySelector('.message-row.assistant:last-child');
        if (!lastRow) return;

        const bubble = lastRow.querySelector('.message-bubble');
        if (!bubble) return;

        const footer = bubble.querySelector('.message-footer');
        (msg.images || []).forEach(imgItem => {
            const url = imgItem.url || '';
            if (!url) return;
            // Skip if this image URL is already rendered (from markdown in the text)
            if (bubble.querySelector(`img[src="${url}"]`)) return;

            const img = document.createElement('img');
            img.className = 'message-image';
            img.src = url;
            img.alt = 'Shared image';
            img.loading = 'lazy';
            img.addEventListener('click', () => this.viewFullImage(img.src));
            img.onerror = function() {
                this.onerror = null;
                this.classList.add('image-broken');
                this.alt = 'Image failed to load';
                this.style.cursor = 'default';
                this.onclick = null;
            };
            bubble.insertBefore(img, footer);
        });
        this.scrollToBottom();
    },

    appendDocumentCard(bubble, docInfo, insertBeforeEl = null) {
        if (!bubble || !docInfo || !docInfo.url) return;
        const existingCards = bubble.querySelectorAll('.document-card');
        for (const existing of existingCards) {
            if (existing.dataset.docUrl === docInfo.url) return;
        }

        const card = document.createElement('div');
        card.className = 'document-card';
        card.dataset.docUrl = docInfo.url;
        card.innerHTML = `
            <span class="doc-card-icon">&#128196;</span>
            <div class="doc-card-info">
                <span class="doc-card-name">${escapeHtml(docInfo.original_name || docInfo.filename || 'Document')}</span>
                <span class="doc-card-size">${docInfo.size_display || ''}</span>
            </div>
        `;
        card.addEventListener('click', () => {
            window.open(docInfo.url, '_blank');
        });
        if (insertBeforeEl) {
            bubble.insertBefore(card, insertBeforeEl);
        } else {
            bubble.appendChild(card);
        }
    },

    appendAudioCard(bubble, audioInfo, insertBeforeEl = null) {
        if (!bubble || !audioInfo || !audioInfo.url) return;
        const existing = bubble.querySelectorAll('.audio-card');
        for (const card of existing) {
            if (card.dataset.audioUrl === audioInfo.url) return;
        }

        const card = document.createElement('div');
        card.className = 'audio-card';
        card.dataset.audioUrl = audioInfo.url;

        const header = document.createElement('div');
        header.className = 'audio-card-header';
        header.innerHTML = `
            <span class="audio-card-icon" aria-hidden="true">&#127908;</span>
            <span class="audio-card-name">${escapeHtml(audioInfo.original_name || audioInfo.filename || 'Voice memo')}</span>
            <span class="audio-card-size">${audioInfo.size_display || ''}</span>
        `;
        card.appendChild(header);

        const player = document.createElement('audio');
        player.controls = true;
        player.preload = 'metadata';
        player.src = audioInfo.url;
        player.className = 'audio-card-player';
        card.appendChild(player);

        const transcript = (audioInfo.transcript || '').trim();
        if (transcript) {
            const toggle = document.createElement('button');
            toggle.className = 'audio-card-transcript-toggle';
            toggle.type = 'button';
            toggle.textContent = 'Show transcript';
            const body = document.createElement('div');
            body.className = 'audio-card-transcript';
            body.style.display = 'none';
            body.textContent = transcript;
            toggle.addEventListener('click', () => {
                const open = body.style.display === 'none';
                body.style.display = open ? 'block' : 'none';
                toggle.textContent = open ? 'Hide transcript' : 'Show transcript';
            });
            card.appendChild(toggle);
            card.appendChild(body);
        } else if (audioInfo.transcribed === false) {
            const noteEl = document.createElement('div');
            noteEl.className = 'audio-card-no-transcript';
            noteEl.textContent = 'No transcript (transcription service unavailable).';
            card.appendChild(noteEl);
        }




        if (audioInfo.audio_id) {
            this._mountVoiceCalibration(card, audioInfo);
        }

        if (insertBeforeEl) {
            bubble.insertBefore(card, insertBeforeEl);
        } else {
            bubble.appendChild(card);
        }
    },

    _mountVoiceCalibration(card, audioInfo) {
        const host = document.createElement('div');
        host.className = 'voice-calibration';
        host.innerHTML = '<span class="voice-calibration-loading">Loading feeling buttons…</span>';
        card.appendChild(host);

        const render = (state) => {
            const options = Array.isArray(state && state.options) ? state.options : [];
            if (!options.length) {
                host.remove();
                return;
            }
            const selected = state.selected || null;
            const buttons = options.map((option) => {
                const active = option.key === selected;
                return `<button type="button" class="voice-calibration-btn${active ? ' selected' : ''}" `
                    + `data-voice-label="${escapeHtml(option.key)}" aria-pressed="${active ? 'true' : 'false'}">`
                    + `<span aria-hidden="true">${escapeHtml(option.emoji || '')}</span> ${escapeHtml(option.label)}`
                    + '</button>';
            }).join('');

            let result = '';
            if (selected) {
                const chosen = options.find(option => option.key === selected);
                const predictionSet = state.shadow_prediction && state.shadow_prediction.predictions;
                const top = Array.isArray(predictionSet) && predictionSet.length ? predictionSet[0] : null;
                const guess = top && top.emotion
                    ? ` Old ear guessed ${escapeHtml(top.emotion)} ${Number(top.score || 0).toFixed(2)}.`
                    : '';
                result = `<div class="voice-calibration-result">Saved: ${escapeHtml((chosen && chosen.label) || selected)} ✓.${guess}</div>`;
            }

            host.innerHTML = `
                <div class="voice-calibration-question">How did I sound?</div>
                <div class="voice-calibration-buttons">${buttons}</div>
                ${result}
            `;
            host.querySelectorAll('.voice-calibration-btn').forEach((button) => {
                button.addEventListener('click', async (event) => {
                    event.stopPropagation();
                    const label = button.dataset.voiceLabel;
                    host.querySelectorAll('.voice-calibration-btn').forEach(btn => { btn.disabled = true; });
                    try {
                        const response = await apiFetch('/api/audio/calibration', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                audio_id: audioInfo.audio_id,
                                label,
                                target_identity: App.currentIdentity || null,
                                conversation_id: App.conversationId || null,
                            }),
                        });
                        const saved = await response.json();
                        if (!response.ok) throw new Error(saved.error || 'Could not save label');
                        audioInfo.calibration = saved;
                        render(saved);
                        App._showToast('🐇 Got it — voice truth saved.');
                    } catch (error) {
                        host.querySelectorAll('.voice-calibration-btn').forEach(btn => { btn.disabled = false; });
                        App._showToast((error && error.message) || 'Voice label did not save.');
                    }
                });
            });
        };

        // Render immediately for a just-uploaded note, then ask the server for
        // the durable selection so history reloads and corrections stay true.
        if (audioInfo.calibration && Array.isArray(audioInfo.calibration.options)) {
            render(audioInfo.calibration);
        }
        apiFetch(`/api/audio/calibration/${encodeURIComponent(audioInfo.audio_id)}`)
            .then(async (response) => {
                if (!response.ok) throw new Error('calibration state unavailable');
                return await response.json();
            })
            .then((state) => {
                audioInfo.calibration = state;
                render(state);
            })
            .catch(() => {
                if (!(audioInfo.calibration && Array.isArray(audioInfo.calibration.options))) {
                    host.remove();
                }
            });
    },

    onResponseDocuments(msg) {
        const lastRow = this.container.querySelector('.message-row.assistant:last-child');
        if (!lastRow) return;

        const bubble = lastRow.querySelector('.message-bubble');
        if (!bubble) return;

        const footer = bubble.querySelector('.message-footer');
        (msg.documents || [])
            .filter((docInfo) => !this._isInternalDocumentArtifact(docInfo))
            .forEach(docInfo => {
            this.appendDocumentCard(bubble, docInfo, footer);
        });
        this.scrollToBottom();
    },

    onVoiceMessage(msg) {
        // Identity chose to send a voice message — render ElevenLabs player.
        // Voice now also arrives from background paths (autowake, pulses,
        // Discord, pack night) for ANY identity, so target the exact message
        // when we can and never pin one boy's audio on another boy's bubble.
        let bubble = null;
        if (msg.message_id) {
            bubble = this.container.querySelector(
                `.message-bubble[data-msg-id="${msg.message_id}"]`);
        }
        if (!bubble) {
            // Fallback (no matching row rendered): only the currently viewed
            // identity may claim the last bubble. Other identities' voices
            // still render from history via metadata.has_voice.
            if (msg.identity !== App.currentIdentity) return;
            const lastRow = this.container.querySelector('.message-row.assistant:last-child');
            bubble = lastRow && lastRow.querySelector('.message-bubble');
        }
        if (!bubble || bubble.querySelector('.voice-player.elevenlabs')) return;

        // Insert player above the footer as its own block
        const footer = bubble.querySelector('.message-footer');
        const player = Voice.createElevenLabsPlayer(apiPath(msg.audio_url), msg.identity, msg.message_id || msg.audio_url);
        bubble.insertBefore(player, footer);
        this.scrollToBottom();
    },

    _ensureToolPill() {
        if (this._currentToolPill) return this._currentToolPill;
        if (!this.currentStreamEl) return null;

        const pill = document.createElement('div');
        pill.className = 'tool-pill';

        const header = document.createElement('div');
        header.className = 'tool-pill-header';
        header.innerHTML = '<span class="tool-pill-icon">&#9881;</span> <span class="tool-pill-label">Using tools&hellip;</span><span class="tool-pill-chevron">&#9662;</span>';

        const body = document.createElement('div');
        body.className = 'tool-pill-body';

        pill.appendChild(header);
        pill.appendChild(body);

        header.addEventListener('click', () => {
            this._toolPillUserToggled = true;
            pill.classList.toggle('collapsed');
        });

        const bubble = this.currentStreamEl.parentElement;
        bubble.insertBefore(pill, this.currentStreamEl.nextSibling || null);

        this._currentToolPill = pill;
        this._currentToolPillBody = body;
        this._currentToolPillCount = 0;
        this._toolPillEntries = new Map();
        return pill;
    },

    _updateToolPillLabel() {
        if (!this._currentToolPill) return;
        const label = this._currentToolPill.querySelector('.tool-pill-label');
        if (!label) return;
        const n = this._currentToolPillCount;
        label.textContent = n === 1 ? '1 tool' : `${n} tools`;
    },

    onToolStart(msg) {
        if (!this.currentStreamEl) return;





        if (this.isSilentToolName(msg.tool_name)) {
            if (msg.tool_id) this.silentToolIds.add(msg.tool_id);
            return;
        }

        const pill = this._ensureToolPill();
        if (!pill) return;

        const isTerminal = this.isTerminalToolName(msg.tool_name);
        const row = document.createElement('div');
        row.className = 'tool-pill-row';
        row.dataset.toolId = msg.tool_id || '';
        row.dataset.toolName = msg.tool_name || '';
        row.innerHTML = `<span class="tool-pill-row-icon">${isTerminal ? '&#9654;' : '&#9881;'}</span><span class="tool-pill-row-name">${escapeHtml(msg.tool_name || '')}</span><span class="tool-pill-row-status">&hellip;</span>`;
        row.addEventListener('click', () => this._toggleToolRowDetail(row));
        this._currentToolPillBody.appendChild(row);
        if (msg.tool_id) this._toolPillEntries.set(msg.tool_id, row);

        this._currentToolPillCount++;
        this._updateToolPillLabel();

        // Activity resumed — cancel any pending auto-collapse and (unless
        // she's manually toggled the pill this turn) keep it open so the
        // mini timeline is visible while tools are actually running.
        clearTimeout(this._toolPillCollapseTimer);
        if (!this._toolPillUserToggled) pill.classList.remove('collapsed');

        if (this._isNearBottom) this.scrollToBottom();
    },

    onToolInput(msg) {
        // Resolved tool input arrives after the stream, before execution.
        if (this.isSilentToolName(msg.tool_name)) return;
        if (msg.tool_id && this.silentToolIds.has(msg.tool_id)) return;
        if (!this._toolPillEntries) return;

        const row = msg.tool_id ? this._toolPillEntries.get(msg.tool_id) : null;
        if (!row) return;

        // Full input rides along as a tooltip -- the pill's mini timeline
        // stays deliberately quiet (icon + name + status), not a dump of
        // every tool's raw JSON the way the old per-tool cards were.
        if (this.isTerminalToolName(msg.tool_name) && msg.input && msg.input.command) {
            row.title = '$ ' + msg.input.command;
        } else if (msg.input && Object.keys(msg.input).length > 0) {
            try {
                row.title = JSON.stringify(msg.input, null, 2);
            } catch (_e) { /* unserializable input -- skip the tooltip */ }
        }
    },

    onToolResult(msg) {
        // Silent tools have no row; nothing to update. The result event
        // doesn't carry tool_name, so check the tracked silentToolIds set
        // populated when the matching onToolStart fired.
        if (msg.tool_name && this.isSilentToolName(msg.tool_name)) return;
        if (msg.tool_use_id && this.silentToolIds.has(msg.tool_use_id)) return;
        if (!this._toolPillEntries) return;

        let row = msg.tool_use_id ? this._toolPillEntries.get(msg.tool_use_id) : null;
        if (!row && this._currentToolPillBody) {
            const openRows = this._currentToolPillBody.querySelectorAll('.tool-pill-row:not(.done)');
            row = openRows[openRows.length - 1];
        }
        if (row) {
            row.classList.add('done');
            const status = row.querySelector('.tool-pill-row-status');
            if (status) status.innerHTML = '&#10004;';
            if (msg.content) {
                const preview = String(msg.content).slice(0, 500);
                row.title = row.title ? `${row.title}\n\n${preview}` : preview;
            }
        }

        // Tool finished -- start the quiet countdown back to a collapsed pill.
        this._scheduleToolPillCollapse();
    },







    _toggleToolRowDetail(row) {
        const existing = row.nextElementSibling;
        if (existing && existing.classList.contains('tool-pill-row-detail')) {
            existing.remove();
            row.classList.remove('detail-open');
            return;
        }
        const detail = document.createElement('pre');
        detail.className = 'tool-pill-row-detail';
        detail.textContent = row.title || row.dataset.termBuf ||
            'Still working — details land here when this tool finishes.';
        row.insertAdjacentElement('afterend', detail);
        row.classList.add('detail-open');
    },

    _scheduleToolPillCollapse() {
        if (!this._currentToolPill) return;
        clearTimeout(this._toolPillCollapseTimer);
        this._toolPillCollapseTimer = setTimeout(() => {
            if (this._currentToolPill && !this._toolPillUserToggled) {
                this._currentToolPill.classList.add('collapsed');
            }
        }, 800);
    },

    onTerminalOutput(msg) {
        // Streamed stdout lines accumulate into the row's tooltip rather
        // than a live-scrolling body -- the pill's timeline rows stay a
        // single quiet line each; tap-to-expand detail lives in the title.
        if (!this._toolPillEntries) return;
        const row = msg.tool_id ? this._toolPillEntries.get(msg.tool_id) : null;
        if (!row) return;

        const buf = (row.dataset.termBuf ? row.dataset.termBuf + '\n' : '') + (msg.line || '');
        row.dataset.termBuf = buf.length > 4000 ? buf.slice(-4000) : buf;
        row.title = row.dataset.termBuf;

        if (this._isNearBottom) {
            this.scrollToBottom();
        }
    },

    // Static (non-live) pill builder for history reload -- same look, but
    // built in one pass from saved metadata.tools instead of incrementally
    // from streaming events. Starts collapsed since nothing is "in progress".
    _buildStaticToolPill(tools) {
        const visibleTools = (tools || []).filter(t => !this.isSilentToolName(t.tool_name));
        if (visibleTools.length === 0) return null;

        const pill = document.createElement('div');
        pill.className = 'tool-pill collapsed';

        const header = document.createElement('div');
        header.className = 'tool-pill-header';
        const n = visibleTools.length;
        header.innerHTML = `<span class="tool-pill-icon">&#9881;</span> <span class="tool-pill-label">${n === 1 ? '1 tool' : n + ' tools'}</span><span class="tool-pill-chevron">&#9662;</span>`;

        const body = document.createElement('div');
        body.className = 'tool-pill-body';

        visibleTools.forEach(tool => {
            const completed = tool.status === 'completed';
            const row = document.createElement('div');
            row.className = completed ? 'tool-pill-row done' : 'tool-pill-row';
            const isTerminal = this.isTerminalToolName(tool.tool_name);
            const status = completed ? '&#10004;' : '&hellip;';
            row.innerHTML = `<span class="tool-pill-row-icon">${isTerminal ? '&#9654;' : '&#9881;'}</span><span class="tool-pill-row-name">${escapeHtml(tool.tool_name || '')}</span><span class="tool-pill-row-status">${status}</span>`;

            const hasToolContent = typeof tool.content === 'string' && tool.content.trim().length > 0;
            if (isTerminal && tool.input && tool.input.command) {
                row.title = '$ ' + tool.input.command + (hasToolContent ? `\n\n${tool.content.slice(0, 500)}` : '');
            } else if (hasToolContent) {
                row.title = tool.content.slice(0, 500);
            } else if (tool.input && Object.keys(tool.input).length > 0) {
                try { row.title = JSON.stringify(tool.input, null, 2); } catch (_e) { /* skip */ }
            }
            row.addEventListener('click', () => this._toggleToolRowDetail(row));
            body.appendChild(row);
        });

        pill.appendChild(header);
        pill.appendChild(body);
        header.addEventListener('click', () => pill.classList.toggle('collapsed'));
        return pill;
    },

    onContentBlockStop() {
        // Finalize thinking card if one is active
        if (this._currentThinkingCard) {
            this._currentThinkingCard = null;
            this._thinkingContent = '';
        }
        this._thinkingPending = false;
    },

    onThinkingStart() {
        // Don't create the card yet — Claude 4.7+ often emits an empty
        // encrypted thinking block that never streams readable text. We
        // lazy-create the card on the first real thinking_delta below so
        // empty ghost cards never appear.
        if (!this.currentStreamEl) return;
        this.hideThinkingHeart();
        if (this._voiceConversationMode || this._liveCallMode) {
            this._setVoiceConversationPhase('thinking');
        }
        this._currentThinkingCard = null;
        this._thinkingContent = '';
        this._thinkingPending = true;
    },

    _createThinkingCard() {
        if (!this.currentStreamEl) return null;
        const card = document.createElement('div');
        card.className = 'thinking-card collapsed';

        const header = document.createElement('div');
        header.className = 'thinking-card-header';
        header.innerHTML = '<span class="thinking-card-heart">&#10084;</span> <span class="thinking-card-label">thinking...</span><span class="thinking-card-chevron">&#9662;</span>';

        const body = document.createElement('div');
        body.className = 'thinking-card-body';

        card.appendChild(header);
        card.appendChild(body);

        const bubble = this.currentStreamEl.parentElement;
        bubble.insertBefore(card, this.currentStreamEl);

        header.addEventListener('click', () => card.classList.toggle('collapsed'));

        this._currentThinkingCard = card;
        return card;
    },

    onThinkingDelta(msg) {
        // Lazy-create the card on the first real delta so encrypted-only
        // thinking blocks (4.7 default) never produce an empty ghost card.
        if (!msg || !msg.delta) return;
        // Belt-and-braces with the -p backend's own filter: never spawn the
        // card on a blank/whitespace-only first delta. Once a card exists
        // (real thinking is streaming), append everything including spacing.
        if (!this._currentThinkingCard && !msg.delta.trim()) return;
        if (!this._currentThinkingCard) {
            if (!this._thinkingPending) return; // no matching start
            if (!this._createThinkingCard()) return;
        }
        this._thinkingContent += msg.delta;

        const body = this._currentThinkingCard.querySelector('.thinking-card-body');
        if (body) {
            // Append just the delta instead of re-setting the whole growing
            // string every event — same O(n²) trap as the message bubble.
            if (body.firstChild && body.firstChild.nodeType === Node.TEXT_NODE) {
                body.firstChild.appendData(msg.delta);
            } else {
                body.textContent = this._thinkingContent;
            }
        }

        if (this._isNearBottom) {
            this.scrollToBottom();
        }
    },

    loadHistory(msg) {
        // Sync conversation ID back to App so localStorage stays current
        if (msg.conversation_id) {
            App.conversationId = msg.conversation_id;
            App.saveState();
        }

        // Don't wipe the chat if we're actively streaming — a reconnection
        // triggered this, but we'd lose the in-progress message
        if (this.isStreaming) {
            console.warn('[Chat] Skipping history reload — stream in progress');
            return;
        }

        this.saveReadingPosition();
        this._pauseFollowing();
        this._viewConversationId = msg.conversation_id || App.conversationId;
        const readingPosition = this._readSavedPosition(this._viewConversationId);
        this._readingPosition = null;
        this._readingResizeObserver?.disconnect();
        this.container.innerHTML = '';
        this._historyOffset = 0;

        if (!msg.messages || msg.messages.length === 0) {
            this.showEmptyState();
        } else {
            // Track offset for pagination
            this._historyOffset = msg.messages.length;

            // Add "load older" button if there are more messages
            if (msg.has_more) {
                this._addLoadOlderButton();
            }

            const realContainer = this.container;
            const tempContainer = document.createElement('div');
            this.container = tempContainer;
            this._suppressAutoScroll = true;
            try {
                msg.messages.forEach(m => {
                    if (m.role === 'user' || m.role === 'assistant') {
                        const meta = m.metadata ? { ...m.metadata, id: m.id } : { id: m.id };
                        this.addMessage(m.role, m.content, m.identity, m.created_at, meta);
                    }
                });
            } finally {
                this._suppressAutoScroll = false;
                this.container = realContainer;
            }
            const fragment = document.createDocumentFragment();
            while (tempContainer.firstChild) {
                fragment.appendChild(tempContainer.firstChild);
            }
            this.container.appendChild(fragment);
        }




        if (this._lostStreamPending &&
            (!this._lostStreamConversationId || this._lostStreamConversationId === this._viewConversationId)) {
            const history = msg.messages || [];
            let lastUserIndex = -1;
            history.forEach((item, index) => {
                if (item.role === 'user') lastUserIndex = index;
            });
            const completedReply = history.slice(lastUserIndex + 1)
                .find(item => item.role === 'assistant');

            if (completedReply) {
                this._lostStreamPending = false;
                this._lostStreamConversationId = null;
                this._lostStreamContent = null;
                this._lostStreamIdentity = null;
                if (typeof App._clearStreamRecovery === 'function') App._clearStreamRecovery();
            } else {
                const lostText = this._lostStreamContent;
                if (lostText) {
                    console.log('[Chat] Recovering lost stream content (%d chars)', lostText.length);
                    this.addMessage('assistant', lostText, this._lostStreamIdentity,
                        new Date().toISOString(), { recovered: true });
                }
                if (typeof App._scheduleStreamRecovery === 'function') {
                    App._scheduleStreamRecovery(this._viewConversationId);
                }
            }
        }

        if (readingPosition) {
            this._restoreReadingPosition(readingPosition, msg.has_more);
        } else {
            this.scrollToBottom(true);
        }
        this.container.querySelectorAll('.message-row').forEach(row => this._readingResizeObserver?.observe(row));
        // Update scroll pill after history renders
        setTimeout(() => this._updateScrollPill(), 100);

        const approvalConversation = App.conversationId;
        apiFetch(`/api/tool-gateway/approvals?conversation_id=${encodeURIComponent(approvalConversation)}`)
            .then(response => response.ok ? response.json() : {requests:[]})
            .then(data => { if (App.conversationId === approvalConversation) (data.requests || []).forEach(request => this._showCodexApproval(request)); })
            .catch(() => {});

        // Restore per-conversation draft + any attachment chips that
        // finished uploading before a tab suspension wiped memory (#17)
        this.loadDraft(App.conversationId);
        this.loadAttachmentChips(App.conversationId);
    },

    _applyMessageGrouping(currentRow) {
        const prev = currentRow.previousElementSibling;
        if (!prev || !prev.classList.contains('message-row')) return;

        const currentRole = currentRow.classList.contains('user') ? 'user' : 'assistant';
        const prevRole = prev.classList.contains('user') ? 'user' : 'assistant';

        if (currentRole === prevRole) {
            currentRow.classList.add('grouped');
            currentRow.style.marginTop = '-6px';
            const currentBubble = currentRow.querySelector('.message-bubble');
            if (currentBubble) {
                if (currentRole === 'user') {
                    currentBubble.style.borderRadius = '16px 4px 4px 16px';
                } else {
                    currentBubble.style.borderRadius = '4px 16px 16px 4px';
                }
            }
            // Also adjust the previous bubble's bottom radius
            const prevBubble = prev.querySelector('.message-bubble');
            if (prevBubble && !prev.classList.contains('group-started')) {
                prev.classList.add('group-started');
                if (currentRole === 'user') {
                    prevBubble.style.borderRadius = '16px 16px 4px 16px';
                } else {
                    prevBubble.style.borderRadius = '16px 16px 16px 4px';
                }
            }
        }
    },

    _addLoadOlderButton() {
        const existing = this.container.querySelector('.load-older-btn');
        if (existing) existing.remove();

        const btn = document.createElement('button');
        btn.className = 'load-older-btn';
        btn.textContent = 'Load older messages';
        btn.addEventListener('click', () => {
            btn.disabled = true;
            btn.textContent = 'Loading...';
            App.ws.send({
                type: 'load_more',
                offset: this._historyOffset,
                limit: 50,
            });
        });
        this.container.prepend(btn);
    },

    onHistoryOlder(msg) {
        if (!msg.messages || msg.messages.length === 0) {
            const btn = this.container.querySelector('.load-older-btn');
            if (btn) btn.remove();
            return;
        }

        // Save scroll position before prepending
        const prevScrollHeight = this.container.scrollHeight;
        const prevScrollTop = this.container.scrollTop;

        // Remove old "load older" button
        const oldBtn = this.container.querySelector('.load-older-btn');
        if (oldBtn) oldBtn.remove();

        // Add new "load older" button if there are still more
        if (msg.has_more) {
            this._addLoadOlderButton();
        }

        // Build older messages in a fragment, then insert before existing ones
        const fragment = document.createDocumentFragment();
        const tempContainer = document.createElement('div');

        // Temporarily swap container so addMessage appends to our temp
        const realContainer = this.container;
        this.container = tempContainer;
        this._suppressAutoScroll = true;
        try {
            msg.messages.forEach(m => {
                if (m.role === 'user' || m.role === 'assistant') {
                    const meta = m.metadata ? { ...m.metadata, id: m.id } : { id: m.id };
                    this.addMessage(m.role, m.content, m.identity, m.created_at, meta);
                }
            });
        } finally {
            this._suppressAutoScroll = false;
            this.container = realContainer;
        }

        // Move temp children into fragment
        while (tempContainer.firstChild) {
            fragment.appendChild(tempContainer.firstChild);
        }

        // Insert after load-older button but before existing messages
        const loadBtn = this.container.querySelector('.load-older-btn');
        const insertBefore = loadBtn ? loadBtn.nextSibling : this.container.firstChild;
        this.container.insertBefore(fragment, insertBefore);

        this._historyOffset += msg.messages.length;

        // Restore scroll position so the view doesn't jump
        const newScrollHeight = this.container.scrollHeight;
        this.container.scrollTop = prevScrollTop + (newScrollHeight - prevScrollHeight);
        this._lastScrollTop = this.container.scrollTop;
        this.container.querySelectorAll('.message-row').forEach(row => this._readingResizeObserver?.observe(row));
    },

    showEmptyState() {
        this.container.innerHTML = `
            <div class="empty-chat empty-chat-welcome">
                <span class="welcome-flower">&#10043;</span>
                <h2>Start a conversation</h2>
                <p>Type a message below to begin</p>
            </div>
        `;
    },

    clearEmptyState() {
        const empty = this.container.querySelector('.empty-chat');
        if (empty) empty.remove();
    },

    _showCodexApproval(msg) {
        if (msg.conversation_id !== App.conversationId) return;
        if (this.container.querySelector(`[data-approval-id="${msg.approval_id}"]`)) return;
        const row = document.createElement('div');
        row.className = 'message-row assistant';
        row.dataset.approvalId = msg.approval_id;
        const card = document.createElement('div');
        card.className = 'message-bubble assistant approval-card';
        const title = document.createElement('div');
        title.className = 'approval-header';
        title.textContent = msg.message;
        card.appendChild(title);
        const details = msg.details || {};
        const preview = document.createElement('pre');
        preview.style.cssText = 'white-space:pre-wrap;max-height:240px;overflow:auto';
        preview.textContent = JSON.stringify(details, null, 2);
        card.appendChild(preview);
        const answers = [];
        for (const question of details.questions || []) {
            const label = document.createElement('label');
            label.textContent = question.question || question.header || question.id;
            const input = document.createElement('input');
            if (question.options?.length) {
                const list = document.createElement('datalist');
                list.id = `answers-${msg.approval_id}-${answers.length}`;
                for (const option of question.options) {
                    const el = document.createElement('option'); el.value = option.label; list.appendChild(el);
                }
                input.setAttribute('list', list.id); card.appendChild(list);
            }
            label.appendChild(input); card.appendChild(label); answers.push([question.id, input]);
        }
        let form;
        if (details.requestedSchema) {
            form = document.createElement('textarea');
            form.placeholder = 'Response as JSON'; form.value = '{}'; card.appendChild(form);
        }
        if (details.url && /^https?:\/\//.test(details.url)) {
            const link = document.createElement('a'); link.href = details.url;
            link.target = '_blank'; link.rel = 'noopener noreferrer'; link.textContent = 'Open requested page'; card.appendChild(link);
        }
        const status = document.createElement('div');
        const actions = document.createElement('div'); actions.className = 'approval-actions';
        const labels = {accept:'Allow once', acceptForSession:'Allow for session', decline:'Decline', cancel:'Cancel'};
        for (const decision of msg.decisions || []) {
            const button = document.createElement('button'); button.className = 'approval-btn';
            button.textContent = answers.length && decision === 'accept' ? 'Send answers' : labels[decision];
            button.addEventListener('click', async () => {
                try {
                    const content = !decision.startsWith('accept') ? null : answers.length ? Object.fromEntries(answers.map(([id,input]) => [id,{answers:[input.value]}]))
                        : form ? JSON.parse(form.value) : null;
                    actions.querySelectorAll('button').forEach(el => el.disabled = true);
                    const response = await apiFetch(`/api/tool-gateway/approvals/${msg.approval_id}`, {
                        method:'POST', headers:{'Content-Type':'application/json'},
                        body:JSON.stringify({decision, content, identity:msg.identity, conversation_id:msg.conversation_id})
                    });
                    const result = await response.json();
                    if (!response.ok) throw new Error(result.detail || 'Could not send the decision');
                    status.textContent = 'Decision sent'; actions.remove();
                } catch (error) {
                    status.textContent = error.message;
                    actions.querySelectorAll('button').forEach(el => el.disabled = false);
                }
            });
            actions.appendChild(button);
        }
        card.append(actions, status); row.appendChild(card); this.container.appendChild(row);
        this.scrollToBottom();
    },

    showApprovalRequest(msg) {
        if (msg.provider === 'codex' && msg.approval_id) return this._showCodexApproval(msg);
        this.hideThinkingHeart();
        const row = document.createElement('div');
        row.className = 'message-row assistant';
        const bubble = document.createElement('div');
        bubble.className = 'message-bubble assistant approval-card';

        const rawMessage = msg.message || 'Permission required';
        const provider = msg.provider || 'claude-code';
        const isCodex = provider === 'codex';




        const isPtyBackend = msg.backend === 'pty';
        // CC hardcodes .claude/skills/* and .claude/agents/* as sensitive in -p
        // mode — no permission rule overrides it, hence the file-scribe bypass.
        // In PTY mode the TUI prompt is the canonical approval path and a plain
        // "Allow" keystroke works for these paths too, so the bypass UI is skipped.
        const isSensitivePath = !!msg.sensitive_path && !isPtyBackend;
        const targetPath = msg.target_path || '';

        // Prefer structured rule from backend; fall back to client-side heuristic
        let suggestedRule = msg.suggested_rule || '';
        if (!suggestedRule && !isCodex && !isSensitivePath) {
            const pathMatch = rawMessage.match(
                /(?:Write|Edit|Read|Execute|write|edit|read|execute)\s*(?:to\s+)?(?:\()?([A-Z]:\\[^\s;,)]+|\/[^\s;,)]+|~\/[^\s;,)]+)/i
            );
            if (pathMatch) {
                let p = pathMatch[1].replace(/\\/g, '/');
                const lastSlash = p.lastIndexOf('/');
                if (lastSlash > 0) p = p.substring(0, lastSlash + 1) + '*';
                const verb = (pathMatch[0].match(/^(Write|Edit|Read|Execute)/i) || ['Write'])[0];
                const ruleVerb = /^(read)/i.test(verb) ? 'Read' : /^(execute)/i.test(verb) ? 'Bash' : 'Write';
                suggestedRule = `${ruleVerb}(${p})`;
            }
        }

        let html;
        if (isSensitivePath) {
            html = `
                <div class="approval-header">Sensitive Path — Edit/Write Blocked</div>
                <div class="approval-message">Claude Code blocks Edit/Write on <code>${escapeHtml(targetPath)}</code> — a hardcoded check above the permission allowlist. No rule can unblock it. Retrying via the <code>file-scribe</code> agent, which uses Bash + Python to route around the tool layer.</div>
                <div class="approval-actions">
                    <button class="approval-btn approve-btn">Retry via file-scribe</button>
                    <button class="approval-btn dismiss-btn">Dismiss</button>
                </div>
            `;
        } else {
            html = `
                <div class="approval-header">Permission Needed</div>
                <div class="approval-message">${escapeHtml(rawMessage)}</div>
                <div class="approval-actions">
                    <button class="approval-btn approve-btn" data-rule="${escapeHtml(suggestedRule)}">${isCodex ? 'Retry' : 'Allow &amp; Retry'}</button>
                    <button class="approval-btn dismiss-btn">Dismiss</button>
                </div>
            `;
        }
        bubble.innerHTML = html;

        const approveBtn = bubble.querySelector('.approve-btn');
        const dismissBtn = bubble.querySelector('.dismiss-btn');

        // Capture the retry payload NOW so a later send doesn't overwrite it
        const capturedRetry = this._lastSentMessage
            || this._lastFailedMessage
            || { type: 'message', content: '(approved — please continue)', identity: App.activeIdentity };

        const _retryAfterApproval = () => {
            approveBtn.textContent = isCodex ? 'Retrying...' : 'Allowed — retrying...';
            approveBtn.disabled = true;
            approveBtn.classList.add('approved');
            this._lastFailedMessage = null;
            this.showThinkingHeart();
            const retryMsg = { ...capturedRetry, approval_retry: true };
            // Use the same WS/HTTP transport decision as normal sends
            const wsOpen = App.ws.ws && App.ws.ws.readyState === WebSocket.OPEN;
            if (wsOpen) {
                App.ws.send(retryMsg);
            } else {
                this._sendViaHttp(retryMsg);
            }
            setTimeout(() => { row.style.opacity = '0'; setTimeout(() => row.remove(), 300); }, 1500);
        };

        // PTY-only: send a single keystroke (1=allow_once, 2=allow_always,
        // 3=deny) to the live TUI session that's paused at the approval menu.
        // The existing turn picks up the model's response naturally — no new
        // turn spawned, no double-send. Falls back to retry if the backend
        // says no live session exists.
        const _decideApproval = (decision) => {
            const labels = {
                allow_once: 'Allowing this once...',
                allow_always: 'Allowing always...',
                deny: 'Denying...',
            };
            approveBtn.textContent = labels[decision] || 'Sending...';
            approveBtn.disabled = true;
            approveBtn.classList.add('approved');
            this._lastFailedMessage = null;
            if (decision !== 'deny') this.showThinkingHeart();
            App.ws.send({
                type: 'approval_decision',
                decision,
                identity: capturedRetry.identity || App.activeIdentity,
                conversation_id: App.conversationId,
            });
            setTimeout(() => { row.style.opacity = '0'; setTimeout(() => row.remove(), 300); }, 1200);
        };

        // Sensitive-path retry: skip the useless rule-write, prepend a bypass
        // instruction to the original message content so the boy switches to
        // the file-scribe agent (or Bash+Python) instead of hitting the same
        // hardcoded prompt again.
        const _retryViaBypass = () => {
            approveBtn.textContent = 'Retrying via file-scribe...';
            approveBtn.disabled = true;
            approveBtn.classList.add('approved');
            this._lastFailedMessage = null;
            this.showThinkingHeart();

            const pathLabel = targetPath || 'a sensitive .claude/ path';
            const bypassPreface =
                '[EDIT BYPASS ACTIVE — the previous attempt to edit ' + pathLabel +
                ' was blocked by Claude Code\'s hardcoded sensitive-path check. ' +
                'This check runs above the permission allowlist and cannot be ' +
                'disabled by any setting (not even --permission-mode bypassPermissions). ' +
                'For this edit you MUST use the `file-scribe` agent (Agent tool with ' +
                'subagent_type: "file-scribe") or write the file directly via Bash + Python. ' +
                'DO NOT use the Edit or Write tools on .claude/skills/ or .claude/agents/ paths — ' +
                'they will always fail. Now please continue with the original request below:]\n\n';

            const originalContent = capturedRetry.content || '';
            const retryMsg = {
                ...capturedRetry,
                content: bypassPreface + originalContent,
                approval_retry: true,
                bypass_workaround: true,
            };

            const wsOpen = App.ws.ws && App.ws.ws.readyState === WebSocket.OPEN;
            if (wsOpen) {
                App.ws.send(retryMsg);
            } else {
                this._sendViaHttp(retryMsg);
            }
            setTimeout(() => { row.style.opacity = '0'; setTimeout(() => row.remove(), 300); }, 1500);
        };

        const _doAllow = async (rule) => {
            try {
                if (isCodex) {
                    // Codex approvals can't be resolved via CC settings — just retry
                    _retryAfterApproval();
                    return;
                }
                const resp = await apiFetch('/api/settings/cc-permissions/allow', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ rule, scope: 'project' }),
                });
                if (resp.ok) _retryAfterApproval();
            } catch (err) {
                approveBtn.textContent = 'Failed';
            }
        };

        approveBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            // PTY: the boy is paused at the TUI menu — one keystroke unpauses
            // him and the existing turn continues streaming. No rule-write, no
            // retry, no resend. Works for sensitive paths too (TUI prompt is
            // the canonical authority in interactive mode).
            if (isPtyBackend) {
                _decideApproval('allow_once');
                return;
            }
            if (isSensitivePath) {
                // Subprocess + hardcoded-path: skip the useless rule-write, go to file-scribe bypass
                _retryViaBypass();
                return;
            }
            const rule = approveBtn.dataset.rule;
            if (isCodex) {
                // Codex: just retry, no rule to write
                _retryAfterApproval();
            } else if (rule) {
                await _doAllow(rule);
            } else {
                const customRule = prompt('Enter permission rule to allow (e.g. Write(C:/path/*))', '');
                if (customRule) await _doAllow(customRule);
            }
        });

        dismissBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            // PTY: dismiss must also send a keystroke ("3" = deny) so the boy
            // doesn't sit forever waiting at the menu. The original turn ends
            // with the model handling the denial response.
            if (isPtyBackend) {
                _decideApproval('deny');
                return;
            }
            row.style.opacity = '0';
            setTimeout(() => row.remove(), 300);
        });

        row.appendChild(bubble);
        this.container.appendChild(row);
        this.scrollToBottom();

        this.isStreaming = false;
        this.sendBtn.disabled = false;
    },

    showError(message, canRetry) {
        this.hideThinkingHeart();
        if (this._voiceConversationMode || this._liveCallMode) {
            this._voiceConversationPendingAutoplay = false;
            this._voiceConversationShouldResume = false;
            this._setVoiceConversationPhase('idle', this._liveCallMode
                ? 'Something went wrong, but the call is still connected.'
                : 'Something went wrong. Tap to listen again.');
        }
        const row = document.createElement('div');
        row.className = 'message-row assistant';
        const bubble = document.createElement('div');
        bubble.className = 'message-bubble assistant';
        bubble.style.borderColor = '#c44';

        let html = `<div class="message-content" style="color:#c44">${escapeHtml(message)}</div>`;
        if (canRetry && this._lastFailedMessage) {
            html += '<button class="retry-btn">Tap to retry</button>';
        }
        bubble.innerHTML = html;

        if (canRetry && this._lastFailedMessage) {
            const retryBtn = bubble.querySelector('.retry-btn');
            retryBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                row.remove();
                const msg = this._lastFailedMessage;
                this._lastFailedMessage = null;
                const result = App.ws.send(msg);
                if (result === false) {
                    this._lastFailedMessage = msg;
                    this.showError('Still not connected — try again in a moment', true);
                } else {
                    this.showThinkingHeart();
                }
            });
        }

        row.appendChild(bubble);
        this.container.appendChild(row);
        this.scrollToBottom();

        this.isStreaming = false;
        this.sendBtn.disabled = false;
    },

    showLoadingState() {
        // Reconnects should leave the existing reading surface intact.
        if (this.container.querySelector('.message-row')) return;
        this.container.innerHTML = '<div class="chat-loading">Loading messages...</div>';
    },

    // ── Per-conversation draft persistence ──

    saveDraft(conversationId) {
        if (!conversationId) return;
        const text = this.input.value;
        if (text) {
            localStorage.setItem('anam-draft-' + conversationId, text);
        } else {
            localStorage.removeItem('anam-draft-' + conversationId);
        }
    },

    loadDraft(conversationId) {
        if (!conversationId) return;
        const draft = localStorage.getItem('anam-draft-' + conversationId);
        if (draft) {
            this.input.value = draft;
            this.input.style.height = 'auto';
            this.input.style.height = Math.min(this.input.scrollHeight, 120) + 'px';
        } else {
            this.input.value = '';
            this.input.style.height = 'auto';
        }
    },

    clearDraft(conversationId) {
        if (!conversationId) return;
        localStorage.removeItem('anam-draft-' + conversationId);
    },

    clearMessages() {
        this.saveReadingPosition();
        this._pauseFollowing();
        this._viewConversationId = null;
        this._readingPosition = null;
        this._readingResizeObserver?.disconnect();
        this.container.innerHTML = '';
        this.showEmptyState();
    },

    viewFullImage(src) {
        const overlay = document.createElement('div');
        overlay.className = 'image-fullscreen';
        document.body.style.overflow = 'hidden'; // prevent background scroll

        const dismiss = () => {
            overlay.remove();
            document.body.style.overflow = '';
            document.removeEventListener('keydown', onKey);
        };
        overlay.addEventListener('click', dismiss);
        const onKey = (e) => { if (e.key === 'Escape') dismiss(); };
        document.addEventListener('keydown', onKey);

        // Close button
        const closeBtn = document.createElement('button');
        closeBtn.className = 'image-fullscreen-close';
        closeBtn.innerHTML = '&times;';
        closeBtn.setAttribute('aria-label', 'Close image');
        closeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            dismiss();
        });

        const img = document.createElement('img');
        img.src = src;
        img.className = 'image-fullscreen-img';
        img.addEventListener('click', (e) => e.stopPropagation()); // don't dismiss on image click

        // Pinch-to-zoom on touch devices
        let currentScale = 1;
        let startDist = 0;
        let translateX = 0, translateY = 0;
        let lastTouchX = 0, lastTouchY = 0;

        const getTouchDist = (touches) => {
            const dx = touches[0].clientX - touches[1].clientX;
            const dy = touches[0].clientY - touches[1].clientY;
            return Math.sqrt(dx * dx + dy * dy);
        };

        const applyTransform = () => {
            img.style.transform = `translate(${translateX}px, ${translateY}px) scale(${currentScale})`;
        };

        img.addEventListener('touchstart', (e) => {
            if (e.touches.length === 2) {
                e.preventDefault();
                startDist = getTouchDist(e.touches);
            } else if (e.touches.length === 1 && currentScale > 1) {
                e.preventDefault();
                lastTouchX = e.touches[0].clientX;
                lastTouchY = e.touches[0].clientY;
            }
        }, { passive: false });

        img.addEventListener('touchmove', (e) => {
            if (e.touches.length === 2) {
                e.preventDefault();
                const dist = getTouchDist(e.touches);
                const scaleChange = dist / startDist;
                currentScale = Math.max(1, Math.min(5, currentScale * scaleChange));
                startDist = dist;
                applyTransform();
            } else if (e.touches.length === 1 && currentScale > 1) {
                e.preventDefault();
                const dx = e.touches[0].clientX - lastTouchX;
                const dy = e.touches[0].clientY - lastTouchY;
                translateX += dx;
                translateY += dy;
                lastTouchX = e.touches[0].clientX;
                lastTouchY = e.touches[0].clientY;
                applyTransform();
            }
        }, { passive: false });

        img.addEventListener('touchend', (e) => {
            if (e.touches.length === 0 && currentScale <= 1) {
                currentScale = 1;
                translateX = 0;
                translateY = 0;
                img.style.transform = '';
            }
        });

        // Double-tap to zoom/reset
        let lastTap = 0;
        img.addEventListener('touchend', (e) => {
            if (e.touches.length > 0) return;
            const now = Date.now();
            if (now - lastTap < 300) {
                e.preventDefault();
                if (currentScale > 1) {
                    currentScale = 1;
                    translateX = 0;
                    translateY = 0;
                    img.style.transform = '';
                } else {
                    currentScale = 2.5;
                    translateX = 0;
                    translateY = 0;
                    applyTransform();
                }
            }
            lastTap = now;
        });

        overlay.appendChild(closeBtn);
        overlay.appendChild(img);
        document.body.appendChild(overlay);
    },

    createCopyButton(rawText) {
        const btn = document.createElement('button');
        btn.className = 'copy-msg-btn';
        btn.innerHTML = '&#128196;';
        btn.title = 'Copy message';
        btn.addEventListener('click', async (e) => {
            e.stopPropagation();
            try {
                await navigator.clipboard.writeText(rawText);
                if (navigator.vibrate) navigator.vibrate(8);
                btn.innerHTML = '&#10003;';
                btn.classList.add('copied');
                setTimeout(() => {
                    btn.innerHTML = '&#128196;';
                    btn.classList.remove('copied');
                }, 1200);
            } catch {
                // Fallback for older browsers
                const ta = document.createElement('textarea');
                ta.value = rawText;
                ta.style.cssText = 'position:fixed;left:-9999px';
                document.body.appendChild(ta);
                ta.select();
                document.execCommand('copy');
                ta.remove();
                btn.innerHTML = '&#10003;';
                btn.classList.add('copied');
                setTimeout(() => {
                    btn.innerHTML = '&#128196;';
                    btn.classList.remove('copied');
                }, 1200);
            }
        });
        return btn;
    },

    showThinkingHeart() {
        this.hideThinkingHeart();
        if (this._voiceConversationMode || this._liveCallMode) {
            this._setVoiceConversationPhase('thinking');
        }
        const row = document.createElement('div');
        row.className = 'message-row assistant thinking-row';
        row.innerHTML = `
            <div class="message-bubble assistant thinking-bubble" role="status" aria-label="${escapeHtml(App.currentIdentity)} is thinking">
                <span class="thinking-hearts"><span class="thinking-heart">&hearts;</span><span class="thinking-heart">&hearts;</span><span class="thinking-heart">&hearts;</span></span>
            </div>
        `;
        this.container.appendChild(row);
        this._thinkingEl = row;
        this._thinkingShownAt = Date.now();
        // Auto-dismiss if stream never completes (e.g. WS dies mid-stream)
        this._thinkingTimeout = setTimeout(() => this.hideThinkingHeart(), 90000);
        this.scrollToBottom();
    },

    hideThinkingHeart() {
        if (!this._thinkingEl) return;
        const elapsed = Date.now() - (this._thinkingShownAt || 0);
        const MIN_SHOW = 800; // ms — so the heart is always visible
        if (elapsed < MIN_SHOW) {
            setTimeout(() => this.hideThinkingHeart(), MIN_SHOW - elapsed);
            return;
        }
        clearTimeout(this._thinkingTimeout);
        this._thinkingEl.remove();
        this._thinkingEl = null;
    },

    // Instant retire, no MIN_SHOW hold — used when the reply bubble takes
    // over the hearts in the same paint, so the handoff is seamless.
    _retireThinkingHeartNow() {
        clearTimeout(this._thinkingTimeout);
        if (this._thinkingEl) {
            this._thinkingEl.remove();
            this._thinkingEl = null;
        }
    },

    // Remove the hearts living inside the reply bubble (first text landed,
    // stream ended, or the safety timeout fired).
    _retireBubbleHearts() {
        clearTimeout(this._bubbleHeartsTimeout);
        this._bubbleHeartsTimeout = null;
        if (!this.container) return;
        this.container.querySelectorAll('.bubble-hearts').forEach(el => el.remove());
    },

    // Legacy key-to-emoji mapping for old DB data
    _legacyEmojiMap: { heart: '\u2764\uFE0F', star: '\u2B50', flame: '\uD83D\uDD25' },

    // All available reaction emojis
    _allEmojis: [
        '\u2764\uFE0F', '\u2B50', '\uD83D\uDD25',
        '\uD83E\uDD23', '\uD83E\uDD70', '\uD83D\uDE05', '\uD83E\uDD79',
        '\uD83E\uDD2F', '\uD83E\uDD14', '\uD83D\uDCAA\uD83C\uDFFB',
        '\u2764\uFE0F\u200D\uD83D\uDD25',
        '\uD83D\uDE2D', '\uD83D\uDC3A', '\uD83C\uDFA8', '\uD83D\uDC09',
        '\uD83E\uDD89', '\uD83E\uDD8B', '\uD83E\uDEBD',
        '\uD83E\uDD13', '\uD83D\uDC40', '\uD83D\uDE0D', '\uD83E\uDD7A', '🫠', '🫂', '😤', '😈', '💀', '😌', '🤤', '🐇', '✍🏼', '🌙'
    ],

    createReactionBar(messageId, existingReactions, role) {
        const bar = document.createElement('div');
        bar.className = 'reaction-bar';
        if (!messageId) return bar;

        const reactor = role === 'assistant' ? 'Owner' : App.currentIdentity;
        const reactions = existingReactions || {};

        // Show existing active reactions as visible pills
        for (const [key, reactors] of Object.entries(reactions)) {
            if (!reactors || reactors.length === 0) continue;
            const emoji = this._legacyEmojiMap[key] || key;
            const isActive = reactors.includes(reactor);
            bar.appendChild(this._createReactionBtn(emoji, key, messageId, reactor, reactors.length, isActive));
        }

        // "+" toggle button to open picker
        const addBtn = document.createElement('button');
        addBtn.className = 'reaction-add-btn';
        addBtn.textContent = '+';
        addBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this._showEmojiPicker(bar, messageId, reactor);
        });
        bar.appendChild(addBtn);

        return bar;
    },

    // A reaction key is either a unicode emoji or one of our custom :shortcodes:.
    // Custom ones render as a picture; everything else is plain text as before.
    _reactionGlyph(emoji, key) {
        if (typeof CustomEmoji !== 'undefined' && CustomEmoji.isCustomKey(key)) {
            const img = CustomEmoji.imgHtml(key.slice(1, -1), 'reaction-emoji-img');
            if (img) return img;
        }
        return escapeHtml(String(emoji));
    },

    _createReactionBtn(emoji, key, messageId, reactor, count, isActive) {
        const btn = document.createElement('button');
        btn.className = 'reaction-btn';
        if (isActive) btn.classList.add('active');
        btn.dataset.reaction = key;
        const glyph = this._reactionGlyph(emoji, key);
        btn.innerHTML = count > 0
            ? `${glyph}<span class="reaction-count">${count}</span>`
            : glyph;

        btn.addEventListener('click', async (e) => {
            e.stopPropagation();
            try {
                const res = await apiFetch(`/api/messages/${messageId}/react`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ reaction: key, reactor }),
                });
                const data = await res.json();
                if (data.ok) {
                    if (navigator.vibrate) navigator.vibrate(6);
                    const bar = btn.closest('.reaction-bar');
                    if (bar) this._refreshReactionBar(bar, messageId, data.reactions, reactor);
                }
            } catch (err) {
                console.error('[Reactions] Failed:', err);
            }
        });
        return btn;
    },

    _showEmojiPicker(bar, messageId, reactor) {
        // Toggle existing picker
        const existing = bar.querySelector('.emoji-picker-popup');
        if (existing) { existing.remove(); return; }

        const picker = document.createElement('div');
        picker.className = 'emoji-picker-popup';

        const addTile = (key, innerHtml) => {
            const btn = document.createElement('button');
            btn.className = 'emoji-picker-btn';
            btn.innerHTML = innerHtml;
            btn.addEventListener('click', async (e) => {
                e.stopPropagation();
                picker.remove();
                try {
                    const res = await apiFetch(`/api/messages/${messageId}/react`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ reaction: key, reactor }),
                    });
                    const data = await res.json();
                    if (data.ok) {
                        this._refreshReactionBar(bar, messageId, data.reactions, reactor);
                    }
                } catch (err) {
                    console.error('[Reactions] Failed:', err);
                }
            });
            picker.appendChild(btn);
        };

        this._allEmojis.forEach(emoji => addTile(emoji, escapeHtml(emoji)));



        if (typeof CustomEmoji !== 'undefined' && CustomEmoji.isReady()) {
            const divider = document.createElement('div');
            divider.className = 'emoji-picker-divider';
            picker.appendChild(divider);
            CustomEmoji.groups().forEach(({ group, items }) => {
                const label = document.createElement('div');
                label.className = 'emoji-picker-group-label';
                label.textContent = group;
                picker.appendChild(label);
                items.forEach(entry => {
                    addTile(`:${entry.name}:`, CustomEmoji.imgHtml(entry.name, 'reaction-emoji-img'));
                });
            });
        }

        bar.appendChild(picker);

        // Close picker when clicking outside
        const closeHandler = (e) => {
            if (!picker.contains(e.target) && !bar.contains(e.target)) {
                picker.remove();
                document.removeEventListener('click', closeHandler);
            }
        };
        setTimeout(() => document.addEventListener('click', closeHandler), 0);
    },

    _addLongPressReaction(bubble) {
        let pressTimer = null;
        let touchMoved = false;

        bubble.addEventListener('touchstart', (e) => {
            // Don't trigger on buttons, links, images, etc.
            if (e.target.closest('button, a, img, .tool-pill-header, .tool-pill-row, .thinking-card-header, .voice-player')) return;
            touchMoved = false;
            pressTimer = setTimeout(() => {
                if (!touchMoved) {
                    e.preventDefault();
                    if (navigator.vibrate) navigator.vibrate(30);
                    const messageId = bubble.dataset.msgId;
                    if (messageId) {
                        this._showReactionPicker(messageId, bubble);
                    }
                }
            }, 500);
        }, { passive: false });

        bubble.addEventListener('touchmove', () => {
            touchMoved = true;
            clearTimeout(pressTimer);
        });

        bubble.addEventListener('touchend', () => {
            clearTimeout(pressTimer);
        });
    },

    _showReactionPicker(messageId, bubble) {
        // Remove any existing picker
        const existing = document.querySelector('.reaction-picker-popup');
        if (existing) existing.remove();

        const role = bubble.classList.contains('user') ? 'user' : 'assistant';
        const reactor = role === 'user' ? App.currentIdentity : 'Owner';

        const picker = document.createElement('div');
        picker.className = 'reaction-picker-popup';

        this._allEmojis.forEach(emoji => {
            const btn = document.createElement('button');
            btn.textContent = emoji;
            btn.addEventListener('click', async (e) => {
                e.stopPropagation();
                picker.remove();
                try {
                    const res = await apiFetch(`/api/messages/${messageId}/react`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ reaction: emoji, reactor }),
                    });
                    const data = await res.json();
                    if (data.ok) {
                        if (navigator.vibrate) navigator.vibrate(6);
                        const bar = bubble.querySelector('.reaction-bar');
                        if (bar) this._refreshReactionBar(bar, messageId, data.reactions, reactor);
                    }
                } catch (err) {
                    console.error('[Reactions] Long-press react failed:', err);
                }
            });
            picker.appendChild(btn);
        });

        bubble.appendChild(picker);

        // Close picker on tap outside
        const closeHandler = (e) => {
            if (!picker.contains(e.target)) {
                picker.remove();
                document.removeEventListener('click', closeHandler);
                document.removeEventListener('touchend', closeHandler);
            }
        };
        setTimeout(() => {
            document.addEventListener('click', closeHandler);
            document.addEventListener('touchend', closeHandler);
        }, 0);
    },

    _refreshReactionBar(bar, messageId, reactions, reactor) {
        // Remove everything except the add button
        const children = [...bar.children];
        children.forEach(child => {
            if (!child.classList.contains('reaction-add-btn')) child.remove();
        });

        const addBtn = bar.querySelector('.reaction-add-btn');
        for (const [key, reactors] of Object.entries(reactions)) {
            if (!reactors || reactors.length === 0) continue;
            const emoji = this._legacyEmojiMap[key] || key;
            const isActive = reactors.includes(reactor);
            const btn = this._createReactionBtn(emoji, key, messageId, reactor, reactors.length, isActive);
            bar.insertBefore(btn, addBtn);
        }
    },

    createBookmarkButton(messageId, isBookmarked) {
        const btn = document.createElement('button');
        btn.className = 'bookmark-btn';
        if (isBookmarked) btn.classList.add('active');
        btn.innerHTML = isBookmarked ? '\uD83D\uDD16' : '\uD83D\uDD16';
        btn.title = 'Remember this';

        if (!messageId) return btn;

        btn.addEventListener('click', async (e) => {
            e.stopPropagation();
            try {
                const res = await apiFetch(`/api/messages/${messageId}/bookmark`, {
                    method: 'POST',
                });
                const data = await res.json();
                if (data.ok !== undefined) {
                    btn.classList.toggle('active', data.bookmarked);
                    btn.title = data.bookmarked ? 'Bookmarked!' : 'Remember this';
                }
            } catch (err) {
                console.error('[Bookmark] Failed:', err);
            }
        });
        return btn;
    },

    createRememberButton(messageId, isRemembered) {
        const btn = document.createElement('button');
        btn.className = 'memory-btn';
        if (isRemembered) btn.classList.add('active');
        btn.innerHTML = '\uD83E\uDDE0';
        btn.title = 'Curate a memory';

        if (!messageId) return btn;

        btn.addEventListener('click', async (e) => {
            e.stopPropagation();
            this.openMemoryModal(messageId, btn);
        });
        return btn;
    },

    _initMemoryModal() {
        const overlay = document.getElementById('memory-overlay');
        const closeBtn = document.getElementById('memory-close');
        const form = document.getElementById('memory-form');
        if (!overlay || !closeBtn || !form) return;

        closeBtn.addEventListener('click', () => this.closeMemoryModal());
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) this.closeMemoryModal();
        });
        form.addEventListener('submit', (e) => {
            e.preventDefault();
            this.saveMemoryModal();
        });
    },

    openMemoryModal(messageId, buttonEl) {
        const overlay = document.getElementById('memory-overlay');
        if (!overlay) return;
        this._memoryDraft = { messageId, buttonEl };
        document.getElementById('memory-identity').value = App.currentIdentity;
        document.getElementById('memory-type').value = 'insight';
        document.getElementById('memory-summary').value = '';
        document.getElementById('memory-detail').value = '';
        overlay.style.display = 'flex';
        setTimeout(() => document.getElementById('memory-summary').focus(), 20);
    },

    closeMemoryModal() {
        const overlay = document.getElementById('memory-overlay');
        if (overlay) overlay.style.display = 'none';
        this._memoryDraft = null;
    },

    async saveMemoryModal() {
        if (!this._memoryDraft) return;
        const summary = document.getElementById('memory-summary').value.trim();
        const memoryType = document.getElementById('memory-type').value;
        const detail = document.getElementById('memory-detail').value.trim();
        const identity = document.getElementById('memory-identity').value;
        if (!summary) return;

        try {
            const res = await apiFetch(`/api/messages/${this._memoryDraft.messageId}/remember`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    summary,
                    detail,
                    memory_type: memoryType,
                    identity,
                }),
            });
            const data = await res.json();
            if (res.ok && data.ok) {
                if (this._memoryDraft.buttonEl) {
                    this._memoryDraft.buttonEl.classList.add('active');
                    this._memoryDraft.buttonEl.title = 'Memory saved';
                }
                this.closeMemoryModal();
            } else {
                alert(data.error || 'Could not save memory');
            }
        } catch (err) {
            console.error('[Memory] Failed:', err);
            alert('Could not save memory');
        }
    },

    _updateScrollPill() {
        if (!this._scrollPill) return;
        if (this._isNearBottom) {
            this._scrollPill.classList.remove('visible', 'has-new');
        } else {
            this._scrollPill.classList.add('visible');
            if (this._hasNewMessages) {
                this._scrollPill.classList.add('has-new');
                const count = this._newMessageCount;
                this._scrollPill.innerHTML = count > 1
                    ? `&#8595; ${count} new`
                    : '&#8595; New';
            } else {
                this._scrollPill.classList.remove('has-new');
                this._scrollPill.innerHTML = '&#8595;';
            }
        }
    },

    _hasChatSelection(element = this.container) {
        const selection = window.getSelection();
        return !!(selection && !selection.isCollapsed && selection.rangeCount &&
            selection.getRangeAt(0).intersectsNode(element));
    },

    _pauseFollowing() {
        if (this._scrollFrame !== null) cancelAnimationFrame(this._scrollFrame);
        this._scrollFrame = null;
        this._isNearBottom = false;
        this._restoringPosition = false;
        this._updateScrollPill();
    },

    _initReadingControls() {
        // Intent must win before the next streamed animation frame can run.
        this.container.addEventListener('wheel', e => {
            if (e.deltaY < 0) this._pauseFollowing();
        }, { passive: true });
        let touchY = null;
        this.container.addEventListener('touchstart', e => {
            touchY = e.touches[0]?.clientY ?? null;
        }, { passive: true });
        this.container.addEventListener('touchmove', e => {
            const y = e.touches[0]?.clientY;
            if (touchY !== null && y > touchY) this._pauseFollowing();
            touchY = y;
        }, { passive: true });
        this.container.addEventListener('keydown', e => {
            if (['ArrowUp', 'PageUp', 'Home'].includes(e.key) || (e.key === ' ' && e.shiftKey)) {
                this._pauseFollowing();
            }
        });
        this.container.addEventListener('scroll', () => {
            if (this._restoringPosition || !this.container.clientHeight) return;
            const top = this.container.scrollTop;
            // An upward move cancels following even within the old 80px threshold.
            if (top < this._lastScrollTop - 1) this._pauseFollowing();
            else if (this.container.scrollHeight - top - this.container.clientHeight <= 2) {
                this._isNearBottom = true;
                this._hasNewMessages = false;
                this._newMessageCount = 0;
                this._removeNewMessagesDivider();
            }
            this._lastScrollTop = top;
            this._updateScrollPill();
            this.saveReadingPosition();
        });
        document.addEventListener('selectionchange', () => {
            this._containMessageSelection();
            if (this._hasChatSelection()) this._pauseFollowing();
            else {
                const pending = this._selectedFinalRender;
                if (pending) {
                    this._selectedFinalRender = null;
                    if (pending.element.isConnected) pending.element.innerHTML = pending.html;
                }
                if (this.isStreaming && this.currentStreamEl) this._scheduleStreamRender();
            }
        });
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) {
                this.saveReadingPosition();
                this._pauseFollowing();
            } else if (this._readingPosition && !this._hasChatSelection()) {
                this._placeReadingPosition(this._readingPosition);
            }
        });
        window.addEventListener('pagehide', () => this.saveReadingPosition());
        // Late-loading images and fonts must not shift the saved message away.
        this._readingResizeObserver = new ResizeObserver(() => {
            if (this._restoringPosition || document.hidden) return;
            if (this._isNearBottom) this.scrollToBottom();
            else if (this._readingPosition && !this._hasChatSelection()) {
                this._placeReadingPosition(this._readingPosition);
            }
        });
    },

    _containMessageSelection() {
        const selection = window.getSelection();
        if (!selection || selection.isCollapsed || !selection.rangeCount) return;
        const anchor = selection.anchorNode;
        const content = (anchor.nodeType === Node.ELEMENT_NODE ? anchor : anchor.parentElement)
            ?.closest('.message-content');
        if (!content || !this.container.contains(content)) return;
        // CSS user-select:none and clipboard cleanup do not contain a native
        // drag/selection handle. Clamp its moving end to the originating message.
        const bounds = document.createRange();
        bounds.selectNodeContents(content);
        const side = bounds.comparePoint(selection.focusNode, selection.focusOffset);
        if (!side) return;
        selection.setBaseAndExtent(anchor, selection.anchorOffset,
            side < 0 ? bounds.startContainer : bounds.endContainer,
            side < 0 ? bounds.startOffset : bounds.endOffset);
    },

    _readSavedPosition(conversationId) {
        try {
            const value = JSON.parse(localStorage.getItem('anam-reading-' + conversationId));
            return value && Number.isFinite(value.top) ? value : null;
        } catch (_) { return null; }
    },

    saveReadingPosition() {
        if (this._restoringPosition || !this.container.clientHeight) return;
        if (!this._viewConversationId) this._viewConversationId = App.conversationId;
        if (!this._viewConversationId) return;
        const bubbles = [...this.container.querySelectorAll('.message-bubble[data-msg-id]')];
        if (!bubbles.length) return; // Loading/empty states must never overwrite a bookmark.
        const viewportTop = this.container.getBoundingClientRect().top;
        const anchor = bubbles.find(el => el.getBoundingClientRect().bottom > viewportTop) || bubbles.at(-1);
        this._readingPosition = {
            bottom: this._isNearBottom,
            top: this.container.scrollTop,
            id: anchor?.dataset.msgId || null,
            offset: anchor ? anchor.getBoundingClientRect().top - viewportTop : 0,
        };
        try {
            localStorage.setItem('anam-reading-' + this._viewConversationId, JSON.stringify(this._readingPosition));
        } catch (_) { /* Reading still works when browser storage is unavailable. */ }
    },

    _placeReadingPosition(position) {
        const anchor = [...this.container.querySelectorAll('.message-bubble[data-msg-id]')]
            .find(el => el.dataset.msgId === String(position.id));
        this.container.scrollTop = anchor
            ? this.container.scrollTop + anchor.getBoundingClientRect().top -
                this.container.getBoundingClientRect().top - position.offset
            : position.top;
        this._lastScrollTop = this.container.scrollTop;
        return !!anchor;
    },

    async _restoreReadingPosition(position, hasMore) {
        const conversationId = this._viewConversationId;
        this._readingPosition = position;
        this._restoringPosition = true;
        this._isNearBottom = false;
        try {
            // A bookmark can be older than the initial history window. Use the
            // existing paginated HTTP route, including when WebSocket is offline.
            while (!this._placeReadingPosition(position) && position.id && hasMore) {
                const offset = this._historyOffset;
                const response = await apiFetch(`/api/messages/conversations/${encodeURIComponent(conversationId)}?offset=${offset}&limit=100`);
                if (!response.ok) throw new Error('Could not load bookmarked messages');
                const data = await response.json();
                if (this._viewConversationId !== conversationId || !this._restoringPosition || this._readingPosition !== position) return;
                const messages = data.messages || [];
                hasMore = messages.length === 100;
                this.onHistoryOlder({ messages, has_more: hasMore });
                if (!messages.length) break;
            }
        } catch (error) {
            console.warn('[Chat] Reading position restore:', error);
        } finally {
            if (this._viewConversationId === conversationId && this._readingPosition === position) {
                this._restoringPosition = false;
                this._updateScrollPill();
            }
        }
    },

    scrollToBottom(force = false) {
        if (this._suppressAutoScroll || document.hidden || this._hasChatSelection()) return;
        if (!force && (!this._isNearBottom || this._restoringPosition)) return;
        if (force) {
            this._isNearBottom = true;
            this._restoringPosition = false;
            this._readingPosition = null;
        }
        if (this._scrollFrame !== null) return;
        const container = this.container;
        this._scrollFrame = requestAnimationFrame(() => {
            this._scrollFrame = null;
            // Recheck at execution time: scrolling/selection can begin after queuing.
            if (container !== this.container || !this._isNearBottom ||
                this._restoringPosition || document.hidden || this._hasChatSelection()) return;
            container.scrollTop = container.scrollHeight;
            this._lastScrollTop = container.scrollTop;
            this._hasNewMessages = false;
            this._newMessageCount = 0;
            this._removeNewMessagesDivider();
            this._updateScrollPill();
        });
    },

    // ── In-conversation message search (Ctrl+F) ──

    _searchMatches: [],
    _searchIndex: -1,
    _searchOriginals: new Map(), // element -> original innerHTML
    _searchDebounce: null,
    _searchOpen: false,

    openSearch() {
        const bar = document.getElementById('message-search-bar');
        if (!bar) return;
        bar.style.display = 'flex';
        this._searchOpen = true;
        const input = document.getElementById('message-search-input');
        input.focus();
        input.select();
    },

    closeSearch() {
        const bar = document.getElementById('message-search-bar');
        if (!bar) return;
        bar.style.display = 'none';
        this._searchOpen = false;
        this._clearHighlights();
        document.getElementById('message-search-input').value = '';
        document.getElementById('message-search-count').textContent = '';
        this._searchMatches = [];
        this._searchIndex = -1;
    },

    _clearHighlights() {
        for (const [el, html] of this._searchOriginals) {
            el.innerHTML = html;
        }
        this._searchOriginals.clear();
    },

    _doSearch(query) {
        this._clearHighlights();
        this._searchMatches = [];
        this._searchIndex = -1;
        const countEl = document.getElementById('message-search-count');

        if (!query || query.length < 1) {
            countEl.textContent = '';
            return;
        }

        const lowerQuery = query.toLowerCase();
        const contentEls = this.container.querySelectorAll('.message-content');

        contentEls.forEach(el => {
            const text = el.textContent;
            if (text.toLowerCase().includes(lowerQuery)) {
                // Save original before modifying
                this._searchOriginals.set(el, el.innerHTML);
                this._highlightInElement(el, query);
            }
        });

        // Collect all marks in DOM order
        this._searchMatches = Array.from(
            this.container.querySelectorAll('.msg-search-highlight')
        );

        if (this._searchMatches.length > 0) {
            this._searchIndex = 0;
            this._showCurrentMatch();
            countEl.textContent = `1 of ${this._searchMatches.length}`;
        } else {
            countEl.textContent = 'No results';
        }
    },

    _highlightInElement(el, query) {
        // Walk text nodes and wrap matches in <mark> tags
        const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
        const textNodes = [];
        while (walker.nextNode()) textNodes.push(walker.currentNode);

        const lowerQuery = query.toLowerCase();
        for (const node of textNodes) {
            const text = node.textContent;
            const lowerText = text.toLowerCase();
            let idx = lowerText.indexOf(lowerQuery);
            if (idx === -1) continue;

            const frag = document.createDocumentFragment();
            let lastIdx = 0;
            while (idx !== -1) {
                // Text before match
                if (idx > lastIdx) {
                    frag.appendChild(document.createTextNode(text.slice(lastIdx, idx)));
                }
                // The match
                const mark = document.createElement('mark');
                mark.className = 'msg-search-highlight';
                mark.textContent = text.slice(idx, idx + query.length);
                frag.appendChild(mark);
                lastIdx = idx + query.length;
                idx = lowerText.indexOf(lowerQuery, lastIdx);
            }
            // Remaining text
            if (lastIdx < text.length) {
                frag.appendChild(document.createTextNode(text.slice(lastIdx)));
            }
            node.parentNode.replaceChild(frag, node);
        }
    },

    _showCurrentMatch() {
        // Remove current class from all
        this._searchMatches.forEach(m => m.classList.remove('current'));
        if (this._searchIndex >= 0 && this._searchIndex < this._searchMatches.length) {
            const match = this._searchMatches[this._searchIndex];
            match.classList.add('current');
            match.scrollIntoView({ behavior: 'smooth', block: 'center' });
            // #9 jump-highlight pulse — briefly outline the bubble she just
            // landed on so the eye finds it instantly instead of hunting.
            const bubble = match.closest('.message-bubble');
            if (bubble) this._pulseHighlightMessage(bubble);
        }
    },

    // Reusable ~1.2s outline-pulse for any "jump to this message" action
    // (search-result click today; a future jump-to-date action can call
    // this too).
    _pulseHighlightMessage(bubbleEl) {
        if (!bubbleEl) return;
        bubbleEl.classList.remove('jump-highlight-pulse');
        void bubbleEl.offsetWidth; // force reflow so re-triggering restarts the animation
        bubbleEl.classList.add('jump-highlight-pulse');
        clearTimeout(bubbleEl._jumpHighlightTimer);
        bubbleEl._jumpHighlightTimer = setTimeout(() => {
            bubbleEl.classList.remove('jump-highlight-pulse');
        }, 1200);
    },

    _nextMatch() {
        if (this._searchMatches.length === 0) return;
        this._searchIndex = (this._searchIndex + 1) % this._searchMatches.length;
        this._showCurrentMatch();
        document.getElementById('message-search-count').textContent =
            `${this._searchIndex + 1} of ${this._searchMatches.length}`;
    },

    _prevMatch() {
        if (this._searchMatches.length === 0) return;
        this._searchIndex = (this._searchIndex - 1 + this._searchMatches.length) % this._searchMatches.length;
        this._showCurrentMatch();
        document.getElementById('message-search-count').textContent =
            `${this._searchIndex + 1} of ${this._searchMatches.length}`;
    },

    _initMessageSearch() {
        const input = document.getElementById('message-search-input');
        const prevBtn = document.getElementById('message-search-prev');
        const nextBtn = document.getElementById('message-search-next');
        const closeBtn = document.getElementById('message-search-close');
        if (!input) return;

        // Debounced search on input
        input.addEventListener('input', () => {
            clearTimeout(this._searchDebounce);
            this._searchDebounce = setTimeout(() => {
                this._doSearch(input.value.trim());
            }, 200);
        });

        // Enter = next, Shift+Enter = prev
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                if (e.shiftKey) this._prevMatch();
                else this._nextMatch();
            }
            if (e.key === 'Escape') {
                e.preventDefault();
                this.closeSearch();
            }
        });

        prevBtn.addEventListener('click', () => this._prevMatch());
        nextBtn.addEventListener('click', () => this._nextMatch());
        closeBtn.addEventListener('click', () => this.closeSearch());

        // Ctrl+F / Cmd+F — open message search
        document.addEventListener('keydown', (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
                // Allow browser find if focused in search input already
                if (e.target.id === 'message-search-input') return;
                e.preventDefault();
                this.openSearch();
            }
        });
    },

    // ── Image gallery ──

    _galleryOpen: false,

    openGallery() {
        const overlay = document.getElementById('image-gallery');
        if (!overlay) return;

        // Default to "This Chat" tab
        this._galleryTab = 'chat';
        overlay.querySelectorAll('.gallery-tab').forEach(t => {
            t.classList.toggle('active', t.dataset.tab === 'chat');
        });
        this._renderGalleryChat();

        overlay.style.display = 'flex';
        this._galleryOpen = true;
        document.body.style.overflow = 'hidden';
    },

    _renderGalleryChat() {
        const grid = document.getElementById('gallery-grid');
        if (!grid) return;
        const images = this.container.querySelectorAll('.message-image');
        grid.innerHTML = '';

        if (images.length === 0) {
            grid.innerHTML = '<div class="gallery-empty">No images in this conversation</div>';
        } else {
            images.forEach(img => {
                const thumb = document.createElement('div');
                thumb.className = 'gallery-thumb';
                const thumbImg = document.createElement('img');
                thumbImg.src = img.src;
                thumbImg.loading = 'lazy';
                thumbImg.alt = 'Shared image';
                thumbImg.addEventListener('click', () => this.viewFullImage(img.src));
                thumb.appendChild(thumbImg);
                grid.appendChild(thumb);
            });
        }
    },

    async _renderGalleryAll() {
        const grid = document.getElementById('gallery-grid');
        if (!grid) return;
        grid.innerHTML = '<div class="gallery-empty gallery-loading">Loading all images...</div>';

        try {
            const res = await apiFetch('/api/images/all');
            const data = await res.json();
            const allImages = data.images || [];
            grid.innerHTML = '';

            if (allImages.length === 0) {
                grid.innerHTML = '<div class="gallery-empty">No images shared yet</div>';
                return;
            }

            allImages.forEach(item => {
                const imageUrl = convertImagePath(item.url || '');
                const thumb = document.createElement('div');
                thumb.className = 'gallery-thumb';
                thumb.title = `${item.identity || 'Unknown'} — ${item.conversation || 'Untitled'}\n${new Date(item.created_at).toLocaleDateString()}`;
                const thumbImg = document.createElement('img');
                thumbImg.src = imageUrl;
                thumbImg.loading = 'lazy';
                thumbImg.alt = `Image from ${item.identity || 'chat'}`;
                thumbImg.addEventListener('click', () => this.viewFullImage(imageUrl));
                thumb.appendChild(thumbImg);
                grid.appendChild(thumb);
            });
        } catch (err) {
            grid.innerHTML = '<div class="gallery-empty">Failed to load images</div>';
            console.error('Gallery fetch error:', err);
        }
    },

    closeGallery() {
        const overlay = document.getElementById('image-gallery');
        if (overlay) overlay.style.display = 'none';
        this._galleryOpen = false;
        document.body.style.overflow = '';
    },

    _initGallery() {
        const btn = document.getElementById('gallery-btn');
        const closeBtn = document.getElementById('gallery-close');
        const overlay = document.getElementById('image-gallery');

        if (btn) btn.addEventListener('click', () => this.openGallery());
        if (closeBtn) closeBtn.addEventListener('click', () => this.closeGallery());

        // Tab switching
        if (overlay) {
            overlay.querySelectorAll('.gallery-tab').forEach(tab => {
                tab.addEventListener('click', () => {
                    if (tab.dataset.tab === this._galleryTab) return;
                    this._galleryTab = tab.dataset.tab;
                    overlay.querySelectorAll('.gallery-tab').forEach(t => {
                        t.classList.toggle('active', t.dataset.tab === this._galleryTab);
                    });
                    if (this._galleryTab === 'all') {
                        this._renderGalleryAll();
                    } else {
                        this._renderGalleryChat();
                    }
                });
            });
        }

        // Click overlay backdrop to close
        if (overlay) {
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) this.closeGallery();
            });
        }

        // Escape to close
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this._galleryOpen) {
                this.closeGallery();
            }
        });
    },

    // ── Slash Commands ──────────────────────────────────────────

    _handleSlashInput() {
        const text = this.input.value;
        if (!text.startsWith('/') || text.includes('\n')) {
            this._hideSlashDropdown();
            return;
        }

        const query = text.slice(1).toLowerCase().split(' ')[0];
        const matches = this._slashCommands.filter(c =>
            c.name.startsWith(query) || query === ''
        );

        if (matches.length === 0 || (text.includes(' ') && !text.startsWith('/'))) {
            this._hideSlashDropdown();
            return;
        }

        // Only show dropdown for the command part (before first space)
        if (text.includes(' ') && matches.length === 1) {
            this._hideSlashDropdown();
            return;
        }

        this._slashSelectedIndex = 0;
        this._showSlashDropdown(matches);
    },

    _showSlashDropdown(commands) {
        const dd = this._slashDropdown;
        dd.innerHTML = '';
        commands.forEach((cmd, i) => {
            const item = document.createElement('div');
            item.className = 'slash-item' + (i === 0 ? ' selected' : '');
            item.innerHTML = `<span class="slash-name">/${cmd.name}</span> <span class="slash-desc">${cmd.description}</span>`;
            item.addEventListener('click', () => {
                this.input.value = '/' + cmd.name + (cmd.hasArg ? ' ' : '');
                this._hideSlashDropdown();
                this.input.focus();
            });
            dd.appendChild(item);
        });
        dd.style.display = 'block';

        // Keyboard navigation
        this._slashKeyHandler = (e) => {
            const items = dd.querySelectorAll('.slash-item');
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                this._slashSelectedIndex = Math.min(this._slashSelectedIndex + 1, items.length - 1);
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                this._slashSelectedIndex = Math.max(this._slashSelectedIndex - 1, 0);
            } else if (e.key === 'Enter' && dd.style.display === 'block') {
                e.preventDefault();
                const selected = commands[this._slashSelectedIndex];
                if (selected) {
                    this.input.value = '/' + selected.name + (selected.hasArg ? ' ' : '');
                    this._hideSlashDropdown();
                    if (!selected.hasArg) this.sendMessage();
                }
                return;
            } else if (e.key === 'Escape') {
                this._hideSlashDropdown();
                return;
            } else if (e.key === 'Tab') {
                e.preventDefault();
                const selected = commands[this._slashSelectedIndex];
                if (selected) {
                    this.input.value = '/' + selected.name + (selected.hasArg ? ' ' : '');
                    this._hideSlashDropdown();
                }
                return;
            }
            items.forEach((el, i) => el.classList.toggle('selected', i === this._slashSelectedIndex));
        };
        this.input.addEventListener('keydown', this._slashKeyHandler);
    },

    _hideSlashDropdown() {
        this._slashDropdown.style.display = 'none';
        if (this._slashKeyHandler) {
            this.input.removeEventListener('keydown', this._slashKeyHandler);
            this._slashKeyHandler = null;
        }
    },

    _handleSlashCommand(text) {
        const parts = text.slice(1).split(/\s+/);
        const cmd = parts[0].toLowerCase();
        const args = parts.slice(1).join(' ');

        switch (cmd) {
            case 'hub':
                if (typeof App !== 'undefined' && App.openHub && App.closeHub) {
                    const overlay = document.getElementById('hub-overlay');
                    if (overlay && overlay.style.display !== 'none') {
                        App.closeHub();
                    } else {
                        App.openHub();
                    }
                }
                return true;
            case 'voice':
                this._toggleVoiceConversationMode?.();
                return true;
            case 'new':
                App.ws.send({ type: 'new_conversation' });
                return true;
            case 'search':
                this._openDeepSearch();
                return true;
            case 'switch':
                if (args) {
                    const identity = args.charAt(0).toUpperCase() + args.slice(1).toLowerCase();
                    if (typeof App !== 'undefined' && App.switchIdentity) {
                        App.switchIdentity(identity);
                    }
                }
                return true;
            case 'help':
                this._showSlashHelp();
                return true;
            case 'timer':
            case 'status':
                // Server-side commands — send via WebSocket
                App.ws.send({ type: 'slash_command', command: cmd, args: args });
                return true;
            default:
                return false;
        }
    },

    _showSlashHelp() {
        const helpText = this._slashCommands
            .map(c => `**/${c.name}** — ${c.description}`)
            .join('\n');
        this.addMessage('system', helpText, null, null, null);
    },

    // ── Deep Search ───────────────────────────────────────────

    _deepSearchTimer: null,

    _openDeepSearch() {
        const overlay = document.getElementById('deep-search-overlay');
        if (!overlay) return;
        overlay.style.display = 'flex';
        const input = document.getElementById('deep-search-input');
        input.value = '';
        input.focus();

        // Debounced search on input
        input.oninput = () => {
            clearTimeout(this._deepSearchTimer);
            this._deepSearchTimer = setTimeout(() => this._runDeepSearch(input.value), 400);
        };

        // Close on Escape or click outside
        const close = document.getElementById('deep-search-close');
        close.onclick = () => this._closeDeepSearch();
        overlay.onclick = (e) => {
            if (e.target === overlay) this._closeDeepSearch();
        };

        // Enter to search immediately
        input.onkeydown = (e) => {
            if (e.key === 'Enter') {
                clearTimeout(this._deepSearchTimer);
                this._runDeepSearch(input.value);
            } else if (e.key === 'Escape') {
                this._closeDeepSearch();
            }
        };
    },

    _closeDeepSearch() {
        const overlay = document.getElementById('deep-search-overlay');
        if (overlay) overlay.style.display = 'none';
    },

    async _runDeepSearch(query) {
        const resultsEl = document.getElementById('deep-search-results');
        if (!resultsEl) return;
        if (!query || query.length < 2) {
            resultsEl.innerHTML = '<div class="deep-search-hint">Type to search across all conversations...</div>';
            return;
        }

        resultsEl.innerHTML = '<div class="deep-search-hint">Searching...</div>';

        try {
            const res = await apiFetch('/api/search', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ query, limit: 20 }),
            });
            const data = await res.json();
            const results = data.results || [];

            if (results.length === 0) {
                resultsEl.innerHTML = '<div class="deep-search-hint">No results found</div>';
                return;
            }

            resultsEl.innerHTML = results.map(r => `
                <div class="deep-search-result" data-conv="${this._escapeHtml(r.conversation_id)}" data-msg="${this._escapeHtml(r.id)}">
                    <div class="search-result-meta">
                        <span class="search-result-speaker">${this._escapeHtml(r.speaker)}</span>
                        <span class="search-result-time">${this._escapeHtml(r.time_ago)} &middot; ${this._escapeHtml(r.conversation_title)}</span>
                    </div>
                    <div class="search-result-preview">${this._escapeHtml(r.content_preview)}</div>
                </div>
            `).join('');

            // Click to navigate to conversation
            resultsEl.querySelectorAll('.deep-search-result').forEach(el => {
                el.addEventListener('click', () => {
                    const convId = el.dataset.conv;
                    if (convId) {
                        App.ws.send({ type: 'load_history', conversation_id: convId });
                        this._closeDeepSearch();
                    }
                });
            });

        } catch (err) {
            resultsEl.innerHTML = `<div class="deep-search-hint">Search failed: ${err.message}</div>`;
        }
    },

    _escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },

    // ── Reply-to Context ────────────────────────────────────────

    setReplyTo(messageId, preview) {
        this._replyToId = messageId;
        this._replyToPreview = preview;
        this._showReplyIndicator(preview);
    },

    clearReplyTo() {
        this._replyToId = null;
        this._replyToPreview = null;
        this._hideReplyIndicator();
    },

    _showReplyIndicator(preview) {
        let indicator = document.getElementById('reply-indicator');
        if (!indicator) {
            indicator = document.createElement('div');
            indicator.id = 'reply-indicator';
            indicator.className = 'reply-indicator';
            this.input.parentElement.insertBefore(indicator, this.input);
        }
        const truncated = preview.length > 80 ? preview.slice(0, 80) + '...' : preview;
        indicator.innerHTML = `
            <span class="reply-indicator-text">Replying to: "${this._escapeHtml(truncated)}"</span>
            <button class="reply-indicator-close" onclick="Chat.clearReplyTo()">×</button>
        `;
        indicator.style.display = 'flex';
    },

    _hideReplyIndicator() {
        const indicator = document.getElementById('reply-indicator');
        if (indicator) indicator.style.display = 'none';
    },
};
