/* ANAM GUIDE: STORY STATE PANEL
   What: The story-state popup on the chat page — the form where roleplay details (era, location, last scene, open threads...) are viewed and edited, saved through /api/story-state (api/story_state.py).
   Loaded by: static/index.html (main chat page); the button only shows for character/roleplay identities.
   Edit here when: You add or rename a story-state field (update FIELD_KEYS here AND the matching form inputs in index.html and the backend). */

(function () {
    'use strict';

    const FIELD_KEYS = [
        'era',
        'in_fic_day',
        'location',
        'who_else',
        'tone_flavor',
        'emotional_temperature',
        'last_scene',
        'character_state',
        'whats_promised',
        'beats_to_hit',
        'not_this_today',
        'open_threads',
        'recent_beats',
        'callback_anchors',
    ];

    const StoryState = {
        characterIdentities: new Set(),
        currentIdentity: null,

        async init() {
            this.btn = document.getElementById('story-state-btn');
            this.overlay = document.getElementById('story-state-overlay');
            this.closeBtn = document.getElementById('story-state-close');
            this.form = document.getElementById('story-state-form');
            this.identityLabel = document.getElementById('story-state-identity-label');
            this.statusEl = document.getElementById('story-state-status');

            if (!this.btn || !this.overlay || !this.form) {
                console.warn('[StoryState] DOM elements missing — skipping init');
                return;
            }

            // Cache field inputs by key
            this.fieldInputs = {};
            for (const key of FIELD_KEYS) {
                this.fieldInputs[key] = this.form.querySelector(`[name="${key}"]`);
            }

            this.btn.addEventListener('click', () => this.open());
            this.closeBtn.addEventListener('click', () => this.close());
            this.overlay.addEventListener('click', (e) => {
                if (e.target === this.overlay) this.close();
            });
            this.form.addEventListener('submit', (e) => this.onSubmit(e));

            // Load which identities are characters
            try {
                const res = await fetch('/api/story-state/identities');
                if (res.ok) {
                    const data = await res.json();
                    this.characterIdentities = new Set(data.identities || []);
                }
            } catch (err) {
                console.warn('[StoryState] Failed to load character identities', err);
            }

            // Listen for identity changes dispatched by App.switchIdentity
            window.addEventListener('anam:identity-changed', (e) => {
                this.onIdentityChange(e.detail && e.detail.identity);
            });

            // Initial state — App might already have set currentIdentity
            const initial = window.App && window.App.currentIdentity;
            if (initial) this.onIdentityChange(initial);
        },

        onIdentityChange(identity) {
            this.currentIdentity = identity;
            if (!this.btn) return;
            if (identity && this.characterIdentities.has(identity)) {
                this.btn.style.display = '';
            } else {
                this.btn.style.display = 'none';
                // If the modal is open and we navigated away, close it
                if (this.overlay && this.overlay.style.display === 'flex') {
                    this.close();
                }
            }
        },

        async open() {
            if (!this.currentIdentity || !this.characterIdentities.has(this.currentIdentity)) {
                return;
            }
            this.identityLabel.textContent = `(${this.currentIdentity})`;
            this.setStatus('Loading…', 'pending');
            this.overlay.style.display = 'flex';
            try {
                const res = await fetch(`/api/story-state/${encodeURIComponent(this.currentIdentity)}`);
                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    this.setStatus(err.error || `Load failed (${res.status})`, 'error');
                    return;
                }
                const data = await res.json();
                this.populateForm(data.fields || {});
                this.setStatus('', '');
            } catch (err) {
                console.error('[StoryState] Load failed', err);
                this.setStatus('Network error', 'error');
            }
        },

        close() {
            if (this.overlay) this.overlay.style.display = 'none';
            this.setStatus('', '');
        },

        populateForm(fields) {
            for (const key of FIELD_KEYS) {
                const el = this.fieldInputs[key];
                if (!el) continue;
                el.value = fields[key] || '';
            }
        },

        readForm() {
            const out = {};
            for (const key of FIELD_KEYS) {
                const el = this.fieldInputs[key];
                if (!el) continue;
                out[key] = el.value.trim();
            }
            // Stamp updated_by + last_updated so we can see who/when
            out.updated_by = 'Owner (UI)';
            const now = new Date();
            const pad = (n) => String(n).padStart(2, '0');
            out.last_updated = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}`;
            return out;
        },

        async onSubmit(e) {
            e.preventDefault();
            if (!this.currentIdentity) return;
            this.setStatus('Saving…', 'pending');
            try {
                const payload = this.readForm();
                const res = await fetch(`/api/story-state/${encodeURIComponent(this.currentIdentity)}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    this.setStatus(err.error || `Save failed (${res.status})`, 'error');
                    return;
                }
                const data = await res.json();
                this.populateForm(data.fields || {});
                this.setStatus('Saved ✓', 'success');
                // Auto-clear after a moment
                setTimeout(() => {
                    if (this.statusEl && this.statusEl.textContent === 'Saved ✓') {
                        this.setStatus('', '');
                    }
                }, 2000);
            } catch (err) {
                console.error('[StoryState] Save failed', err);
                this.setStatus('Network error', 'error');
            }
        },

        setStatus(text, kind) {
            if (!this.statusEl) return;
            this.statusEl.textContent = text;
            this.statusEl.dataset.kind = kind || '';
        },
    };

    window.StoryState = StoryState;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => StoryState.init());
    } else {
        StoryState.init();
    }
})();
