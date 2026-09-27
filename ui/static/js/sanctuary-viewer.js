/* ANAM GUIDE: HERO BANNER (SANCTUARY VIEWER)
   Fills the hero card at the top of chat: pulls sanctuary state from the
   hearth-hub cloud worker, sets portrait/location/mood/thought, and handles
   the tap-to-expand view. Looks live in anam.css SECTION 4 + MOBILE;
   structure in static/index.html #sanctuary-viewer. */
/* Sanctuary rendering + expanded view */

// Hearth Hub — cloud sanctuary state API
const SANCTUARY_API = (() => {
    const raw = (window.__sanctuaryApiBase || '').trim();
    if (raw && raw !== '##SANCTUARY_API_BASE##') return raw.replace(/\/+$/, '');
    return 'https://hearth-hub.YOUR-BACKEND.YOUR-ACCOUNT.workers.dev';
})();

const Sanctuary = {
    viewer: null,
    expanded: null,
    state: null,
    pollInterval: null,
    _stateLoading: false,
    _stateRequestId: 0,
    _lastMainBgUrl: '',
    _lastMainPortraitUrl: '',

    init() {
        this.viewer = document.getElementById('sanctuary-viewer');
        this.expanded = document.getElementById('sanctuary-expanded');

        this.viewer.addEventListener('click', () => this.showExpanded());
        document.getElementById('sanctuary-close').addEventListener('click', (e) => {
            e.stopPropagation();
            this.hideExpanded();
        });
        this.expanded.addEventListener('click', (e) => {
            if (e.target === this.expanded) this.hideExpanded();
        });

        this.loadState();
        if (this.pollInterval) clearInterval(this.pollInterval);
        this.pollInterval = setInterval(() => {
            if (!document.hidden) this.loadState();
        }, 30000);
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden) this.loadState();
        });
    },

    async loadState() {
        if (this._stateLoading) return;
        this._stateLoading = true;
        const requestId = ++this._stateRequestId;
        try {
            const data = await fetchJson(`${SANCTUARY_API}/api/sanctuary/state`, { timeoutMs: 8000, retries: 1 });
            if (requestId !== this._stateRequestId) return;
            this.state = data;
            this.render();
        } catch (err) {
            console.warn('[Sanctuary] Failed to load state:', err);
        } finally {
            this._stateLoading = false;
        }
    },

    render() {
        if (!this.state) return;

        const identity = App.currentIdentity;
        const info = this.state.identities?.[identity] || {};
        const mood = info.mood || ''; // still used to pick the portrait's mood variant
        const thought = info.thought || '';
        const timeOfDay = this.state.time_of_day || 'afternoon';

        // Background (cache-bust with timestamp so time-of-day variants load fresh)
        const bgEl = this.viewer.querySelector('.sanctuary-bg');
        const cacheBust = Math.floor(Date.now() / 60000); // changes every minute
        const bgUrl = `${SANCTUARY_API}/api/sanctuary/background/${encodeURIComponent(identity)}?t=${cacheBust}`;
        if (bgUrl !== this._lastMainBgUrl) {
            bgEl.style.backgroundImage = `url(${bgUrl})`;
            this._lastMainBgUrl = bgUrl;
        }

        // Time-of-day filter on background
        bgEl.classList.remove('tod-morning', 'tod-afternoon', 'tod-evening', 'tod-night');
        bgEl.classList.add(`tod-${timeOfDay}`);

        // Portrait
        const portraitEl = this.viewer.querySelector('.sanctuary-portrait');
        const moodParam = mood ? mood.split(',')[0].trim().toLowerCase() : 'default';
        const portraitUrl = `${SANCTUARY_API}/api/sanctuary/portrait/${encodeURIComponent(identity)}?mood=${encodeURIComponent(moodParam)}&t=${cacheBust}`;
        if (portraitUrl !== this._lastMainPortraitUrl) {
            portraitEl.src = portraitUrl;
            this._lastMainPortraitUrl = portraitUrl;
        }
        portraitEl.onerror = () => { portraitEl.style.display = 'none'; };
        portraitEl.onload = () => { portraitEl.style.display = 'block'; };






        const thoughtEl = this.viewer.querySelector('.sanctuary-thought');
        if (thoughtEl) {
            thoughtEl.textContent = thought;



            thoughtEl.style.display = thought ? '' : 'none';
        }
    },

    showExpanded() {
        if (!this.state) return;
        this.expanded.classList.add('visible');
        this.renderExpanded();
    },

    hideExpanded() {
        this.expanded.classList.remove('visible');
    },

    renderExpanded() {
        const grid = this.expanded.querySelector('.sanctuary-grid');
        grid.innerHTML = '';

        const identities = this.state.identities || {};
        const cacheBust = Math.floor(Date.now() / 60000);
        const timeOfDay = this.state.time_of_day || 'afternoon';

        for (const [name, info] of Object.entries(identities)) {
            const card = document.createElement('div');
            card.className = 'sanctuary-room-card';
            card.addEventListener('click', () => {
                App.switchIdentity(name);
                this.hideExpanded();
            });

            const moodParam = info.mood ? info.mood.split(',')[0].trim().toLowerCase() : 'default';
            const thought = info.thought || '';
            const action = info.action || '';

            card.innerHTML = `
                <div class="sanctuary-room-bg tod-${timeOfDay}" style="background-image: url(${SANCTUARY_API}/api/sanctuary/background/${encodeURIComponent(name)}?t=${cacheBust})">
                    <img class="sanctuary-room-portrait"
                         src="${SANCTUARY_API}/api/sanctuary/portrait/${encodeURIComponent(name)}?mood=${encodeURIComponent(moodParam)}&t=${cacheBust}"
                         alt="${escapeHtml(name)}"
                         onerror="this.style.display='none'">
                </div>
                <div class="sanctuary-room-info">
                    <div class="sanctuary-room-name">${escapeHtml(name)}</div>
                    <div class="sanctuary-room-location">${locationDisplayName(info.location)}</div>
                    <div class="sanctuary-room-mood">${escapeHtml(info.mood || '')}</div>
                    ${thought ? `<div class="sanctuary-room-thought">${escapeHtml(thought)}</div>` : ''}
                </div>
            `;
            grid.appendChild(card);
        }
    },

    destroy() {
        if (this.pollInterval) {
            clearInterval(this.pollInterval);
            this.pollInterval = null;
        }
    },
};
