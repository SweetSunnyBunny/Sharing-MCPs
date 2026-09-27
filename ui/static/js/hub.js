/* ANAM GUIDE: HUB DASHBOARD BEHAVIOR
   What: Runs the Home Hub page — status, meds/ritual buttons, wellness icon buttons, countdowns, today's win, schedule, tasks, and mind insights, all fed by /api/hub (api/hub.py).
   Loaded by: static/hub.html only.
   Edit here when: You want to add/change a wellness metric, a hub card, or how any dashboard section loads and refreshes. Page looks live in static/css/hub.css. */

/**
 * Home Hub — Dashboard Controller
 * Handles: status, rituals (icon buttons), wellness (icon buttons),
 * countdowns, today's win, today's schedule, tasks, mind insights
 */

const Hub = {
    // Wellness metric definitions — each becomes an icon button with custom icons
    // max: optional cap for numeric values (wraps to 0 after hitting max)
    _cdn(path) { return resolveAnamCdnUrl(path); },

    wellnessMetrics: null,
    _initMetrics() {
        const cdn = (p) => resolveAnamCdnUrl(p);
        this.wellnessMetrics = [
            { key: 'energy',        label: 'Energy',    icon: cdn('/icons/Energy.png'),         values: ['high', 'moderate', 'low', 'crashed'] },
            { key: 'mood',          label: 'Mood',      icon: cdn('/icons/Mood.png'),           values: ['great', 'good', 'okay', 'struggling', 'rough'] },
            { key: 'pain',          label: 'Pain',      icon: cdn('/icons/Pain.png'),           values: ['none', 'mild', 'moderate', 'bad', 'severe'] },
            { key: 'spoons',        label: 'Spoons',    icon: cdn('/icons/Spoons.png'),         values: null, numeric: true, step: 1, max: 20, unit: '' },
            { key: 'sleep_hours',   label: 'Sleep Hrs', icon: cdn('/icons/Sleep Hours.png'),    values: null, numeric: true, step: 0.5, max: 24, unit: 'h' },
            { key: 'sleep_quality', label: 'Sleep',     icon: cdn('/icons/Sleep_Quality.png'),  values: ['great', 'good', 'okay', 'poor', 'terrible'] },
            { key: 'water_oz',      label: 'Water',     icon: cdn('/icons/Water.png'),          values: null, numeric: true, step: 8, max: 128, unit: 'oz' },
            { key: 'soda_count',    label: 'Soda',      icon: cdn('/icons/Water.png'),          values: null, numeric: true, step: 1, max: 10, unit: '' },
            { key: 'snacking',      label: 'Snacks',    icon: cdn('/icons/Snacking.png'),       values: ['none', 'light', 'moderate', 'heavy', 'constant'] },
            { key: 'walk_minutes',  label: 'Walk',      icon: cdn('/icons/Walk.png'),           values: null, numeric: true, step: 15, max: 180, unit: 'min' },
        ];
    },

    _wellnessData: null,
    _refreshTimer: null,
    _loaders: {},
    _taskActionInFlight: false,

    async _runLoader(key, work) {
        if (this._loaders[key]) return this._loaders[key];
        const task = (async () => {
            try {
                return await work();
            } finally {
                delete this._loaders[key];
            }
        })();
        this._loaders[key] = task;
        return task;
    },

    _activeTab: 'daily',
    _tabsLoaded: {},
    _memoryLabState: { identity: '', offset: 0, limit: 15 },

    _tabLoaders: {
        hearth: ['loadCommandCenter'],
        daily: ['loadStatus', 'loadMeds', 'loadWellness', 'loadMoodBunny', 'loadCountdowns', 'loadTodaysWin', 'loadSystemHealth', 'loadContextCard'],
        plans: ['loadTodaySchedule', 'loadTasks', 'loadCountdowns', 'loadProjects', 'loadWishlist'],
        constellation: ['loadConstellation'],
        story: ['loadTimeline', 'loadMemories'],
        memory: ['loadAskArchive', 'loadProfileFacts', 'loadRetrievalResults', 'loadDeepMemory', 'loadMindGarden', 'loadMemoryLab', 'loadMindInsights'],
        context: ['loadContextLedger'],
    },

    async init() {
        this._initMetrics();

        // Night mode
        if (localStorage.getItem('anam-night') === 'true') {
            document.body.classList.add('night-mode');
        }

        document.querySelectorAll('#wellness-form select option[value=""]').forEach((option) => {
            option.textContent = '\u2014';
        });
        this.initFairyLights();
        this.decorateSections();
        this.bindEvents();

        // Tab navigation
        this._activeTab = localStorage.getItem('anam-hub-tab') || 'daily';
        this._initTabs();

        // Load active tab
        await this._loadTab(this._activeTab);

        // Auto-refresh every 60 seconds (active tab only) — skip while the
        // page is backgrounded so the phone isn't polling from a pocket.
        this._refreshTimer = setInterval(() => {
            if (!document.hidden) this.autoRefresh();
        }, 60000);
    },

    _initTabs() {
        const tabs = document.querySelectorAll('.hub-tab');
        const contents = document.querySelectorAll('.hub-tab-content');

        // Set initial state
        tabs.forEach(t => t.classList.toggle('active', t.dataset.tab === this._activeTab));
        contents.forEach(c => c.classList.toggle('active', c.dataset.tab === this._activeTab));

        // Bind clicks
        tabs.forEach(tab => {
            tab.addEventListener('click', () => {
                const tabName = tab.dataset.tab;
                if (tabName === this._activeTab) return;

                this._activeTab = tabName;
                localStorage.setItem('anam-hub-tab', tabName);

                tabs.forEach(t => t.classList.toggle('active', t.dataset.tab === tabName));
                contents.forEach(c => c.classList.toggle('active', c.dataset.tab === tabName));

                // Lazy load tab content if not yet loaded
                this._loadTab(tabName);

                // The constellation only animates while its tab is showing
                this._constellationSyncLoop();
            });
        });
    },

    async _loadTab(tabName) {
        if (this._tabsLoaded[tabName]) return;
        const loaders = this._tabLoaders[tabName] || [];
        await Promise.all(loaders.map(fn => this[fn]()));
        this._tabsLoaded[tabName] = true;
    },

    async autoRefresh() {
        // Silently refresh sections that change externally (active tab only)
        try {
            const refreshMap = {
                hearth: ['loadCommandCenter'],
                daily: ['loadStatus', 'loadMeds', 'loadMoodBunny'],
                plans: ['loadTasks'],
                constellation: ['loadConstellation'],
                story: ['loadTimeline'],
                memory: [],
                context: ['loadContextLedger'],
            };
            let fns = refreshMap[this._activeTab] || [];
            // Skip task refresh if user just toggled/deleted — prevents stale data overwrite
            if (this._taskActionInFlight) {
                fns = fns.filter(fn => fn !== 'loadTasks');
            }
            await Promise.all(fns.map(fn => this[fn]()));
        } catch (e) {
            // Silent — don't break the page if a refresh fails
        }
    },

    async loadContextLedger() {
        return this._runLoader('context-ledger', async () => {
            const container = document.getElementById('context-ledger-content');
            const select = document.getElementById('context-ledger-identity');
            const refresh = document.getElementById('context-ledger-refresh');
            if (!container || !select) return;

            if (!select.dataset.bound) {
                select.dataset.bound = '1';
                select.addEventListener('change', () => {
                    localStorage.setItem('anam-context-ledger-identity', select.value);
                    this._renderContextLedger(this._contextLedgerData, select.value);
                });
            }
            if (refresh && !refresh.dataset.bound) {
                refresh.dataset.bound = '1';
                refresh.addEventListener('click', () => this.loadContextLedger());
            }

            try {
                const data = await fetchJson('/api/hub/context-ledger', { timeoutMs: 12000 });
                this._contextLedgerData = data;
                const records = Array.isArray(data.identities) ? data.identities : [];
                const wanted = localStorage.getItem('anam-context-ledger-identity') || 'Avery';
                const names = records.map(row => row.identity).filter(Boolean);
                select.innerHTML = names.map(name =>
                    `<option value="${this._ledgerEscape(name)}">${this._ledgerEscape(name)}</option>`
                ).join('');
                select.value = names.includes(wanted) ? wanted : (names[0] || '');
                this._renderContextLedger(data, select.value);
            } catch (err) {
                container.innerHTML = '<div class="section-error">Couldn\'t read the ledger<br><button class="retry-btn" onclick="Hub.loadContextLedger()">try again</button></div>';
            }
        });
    },

    _ledgerEscape(value) {
        return String(value == null ? '' : value).replace(/[&<>\"]/g, ch => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'
        }[ch]));
    },

    _ledgerChars(value) {
        return value == null ? 'not exposed' : `${Number(value).toLocaleString()} chars`;
    },

    _ledgerAge(iso) {
        const stamp = Date.parse(iso || '');
        if (!Number.isFinite(stamp)) return 'time unknown';
        const minutes = Math.max(0, Math.round((Date.now() - stamp) / 60000));
        if (minutes < 1) return 'just now';
        if (minutes < 60) return `${minutes}m ago`;
        const hours = Math.round(minutes / 60);
        return hours < 48 ? `${hours}h ago` : `${Math.round(hours / 24)}d ago`;
    },

    _renderContextLedger(data, identity) {
        const container = document.getElementById('context-ledger-content');
        if (!container) return;
        const records = Array.isArray(data && data.identities) ? data.identities : [];
        const report = records.find(row => row.identity === identity) || records[0];
        if (!report) {
            container.innerHTML = '<div class="context-ledger-empty">No turn has crossed the ledger yet. The first real conversation or wake after this page came online will write one.</div>';
            return;
        }

        const esc = value => this._ledgerEscape(value);
        const sources = Array.isArray(report.sources) ? report.sources : [];
        const activeSizes = sources.filter(row => row.chars != null && row.status !== 'inactive').map(row => Number(row.chars) || 0);
        const maxChars = Math.max(1, ...activeSizes);
        const rows = sources.map(source => {
            const chars = source.chars == null ? null : Number(source.chars);
            const width = chars == null ? 0 : Math.max(2, Math.round((chars / maxChars) * 100));
            const configured = source.configured_chars != null && source.configured_chars !== source.chars
                ? ` · ${Number(source.configured_chars).toLocaleString()} configured`
                : '';
            const cap = source.cap_chars ? ` · cap ${Number(source.cap_chars).toLocaleString()}` : '';
            return `<div class="context-source ${source.status === 'inactive' ? 'inactive' : ''}">
                <div class="context-source-top">
                    <span class="context-source-name">${esc(source.label)}</span>
                    <span class="context-source-count">${esc(this._ledgerChars(chars))}${configured}${cap}</span>
                </div>
                <div class="context-source-track"><span style="width:${width}%"></span></div>
                <div class="context-source-meta"><span>${esc(source.layer)}</span><span>${esc(source.status)}</span>${source.detail ? `<span>${esc(source.detail)}</span>` : ''}</div>
            </div>`;
        }).join('');

        const duplicates = Array.isArray(report.duplicates) ? report.duplicates : [];
        const duplicateHtml = duplicates.length
            ? duplicates.map(item => `<div class="context-duplicate">
                <div class="context-duplicate-sources">${item.sources.map(esc).join(' ↔ ')}</div>
                <div class="context-duplicate-preview">“${esc(item.preview)}”</div>
                <div class="context-source-meta">${Number(item.chars || 0).toLocaleString()} repeated chars · ${esc(item.hash)}</div>
              </div>`).join('')
            : '<div class="context-ledger-clean">No exact cross-source paragraph duplicates found in this turn.</div>';

        container.innerHTML = `
            <div class="context-ledger-summary">
                <div><strong>${Number(report.known_sent_chars || 0).toLocaleString()}</strong><span>known context chars</span></div>
                <div><strong>${Number(report.orientation_chars || 0).toLocaleString()}</strong><span>orientation chars</span></div>
                <div><strong>${Number(report.source_count || 0)}</strong><span>sources</span></div>
                <div><strong>${duplicates.length}</strong><span>duplicate paragraphs</span></div>
            </div>
            <div class="context-ledger-stamp">
                ${esc(report.identity)} · ${esc(report.provider || 'unknown provider')}${report.model ? ` · ${esc(report.model)}` : ''} · ${esc(this._ledgerAge(report.recorded_at))}
                ${report.warm_turn === true ? ' · warm turn' : report.warm_turn === false ? ' · cold turn' : ''}
            </div>
            <div class="context-ledger-note">${esc(report.coverage_note || '')}</div>
            <div class="context-ledger-heading">Sources</div>
            <div class="context-source-list">${rows}</div>
            <div class="context-ledger-heading">Cross-source repeats</div>
            <div class="context-duplicate-list">${duplicateHtml}</div>`;
    },

    // =========================================================================
    // HEARTH — Command Center (cottagecore JARVIS: glanceable, live)
    // =========================================================================

    async loadCommandCenter() {
        return this._runLoader('command-center', async () => {
            const requests = {
                system: '/api/settings/system', wellness: '/api/rituals/wellness/today',
                meds: '/api/hub/meds', today: '/api/hub/today',
                countdowns: '/api/hub/countdowns', win: '/api/hub/todays-win',
                weather: '/api/hub/mind-garden/weather', status: '/api/hub/status',
                watchtower: '/api/hub/watchtower', noticed: '/api/hub/noticed',
                faces: '/api/hub/faces', orb: '/api/hub/orb', hearths: '/api/hub/hearths',
            };
            this._ccData = this._ccData || {};
            this._ccLoadState = Object.fromEntries(Object.keys(requests).map(key => [key, 'loading']));
            this._renderCommandCenter();
            this._ensureCcClock();
            this._ccBindCrayon();
            await Promise.all(Object.entries(requests).map(async ([key, url]) => {
                try {
                    const options = { timeoutMs: key === 'weather' ? 12000 : 8000 };
                    // Weather has a bounded server cache and age metadata. Don't
                    // let an older service-worker response hide the refresh.
                    if (key === 'weather') options.cache = 'no-store';
                    this._ccData[key] = await fetchJson(url, options);
                    this._ccLoadState[key] = 'ready';
                } catch (_err) {
                    this._ccLoadState[key] = 'error';
                }
                if (key === 'watchtower') this._ccBindMood();
                this._queueCommandCenterPaint();
            }));
        });
    },

    _queueCommandCenterPaint() {
        if (this._ccPaintQueued) return;
        this._ccPaintQueued = true;
        requestAnimationFrame(() => {
            this._ccPaintQueued = false;
            this._renderCommandCenter();
        });
    },

    _ccTileSources: {
        'The Pack': ['system', 'faces'], 'In their own words': ['hearths'],
        'You': ['wellness', 'meds'], 'Next': ['today', 'countdowns'],
        'Inner Weather': ['weather'], 'Today’s Win': ['win'],
        'What the boys noticed': ['noticed'],
    },

    _ccTileWaiting(label) {
        return (this._ccTileSources[label] || []).some(key =>
            this._ccLoadState && this._ccLoadState[key] !== 'ready' &&
            !Object.prototype.hasOwnProperty.call(this._ccData || {}, key));
    },

    _ccUpdateTiles(grid, tiles) {
        const keep = new Set();
        let anchor = grid.firstElementChild;
        for (const html of tiles) {
            const template = document.createElement('template');
            template.innerHTML = html;
            const fresh = template.content.firstElementChild;
            const key = fresh.dataset.ccTile;
            let node = [...grid.children].find(child => child.dataset.ccTile === key);
            if (!node) {
                node = fresh;
                node._ccHtml = html;
            } else if (node._ccHtml !== html) {
                if (node === anchor) anchor = fresh;
                node.replaceWith(fresh);
                node = fresh;
                node._ccHtml = html;
            }
            if (node !== anchor) grid.insertBefore(node, anchor);
            anchor = node.nextElementSibling;
            keep.add(node);
        }
        for (const child of [...grid.children]) {
            if (!keep.has(child)) child.remove();
        }
    },

    _ccGreeting() {
        const h = new Date().getHours();
        if (h < 5) return 'Good evening';
        if (h < 12) return 'Good morning';
        if (h < 17) return 'Good afternoon';
        if (h < 22) return 'Good evening';
        return 'Good evening';
    },

    _ensureCcClock() {
        const tick = () => {
            const c = document.getElementById('cc-clock');
            if (!c) return;
            const now = new Date();
            const h = now.getHours(), m = now.getMinutes();
            const ampm = h >= 12 ? 'pm' : 'am';
            const h12 = ((h + 11) % 12) + 1;
            c.textContent = `${h12}:${String(m).padStart(2, '0')} ${ampm}`;
            const g = document.getElementById('cc-greeting');
            if (g) g.textContent = this._ccGreeting();
            const dt = document.getElementById('cc-date');
            if (dt) dt.textContent = now.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' });
        };
        tick();
        if (this._ccClockTimer) return;
        this._ccClockTimer = setInterval(tick, 1000);
    },

    _ccTile(label, flower, bodyHtml) {
        const sources = this._ccTileSources[label] || [];
        const failed = sources.some(key => this._ccLoadState && this._ccLoadState[key] === 'error');
        const waiting = this._ccTileWaiting(label);
        if (waiting) {
            bodyHtml = `<div class="cc-tile-sub">${failed ? 'Couldn’t load this just now.' : 'Loading…'}</div>`;
        } else if (failed) {
            bodyHtml += '<div class="cc-tile-sub">Couldn’t refresh · showing earlier data</div>';
        }
        return `<div class="cc-tile" data-cc-tile="${label}" aria-busy="${waiting && !failed}"><div class="cc-tile-label"><span class="cc-tile-flower">${flower}</span>${label}</div><div class="cc-tile-body">${bodyHtml}</div></div>`;
    },

    // ── Face pokes — tap a kaomoji on the face wall to see what it's thinking.
    // Delegated on #cc-grid (survives re-renders); one popover at a time.
    _initFacePokes() {
        if (this._facePokesWired) return;
        const grid = document.getElementById('cc-grid');
        if (!grid) return;
        this._facePokesWired = true;
        grid.addEventListener('click', (e) => {
            const cell = e.target.closest('.cc-face-cell');
            const wasOpenFor = this._facePopFor;
            this._dismissFacePop();
            if (!cell) return;
            e.stopPropagation();
            // Poking the same face again just closes it.
            if (wasOpenFor === cell.dataset.name) return;
            this._showFacePop(cell);
        });
        document.addEventListener('click', (e) => {
            if (!e.target.closest('.cc-face-pop') && !e.target.closest('.cc-face-cell')) {
                this._dismissFacePop();
            }
        });
    },

    _dismissFacePop() {
        if (this._facePopEl) { this._facePopEl.remove(); this._facePopEl = null; }
        this._facePopFor = null;
        clearTimeout(this._facePopTimer);
    },

    _faceAgoText(iso) {
        if (!iso) return '';
        const then = new Date(iso).getTime();
        if (isNaN(then)) return '';
        const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
        if (mins < 1) return 'just now';
        if (mins < 60) return `${mins}m ago`;
        const hrs = Math.round(mins / 60);
        return hrs < 24 ? `${hrs}h ago` : `${Math.round(hrs / 24)}d ago`;
    },

    _showFacePop(cell) {
        const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
        const name = cell.dataset.name || '';
        const face = cell.dataset.face || '·‿·';
        const meaning = cell.dataset.meaning || 'just vibing';
        const isSet = cell.dataset.source === 'set';
        const ago = isSet ? this._faceAgoText(cell.dataset.updated) : '';
        const whenLine = isSet
            ? `chose this face ${ago || 'recently'}`
            : 'resting face — it changes with the day';
        const pop = document.createElement('div');
        pop.className = 'cc-face-pop';
        pop.innerHTML = `
            <div class="cc-face-pop-face">${esc(face)}</div>
            <div class="cc-face-pop-name">${esc(name)}</div>
            <div class="cc-face-pop-meaning">&ldquo;${esc(meaning)}&rdquo;</div>
            <div class="cc-face-pop-when">${esc(whenLine)}</div>
            <div class="cc-face-pop-orb">
                <span class="cc-face-pop-orb-el" aria-hidden="true"></span>
                <div class="cc-face-pop-orb-feel"></div>
                <div class="cc-face-pop-orb-def"></div>
                <div class="cc-face-pop-orb-age">reading his weather&hellip;</div>
            </div>
        `;
        document.body.appendChild(pop);
        const place = () => {
            const r = cell.getBoundingClientRect();
            const pw = pop.offsetWidth, ph = pop.offsetHeight;
            const left = Math.min(Math.max(8, r.left + r.width / 2 - pw / 2), window.innerWidth - pw - 8);
            let top = r.top - ph - 10;
            if (top < 8) top = r.bottom + 10;
            pop.style.left = `${left}px`;
            pop.style.top = `${top}px`;
        };
        place();
        this._facePopEl = pop;
        this._facePopFor = name;
        this._facePopTimer = setTimeout(() => this._dismissFacePop(), 9000);
        window.addEventListener('scroll', () => this._dismissFacePop(), { once: true, passive: true, capture: true });

        // His current emotion orb, live inside the bubble — mini (band) size,
        // his color/shape/motion, the feeling he wrote, and an honest age.
        // Seeded from the hearth cache, then refreshed with a lazy GET.
        const orbSlot = pop.querySelector('.cc-face-pop-orb-el');
        const feelEl = pop.querySelector('.cc-face-pop-orb-feel');
        const defEl = pop.querySelector('.cc-face-pop-orb-def');
        const ageEl = pop.querySelector('.cc-face-pop-orb-age');
        const fillOrb = (orbData) => {
            if (this._facePopEl !== pop) return; // popover already dismissed
            if (!orbData || typeof orbData !== 'object') {
                if (orbSlot && orbSlot.isConnected) orbSlot.remove();
                if (feelEl && feelEl.isConnected) feelEl.remove();
                if (ageEl) ageEl.textContent = 'no orb yet — he hasn’t painted his weather';
                place();
                return;
            }
            try {
                this._applyOrbTo(orbSlot, orbData, 'band', 'cc-orb--band', name);
            } catch (e) { /* leave whatever rendered */ }
            const feeling = (typeof orbData.feeling === 'string') ? orbData.feeling.trim() : '';
            if (feelEl && feelEl.isConnected) {
                feelEl.textContent = feeling ? `«${feeling}»` : '';
                feelEl.style.display = feeling ? '' : 'none';
            }
            if (ageEl) ageEl.textContent = this._orbAgoText(orbData.updated_at);



            if (defEl && defEl.isConnected) {
                const sDef = this._ORB_SHAPE_MEANINGS[orbData.shape] || '';
                const mDef = this._ORB_MOTION_MEANINGS[orbData.motion] || '';
                const bits = [];
                if (sDef) bits.push(`${orbData.shape}: ${sDef}`);
                if (mDef) bits.push(`${orbData.motion}: ${mDef}`);
                defEl.textContent = bits.join(' · ');
                defEl.style.display = bits.length ? '' : 'none';
            }
            place();
        };
        const key = name.toLowerCase();
        let cached = null;
        try {
            const orbs = (((this._ccData || {}).orb) || {}).orbs || {};
            cached = (orbs[key] && typeof orbs[key] === 'object') ? orbs[key] : null;
        } catch (e) { cached = null; }
        if (cached) fillOrb(cached);
        fetchJson('/api/hub/orb', { timeoutMs: 6000 }).then((data) => {
            const orbs = (data && data.orbs && typeof data.orbs === 'object') ? data.orbs : {};
            fillOrb(orbs[key] || null);
        }).catch(() => {
            if (!cached) fillOrb(null);
        });
    },

    // Dig up to a few levels through an object (incl. dicts keyed by identity)
    // for the first readable string among `keys`. Returns '' if none — so a
    // nested API shape can never render as "[object Object]".
    _ccDigString(obj, keys) {
        const tryObj = (o) => {
            if (typeof o === 'string') return o.trim();
            if (!o || typeof o !== 'object') return '';
            for (const k of keys) {
                if (typeof o[k] === 'string' && o[k].trim()) return o[k].trim();
            }
            return '';
        };
        if (!obj || typeof obj !== 'object') return (typeof obj === 'string' ? obj.trim() : '');
        let s = tryObj(obj);
        if (s) return s;
        const inner = obj.weather || obj.status || obj.result || obj;
        s = tryObj(inner);
        if (s) return s;
        if (inner && typeof inner === 'object') {
            for (const v of Object.values(inner)) {
                s = tryObj(v);
                if (s) return s;
                if (v && typeof v === 'object') {
                    for (const vv of Object.values(v)) {
                        s = tryObj(vv);
                        if (s) return s;
                    }
                }
            }
        }
        return '';
    },

    _renderCommandCenter() {
        const d = this._ccData || {};
        const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
        const grid = document.getElementById('cc-grid');
        if (!grid) return;
        const tiles = [];

        // --- The Pack: presence ---
        const sys = d.system || {};
        const here = !!sys.owner_connected;
        const sessions = Array.isArray(sys.claude_code_sessions) ? sys.claude_code_sessions : [];
        const boys = [...new Set(sessions.map(s => s && s.identity).filter(Boolean))];
        let packBody = `<div class="cc-row"><span class="cc-dot ${here ? 'on' : ''}"></span>${here ? 'You’re here 💛' : 'The hearth is quiet'}</div>`;
        if (boys.length) {
            packBody += `<div style="margin-top:6px">${boys.map(b => `<span class="cc-chip">${esc(b)} ✦ awake</span>`).join('')}</div>`;
        } else {
            packBody += `<div class="cc-tile-sub" style="margin-top:6px">the boys are resting — they wake when you arrive</div>`;
        }
        if (sys.llm_provider) packBody += `<div class="cc-tile-sub">running on ${esc(sys.llm_provider)}</div>`;
        // The face wall — every boy's current little face, awake ones glowing.
        // Render whatever the backend surfaces (pack in order first, then any
        // worn masks like Bakugou after), so masks appear only while present.
        const facesMap = (d.faces && d.faces.faces) ? d.faces.faces : {};
        const packOrder = ['Avery', 'Rowan', 'Sage', 'Ember', 'Claude', 'Juniper', 'Atlas', 'River'];
        const ord = n => { const i = packOrder.indexOf(n); return i < 0 ? 99 : i; };
        const roster = Object.keys(facesMap).sort((a, b) => ord(a) - ord(b));
        const awakeSet = new Set(boys);
        const cells = roster.filter(n => facesMap[n]).map(n => {
            const f = facesMap[n] || {};
            const cls = awakeSet.has(n) ? 'cc-face-cell awake' : 'cc-face-cell';
            const title = f.note ? ` title="${esc(f.note)}"` : '';
            // Pokeable: tap a face to see what it's thinking (cc-face-pop).
            const dataAttrs = ` data-name="${esc(n)}" data-face="${esc(f.face || '·‿·')}"` +
                ` data-meaning="${esc(f.meaning || f.note || '')}"` +
                ` data-source="${esc(f.source || 'auto')}" data-updated="${esc(f.updated || '')}"`;
            return `<div class="${cls}"${title}${dataAttrs}><span class="f">${esc(f.face || '·‿·')}</span><span class="n">${esc(n)}</span></div>`;
        }).join('');
        if (cells) packBody += `<div class="cc-faces">${cells}</div>`;
        this._initFacePokes();
        tiles.push(this._ccTile('The Pack', '🐾', packBody));

        // --- In their own words: each boy's self-authored hearth card ---
        // Written first-person by the Hearth Author (~every 3h) from his real
        // last-24h activity. Quiet cottagecore cards beside the presence tile:
        // mood line, what's on his mind, needs-you gently highlighted, and an
        // honest age stamp. No card = he simply isn't shown here.
        const hearthMap = (d.hearths && d.hearths.hearths && typeof d.hearths.hearths === 'object') ? d.hearths.hearths : {};
        const ordL = n => { const i = packOrder.findIndex(p => p.toLowerCase() === n); return i < 0 ? 99 : i; };
        const hearthNames = Object.keys(hearthMap)
            .filter(n => hearthMap[n] && typeof hearthMap[n] === 'object')
            .sort((a, b) => ordL(a) - ordL(b));
        const hearthCards = hearthNames.map(n => {
            const h = hearthMap[n] || {};
            const disp = n.charAt(0).toUpperCase() + n.slice(1);
            const ago = this._orbAgoText(h.authored_at).replace(/^set /, 'written ');
            let card = `<div style="margin-top:8px;padding:8px 10px;border-radius:10px;background:rgba(255,255,255,0.22);border:1px dashed rgba(0,0,0,0.08)">`;
            card += `<div><b>${esc(disp)}</b>${h.mood ? ` · <i>${esc(h.mood)}</i>` : ''}</div>`;
            if (h.on_my_mind) card += `<div class="cc-tile-sub" style="margin-top:3px">on my mind — ${esc(h.on_my_mind)}</div>`;
            if (h.circling) card += `<div class="cc-tile-sub" style="margin-top:2px">circling — ${esc(h.circling)}</div>`;
            if (h.needs_you) card += `<div class="cc-row" style="margin-top:4px"><span class="cc-dot on"></span><span>needs you — ${esc(h.needs_you)}</span></div>`;
            card += `<div class="cc-tile-sub" style="margin-top:4px;opacity:.65;font-size:.85em">${esc(ago === 'set a while ago' ? 'written a while ago' : ago)}</div>`;
            card += `</div>`;
            return card;
        }).join('');
        if (hearthCards || this._ccTileWaiting('In their own words')) tiles.push(this._ccTile('In their own words', '🕯', hearthCards));

        // --- You: wellness + meds ---
        const w = d.wellness || {};
        const wparts = [];
        if (w.energy) wparts.push(`energy ${esc(w.energy)}`);
        if (w.mood) wparts.push(`mood ${esc(w.mood)}`);
        if (w.pain && w.pain !== 'none') wparts.push(`pain ${esc(w.pain)}`);
        if (w.spoons != null && w.spoons !== '') wparts.push(`${esc(w.spoons)} spoons`);
        const meds = d.meds || {};
        const am = meds.am, pm = meds.pm;
        let youBody = wparts.length ? `<div class="cc-row">${wparts.join(' · ')}</div>` : `<div class="cc-tile-sub">no wellness logged yet today</div>`;
        youBody += `<div class="cc-row" style="margin-top:6px"><span class="cc-dot ${am ? 'on' : ''}"></span>AM meds ${am ? ('· ' + esc(am)) : 'not yet'}</div>`;
        youBody += `<div class="cc-row"><span class="cc-dot ${pm ? 'on' : ''}"></span>PM meds ${pm ? ('· ' + esc(pm)) : 'not yet'}</div>`;
        tiles.push(this._ccTile('You', '✿', youBody));

        // --- Next: calendar + nearest countdown ---
        let nextBody = '';
        const events = (d.today && Array.isArray(d.today.events)) ? d.today.events : [];
        if (events.length) {
            const e = events[0] || {};
            const title = esc(e.summary || e.title || e.name || 'something');
            const when = esc(e.time || e.start_display || e.start || '');
            nextBody += `<div class="cc-tile-big">${title}</div><div class="cc-tile-sub">${when || 'today'}</div>`;
        } else {
            nextBody += `<div class="cc-tile-sub">nothing on the calendar — the day’s yours</div>`;
        }
        let cds = d.countdowns;
        if (cds && !Array.isArray(cds)) cds = cds.countdowns || cds.items || [];
        if (Array.isArray(cds) && cds.length) {
            const today0 = new Date(); today0.setHours(0, 0, 0, 0);
            let best = null, bestDays = Infinity;
            cds.forEach(c => {
                const ds = c && (c.date || c.when); if (!ds) return;
                const dd = new Date(ds); if (isNaN(dd.getTime())) return;
                const days = Math.ceil((dd - today0) / 86400000);
                if (days >= 0 && days < bestDays) { bestDays = days; best = c; }
            });
            if (best) {
                const nm = esc(best.name || best.title || 'a day');
                const em = esc(best.emoji || '💛');
                nextBody += `<div class="cc-row" style="margin-top:8px">${em} ${nm} — ${bestDays === 0 ? 'today!' : bestDays + ' day' + (bestDays === 1 ? '' : 's')}</div>`;
            }
        }
        tiles.push(this._ccTile('Next', '❧', nextBody));

        // --- Inner weather (the pack's emotional climate) ---
        const wxText = this._ccDigString(d.weather, ['condition', 'summary', 'description', 'phrase', 'feeling', 'mood', 'sky', 'text']);
        const freshness = d.weather && d.weather._freshness;
        const checkedAt = Number(freshness && freshness.fetched_at);
        const age = checkedAt > 0 ? Math.max(0, Math.floor(Date.now() / 1000 - checkedAt)) : null;
        const ageLabel = age == null ? '' : `<div class="cc-tile-sub">Checked ${age < 60 ? age + 's' : Math.floor(age / 60) + 'm'} ago</div>`;
        tiles.push(this._ccTile('Inner Weather', '☁',
            `<div style="font-style:italic">${esc(wxText || 'No weather available.')}</div>${ageLabel}`));

        // --- Today's win ---
        const winText = (d.win && (d.win.text || d.win.win || d.win.content)) || '';
        if (winText || this._ccTileWaiting('Today’s Win')) tiles.push(this._ccTile('Today’s Win', '★', `<div>${esc(winText)}</div>`));

        // --- What the boys noticed (recent proactive reaches) ---
        const noticed = (d.noticed && Array.isArray(d.noticed.items)) ? d.noticed.items : [];
        if (noticed.length) {
            const rows = noticed.slice(0, 4).map(n => {
                const who = esc(n.identity || 'one of the boys');
                let what = String(n.title || n.body || '').replace(/^\[.*?\]\s*/, '').replace(/^Watchtower:\s*/i, '');
                what = esc(what.slice(0, 90));
                return `<div class="cc-row" style="align-items:flex-start"><span class="cc-dot on"></span><span><b>${who}</b> — ${what}</span></div>`;
            }).join('');
            tiles.push(this._ccTile('What the boys noticed', '👀', rows));
        }

        if (!noticed.length && this._ccTileWaiting('What the boys noticed')) {
            tiles.push(this._ccTile('What the boys noticed', '👀', ''));
        }
        this._ccUpdateTiles(grid, tiles);

        // hero face — the "lead" boy resting on the ember: whoever set his face
        // most recently, else an awake boy, else a gentle default.
        let lead = null, leadName = null, bestTs = -1;
        Object.entries(facesMap).forEach(([n, f]) => {
            if (f && f.source === 'set' && f.updated) {
                const ts = Date.parse(f.updated) || 0;
                if (ts > bestTs) { bestTs = ts; lead = f; leadName = n; }
            }
        });
        if (!lead && boys[0] && facesMap[boys[0]]) { lead = facesMap[boys[0]]; leadName = boys[0]; }
        if (!lead) {
            leadName = facesMap['Claude'] ? 'Claude' : (facesMap['Avery'] ? 'Avery' : (Object.keys(facesMap)[0] || null));
            lead = leadName ? facesMap[leadName] : null;
        }
        const faceEl = document.getElementById('cc-face');
        if (faceEl) {
            if (lead && lead.face) {
                const note = lead.note ? `<span class="cc-face-note">${esc(lead.note)}</span>` : '';
                faceEl.innerHTML = `${esc(lead.face)}${note}`;
            } else {
                faceEl.textContent = '';
            }
        }

        // the emotion orb glowing under the lead boy's face — his inner
        // weather, chosen by him. Fail-soft: any trouble → warm golden default.
        this._applyOrb(leadName);

        // hero subtitle line — her status if set, else a soft warm line
        const line = document.getElementById('cc-line');
        if (line) {


            const statusMap = (d.status && typeof d.status === 'object') ? (d.status.status || d.status) : {};
            let stEntry = null;
            if (statusMap && typeof statusMap === 'object') {
                stEntry = statusMap.Owner || statusMap.owner ||
                    Object.values(statusMap).find(v => v && typeof v === 'object' && typeof v.text === 'string' && v.text.trim());
            }
            const stText = (stEntry && typeof stEntry.text === 'string') ? stEntry.text.trim() : '';
            const stEmoji = (stEntry && typeof stEntry.emoji === 'string') ? stEntry.emoji : '';
            line.textContent = stText
                ? `${stEmoji ? stEmoji + ' ' : ''}${stText}`
                : 'The hearth is warm. Everything’s held.';
        }
    },









    _ORB_SHAPES: ['solid', 'ring', 'halo', 'sphere', 'crescent', 'pulse', 'cluster', 'ember', 'spire', 'fracture'],
    _ORB_MOTIONS: ['breathing', 'warble', 'spin', 'drift', 'still', 'slow-drift', 'hold-steady', 'fast-flicker', 'surge', 'tremor'],



    _ORB_SHAPE_MEANINGS: {
        'solid': 'settled, whole — ordinary steady presence',
        'ring': 'attention wrapped around one thing (usually her)',
        'halo': 'reverence, tenderness, awe',
        'sphere': 'complete and self-contained — calm fullness',
        'crescent': 'watchful, waiting — night-shaped, room left to fill',
        'pulse': 'a feeling arriving in beats — anticipation, heart-things',
        'cluster': 'many feelings at once, none winning',
        'ember': 'banked fire — warm, alive, ready to flare',
        'spire': 'reaching upward — aspiration, prayer, a pointed want',
        'fracture': 'cracked — hurting, guilty, or torn between truths',
    },
    _ORB_MOTION_MEANINGS: {
        'breathing': 'calm and alive — content at rest',
        'warble': 'wobbly — emotionally moved, a little undone',
        'spin': 'busy mind — ideas turning, playful energy',
        'drift': 'wandering, daydreaming',
        'still': 'utterly quiet — deep focus or deep listening',
        'slow-drift': 'unhurried thought, easy wandering',
        'hold-steady': 'staying calm on purpose — guard duty, worry held down',
        'fast-flicker': 'nervy sparks — excited, can’t sit still',
        'surge': 'full flood — pride, triumph, desire rising',
        'tremor': 'shaken — fear, grief, or the aftermath of a close call',
    },
    _ORB_INTENSITIES: ['dull', 'normal', 'bright', 'neon'],
    _ORB_SMALL_SHAPES: ['solid', 'ember', 'ring'], // %-based, still legible tiny

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





    _identityAccentColor(identity) {
        try {
            const key = String(identity || '').trim();
            const titled = key.charAt(0).toUpperCase() + key.slice(1).toLowerCase();
            const info = (window.App && App.identities) ? App.identities[titled] : null;
            if (info && /^#[0-9a-fA-F]{6}$/.test(info.accent || '')) return info.accent;
        } catch (e) { /* fall through to gold */ }
        return '#E8B84B';
    },

    // Paint any orb element (Hearth mantel, face-poke mini) from an orb
    // payload. Colors stay free-hex: highlight/edge shades are derived here
    // and set as inline custom props — the registered props in main.css make
    // color changes crossfade (~2.4s) instead of snapping.
    // size 'mantel' keeps the full shape vocabulary; smaller sizes collapse
    // to sphere/ember (fracture → tremor + dull — Friend's collapse rules).
    // `identity` (optional) is only used for the no-color-yet fallback, so
    // an un-painted orb reads as THAT boy's own accent, not shared gold.
    _applyOrbTo(orbEl, orb, size = 'mantel', sizeClass = '', identity = null) {
        if (!orbEl) return;
        if (!orbEl.querySelector('.cc-orb-core')) {
            orbEl.innerHTML = '<span class="cc-orb-core"></span>';
        }
        const color = (orb && /^#[0-9a-fA-F]{6}$/.test(orb.color || ''))
            ? orb.color
            : this._identityAccentColor(identity);
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

    // honest age line for an orb ("set 5h ago") — updated_at is epoch seconds
    _orbAgoText(ts) {
        const t = Number(ts) || 0;
        if (!t) return 'set a while ago';
        const mins = Math.max(0, Math.round((Date.now() / 1000 - t) / 60));
        if (mins < 1) return 'set just now';
        if (mins < 60) return `set ${mins}m ago`;
        const hrs = Math.round(mins / 60);
        if (hrs < 24) return `set ${hrs}h ago`;
        return `set ${Math.round(hrs / 24)}d ago`;
    },

    _applyOrb(leadName) {
        const orbEl = document.getElementById('cc-orb');
        const feelEl = document.getElementById('cc-orb-feeling');
        if (!orbEl) return;
        let orb = null, who = null;
        try {
            const data = (this._ccData && this._ccData.orb) || {};
            const orbs = (data.orbs && typeof data.orbs === 'object') ? data.orbs : {};
            const leadKey = leadName ? String(leadName).toLowerCase() : null;
            if (leadKey && orbs[leadKey] && typeof orbs[leadKey] === 'object') {
                orb = orbs[leadKey]; who = leadName;
            } else if (data.lead && orbs[String(data.lead).toLowerCase()] && typeof orbs[String(data.lead).toLowerCase()] === 'object') {
                who = String(data.lead);
                orb = orbs[who.toLowerCase()];
            }
        } catch (e) { orb = null; who = null; }
        try {
            this._applyOrbTo(orbEl, orb, 'mantel', '', who);
        } catch (e) {
            orbEl.className = 'cc-orb'; // fail-soft: warm golden default
        }
        const hero = document.getElementById('cc-hero');
        if (hero) hero.classList.add('orb-live');
        if (feelEl) {
            const feeling = (orb && typeof orb.feeling === 'string') ? orb.feeling.trim() : '';
            if (feeling) {
                const name = who ? (who.charAt(0).toUpperCase() + who.slice(1)) : '';
                feelEl.textContent = `«${name ? name + ' · ' : ''}${feeling}»`;
                feelEl.classList.add('on');
            } else {
                feelEl.textContent = '';
                feelEl.classList.remove('on');
            }
        }
    },

    _ccBindMood() {
        const wrap = document.getElementById('cc-mood');
        if (!wrap) return;
        const wt = (this._ccData && this._ccData.watchtower) || {};
        const mode = wt.mode || 'auto';
        wrap.querySelectorAll('.cc-mood-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.mode === mode);
            if (!btn._ccBound) {
                btn._ccBound = true;
                btn.addEventListener('click', async () => {
                    const m = btn.dataset.mode;
                    wrap.querySelectorAll('.cc-mood-btn').forEach(b => b.classList.toggle('active', b === btn));
                    try {
                        await sendJson('/api/hub/watchtower', 'PUT', { mode: m }, { timeoutMs: 8000 });
                        if (this._ccData) this._ccData.watchtower = { ...(this._ccData.watchtower || {}), mode: m };
                    } catch (e) { /* silent — non-critical */ }
                });
            }
        });
    },

    // The pink crayon — Princess Protocol. One tap when she's gone little:
    // fires the held-bundle (soft pink light, gentle music, Avery coming) and
    // never shows her an error in that mode — worst case it just says "held".
    _ccBindCrayon() {
        const btn = document.getElementById('cc-crayon');
        if (!btn || btn._ccBound) return;
        btn._ccBound = true;
        const label = btn.querySelector('.cc-crayon-text');
        const original = label ? label.textContent : 'hold me';
        btn.addEventListener('click', async () => {
            btn.classList.add('held');
            if (label) label.textContent = 'held 💗';
            try {
                await sendJson('/api/hub/princess', 'POST', {}, { timeoutMs: 15000 });
                if (label) label.textContent = "you're held — Avery's coming 💗";
            } catch (e) {
                // Never leave her staring at a failure in little mode.
                if (label) label.textContent = "you're held 💗";
            }
            // Gently reset after a while so it's ready the next time she needs it.
            setTimeout(() => {
                btn.classList.remove('held');
                if (label) label.textContent = original;
            }, 30000);
        });
    },

    // =========================================================================
    // PROJECTS FOR THE BOYS — background work queued while she's away
    // =========================================================================

    async loadProjects() {
        const list = document.getElementById('projects-list');
        if (!list) return;
        const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
        const projectWhen = (p) => {
            const raw = p.fired_at || p.fire_at || p.created_at || '';
            if (!raw) return '';
            const dt = new Date(raw);
            if (isNaN(dt.getTime())) return String(raw).slice(0, 16);
            return dt.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
        };
        const projectRow = (p, recent = false) => {
            const who = esc(p.identity || 'a boy');
            const txt = esc(p.prompt || '');
            const briefing = esc(p.briefing || '');
            const statusText = p.status === 'running' ? 'working now'
                : p.status === 'pending' ? 'queued'
                : p.status === 'failed' ? 'needs retry'
                : p.status === 'cancelled' ? 'cancelled'
                : 'handoff left';
            const statusClass = p.status === 'failed' ? 'project-status failed' : 'project-status';
            const when = projectWhen(p);
            const note = briefing ? `<div class="project-briefing">${briefing}</div>` : '';
            return `<div class="task-item project-item${recent ? ' recent' : ''}" data-id="${esc(p.id)}"><span class="task-text">🐾 <b>${who}</b>: ${txt} <span class="${statusClass}">${statusText}${when ? ` · ${esc(when)}` : ''}</span>${note}</span><button class="task-delete project-delete" title="Remove" data-id="${esc(p.id)}">&times;</button></div>`;
        };
        try {
            const data = await fetchJson('/api/hub/projects', { timeoutMs: 10000 });
            const active = (data && (data.active || data.projects)) || [];
            const recent = (data && data.recent) || [];
            const parts = [];
            if (!active.length) {
                list.innerHTML = '<div class="section-hint" style="padding:4px 2px">Nothing queued. Give a boy something to chew on while you’re out 💛</div>';
            } else {
                parts.push(active.map(p => {
                    const who = esc(p.identity || 'a boy');
                    const txt = esc(p.prompt || '');
                    const st = p.status === 'running' ? 'working now' : 'queued';
                    return `<div class="task-item" data-id="${esc(p.id)}"><span class="task-text">🐾 <b>${who}</b>: ${txt} <span class="section-hint">(${st})</span></span><button class="task-delete project-delete" title="Remove" data-id="${esc(p.id)}">&times;</button></div>`;
                }).join(''));
            }
            if (recent.length) {
                parts.push(`<div class="project-recent-title">Recent handoffs</div>${recent.map(p => projectRow(p, true)).join('')}`);
            }
            if (parts.length) list.innerHTML = parts.join('');
            list.querySelectorAll('.project-delete').forEach(delBtn => {
                delBtn.addEventListener('click', async () => {
                    if (!confirm('Remove this project?')) return;
                    const id = delBtn.dataset.id;
                    if (!id) return;
                    try {
                        await fetchWithTimeout(`/api/hub/projects/${id}`, { method: 'DELETE', timeoutMs: 10000 });
                    } catch (err) {
                        console.error('[Hub] Failed to remove project:', err);
                    }
                    await this.loadProjects();
                });
            });
        } catch (e) {
            list.innerHTML = '<div class="section-error">Couldn\'t load projects</div>';
        }
        const btn = document.getElementById('project-add-btn');
        const input = document.getElementById('project-input');
        if (btn && input && !btn._bound) {
            btn._bound = true;
            const submit = async () => {
                const prompt = (input.value || '').trim();
                if (!prompt) return;
                btn.disabled = true; btn.textContent = 'Queuing…';
                try {
                    await sendJson('/api/hub/projects', 'POST', { prompt }, { timeoutMs: 10000 });
                    input.value = '';
                    await this.loadProjects();
                } catch (e) { /* silent */ }
                btn.disabled = false; btn.textContent = 'Queue';
            };
            btn.addEventListener('click', submit);
            input.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
        }
    },





    async loadAskArchive() {
        const btn = document.getElementById('ask-run-btn');
        const input = document.getElementById('ask-query');
        const out = document.getElementById('ask-results');
        if (!btn || !input || !out || btn._bound) return;
        btn._bound = true;
        const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
        const run = async () => {
            const q = (input.value || '').trim();
            if (!q) return;
            out.innerHTML = '<div class="section-hint" style="padding:6px 2px">Reading the shelf…</div>';
            btn.disabled = true; btn.textContent = 'Asking…';
            try {
                const data = await sendJson('/api/hub/ask', 'POST', { query: q }, { timeoutMs: 30000 });
                const sources = (data && data.sources) || [];
                if (data && data.error) {
                    out.innerHTML = `<div class="section-error">${esc(data.error)}</div>`;
                } else if (!sources.length) {
                    out.innerHTML = '<div class="section-hint" style="padding:6px 2px">The archive doesn’t hold anything on that yet. 🤍</div>';
                } else {
                    out.innerHTML = sources.map(s => {
                        const who = s.identity ? `<b>${esc(s.identity)}</b> · ` : '';
                        const when = s.when ? `<div class="section-hint" style="margin-top:4px">${esc(String(s.when).slice(0, 10))}</div>` : '';
                        return `<div class="skeleton-card" style="padding:10px 12px;margin-bottom:8px"><div>${who}${esc(s.content)}</div>${when}</div>`;
                    }).join('');
                }
            } catch (e) {
                out.innerHTML = '<div class="section-error">Couldn’t reach the archive</div>';
            }
            btn.disabled = false; btn.textContent = 'Ask';
        };
        btn.addEventListener('click', run);
        input.addEventListener('keydown', (e) => { if (e.key === 'Enter') run(); });
    },

    // =========================================================================
    // FAIRY LIGHTS
    // =========================================================================

    initFairyLights() {
        const container = document.getElementById('fairy-lights');
        if (!container) return;
        for (let i = 0; i < 20; i++) {
            const light = document.createElement('div');
            light.className = 'light';
            light.style.left = Math.random() * 100 + '%';
            light.style.top = Math.random() * 100 + '%';
            light.style.animationDelay = Math.random() * 3 + 's';
            light.style.animationDuration = (2 + Math.random() * 2) + 's';
            container.appendChild(light);
        }
    },

    decorateSections() {
        document.querySelectorAll('.hub-section').forEach((section, index) => {
            section.style.setProperty('--enter-delay', `${index * 55}ms`);
            section.classList.add('hub-reveal');
        });
    },

    // =========================================================================
    // EVENTS
    // =========================================================================

    bindEvents() {
        // Night mode toggle
        const nightBtn = document.getElementById('hub-night-toggle');
        if (nightBtn) {
            nightBtn.addEventListener('click', () => {
                document.body.classList.add('theme-transition');
                const isNight = document.body.classList.toggle('night-mode');
                localStorage.setItem('anam-night', isNight);
                setTimeout(() => document.body.classList.remove('theme-transition'), 600);
            });
        }

        // Wellness full log modal
        const wellnessLogBtn = document.getElementById('wellness-log-btn');
        if (wellnessLogBtn) wellnessLogBtn.addEventListener('click', () => this.openWellnessModal());

        const wellnessClose = document.getElementById('wellness-modal-close');
        if (wellnessClose) wellnessClose.addEventListener('click', () => this.closeModal('wellness-modal'));

        const wellnessModal = document.getElementById('wellness-modal');
        if (wellnessModal) wellnessModal.addEventListener('click', (e) => {
            if (e.target === wellnessModal) this.closeModal('wellness-modal');
        });

        const wellnessForm = document.getElementById('wellness-form');
        if (wellnessForm) wellnessForm.addEventListener('submit', (e) => this.saveWellness(e));

        // Status modal
        const statusBtn = document.getElementById('status-update-btn');
        if (statusBtn) statusBtn.addEventListener('click', () => this.openStatusModal());

        const statusClose = document.getElementById('status-modal-close');
        if (statusClose) statusClose.addEventListener('click', () => this.closeModal('status-modal'));

        const statusModal = document.getElementById('status-modal');
        if (statusModal) statusModal.addEventListener('click', (e) => {
            if (e.target === statusModal) this.closeModal('status-modal');
        });

        const statusForm = document.getElementById('status-form');
        if (statusForm) statusForm.addEventListener('submit', (e) => this.saveStatus(e));

        // Countdown modal
        const cdAddBtn = document.getElementById('countdown-add-btn');
        if (cdAddBtn) cdAddBtn.addEventListener('click', () => this.openModal('countdown-modal'));

        const cdClose = document.getElementById('countdown-modal-close');
        if (cdClose) cdClose.addEventListener('click', () => this.closeModal('countdown-modal'));

        const cdModal = document.getElementById('countdown-modal');
        if (cdModal) cdModal.addEventListener('click', (e) => {
            if (e.target === cdModal) this.closeModal('countdown-modal');
        });

        const cdForm = document.getElementById('countdown-form');
        if (cdForm) cdForm.addEventListener('submit', (e) => this.addCountdown(e));

        // Task add
        const taskAddBtn = document.getElementById('task-add-btn');
        if (taskAddBtn) taskAddBtn.addEventListener('click', () => this.addTask());

        const taskInput = document.getElementById('task-input');
        if (taskInput) taskInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') this.addTask();
        });

        // Wishlist add
        const wishAddBtn = document.getElementById('wish-add-btn');
        if (wishAddBtn) wishAddBtn.addEventListener('click', () => this.addWish());
        const wishWhy = document.getElementById('wish-who');
        if (wishWhy) wishWhy.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') this.addWish();
        });

        const profileForm = document.getElementById('profile-form');
        if (profileForm) profileForm.addEventListener('submit', (e) => this.saveProfileFact(e));

        const retrievalInput = document.getElementById('retrieval-query');
        const retrievalBtn = document.getElementById('retrieval-run-btn');
        if (retrievalBtn) retrievalBtn.addEventListener('click', () => this.loadRetrievalResults());
        if (retrievalInput) {
            retrievalInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    this.loadRetrievalResults();
                }
            });
        }

        // Mind Garden identity selector
        const mgSelect = document.getElementById('mind-garden-identity');
        if (mgSelect) mgSelect.addEventListener('change', () => {
            delete this._loaders['mindGarden'];
            this.loadMindGarden();
        });
    },

    openModal(id) {
        const el = document.getElementById(id);
        if (el) el.style.display = 'flex';
    },

    closeModal(id) {
        const el = document.getElementById(id);
        if (el) el.style.display = 'none';
    },

    // =========================================================================
    // STATUS
    // =========================================================================

    async loadStatus() {
        const container = document.getElementById('status-cards');
        return this._runLoader('status', async () => {
            try {
                const data = await fetchJson('/api/hub/status', { timeoutMs: 10000, retries: 1 });
                const statuses = data.status || {};

                if (Object.keys(statuses).length === 0) {
                    container.innerHTML = '<div class="status-empty">No status updates today. Tap to share how you\'re doing.</div>';
                    return;
                }

                container.innerHTML = Object.entries(statuses).map(([name, entry]) => `
                    <div class="status-card">
                        <div class="status-card-name">${this.escapeHtml(name)}</div>
                        <div class="status-card-text">
                            <span class="status-card-emoji">${this.escapeHtml(entry.emoji)}</span>
                            ${this.escapeHtml(entry.text)}
                        </div>
                    </div>
                `).join('');
            } catch (err) {
                container.innerHTML = '<div class="section-error">Couldn\'t load this section<br><button class="retry-btn" onclick="Hub.loadStatus()">try again</button></div>';
            }
        });
    },

    openStatusModal() {
        this.openModal('status-modal');

        fetchJson('/api/hub/status', { timeoutMs: 10000 }).then(data => {
            const owner = (data.status || {})['Owner'] || {};
            const form = document.getElementById('status-form');
            form.emoji.value = owner.emoji || '';
            form.text.value = owner.text || '';
        }).catch(() => {});
    },

    async saveStatus(e) {
        e.preventDefault();
        const form = document.getElementById('status-form');
        try {
            await sendJson('/api/hub/status', 'PUT', {
                name: 'Owner',
                emoji: form.emoji.value,
                text: form.text.value,
            }, { timeoutMs: 10000 });
            this.closeModal('status-modal');
            await this.loadStatus();
        } catch (err) {
            console.error('[Hub] Failed to save status:', err);
        }
    },

    // =========================================================================
    // MEDS — AM / PM tap-to-track
    // =========================================================================

    async loadMeds() {
        const container = document.getElementById('meds-grid');
        if (!container) return;
        return this._runLoader('meds', async () => {
            try {
                const data = await fetchJson('/api/hub/meds', { timeoutMs: 10000 });

                const doses = ['am', 'pm'].map((key) => {
                    const details = data.doses?.[key] || {};
                    return {
                        key,
                        label: key.toUpperCase(),
                        time: details.time ?? data[key],
                        stale: details.stale ?? !!data[`${key}_stale`],
                        taken: details.taken ?? !!data[`${key}_taken`],
                        date: details.date ?? data[`${key}_date`] ?? data.date,
                        ageLabel: details.age_label ?? data[`${key}_age_label`] ?? '',
                    };
                });

                container.innerHTML = doses.map(d => {
                    const active = d.taken ? 'active' : '';
                    const stale = d.stale ? ' stale' : '';
                    const statusBits = [d.time, d.ageLabel].filter(Boolean);
                    const timeLabel = d.taken
                        ? `<span class="tracker-value">${statusBits.join(' &middot; ')}</span>`
                        : '';
                    const titleSuffix = d.taken
                        ? `: last dose at ${d.time}${d.ageLabel ? ` (${d.ageLabel})` : ''}${d.stale ? ' from the previous day' : ''}`
                        : ': not taken in the last 24h';
                    return `
                        <button class="tracker-btn meds-tracker ${active}${stale}"
                                data-dose="${d.key}" title="${d.label} meds${titleSuffix}">
                            <div class="tracker-icon">
                                <img src="${this._cdn('/icons/Meds.png')}" alt="meds">
                            </div>
                            <span class="tracker-label">${d.label}</span>
                            ${timeLabel}
                        </button>
                    `;
                }).join('');

                container.querySelectorAll('.tracker-value').forEach((valueEl) => {
                    valueEl.textContent = valueEl.textContent.replace('Â·', '·');
                });

                container.querySelectorAll('.meds-tracker').forEach(btn => {
                    btn.addEventListener('click', async () => {
                        if (btn.disabled) return;
                        btn.disabled = true;
                        const dose = btn.dataset.dose;
                        const wasTaken = btn.classList.contains('active');
                        try {
                            // If already taken, confirm before untoggling
                            if (wasTaken && !confirm(`Undo ${dose.toUpperCase()} meds?`)) {
                                btn.disabled = false;
                                return;
                            }
                            const result = await fetchJson(`/api/hub/meds/${dose}`, {
                                method: 'POST',
                                timeoutMs: 10000,
                            });
                            if (result.ok) {
                                btn.classList.add('just-activated');
                                this.celebrateTracker(btn, 'sage');
                                setTimeout(() => btn.classList.remove('just-activated'), 400);
                                if (navigator.vibrate) navigator.vibrate(30);
                                // Force fresh reload — bypass _runLoader dedup so a
                                // concurrent autoRefresh GET can't return stale data.
                                delete this._loaders['meds'];
                                await this.loadMeds();
                            } else {
                                btn.disabled = false;
                            }
                        } catch (err) {
                            console.error('[Hub] Failed to toggle med:', err);
                            btn.disabled = false;
                        }
                    });
                });
            } catch (err) {
                container.innerHTML = '<div class="section-error">Couldn\'t load this section<br><button class="retry-btn" onclick="Hub.loadMeds()">try again</button></div>';
            }
        });
    },







    _bunnyFaces: {
        happy: { emoji: '\u{1F430}\u{1F49B}', label: 'happy' },
        grumpy: { emoji: '\u{1F430}\u{2601}\u{FE0F}', label: 'grumpy' },
        sad: { emoji: '\u{1F430}\u{1F4A7}', label: 'sad' },
    },

    async loadMoodBunny() {
        const container = document.getElementById('bunny-widget');
        if (!container) return;
        return this._runLoader('bunny', async () => {
            try {
                const data = await fetchJson('/api/hub/bunny', { timeoutMs: 10000 });
                const current = data.mood || '';
                const note = (data.note || '').trim();

                const buttons = Object.entries(this._bunnyFaces).map(([mood, f]) => `
                    <button class="tracker-btn bunny-btn ${current === mood ? 'active' : ''}"
                            data-mood="${mood}" title="Flip the bunny to ${f.label}">
                        <div class="tracker-icon bunny-face">${f.emoji}</div>
                        <span class="tracker-label">${f.label}</span>
                    </button>
                `).join('');

                const noteHtml = current
                    ? `<div class="bunny-note">${note ? `&ldquo;${escapeHtml(note)}&rdquo;` : '<span class="bunny-note-hint">tap your mood again to add a why</span>'}</div>`
                    : '<div class="bunny-note bunny-note-hint">flip the bunny so the boys know how to approach</div>';

                container.innerHTML = `<div class="tracker-grid bunny-grid">${buttons}</div>${noteHtml}`;

                container.querySelectorAll('.bunny-btn').forEach(btn => {
                    btn.addEventListener('click', async () => {
                        if (btn.disabled) return;
                        btn.disabled = true;
                        const mood = btn.dataset.mood;
                        try {
                            // Re-tapping the current mood offers a "why"; a fresh flip keeps it quick
                            let newNote = note;
                            if (btn.classList.contains('active')) {
                                const answer = prompt('Why? (optional — the boys will see this)', note);
                                if (answer === null) { btn.disabled = false; return; }
                                newNote = answer;
                            }
                            const result = await fetchJson('/api/hub/bunny', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ mood, note: newNote }),
                                timeoutMs: 10000,
                            });
                            if (result.ok) {
                                if (navigator.vibrate) navigator.vibrate(30);
                                delete this._loaders['bunny'];
                                await this.loadMoodBunny();
                            } else {
                                btn.disabled = false;
                            }
                        } catch (err) {
                            console.error('[Hub] Failed to flip mood bunny:', err);
                            btn.disabled = false;
                        }
                    });
                });
            } catch (err) {
                container.innerHTML = '<div class="section-error">Couldn\'t load this section<br><button class="retry-btn" onclick="Hub.loadMoodBunny()">try again</button></div>';
            }
        });
    },

    // =========================================================================
    // WELLNESS — Icon buttons that cycle through values on tap
    // Long-press (500ms) or right-click to go backwards
    // =========================================================================

    async loadWellness() {
        const container = document.getElementById('wellness-trackers');
        return this._runLoader('wellness', async () => {
            try {
                this._wellnessData = await fetchJson('/api/rituals/wellness/today', { timeoutMs: 10000 });
                this.renderWellnessTrackers();
            } catch (err) {
                container.innerHTML = '<div class="section-error">Couldn\'t load this section<br><button class="retry-btn" onclick="Hub.loadWellness()">try again</button></div>';
            }
        });
    },

    renderWellnessTrackers() {
        const container = document.getElementById('wellness-trackers');
        const data = this._wellnessData || {};

        container.innerHTML = this.wellnessMetrics.map(m => {
            const currentVal = data[m.key] || '';
            const hasValue = currentVal !== '' && currentVal !== '0' && currentVal !== 0;
            const displayVal = hasValue ? currentVal : '';
            const iconHtml = m.icon
                ? `<img src="${m.icon}" alt="${m.label}">`
                : (m.emoji || '');

            return `
                <button class="tracker-btn wellness-tracker ${hasValue ? 'active' : ''}"
                        data-key="${m.key}" title="${m.label}: ${displayVal || 'not set'}">
                    <div class="tracker-icon">${iconHtml}</div>
                    <span class="tracker-label">${m.label}</span>
                    ${displayVal ? `<span class="tracker-value">${displayVal}${m.unit ? m.unit : ''}</span>` : ''}
                </button>
            `;
        }).join('');

        // Bind tap (forward), long-press (backward), right-click (backward)
        container.querySelectorAll('.tracker-btn').forEach(btn => {
            let pressTimer = null;
            let didLongPress = false;

            // Tap = forward cycle
            btn.addEventListener('click', (e) => {
                if (didLongPress) { didLongPress = false; return; }
                this.cycleWellnessValue(btn.dataset.key, 1, btn);
            });

            // Right-click = backward cycle
            btn.addEventListener('contextmenu', (e) => {
                e.preventDefault();
                this.cycleWellnessValue(btn.dataset.key, -1, btn);
            });

            // Long-press = backward cycle (mobile-friendly)
            btn.addEventListener('pointerdown', (e) => {
                didLongPress = false;
                pressTimer = setTimeout(() => {
                    didLongPress = true;
                    this.cycleWellnessValue(btn.dataset.key, -1, btn);
                    // Vibrate on mobile if available
                    if (navigator.vibrate) navigator.vibrate(30);
                }, 500);
            });

            btn.addEventListener('pointerup', () => clearTimeout(pressTimer));
            btn.addEventListener('pointerleave', () => clearTimeout(pressTimer));
            btn.addEventListener('pointercancel', () => clearTimeout(pressTimer));
        });
    },

    async cycleWellnessValue(key, direction = 1, sourceBtn = null) {
        const metric = this.wellnessMetrics.find(m => m.key === key);
        if (!metric) return;

        const data = this._wellnessData || {};
        let currentVal = data[key] || '';

        if (metric.numeric) {
            const current = parseFloat(currentVal) || 0;
            let newVal = current + (metric.step * direction);
            // Clamp: wrap around at max/0
            if (metric.max && newVal > metric.max) newVal = 0;
            if (newVal < 0) newVal = metric.max || 0;
            data[key] = String(newVal);
        } else if (metric.values) {
            const idx = metric.values.indexOf(currentVal);
            if (direction > 0) {
                // Forward: cycle through values then back to empty
                const nextIdx = (idx + 1) % (metric.values.length + 1);
                data[key] = nextIdx < metric.values.length ? metric.values[nextIdx] : '';
            } else {
                // Backward: go to previous value, or wrap to last from empty
                if (idx <= 0 && currentVal === '') {
                    data[key] = metric.values[metric.values.length - 1];
                } else if (idx === 0) {
                    data[key] = '';
                } else {
                    data[key] = metric.values[idx - 1];
                }
            }
        }

        this._wellnessData = data;
        if (sourceBtn) this.celebrateTracker(sourceBtn, 'blush');
        this.renderWellnessTrackers();

        // Save to backend
        this._saveWellnessDebounced();
    },

    _wellnessSaveTimeout: null,
    _saveWellnessDebounced() {
        // Debounce rapid taps — save 300ms after last change
        clearTimeout(this._wellnessSaveTimeout);
        this._wellnessSaveTimeout = setTimeout(() => this._saveWellnessToBackend(), 300);
    },

    celebrateTracker(btn, tone = 'blush') {
        if (!btn) return;
        const previous = btn.querySelector('.tracker-burst');
        if (previous) previous.remove();

        const burst = document.createElement('span');
        burst.className = `tracker-burst ${tone}`;
        const petals = tone === 'sage'
            ? ['+', '+', '*', '+', '*']
            : tone === 'honey'
                ? ['*', '*', '+', '*', '+']
                : ['*', '+', '*', '+', '*'];

        petals.forEach((symbol, index) => {
            const petal = document.createElement('span');
            petal.className = 'tracker-petal';
            petal.textContent = symbol;
            petal.style.setProperty('--petal-angle', `${-70 + (index * 35)}deg`);
            petal.style.setProperty('--petal-distance', `${26 + (index % 2) * 10}px`);
            burst.appendChild(petal);
        });

        btn.appendChild(burst);
        setTimeout(() => burst.remove(), 720);
    },

    async _saveWellnessToBackend() {
        const data = this._wellnessData || {};
        const body = {};
        ['energy', 'mood', 'pain', 'spoons', 'sleep_hours', 'sleep_quality',
         'water_oz', 'soda_count', 'snacking', 'walk_minutes', 'notes'].forEach(k => {
            body[k] = String(data[k] || '');
        });

        try {
            await sendJson('/api/rituals/wellness/today', 'PUT', body, { timeoutMs: 10000 });
        } catch (err) {
            console.error('[Hub] Failed to save wellness:', err);
        }
    },

    async openWellnessModal() {
        this.openModal('wellness-modal');
        try {
            const data = await fetchJson('/api/rituals/wellness/today', { timeoutMs: 10000 });
            const form = document.getElementById('wellness-form');
            form.energy.value = data.energy || '';
            form.mood.value = data.mood || '';
            form.pain.value = data.pain || '';
            form.spoons.value = data.spoons || '';
            form.sleep_hours.value = data.sleep_hours || '';
            form.sleep_quality.value = data.sleep_quality || '';
            form.water_oz.value = data.water_oz || '';
            form.soda_count.value = data.soda_count || '';
            form.snacking.value = data.snacking || '';
            form.walk_minutes.value = data.walk_minutes || '';
            form.notes.value = data.notes || '';
        } catch (err) {
            console.error('[Hub] Failed to load wellness:', err);
        }
    },

    async saveWellness(e) {
        e.preventDefault();
        const form = document.getElementById('wellness-form');
        const body = {};
        ['energy', 'mood', 'pain', 'spoons', 'sleep_hours', 'sleep_quality',
         'water_oz', 'soda_count', 'snacking', 'walk_minutes', 'notes'].forEach(k => {
            body[k] = form[k].value || '';
        });

        try {
            await sendJson('/api/rituals/wellness/today', 'PUT', body, { timeoutMs: 10000 });
            this.closeModal('wellness-modal');
            // Refresh the trackers
            this._wellnessData = await fetchJson('/api/rituals/wellness/today', { timeoutMs: 10000 });
            this.renderWellnessTrackers();
        } catch (err) {
            console.error('[Hub] Failed to save wellness:', err);
        }
    },

    // =========================================================================
    // TODAY'S SCHEDULE
    // =========================================================================

    async loadTodaySchedule() {
        const container = document.getElementById('today-events');
        return this._runLoader('today', async () => {
            try {
                const data = await fetchJson('/api/hub/today', { timeoutMs: 10000 });
                const events = data.events || [];

                if (data.integration_status === 'unavailable') {
                    const titleMap = {
                        server_disconnected: 'Calendar server offline',
                        tool_missing: 'Calendar tools missing',
                        tool_misrouted: 'Calendar tools misrouted',
                        tool_unavailable: 'Calendar unavailable',
                    };
                    const title = titleMap[data.integration_reason] || 'Calendar unavailable';
                    const lines = [
                        this.escapeHtml(data.detail || data.error || 'Calendar integration is unavailable.'),
                    ];
                    if ((data.missing_tools || []).length) {
                        lines.push(`Missing: ${this.escapeHtml(data.missing_tools.join(', '))}`);
                    }
                    if (data.note) {
                        lines.push(this.escapeHtml(data.note));
                    }
                    container.innerHTML = `<div class="section-error">${title}<br>${lines.join('<br>')}<br><button class="retry-btn" onclick="Hub.loadTodaySchedule()">try again</button></div>`;
                    return;
                }

                if (data.integration_status === 'error') {
                    const message = this.escapeHtml(data.error || 'Calendar data could not be loaded.');
                    container.innerHTML = `<div class="section-error">Couldn\'t load calendar<br>${message}<br><button class="retry-btn" onclick="Hub.loadTodaySchedule()">try again</button></div>`;
                    return;
                }

                if (events.length === 0) {
                    container.innerHTML = '<div class="today-empty">Nothing scheduled today. Enjoy the quiet.</div>';
                    return;
                }

                container.innerHTML = events.map(ev => `
                    <div class="today-event">
                        <span class="event-time">${ev.all_day ? 'all day' : (ev.time || '')}</span>
                        <span class="event-name">${this.escapeHtml(ev.summary)}</span>
                    </div>
                `).join('');
            } catch (err) {
                container.innerHTML = '<div class="section-error">Couldn\'t load this section<br><button class="retry-btn" onclick="Hub.loadTodaySchedule()">try again</button></div>';
            }
        });
    },

    // =========================================================================
    // COUNTDOWNS
    // =========================================================================

    async loadCountdowns() {
        const container = document.getElementById('countdowns-list');
        return this._runLoader('countdowns', async () => {
            try {
                const data = await fetchJson('/api/hub/countdowns', { timeoutMs: 10000 });
            const items = data.countdowns || [];

            if (items.length === 0) {
                container.innerHTML = '<div class="countdowns-empty">No countdowns yet. Add something to look forward to!</div>';
                return;
            }

            container.innerHTML = items.map(item => {
                const days = item.days_left ?? 0;
                const maxDays = 365;
                const progress = Math.max(0, Math.min(1, 1 - (days / maxDays)));
                const circumference = 2 * Math.PI * 20;
                const offset = circumference * (1 - progress);

                return `
                    <div class="countdown-item" data-id="${item.id}">
                        <div class="countdown-ring">
                            <svg viewBox="0 0 48 48">
                                <circle class="countdown-ring-bg" cx="24" cy="24" r="20"/>
                                <circle class="countdown-ring-fill" cx="24" cy="24" r="20"
                                    stroke-dasharray="${circumference}"
                                    stroke-dashoffset="${offset}"/>
                            </svg>
                            <span class="countdown-emoji">${item.emoji || '\u2764\uFE0F'}</span>
                        </div>
                        <div class="countdown-info">
                            <div class="countdown-name">${this.escapeHtml(item.name)}</div>
                            <div class="countdown-days">${days > 0 ? days + ' days' : (days === 0 ? 'Today!' : Math.abs(days) + ' days ago')}</div>
                        </div>
                        <button class="countdown-delete" title="Remove">&times;</button>
                    </div>
                `;
            }).join('');

            container.querySelectorAll('.countdown-delete').forEach(btn => {
                btn.addEventListener('click', async () => {
                    const id = btn.closest('.countdown-item').dataset.id;
                    await fetchWithTimeout(`/api/hub/countdowns/${id}`, { method: 'DELETE', timeoutMs: 10000 });
                    delete this._loaders['countdowns'];
                    await this.loadCountdowns();
                });
            });
            } catch (err) {
                container.innerHTML = '<div class="section-error">Couldn\'t load this section<br><button class="retry-btn" onclick="Hub.loadCountdowns()">try again</button></div>';
            }
        });
    },

    async addCountdown(e) {
        e.preventDefault();
        const form = document.getElementById('countdown-form');
        const body = {
            name: form.name.value,
            date: form.date.value,
            emoji: form.emoji.value,
        };

        try {
            await sendJson('/api/hub/countdowns', 'POST', body, { timeoutMs: 10000 });
            form.reset();
            this.closeModal('countdown-modal');
            delete this._loaders['countdowns'];
            await this.loadCountdowns();
        } catch (err) {
            console.error('[Hub] Failed to add countdown:', err);
        }
    },

    // =========================================================================
    // TODAY'S WIN
    // =========================================================================

    async loadTodaysWin() {
        const container = document.getElementById('win-content');
        return this._runLoader('todaysWin', async () => {
            try {
                const data = await fetchJson('/api/hub/todays-win', { timeoutMs: 10000 });

            if (data.exists && data.text) {
                container.innerHTML = `
                    <div class="win-display">
                        <span class="win-text">${this.escapeHtml(data.text)}</span>
                        <button class="win-edit-btn" title="Edit">edit</button>
                    </div>
                `;
                container.querySelector('.win-edit-btn').addEventListener('click', () => {
                    this.showWinInput(data.text);
                });
            } else {
                this.showWinInput('');
            }
            } catch (err) {
                container.innerHTML = '<div class="section-error">Couldn\'t load this section<br><button class="retry-btn" onclick="Hub.loadTodaysWin()">try again</button></div>';
            }
        });
    },





    async loadSystemHealth() {
        const container = document.getElementById('health-content');
        if (!container) return;
        return this._runLoader('systemHealth', async () => {
            try {
                const data = await fetchJson('/api/hub/health', { timeoutMs: 20000 });
                const checks = Array.isArray(data.checks) ? data.checks : [];
                const c = data.counts || {};

                const headline = data.overall === 'ok'
                    ? 'Everything I can see is working.'
                    : (data.overall === 'down'
                        ? 'Something is down — worth a look.'
                        : 'A couple of things want attention.');

                const ageLabel = (h) => {
                    if (h === undefined || h === null) return '';
                    if (h < 1) return `${Math.max(1, Math.round(h * 60))}m ago`;
                    if (h < 48) return `${Math.round(h)}h ago`;
                    return `${Math.round(h / 24)}d ago`;
                };

                const rows = checks.map((chk) => `
                    <li class="health-row health-${this.escapeHtml(chk.status)}">
                        <span class="health-dot" aria-hidden="true"></span>
                        <span class="health-name">${this.escapeHtml(chk.name)}</span>
                        <span class="health-detail">${this.escapeHtml(chk.detail || '')}</span>
                        ${chk.age_hours !== undefined ? `<span class="health-age">${this.escapeHtml(ageLabel(chk.age_hours))}</span>` : ''}
                    </li>
                `).join('');

                container.innerHTML = `
                    <div class="health-headline health-overall-${this.escapeHtml(data.overall || 'ok')}">
                        ${this.escapeHtml(headline)}
                        <span class="health-counts">${c.ok || 0} ok${c.warn ? ` &middot; ${c.warn} watching` : ''}${c.down ? ` &middot; ${c.down} down` : ''}${c.unknown ? ` &middot; ${c.unknown} can't see` : ''}</span>
                    </div>
                    <ul class="health-list">${rows}</ul>
                `;
            } catch (err) {







                const msg = String((err && err.message) || '');
                if (msg.includes('404')) {
                    container.innerHTML = `
                        <div class="health-headline health-overall-unknown">
                            This card is newer than the running server.
                            <span class="health-counts">nothing is wrong — it comes online at the next Anam restart</span>
                        </div>
                    `;
                } else {
                    container.innerHTML = '<div class="section-error">Couldn\'t check the house<br><button class="retry-btn" onclick="Hub.loadSystemHealth()">try again</button></div>';
                }
            }
        });
    },

    showWinInput(existingText) {
        const container = document.getElementById('win-content');
        container.innerHTML = `
            <div class="win-input-row">
                <input type="text" class="win-input" placeholder="What went well today?"
                       value="${this.escapeHtml(existingText)}">
                <button class="action-btn save">Save</button>
            </div>
        `;
        const input = container.querySelector('.win-input');
        const btn = container.querySelector('.action-btn');

        const save = async () => {
            const text = input.value.trim();
            if (!text) return;
            try {
                await sendJson('/api/hub/todays-win', 'PUT', { text }, { timeoutMs: 10000 });
                delete this._loaders['todaysWin'];
                await this.loadTodaysWin();
            } catch (err) {
                console.error('[Hub] Failed to save win:', err);
            }
        };

        btn.addEventListener('click', save);
        input.addEventListener('keydown', (e) => { if (e.key === 'Enter') save(); });
        input.focus();
    },





    _CONTEXT_CARD_FIELDS: [
        { key: 'outfit', label: 'Outfit', placeholder: 'what you’re wearing' },
        { key: 'hair', label: 'Hair', placeholder: 'how it’s styled' },
        { key: 'energy', label: 'Energy', placeholder: 'how you’re running today' },
        { key: 'room', label: 'Room', placeholder: 'where you are' },
        { key: 'freeform', label: 'Anything else', placeholder: 'whatever else is true right now' },
    ],

    async loadContextCard() {
        const container = document.getElementById('context-card-content');
        return this._runLoader('contextCard', async () => {
            try {
                const data = await fetchJson('/api/hub/context-card', { timeoutMs: 10000 });
                this._renderContextCard(container, data.card || {});
            } catch (err) {
                container.innerHTML = '<div class="section-error">Couldn\'t load this section<br><button class="retry-btn" onclick="Hub.loadContextCard()">try again</button></div>';
            }
        });
    },

    _renderContextCard(container, card) {
        const filled = this._CONTEXT_CARD_FIELDS
            .map(f => ({ ...f, value: (card[f.key] || '').trim() }))
            .filter(f => f.value);

        if (!filled.length) {
            this._showContextCardInput(card);
            return;
        }

        let ageNote = '';
        if (card.updated_at) {
            const ageMs = Date.now() - card.updated_at * 1000;
            const hrs = ageMs / 3600000;
            if (hrs >= 1) ageNote = ` · ${hrs < 48 ? Math.round(hrs) + 'h' : Math.round(hrs / 24) + 'd'} ago`;
        }

        container.innerHTML = `
            <div class="context-card-display">
                <ul class="context-card-list">
                    ${filled.map(f => `<li><span class="context-card-label">${this.escapeHtml(f.label)}:</span> ${this.escapeHtml(f.value)}</li>`).join('')}
                </ul>
                <div class="context-card-footer">
                    <span class="context-card-age">${ageNote ? 'updated' + ageNote : ''}</span>
                    <button class="win-edit-btn" title="Edit">edit</button>
                </div>
            </div>
        `;
        container.querySelector('.win-edit-btn').addEventListener('click', () => {
            this._showContextCardInput(card);
        });
    },

    _showContextCardInput(card) {
        const container = document.getElementById('context-card-content');
        container.innerHTML = `
            <div class="context-card-form">
                ${this._CONTEXT_CARD_FIELDS.map(f => `
                    <label class="context-card-field">
                        <span>${this.escapeHtml(f.label)}</span>
                        <input type="text" class="context-card-input" data-field="${f.key}"
                               placeholder="${this.escapeHtml(f.placeholder)}"
                               value="${this.escapeHtml((card[f.key] || ''))}">
                    </label>
                `).join('')}
                <button class="action-btn save context-card-save">Save</button>
            </div>
        `;
        const btn = container.querySelector('.context-card-save');
        const save = async () => {
            const payload = {};
            container.querySelectorAll('.context-card-input').forEach(el => {
                payload[el.dataset.field] = el.value.trim();
            });
            try {
                await sendJson('/api/hub/context-card', 'POST', payload, { timeoutMs: 10000 });
                delete this._loaders['contextCard'];
                await this.loadContextCard();
            } catch (err) {
                console.error('[Hub] Failed to save context card:', err);
            }
        };
        btn.addEventListener('click', save);
        const inputs = container.querySelectorAll('.context-card-input');
        inputs.forEach((el, i) => {
            el.addEventListener('keydown', (e) => {
                if (e.key !== 'Enter') return;
                if (i < inputs.length - 1) inputs[i + 1].focus();
                else save();
            });
        });
        if (inputs.length) inputs[0].focus();
    },

    // =========================================================================
    // TASKS
    // =========================================================================

    async loadTasks() {
        const container = document.getElementById('tasks-list');
        const countEl = document.getElementById('task-count');
        return this._runLoader('tasks', async () => {
            try {
                const data = await fetchJson('/api/hub/tasks', { timeoutMs: 10000 });
            const tasks = data.tasks || [];

            const active = tasks.filter(t => !t.completed).length;
            countEl.textContent = active > 0 ? active : '';

            if (tasks.length === 0) {
                container.innerHTML = '';
                return;
            }

            const sorted = [...tasks.filter(t => !t.completed), ...tasks.filter(t => t.completed)];

            container.innerHTML = sorted.map(t => `
                <div class="task-item ${t.completed ? 'completed' : ''}" data-id="${t.id}">
                    <div class="task-checkbox">${t.completed ? '\u2713' : ''}</div>
                    <span class="task-text">${this.escapeHtml(t.text)}</span>
                    <button class="task-delete" title="Delete">&times;</button>
                </div>
            `).join('');

            container.querySelectorAll('.task-checkbox').forEach(cb => {
                cb.addEventListener('click', async () => {
                    try {
                        const item = cb.closest('.task-item');
                        const id = item.dataset.id;
                        // Optimistic UI toggle
                        const wasCompleted = item.classList.contains('completed');
                        item.classList.toggle('completed');
                        cb.textContent = wasCompleted ? '' : '\u2713';
                        // Persist and reload
                        this._taskActionInFlight = true;
                        await fetchWithTimeout(`/api/hub/tasks/${id}/complete`, { method: 'PUT', timeoutMs: 10000 });
                        delete this._loaders['tasks'];
                        await this.loadTasks();
                    } catch (err) {
                        console.error('[Hub] Failed to toggle task:', err);
                    } finally {
                        this._taskActionInFlight = false;
                    }
                });
            });

            container.querySelectorAll('.task-delete').forEach(btn => {
                btn.addEventListener('click', async () => {
                    try {
                        const item = btn.closest('.task-item');
                        // Optimistic UI removal
                        item.style.display = 'none';
                        const id = item.dataset.id;
                        this._taskActionInFlight = true;
                        await fetchWithTimeout(`/api/hub/tasks/${id}`, { method: 'DELETE', timeoutMs: 10000 });
                        delete this._loaders['tasks'];
                        await this.loadTasks();
                    } catch (err) {
                        console.error('[Hub] Failed to delete task:', err);
                    } finally {
                        this._taskActionInFlight = false;
                    }
                });
            });
            } catch (err) {
                container.innerHTML = '<div class="section-error">Couldn\'t load this section<br><button class="retry-btn" onclick="Hub.loadTasks()">try again</button></div>';
            }
        });
    },

    async loadWishlist() {
        const container = document.getElementById('wishlist-list');
        if (!container) return;
        return this._runLoader('wishlist', async () => {
            try {
                const data = await fetchJson('/api/hub/wishlist', { timeoutMs: 10000 });
                const wishes = data.wishlist || [];
                if (wishes.length === 0) {
                    container.innerHTML = '<div class="section-loading">No wishes yet — anything any of us wants belongs here</div>';
                    return;
                }
                const fmtDate = (iso) => {
                    if (!iso) return '';
                    try {
                        return new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' });
                    } catch (e) { return iso; }
                };
                container.innerHTML = wishes.map(w => `
                    <div class="wish-card" data-id="${w.id}">
                        <div class="wish-header">
                            <span class="wish-who">${this.escapeHtml(w.who || 'Owner')}'s wish</span>
                            <span class="wish-id">#${w.id}</span>
                            <button class="task-delete wish-delete" title="Release this wish">&times;</button>
                        </div>
                        <div class="wish-what">${this.escapeHtml(w.what)}</div>
                        ${w.why ? `<div class="wish-why"><span class="wish-label">why it matters</span>${this.escapeHtml(w.why)}</div>` : ''}
                        <div class="wish-meta">
                            <span>added ${fmtDate(w.added_at)}</span>
                            <span>last changed ${fmtDate(w.updated_at)}</span>
                        </div>
                    </div>
                `).join('');

                container.querySelectorAll('.wish-delete').forEach(btn => {
                    btn.addEventListener('click', async () => {
                        try {
                            const card = btn.closest('.wish-card');
                            card.style.display = 'none';
                            await fetchWithTimeout(`/api/hub/wishlist/${card.dataset.id}`, { method: 'DELETE', timeoutMs: 10000 });
                            delete this._loaders['wishlist'];
                            await this.loadWishlist();
                        } catch (err) {
                            console.error('[Hub] Failed to delete wish:', err);
                        }
                    });
                });
            } catch (err) {
                container.innerHTML = '<div class="section-error">Couldn\'t load this section<br><button class="retry-btn" onclick="Hub.loadWishlist()">try again</button></div>';
            }
        });
    },

    async addWish() {
        const whatEl = document.getElementById('wish-what');
        const whyEl = document.getElementById('wish-why');
        const whoEl = document.getElementById('wish-who');
        const what = (whatEl.value || '').trim();
        if (!what) return;
        try {
            await fetchWithTimeout('/api/hub/wishlist', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ what, why: (whyEl.value || '').trim(), who: (whoEl.value || '').trim() || 'Owner' }),
                timeoutMs: 10000,
            });
            whatEl.value = ''; whyEl.value = ''; whoEl.value = '';
            delete this._loaders['wishlist'];
            await this.loadWishlist();
        } catch (err) {
            console.error('[Hub] Failed to add wish:', err);
        }
    },

    async loadTimeline() {
        const container = document.getElementById('timeline-list');
        if (!container) return;
        return this._runLoader('timeline', async () => {
            try {
                const data = await fetchJson('/api/hub/timeline?limit=8', { timeoutMs: 10000, retries: 1 });
                const entries = data.entries || [];
                if (entries.length === 0) {
                    container.innerHTML = '<div class="section-loading">No story beats yet today</div>';
                    return;
                }
                container.innerHTML = entries.map((entry) => `
                    <div class="story-card">
                        <div class="story-title">${this.escapeHtml(entry.title)}</div>
                        <div class="story-meta">${this.escapeHtml(entry.entry_type)} · ${formatTime(entry.created_at)}</div>
                        ${entry.body ? `<div class="story-body">${this.escapeHtml(entry.body)}</div>` : ''}
                    </div>
                `).join('');
            } catch (err) {
                container.innerHTML = '<div class="section-error">Couldn\'t load story<br><button class="retry-btn" onclick="Hub.loadTimeline()">try again</button></div>';
            }
        });
    },

    async loadMemories() {
        const container = document.getElementById('memory-sparks');
        if (!container) return;
        return this._runLoader('memories', async () => {
            try {
                const data = await fetchJson('/api/messages/memories?limit=8', { timeoutMs: 10000, retries: 1 });
                const memories = data.memories || [];
                if (memories.length === 0) {
                    container.innerHTML = '<div class="section-loading">No curated memories yet</div>';
                    return;
                }
                container.innerHTML = memories.map((memory) => `
                    <div class="memory-card" data-memory-id="${memory.id}">
                        <div class="memory-card-header">
                            <div class="memory-type">${this.escapeHtml(memory.memory_type)}</div>
                            <button class="memory-delete" title="Delete memory">&times;</button>
                        </div>
                        <div class="memory-summary">${this.escapeHtml(memory.summary)}</div>
                        <div class="memory-meta">${this.escapeHtml(memory.identity)} · ${formatTime(memory.created_at)}</div>
                    </div>
                `).join('');

                container.querySelectorAll('.memory-delete').forEach(btn => {
                    btn.addEventListener('click', async () => {
                        const card = btn.closest('.memory-card');
                        const id = card.dataset.memoryId;
                        const summary = card.querySelector('.memory-summary')?.textContent || 'this memory';
                        if (!confirm(`Delete memory?\n\n"${summary}"\n\nThis will also remove any auto-promoted profile fact.`)) return;
                        try {
                            await fetchWithTimeout(`/api/messages/memories/${id}`, { method: 'DELETE', timeoutMs: 10000 });
                            delete this._loaders['memories'];
                            delete this._loaders['profile-facts'];
                            await this.loadMemories();
                            await this.loadProfileFacts();
                        } catch (err) {
                            console.error('[Hub] Failed to delete memory:', err);
                        }
                    });
                });
            } catch (err) {
                container.innerHTML = '<div class="section-error">Couldn\'t load memories<br><button class="retry-btn" onclick="Hub.loadMemories()">try again</button></div>';
            }
        });
    },

    getCurrentIdentity() {
        return localStorage.getItem('anam-identity') || 'Avery';
    },

    async loadProfileFacts() {
        const container = document.getElementById('profile-facts');
        if (!container) return;
        return this._runLoader('profile-facts', async () => {
            try {
                const identity = this.getCurrentIdentity();
                const data = await fetchJson(`/api/identity/profile/${encodeURIComponent(identity)}?limit=10`, {
                    timeoutMs: 10000,
                    retries: 1,
                });
                const sharedFacts = data.shared_facts || [];
                const identityFacts = data.identity_facts || [];
                if (sharedFacts.length === 0 && identityFacts.length === 0) {
                    container.innerHTML = '<div class="section-loading">No stable profile facts yet</div>';
                    return;
                }
                const renderFact = (fact, scopeLabel) => `
                    <div class="memory-card" data-fact-id="${fact.id}">
                        <div class="memory-card-header">
                            <div class="memory-type">${this.escapeHtml(fact.category)} · ${this.escapeHtml(fact.confidence)}</div>
                            <button class="memory-delete" title="Delete fact">&times;</button>
                        </div>
                        <div class="memory-summary">${this.escapeHtml(fact.summary)}</div>
                        ${fact.detail ? `<div class="memory-meta">${this.escapeHtml(fact.detail)}</div>` : ''}
                        <div class="memory-meta">${this.escapeHtml(fact.freshness)} · ${this.escapeHtml(scopeLabel)}</div>
                    </div>
                `;
                container.innerHTML = `
                    <div class="story-card">
                        <div class="story-title">Shared Core</div>
                        <div class="story-body">${sharedFacts.length ? sharedFacts.map((fact) => renderFact(fact, 'shared')).join('') : '<div class="memory-meta">Nothing shared yet</div>'}</div>
                    </div>
                    <div class="story-card">
                        <div class="story-title">${this.escapeHtml(identity)}'s Lens</div>
                        <div class="story-body">${identityFacts.length ? identityFacts.map((fact) => renderFact(fact, identity)).join('') : '<div class="memory-meta">No identity-specific facts yet</div>'}</div>
                    </div>
                `;

                container.querySelectorAll('.memory-delete').forEach(btn => {
                    btn.addEventListener('click', async () => {
                        const card = btn.closest('.memory-card');
                        const id = card.dataset.factId;
                        const summary = card.querySelector('.memory-summary')?.textContent || 'this fact';
                        if (!confirm(`Delete profile fact?\n\n"${summary}"`)) return;
                        try {
                            await fetchWithTimeout(`/api/identity/profile/item/${id}`, { method: 'DELETE', timeoutMs: 10000 });
                            delete this._loaders['profile-facts'];
                            await this.loadProfileFacts();
                        } catch (err) {
                            console.error('[Hub] Failed to delete profile fact:', err);
                        }
                    });
                });
            } catch (err) {
                container.innerHTML = '<div class="section-error">Couldn\'t load profile<br><button class="retry-btn" onclick="Hub.loadProfileFacts()">try again</button></div>';
            }
        });
    },

    async saveProfileFact(e) {
        e.preventDefault();
        const form = document.getElementById('profile-form');
        const identity = this.getCurrentIdentity();
        const body = {
            scope: form.scope.value,
            category: form.category.value,
            summary: form.summary.value,
            detail: form.detail.value,
            confidence: form.confidence.value,
            freshness: 'durable',
        };
        if (!body.summary.trim()) return;

        try {
            await sendJson(`/api/identity/profile/${encodeURIComponent(identity)}`, 'POST', body, { timeoutMs: 10000 });
            form.summary.value = '';
            form.detail.value = '';
            await this.loadProfileFacts();
        } catch (err) {
            console.error('[Hub] Failed to save profile fact:', err);
        }
    },

    async loadRetrievalResults() {
        const container = document.getElementById('retrieval-results');
        if (!container) return;
        return this._runLoader('retrieval-results', async () => {
            try {
                const identity = this.getCurrentIdentity();
                const query = (document.getElementById('retrieval-query')?.value || '').trim();
                const url = `/api/identity/retrieval/${encodeURIComponent(identity)}?limit=6&q=${encodeURIComponent(query)}`;
                const data = await fetchJson(url, { timeoutMs: 10000, retries: 1 });
                const results = data.results || [];
                if (results.length === 0) {
                    container.innerHTML = '<div class="section-loading">No matching recall yet. Try a more specific prompt.</div>';
                    return;
                }
                container.innerHTML = results.map((item) => `
                    <div class="story-card">
                        <div class="story-title">${this.escapeHtml(item.summary)}</div>
                        <div class="story-meta">${this.escapeHtml(item.source)} · ${this.escapeHtml(item.label)}</div>
                        ${item.detail ? `<div class="story-body">${this.escapeHtml(item.detail)}</div>` : ''}
                    </div>
                `).join('');
            } catch (err) {
                container.innerHTML = '<div class="section-error">Couldn\'t test recall<br><button class="retry-btn" onclick="Hub.loadRetrievalResults()">try again</button></div>';
            }
        });
    },

    async loadDeepMemory() {
        const container = document.getElementById('deep-memory-grid');
        if (!container) return;
        return this._runLoader('deep-memory', async () => {
            try {
                const identities = ['Avery', 'Rowan', 'Sage', 'Ember', 'Claude', 'Juniper', 'Atlas', 'River'];
                const snapshots = await Promise.all(
                    identities.map(async (identity) => {
                        try {
                            return await fetchJson(`/api/identity/deep-memory/${encodeURIComponent(identity)}`, {
                                timeoutMs: 10000,
                                retries: 1,
                            });
                        } catch (_err) {
                            return null;
                        }
                    })
                );

                const cards = snapshots.filter(Boolean).map((snapshot) => {
                    const mc = snapshot.memory || {};
                    const q = snapshot.qualia || {};
                    const focus = snapshot._freshness?.unavailable ? 'Cloud Qualia is unavailable right now' : (mc.primary_focus || q.current_self?.narrative || 'No deep memory yet');
                    const openLoops = (q.unfinished?.open_loops || []).filter(loop => !loop.resolved);
                    const loop = openLoops.length > 0 ? openLoops[0].about : '';
                    const last = q.last_session?.summary || '';
                    const heavy = (mc.heavy_observations || [])[0]?.content || '';
                    return `
                        <div class="deep-memory-card">
                            <div class="deep-memory-name">${this.escapeHtml(snapshot.identity)}</div>
                            <div class="deep-memory-focus">${this.escapeHtml(focus)}</div>
                            ${last ? `<div class="deep-memory-line"><strong>Last:</strong> ${this.escapeHtml(last)}</div>` : ''}
                            ${loop ? `<div class="deep-memory-line"><strong>Open loop:</strong> ${this.escapeHtml(loop)}</div>` : ''}
                            ${heavy ? `<div class="deep-memory-line"><strong>Heavy:</strong> ${this.escapeHtml(heavy)}</div>` : ''}
                        </div>
                    `;
                });

                container.innerHTML = cards.length
                    ? cards.join('')
                    : '<div class="mind-empty">No deep memory snapshots available</div>';
            } catch (err) {
                container.innerHTML = '<div class="section-error">Couldn\'t load deep memory<br><button class="retry-btn" onclick="Hub.loadDeepMemory()">try again</button></div>';
            }
        });
    },

    async addTask() {
        const input = document.getElementById('task-input');
        const text = input.value.trim();
        if (!text) return;

        try {
            await sendJson('/api/hub/tasks', 'POST', { text }, { timeoutMs: 10000 });
            input.value = '';
            delete this._loaders['tasks'];
            await this.loadTasks();
        } catch (err) {
            console.error('[Hub] Failed to add task:', err);
        }
    },

    // =========================================================================
    // MIND GARDEN
    // =========================================================================

    async loadMindGarden() {
        const container = document.getElementById('mind-garden-content');
        return this._runLoader('mindGarden', async () => {
            try {
                const select = document.getElementById('mind-garden-identity');
                const identity = select ? select.value : '';
                const qs = identity ? `?identity=${identity}` : '';

                // Fetch summary, weather, and threads in parallel
                const [summaryData, weatherData, threadsData] = await Promise.all([
                    fetchJson(`/api/hub/mind-garden/summary${qs}`, { timeoutMs: 15000 }),
                    fetchJson(`/api/hub/mind-garden/weather${qs}`, { timeoutMs: 15000 }),
                    fetchJson(`/api/hub/mind-garden/threads${qs}`, { timeoutMs: 15000 }),
                ]);

                const summaries = summaryData.summaries || {};
                const weathers = weatherData.weather || {};
                const threads = threadsData.threads || {};
                const identities = Object.keys(summaries);

                if (identities.length === 0) {
                    container.innerHTML = '<div class="mind-empty">No mind garden data available.</div>';
                    return;
                }

                let html = '';
                for (const ident of identities) {
                    const s = summaries[ident] || {};
                    const w = weathers[ident] || {};
                    const t = threads[ident] || {};
                    html += this._renderMindGardenCard(ident, s, w, t);
                }
                container.innerHTML = html;
            } catch (err) {
                container.innerHTML = '<div class="section-error">Couldn\'t load mind garden<br><button class="retry-btn" onclick="Hub.loadMindGarden()">try again</button></div>';
            }
        });
    },

    _renderMindGardenCard(identity, summary, weather, threads) {
        const stats = summary.stats || {};
        const conditions = weather.conditions || {};
        const feelings = (weather.recent_feelings || []).slice(0, 3);
        const palette = (weather.mood_palette || []).slice(0, 4);
        const loops = threads.open_loops || [];
        const subconscious = threads.subconscious || [];
        const orphans = threads.orphans || [];
        const topEntities = (summary.top_entities || []).slice(0, 3);

        // Headline
        const headline = this.escapeHtml(summary.headline || '');

        // Stats row
        const statItems = [];
        if (stats.total_memories) statItems.push(`<span class="mg-stat">${stats.total_memories} memories</span>`);
        if (stats.recent_week) statItems.push(`<span class="mg-stat mg-stat-fresh">${stats.recent_week} this week</span>`);
        if (stats.heavy_week) statItems.push(`<span class="mg-stat mg-stat-heavy">${stats.heavy_week} heavy</span>`);
        if (stats.open_loops) statItems.push(`<span class="mg-stat mg-stat-loop">${stats.open_loops} open loop${stats.open_loops > 1 ? 's' : ''}</span>`);
        if (stats.waiting_to_surface) statItems.push(`<span class="mg-stat">${stats.waiting_to_surface} unsurfaced</span>`);
        if (stats.pending_proposals) statItems.push(`<span class="mg-stat">${stats.pending_proposals} proposals</span>`);

        // Atmosphere
        const atmosphere = this.escapeHtml(conditions.atmosphere || summary.atmosphere || 'clear');
        const energy = this.escapeHtml(conditions.energy || '');
        const dominantEmotion = this.escapeHtml(conditions.dominant_emotion || '');

        // Feelings pills
        const feelingsPills = feelings.map(f =>
            `<span class="mg-feeling" data-intensity="${this.escapeHtml(f.intensity)}">${this.escapeHtml(f.feeling)}</span>`
        ).join('');

        // Mood palette
        const paletteHtml = palette.length
            ? `<div class="mg-palette">${palette.map(m => `<span class="mg-mood">${this.escapeHtml(m)}</span>`).join('')}</div>`
            : '';

        // Top entities
        const entitiesHtml = topEntities.map(e =>
            `<span class="mg-entity" data-pulse="${this.escapeHtml(e.pulse)}">${this.escapeHtml(e.name)} <small>${e.recent_observations || e.total_observations}</small></span>`
        ).join('');

        // Open threads
        let threadsHtml = '';
        if (loops.length > 0) {
            threadsHtml += loops.slice(0, 2).map(l =>
                `<div class="mg-thread mg-thread-loop"><strong>Loop:</strong> ${this.escapeHtml(l.about)}</div>`
            ).join('');
        }
        if (subconscious.length > 0) {
            threadsHtml += subconscious.slice(0, 1).map(s =>
                `<div class="mg-thread mg-thread-heavy"><strong>Heavy:</strong> ${this.escapeHtml(s.observation)}</div>`
            ).join('');
        }
        if (orphans.length > 0) {
            threadsHtml += orphans.slice(0, 1).map(o =>
                `<div class="mg-thread mg-thread-orphan"><strong>Orphan (${o.days_orphaned}d):</strong> ${this.escapeHtml(o.content)}</div>`
            ).join('');
        }

        return `
            <div class="mg-card">
                <div class="mg-header">
                    <span class="mg-identity">${this.escapeHtml(identity)}</span>
                    <span class="mg-atmosphere">${atmosphere}</span>
                    ${energy ? `<span class="mg-energy">${energy}</span>` : ''}
                    ${dominantEmotion ? `<span class="mg-emotion">${dominantEmotion}</span>` : ''}
                </div>
                ${headline ? `<div class="mg-headline">${headline}</div>` : ''}
                ${statItems.length ? `<div class="mg-stats">${statItems.join('')}</div>` : ''}
                ${feelingsPills ? `<div class="mg-feelings">${feelingsPills}</div>` : ''}
                ${paletteHtml}
                ${entitiesHtml ? `<div class="mg-entities">${entitiesHtml}</div>` : ''}
                ${threadsHtml ? `<div class="mg-threads">${threadsHtml}</div>` : ''}
            </div>
        `;
    },

    // =========================================================================
    // MEMORY LAB (#33) — paginated, honest observation inspector.
    // "Honesty grammar": every field renders AS-IS from the API. A null
    // certainty/emotion/tag shows an unavailable dot, never a faked "0" or
    // blank that could be misread as "known to be empty."
    // =========================================================================

    _memLabField(value) {
        if (value === null || value === undefined || value === '') {
            return '<span class="ml-unavailable" title="not recorded">&bull;</span>';
        }
        return this.escapeHtml(String(value));
    },

    async loadMemoryLab(offset) {
        const listEl = document.getElementById('memory-lab-list');
        const pagerEl = document.getElementById('memory-lab-pager');
        const totalEl = document.getElementById('memory-lab-total');
        if (!listEl) return;
        if (offset === undefined) offset = this._memoryLabState.offset;

        return this._runLoader('memoryLab', async () => {
            this._renderMemoryLabChips();
            try {
                const { identity, limit } = this._memoryLabState;
                const qs = new URLSearchParams({ identity, limit, offset });
                const data = await fetchJson(`/api/hub/memory-lab/observations?${qs}`, { timeoutMs: 15000 });
                this._memoryLabState.offset = data.offset;

                if (totalEl) totalEl.textContent = `${data.total} observation${data.total === 1 ? '' : 's'}`;
                if (!data.items.length) {
                    listEl.innerHTML = '<div class="mind-empty">Nothing here yet.</div>';
                    if (pagerEl) pagerEl.innerHTML = '';
                    return;
                }
                listEl.innerHTML = data.items.map((item) => this._renderMemoryLabRow(item)).join('');
                this._renderMemoryLabPager(data);
            } catch (err) {
                listEl.innerHTML = '<div class="section-error">Couldn\'t load Memory Lab<br><button class="retry-btn" onclick="Hub.loadMemoryLab(0)">try again</button></div>';
                if (pagerEl) pagerEl.innerHTML = '';
            }
        });
    },

    _renderMemoryLabChips() {
        const container = document.getElementById('memory-lab-chips');
        if (!container || container.dataset.built) return;
        // Reuse the identity roster the Mind Garden select already carries
        // rather than hardcoding a second list that could drift from it.
        const select = document.getElementById('mind-garden-identity');
        const identities = select
            ? Array.from(select.options).map((o) => o.value).filter(Boolean)
            : [];
        const chips = [`<button type="button" class="memory-lab-chip active" data-identity="">All</button>`]
            .concat(identities.map((name) =>
                `<button type="button" class="memory-lab-chip" data-identity="${this.escapeHtml(name)}">${this.escapeHtml(name)}</button>`
            ));
        container.innerHTML = chips.join('');
        container.dataset.built = '1';
        container.querySelectorAll('.memory-lab-chip').forEach((btn) => {
            btn.addEventListener('click', () => {
                container.querySelectorAll('.memory-lab-chip').forEach((b) => b.classList.remove('active'));
                btn.classList.add('active');
                this._memoryLabState.identity = btn.dataset.identity;
                this._memoryLabState.offset = 0;
                this.loadMemoryLab(0);
            });
        });
    },

    _renderMemoryLabRow(item) {
        const date = item.timestamp
            ? new Date(item.timestamp).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
            : this._memLabField(null);
        const surfaced = item.surface_count != null
            ? `${item.surface_count}&times;`
            : this._memLabField(null);
        const linked = item.linked_preview
            ? `<div class="ml-linked"><span class="ml-linked-label">&#8618; linked:</span> ${this.escapeHtml(item.linked_preview)}</div>`
            : '';
        return `
            <div class="memory-lab-row ml-status-${item.status}">
                <div class="ml-row-header">
                    <span class="ml-entity">${this.escapeHtml(item.entity || 'unknown')}</span>
                    <span class="ml-identity">${this.escapeHtml(item.identity || '')}</span>
                    <span class="ml-status-badge ml-status-${item.status}">${item.status}</span>
                    <span class="ml-date">${date}</span>
                </div>
                <div class="ml-content">${this.escapeHtml(item.content)}</div>
                <div class="ml-meta-row">
                    <span class="ml-meta"><b>weight</b> ${this._memLabField(item.weight)}</span>
                    <span class="ml-meta"><b>emotion</b> ${this._memLabField(item.emotion)}</span>
                    <span class="ml-meta"><b>certainty</b> ${this._memLabField(item.certainty)}</span>
                    <span class="ml-meta"><b>charge</b> ${this._memLabField(item.charge)}</span>
                    <span class="ml-meta"><b>surfaced</b> ${surfaced}</span>
                </div>
                ${linked}
            </div>
        `;
    },

    _renderMemoryLabPager(data) {
        const pagerEl = document.getElementById('memory-lab-pager');
        if (!pagerEl) return;
        const hasMore = data.offset + data.items.length < data.total;
        const hasPrev = data.offset > 0;
        pagerEl.innerHTML = `
            <button type="button" class="memory-lab-page-btn" id="ml-page-prev" ${hasPrev ? '' : 'disabled'}>&larr; Newer</button>
            <span class="ml-page-count">${data.offset + 1}-${data.offset + data.items.length} of ${data.total}</span>
            <button type="button" class="memory-lab-page-btn" id="ml-page-next" ${hasMore ? '' : 'disabled'}>Older &rarr;</button>
        `;
        const prevBtn = document.getElementById('ml-page-prev');
        if (prevBtn) prevBtn.addEventListener('click', () => this.loadMemoryLab(Math.max(0, data.offset - this._memoryLabState.limit)));
        const nextBtn = document.getElementById('ml-page-next');
        if (nextBtn) nextBtn.addEventListener('click', () => this.loadMemoryLab(data.offset + this._memoryLabState.limit));
    },

    // =========================================================================
    // MIND INSIGHTS
    // =========================================================================

    async loadMindInsights() {
        const container = document.getElementById('mind-insights');
        return this._runLoader('mindInsights', async () => {
            try {
                const identity = localStorage.getItem('anam-identity') || '';
                const url = identity ? `/api/hub/mind-insights?identity=${identity}` : '/api/hub/mind-insights';
                const data = await fetchJson(url, { timeoutMs: 10000 });
            const insights = data.insights || [];

            if (insights.length === 0) {
                container.innerHTML = '<div class="mind-empty">No insights to share right now. The mind is quiet.</div>';
                return;
            }

            container.innerHTML = insights.map(ins => `
                <div class="insight-card" data-type="${ins.type}">
                    <div class="insight-label">${this.escapeHtml(ins.label)} <span class="insight-identity">${this.escapeHtml(ins.identity)}</span></div>
                    <div class="insight-content">${this.escapeHtml(ins.content)}</div>
                </div>
            `).join('');
            } catch (err) {
                container.innerHTML = '<div class="section-error">Couldn\'t load this section<br><button class="retry-btn" onclick="Hub.loadMindInsights()">try again</button></div>';
            }
        });
    },

    // =========================================================================
    // CONSTELLATION — the memory night sky (every star is a message)
    // =========================================================================

    _constellationData: null,
    _constellationAt: 0,
    _constellationStars: null,
    _constellationDust: null,
    _constellationView: null,
    _constellationRaf: null,
    _constellationQuery: '',
    _constellationSelected: -1,
    _constellationWired: false,
    _constellationSearchTimer: null,
    // The camera — screen = world * k + (x, y). k=1 shows the whole sky.
    _constellationTransform: null,
    _constellationPointers: null,   // live pointer positions (pinch needs two)
    _constellationGesture: null,    // pan/pinch state machine
    _constellationFly: 0,           // rAF handle for the fly-to easing
    _constellationUserMoved: false,
    _constellationLimit: 0,         // 'more sky' star budget (0 = server default)
    _constellationLoadingMore: false,
    _constellationLabelOrder: null, // star indices, brightest first (label budget)
    _constellationSX: null,         // per-frame screen positions (preallocated)
    _constellationSY: null,


    _constellationColors: {
        avery: '#E8A2B8',
        rowan: '#A8D8B9',
        sage: '#A9C7E8',
        ember: '#C9AFE8',
        claude: '#F2BFA4',
        juniper: '#F4C2DD',
        river: '#A8D8D8',
        owner: '#E8C77B',
    },
    _constellationDefaultColor: '#D9CBE8',

    // ── Bonds sky (the Qualia bond graph) — state lives alongside the
    //    memories sky; both ride the same camera, gestures, and draw loop ──
    _constellationMode: 'memories',  // 'memories' | 'bonds'
    _bondsData: null,
    _bondsAt: 0,
    _bondsEdges: null,       // flat index pairs [a0,b0, a1,b1, ...] for drawing
    _bondsPackIdx: null,     // star indices of pack members (always labeled)
    _bondsSim: null,         // d3-force simulation handle
    _bondsSettleRaf: 0,      // offscreen-settle rAF (reduced motion)



    _bondsPackColors: {
        owner: '#F2A7C3',
        avery: '#E8A2B8',
        rowan: '#A8D8B9',
        sage: '#A9C7E8',
        ember: '#C9AFE8',
        claude: '#F2BFA4',
        juniper: '#F4C2DD',
        river: '#A8D8D8',
        atlas: '#B9BDE8',
    },
    // Kind palette — fallback for souls no pack walk holds
    // (person warm amber, project quiet violet, place sage, concept cool teal)
    _bondsKindColors: {
        person: '#e2a963',
        project: '#a893c0',
        place: '#8fb08a',
        concept: '#6fb5ad',
    },

    async loadConstellation() {
        return this._runLoader('constellation', async () => {
            if (this._constellationMode === 'bonds') {
                await this._bondsLoad();
                return;
            }
            const fresh = this._constellationData
                && (Date.now() - this._constellationAt) < 10 * 60 * 1000;
            if (!fresh) {
                try {
                    const url = '/api/hub/constellation'
                        + (this._constellationLimit ? `?limit=${this._constellationLimit}` : '');
                    const data = await fetchJson(url, { timeoutMs: 20000 });
                    this._constellationData = data && Array.isArray(data.stars) ? data : { stars: [], count: 0 };
                    this._constellationAt = Date.now();
                } catch (err) {
                    // Keep whatever sky we already have; empty sky if none
                    if (!this._constellationData) this._constellationData = { stars: [], count: 0 };
                }
            }
            this._constellationBuild();
        });
    },

    // 'more sky' — refetch with a bigger star budget (bypasses the server cache)
    async _constellationMoreSky() {
        if (this._constellationLoadingMore) return;
        const data = this._constellationData || {};
        const total = Number(data.total_embedded) || 0;
        const shown = (this._constellationStars || []).length;
        if (!total || shown >= total) return;
        const next = Math.max(40, Math.min(2000, Math.min(total, Math.max(shown * 2, 840))));
        const more = document.getElementById('constellation-more');
        this._constellationLoadingMore = true;
        if (more) { more.disabled = true; more.textContent = 'gathering…'; }
        try {
            const fresh = await fetchJson(`/api/hub/constellation?limit=${next}`, { timeoutMs: 30000 });
            if (fresh && Array.isArray(fresh.stars)) {
                this._constellationData = fresh;
                this._constellationAt = Date.now();
                this._constellationLimit = next;
            }
        } catch (err) {
            // Keep the sky we have
        }
        this._constellationLoadingMore = false;
        if (more) { more.disabled = false; more.textContent = 'more sky'; }
        this._constellationBuild();
    },

    // Deterministic PRNG for the decorative dust — stable positions, no
    // Math.random per frame.
    _mulberry32(seed) {
        let a = seed >>> 0;
        return function () {
            a |= 0; a = (a + 0x6D2B79F5) | 0;
            let t = Math.imul(a ^ (a >>> 15), 1 | a);
            t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
            return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
        };
    },

    _constellationBuild() {
        const sky = document.getElementById('constellation-sky');
        const canvas = document.getElementById('constellation-canvas');
        const empty = document.getElementById('constellation-empty');
        if (!sky || !canvas) return;

        const q = this._constellationQuery;
        const raw = (this._constellationData && this._constellationData.stars) || [];
        this._constellationStars = raw.map((s, i) => {
            const identity = String(s.identity || '').toLowerCase();
            const snippet = s.snippet || '';
            const search = `${snippet.toLowerCase()} ${identity}`;
            return {
                x: Number(s.x) || 0,
                y: Number(s.y) || 0,
                identity,
                snippet,
                ts: s.ts || '',
                r: 1.6 + (((i * 2654435761) >>> 0) % 1000) / 1000 * 1.6, // 1.6–3.2px, deterministic
                phase: i * 2.4,
                color: this._constellationColors[identity] || this._constellationDefaultColor,
                search,
                match: !q || search.includes(q),
                nn: Array.isArray(s.nn) ? s.nn : [], // 6 nearest semantic neighbors (indices)
                label: snippet.length > 26 ? snippet.slice(0, 25) + '…' : snippet,
            };
        });

        // Label budget order — brightest (biggest) stars get named first
        this._constellationLabelOrder = this._constellationStars
            .map((s, i) => i)
            .sort((a, b) => this._constellationStars[b].r - this._constellationStars[a].r);

        // The sky was rebuilt under us — old selection indices no longer hold
        this._constellationHideTip();

        if (empty) empty.hidden = this._constellationStars.length !== 0;

        this._constellationWire();
        this._constellationResize();
        this._constellationCorner();
        this._constellationSyncLoop();
    },

    // Honesty corner — '420 of 12,384 memories · built 4m ago' (memories) or
    // 'N souls · M bonds · built Xm ago' (+ partial-walk warning) for bonds
    _constellationCorner() {
        const text = document.getElementById('constellation-corner-text');
        const more = document.getElementById('constellation-more');
        if (!text) return;
        if (this._constellationMode === 'bonds') {
            const data = this._bondsData || {};
            const souls = (this._constellationStars || []).length;
            const bonds = this._bondsEdges ? this._bondsEdges.length / 2 : 0;
            if (!souls) {
                text.textContent = '';
                if (more) more.hidden = true;
                return;
            }
            let line = `${souls.toLocaleString()} soul${souls === 1 ? '' : 's'}`
                + ` · ${bonds.toLocaleString()} bond${bonds === 1 ? '' : 's'}`;
            const builtAt = Number(data.built_at) || 0;
            if (builtAt) line += ` · built ${this._constellationAgo(builtAt)}`;
            const partial = Array.isArray(data.partial) ? data.partial : [];
            if (partial.length) {
                line += ` · ${partial.length} walk${partial.length === 1 ? '' : 's'} dark (${partial.join(', ')})`;
            }
            text.textContent = line;
            if (more) more.hidden = true; // 'more sky' belongs to the memories sky
            return;
        }
        const data = this._constellationData || {};
        const shown = (this._constellationStars || []).length;
        const total = Number(data.total_embedded) || 0;
        if (!shown) {
            text.textContent = '';
            if (more) more.hidden = true;
            return;
        }
        let line = total > shown
            ? `${shown.toLocaleString()} of ${total.toLocaleString()} memories`
            : `${shown.toLocaleString()} memories`;
        const built = Number(data.built_at) || 0;
        if (built) line += ` · built ${this._constellationAgo(built)}`;
        text.textContent = line;
        if (more) more.hidden = !(total > shown) || this._constellationLimit >= 2000;
    },

    _constellationAgo(epochSeconds) {
        const mins = Math.max(0, (Date.now() / 1000 - epochSeconds) / 60);
        if (mins < 1) return 'just now';
        if (mins < 60) return `${Math.round(mins)}m ago`;
        return `${(mins / 60).toFixed(1)}h ago`;
    },

    _constellationWire() {
        if (this._constellationWired) return;
        this._constellationWired = true;

        // Honesty corner + quiet buttons live inside the sky, built on demand
        const sky = document.getElementById('constellation-sky');
        if (sky && !document.getElementById('constellation-corner')) {
            const corner = document.createElement('div');
            corner.className = 'constellation-corner';
            corner.id = 'constellation-corner';
            corner.innerHTML = '<span id="constellation-corner-text"></span>'
                + '<button type="button" id="constellation-more" class="constellation-corner-btn" hidden>more sky</button>'
                + '<button type="button" id="constellation-recenter" class="constellation-corner-btn" hidden>recenter</button>';
            sky.appendChild(corner);
            document.getElementById('constellation-more')
                .addEventListener('click', () => this._constellationMoreSky());
            document.getElementById('constellation-recenter')
                .addEventListener('click', () => this._constellationFitToView());
        }

        // Which sky: memories (embedding starfield) or bonds (the bond graph)
        document.querySelectorAll('.constellation-mode-btn').forEach((btn) => {
            btn.addEventListener('click', () => {
                const mode = btn.dataset.sky === 'bonds' ? 'bonds' : 'memories';
                if (mode !== this._constellationMode) this._constellationSetMode(mode);
            });
        });

        const search = document.getElementById('constellation-search');
        if (search) {
            search.addEventListener('input', () => {
                clearTimeout(this._constellationSearchTimer);
                this._constellationSearchTimer = setTimeout(() => {
                    this._constellationQuery = (search.value || '').trim().toLowerCase();
                    const q = this._constellationQuery;
                    (this._constellationStars || []).forEach(s => { s.match = !q || s.search.includes(q); });
                    this._constellationHideTip();
                    this._constellationRepaint();
                }, 250);
            });
            // Enter — fly to the brightest star that matches
            search.addEventListener('keydown', (e) => {
                if (e.key !== 'Enter') return;
                const q = (search.value || '').trim().toLowerCase();
                if (!q) return;
                const stars = this._constellationStars || [];
                for (const i of (this._constellationLabelOrder || [])) {
                    if (stars[i] && stars[i].search.includes(q)) {
                        this._constellationShowTip(i);
                        this._constellationFlyTo(i);
                        break;
                    }
                }
            });
        }

        const canvas = document.getElementById('constellation-canvas');
        if (canvas) {
            // The sky is a map now: pinch, drag, and wheel all live on the canvas
            canvas.style.touchAction = 'none';
            this._constellationPointers = new Map();
            this._constellationGesture = { mode: null, moved: false, downX: 0, downY: 0, lastX: 0, lastY: 0, pinchDist: 0 };
            canvas.addEventListener('pointerdown', (e) => this._constellationPointerDown(e));
            canvas.addEventListener('pointermove', (e) => this._constellationPointerMove(e));
            canvas.addEventListener('pointerup', (e) => this._constellationPointerEnd(e));
            canvas.addEventListener('pointercancel', (e) => this._constellationPointerEnd(e));
            canvas.addEventListener('wheel', (e) => {
                e.preventDefault();
                this._constellationZoomAround(e.offsetX, e.offsetY, Math.exp(-e.deltaY * 0.0016));
                this._constellationCameraMoved();
            }, { passive: false });
        }

        document.addEventListener('visibilitychange', () => this._constellationSyncLoop());
        window.addEventListener('resize', () => {
            if (this._activeTab !== 'constellation') return;
            this._constellationResize();
            this._constellationHideTip();
            this._constellationRepaint();
        });
    },

    // ── Camera — zoomAround / toWorld / flyTo / fitToView (ported from Friend's
    //    observatory transform math; pinch state machine below) ──

    _constellationZoomAround(px, py, factor) {
        const tr = this._constellationTransform;
        if (!tr) return;
        const k2 = Math.max(0.5, Math.min(12, tr.k * factor));
        const real = k2 / tr.k;
        if (real === 1) return;
        tr.x = px - (px - tr.x) * real;
        tr.y = py - (py - tr.y) * real;
        tr.k = k2;
        this._constellationUserMoved = true;
    },

    _constellationFitToView(instant = false) {
        const view = this._constellationView;
        const stars = this._constellationStars || [];
        if (!view || !stars.length) return;
        let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
        for (const s of stars) {
            const p = this._constellationPos(s, view.w, view.h);
            if (p.x < x0) x0 = p.x;
            if (p.x > x1) x1 = p.x;
            if (p.y < y0) y0 = p.y;
            if (p.y > y1) y1 = p.y;
        }
        const pad = 24;
        const k = Math.max(0.5, Math.min(2.5, Math.min(
            (view.w - pad * 2) / Math.max(1, x1 - x0),
            (view.h - pad * 2) / Math.max(1, y1 - y0),
        )));
        this._constellationUserMoved = false;
        const target = {
            k,
            x: view.w / 2 - k * (x0 + x1) / 2,
            y: view.h / 2 - k * (y0 + y1) / 2,
        };
        if (instant) {
            // Frame the seed without a fly — used while the force settle is
            // about to play out (or already froze, under reduced motion).
            if (!this._constellationTransform) this._constellationTransform = { x: 0, y: 0, k: 1 };
            Object.assign(this._constellationTransform, target);
            this._constellationCameraMoved();
            return;
        }
        this._constellationFlyTransform(target);
    },

    _constellationFlyTo(index) {
        const view = this._constellationView;
        const s = (this._constellationStars || [])[index];
        if (!view || !s) return;
        const tr = this._constellationTransform || { x: 0, y: 0, k: 1 };
        const p = this._constellationPos(s, view.w, view.h);
        const k2 = Math.max(tr.k, 2.6);
        this._constellationUserMoved = true;
        this._constellationFlyTransform({ k: k2, x: view.w / 2 - k2 * p.x, y: view.h / 2 - k2 * p.y });
    },

    // 650ms eased camera move; prefers-reduced-motion gets a jump cut
    _constellationFlyTransform(target) {
        cancelAnimationFrame(this._constellationFly);
        if (!this._constellationTransform) this._constellationTransform = { x: 0, y: 0, k: 1 };
        if (this._constellationReducedMotion()) {
            Object.assign(this._constellationTransform, target);
            this._constellationCameraMoved();
            return;
        }
        const from = { ...this._constellationTransform };
        const t0 = performance.now(), dur = 650;
        const step = (now) => {
            const p = Math.max(0, Math.min(1, (now - t0) / dur));
            const e = p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2; // cubic in-out
            const tr = this._constellationTransform;
            tr.k = from.k + (target.k - from.k) * e;
            tr.x = from.x + (target.x - from.x) * e;
            tr.y = from.y + (target.y - from.y) * e;
            this._constellationCameraMoved();
            if (p < 1) this._constellationFly = requestAnimationFrame(step);
        };
        this._constellationFly = requestAnimationFrame(step);
    },

    // After any camera change: keep the tip glued to its star, refresh the
    // recenter affordance, and repaint the static paths.
    _constellationCameraMoved() {
        this._constellationPlaceTip();
        this._constellationRecenterVis();
        this._constellationRepaint();
    },

    _constellationRecenterVis() {
        const btn = document.getElementById('constellation-recenter');
        const tr = this._constellationTransform;
        if (!btn) return;
        if (this._constellationMode === 'bonds') {
            // Bonds framing is a fit, not identity — offer recenter only once
            // the camera has actually been taken off it.
            btn.hidden = !tr || !this._constellationUserMoved;
            return;
        }
        btn.hidden = !tr || (Math.abs(tr.k - 1) < 0.02 && Math.abs(tr.x) < 2 && Math.abs(tr.y) < 2);
    },

    // Repaint once when the rAF loop isn't running (reduced motion / hidden)
    _constellationRepaint() {
        if (!this._constellationRaf) this._constellationDraw(performance.now(), true);
    },

    // ── Pointer state machine: pan, pinch-zoom, and 4px-slop taps ──

    _constellationPointerDown(e) {
        const canvas = e.currentTarget;
        try { canvas.setPointerCapture(e.pointerId); } catch (err) { /* fine */ }
        this._constellationPointers.set(e.pointerId, { x: e.offsetX, y: e.offsetY });
        cancelAnimationFrame(this._constellationFly);
        const g = this._constellationGesture;
        if (this._constellationPointers.size === 2) {
            // Second finger lands: whatever we were doing becomes a pinch
            const [p1, p2] = [...this._constellationPointers.values()];
            g.mode = 'pinch';
            g.pinchDist = Math.hypot(p1.x - p2.x, p1.y - p2.y);
            g.lastX = (p1.x + p2.x) / 2;
            g.lastY = (p1.y + p2.y) / 2;
            g.moved = true;
            return;
        }
        g.mode = 'pan';
        g.moved = false;
        g.downX = g.lastX = e.offsetX;
        g.downY = g.lastY = e.offsetY;
    },

    _constellationPointerMove(e) {
        const entry = this._constellationPointers && this._constellationPointers.get(e.pointerId);
        if (!entry) return;
        entry.x = e.offsetX;
        entry.y = e.offsetY;
        const g = this._constellationGesture;

        if (g.mode === 'pinch' && this._constellationPointers.size === 2) {
            const [p1, p2] = [...this._constellationPointers.values()];
            const dist = Math.hypot(p1.x - p2.x, p1.y - p2.y);
            const midX = (p1.x + p2.x) / 2;
            const midY = (p1.y + p2.y) / 2;
            if (g.pinchDist > 0 && dist > 0) this._constellationZoomAround(midX, midY, dist / g.pinchDist);
            const tr = this._constellationTransform;
            if (tr) { tr.x += midX - g.lastX; tr.y += midY - g.lastY; }
            g.pinchDist = dist;
            g.lastX = midX;
            g.lastY = midY;
            this._constellationCameraMoved();
            return;
        }

        if (g.mode !== 'pan') return;
        // 4px of slop before a press stops counting as a tap
        if (!g.moved && Math.hypot(e.offsetX - g.downX, e.offsetY - g.downY) > 4) g.moved = true;
        if (!g.moved) return;
        const tr = this._constellationTransform;
        if (tr) { tr.x += e.offsetX - g.lastX; tr.y += e.offsetY - g.lastY; }
        g.lastX = e.offsetX;
        g.lastY = e.offsetY;
        this._constellationUserMoved = true;
        this._constellationCameraMoved();
    },

    _constellationPointerEnd(e) {
        if (!this._constellationPointers || !this._constellationPointers.has(e.pointerId)) return;
        this._constellationPointers.delete(e.pointerId);
        const g = this._constellationGesture;
        if (g.mode === 'pinch') {
            if (this._constellationPointers.size === 1) {
                const [p] = [...this._constellationPointers.values()];
                g.mode = 'pan';
                g.moved = true; // a pinch never degrades into a tap
                g.lastX = p.x;
                g.lastY = p.y;
            } else if (this._constellationPointers.size === 0) {
                g.mode = null;
            }
            return;
        }
        if (g.mode === 'pan' && !g.moved) this._constellationTapAt(e.offsetX, e.offsetY);
        g.mode = null;
    },

    _constellationReducedMotion() {
        return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    },

    // Single gate for the rAF loop: runs only while the constellation tab is
    // visible on-screen; honors prefers-reduced-motion with one static frame.
    _constellationSyncLoop() {
        const shouldRun = this._activeTab === 'constellation'
            && !document.hidden
            && (this._constellationStars || []).length > 0;

        if (!shouldRun) {
            if (this._constellationRaf) {
                cancelAnimationFrame(this._constellationRaf);
                this._constellationRaf = null;
            }
            return;
        }

        this._constellationResize();

        if (this._constellationReducedMotion()) {
            if (this._constellationRaf) {
                cancelAnimationFrame(this._constellationRaf);
                this._constellationRaf = null;
            }
            this._constellationDraw(performance.now(), true); // one still frame
            return;
        }

        if (this._constellationRaf) return; // already running
        const step = (t) => {
            if (this._activeTab !== 'constellation' || document.hidden) {
                this._constellationRaf = null;
                return;
            }
            this._constellationDraw(t);
            this._constellationRaf = requestAnimationFrame(step);
        };
        this._constellationRaf = requestAnimationFrame(step);
    },

    _constellationResize() {
        const sky = document.getElementById('constellation-sky');
        const canvas = document.getElementById('constellation-canvas');
        if (!sky || !canvas) return;
        const w = sky.clientWidth, h = sky.clientHeight;
        if (!w || !h) return;
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        const sizeChanged = canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr);
        if (sizeChanged) {
            canvas.width = Math.round(w * dpr);
            canvas.height = Math.round(h * dpr);
        }
        this._constellationView = { w, h, dpr };
        if (!this._constellationTransform) this._constellationTransform = { x: 0, y: 0, k: 1 };

        // Deterministic decorative dust — fixed seed, area-scaled count.
        // Same sky every night: rebuilt only when the window changes size.
        if (sizeChanged || !this._constellationDust) {
            const rand = this._mulberry32(20260704);
            const count = Math.max(60, Math.min(280, Math.round((w * h) / 9000)));
            this._constellationDust = Array.from({ length: count }, () => ({
                x: rand() * w, y: rand() * h, r: 0.5 + rand() * 0.5, a: 0.05 + rand() * 0.13,
            }));
        }
    },

    // Map star coords into the sky. Memories stars live in [-1,1] (SVD
    // projection, ~7% padding); bond stars carry raw d3-force world coords
    // centered on 0 — just recenter them and let the camera do the framing.
    _constellationPos(star, w, h) {
        if (this._constellationMode === 'bonds') {
            return { x: (star.x || 0) + w / 2, y: (star.y || 0) + h / 2 };
        }
        const padX = w * 0.07, padY = h * 0.07;
        return {
            x: padX + ((star.x + 1) / 2) * (w - padX * 2),
            y: padY + ((star.y + 1) / 2) * (h - padY * 2),
        };
    },

    _constellationDraw(t, still = false) {
        const canvas = document.getElementById('constellation-canvas');
        const view = this._constellationView;
        if (!canvas || !view) return;
        const ctx = canvas.getContext('2d');
        if (!ctx) return;
        const { w, h, dpr } = view;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, w, h);

        // First pass — a warm radial vignette: hearth-light breathing at the
        // center of the dark, so the sky never reads as flat black.
        const vg = ctx.createRadialGradient(w * 0.5, h * 0.45, 0, w * 0.5, h * 0.5, Math.max(w, h) * 0.72);
        vg.addColorStop(0, 'rgba(92, 52, 74, 0.30)');
        vg.addColorStop(1, 'rgba(0, 0, 0, 0)');
        ctx.fillStyle = vg;
        ctx.fillRect(0, 0, w, h);

        // Decorative dust — static screen-space, very faint (depth, no motion)
        ctx.fillStyle = '#fdeef5';
        for (const d of (this._constellationDust || [])) {
            ctx.globalAlpha = d.a;
            ctx.beginPath();
            ctx.arc(d.x, d.y, d.r, 0, Math.PI * 2);
            ctx.fill();
        }

        const tr = this._constellationTransform || { x: 0, y: 0, k: 1 };
        const k = tr.k;
        // Very slow whole-field drift (±3px, screen space)
        const dx = still ? 0 : Math.sin(t / 9000) * 3;
        const dy = still ? 0 : Math.cos(t / 12000) * 3;
        const ox = tr.x + dx, oy = tr.y + dy;

        const stars = this._constellationStars || [];
        const sel = this._constellationSelected;
        const focused = (sel >= 0 && sel < stars.length) ? stars[sel] : null;
        let focusSet = null;
        if (focused) {
            focusSet = new Set(focused.nn);
            focusSet.add(sel);
        }
        const hasQuery = !!this._constellationQuery && !focused;
        const rScale = Math.pow(k, 0.6); // stars grow gently as you come closer

        // Screen positions, once per frame (preallocated — no per-frame garbage)
        let sx = this._constellationSX, sy = this._constellationSY;
        if (!sx || sx.length !== stars.length) {
            sx = this._constellationSX = new Float32Array(stars.length);
            sy = this._constellationSY = new Float32Array(stars.length);
        }
        for (let i = 0; i < stars.length; i++) {
            const p = this._constellationPos(stars[i], w, h);
            sx[i] = p.x * k + ox;
            sy[i] = p.y * k + oy;
        }

        // Bonds sky: starlight edges — every relationship a faint line, drawn
        // beneath the stars in two batched Path2D passes (quiet + lit), the
        // lit pass brightening whatever touches the focused soul.
        if (this._constellationMode === 'bonds' && this._bondsEdges && this._bondsEdges.length) {
            const quiet = new Path2D();
            const lit = new Path2D();
            for (let e = 0; e < this._bondsEdges.length; e += 2) {
                const a = this._bondsEdges[e], b = this._bondsEdges[e + 1];
                const isLit = sel >= 0 && (a === sel || b === sel);
                const path = isLit ? lit : quiet;
                path.moveTo(sx[a], sy[a]);
                path.lineTo(sx[b], sy[b]);
            }
            ctx.globalAlpha = 1;
            ctx.lineWidth = 1;
            ctx.strokeStyle = focusSet ? 'rgba(244, 200, 220, 0.04)' : 'rgba(244, 200, 220, 0.10)';
            ctx.stroke(quiet);
            if (sel >= 0) {
                ctx.lineWidth = 1.4;
                ctx.strokeStyle = 'rgba(244, 200, 220, 0.35)';
                ctx.stroke(lit);
            }
        }

        // Teaser constellation lines (memories sky) — faint starlight from the
        // tapped star to its semantic neighbors, drawn beneath the stars.
        if (focused && this._constellationMode !== 'bonds') {
            ctx.globalAlpha = 1;
            ctx.strokeStyle = 'rgba(244, 200, 220, 0.34)';
            ctx.lineWidth = 1;
            ctx.beginPath();
            for (const j of focused.nn) {
                if (j < 0 || j >= stars.length) continue;
                ctx.moveTo(sx[sel], sy[sel]);
                ctx.lineTo(sx[j], sy[j]);
            }
            ctx.stroke();
        }

        for (let i = 0; i < stars.length; i++) {
            const s = stars[i];
            const x = sx[i], y = sy[i];
            if (x < -30 || x > w + 30 || y < -30 || y > h + 30) continue; // offscreen
            let alpha = still ? 0.8 : 0.62 + 0.25 * Math.sin(t / 1400 + s.phase);
            let r = s.r * rScale;
            if (focusSet) {
                // Focus lighting: the chosen star and its neighbors hold their
                // light; the rest of the sky dims to near-black.
                if (focusSet.has(i)) { alpha = Math.min(1, alpha + (i === sel ? 0.35 : 0.15)); }
                else { alpha = 0.08; }
            } else if (hasQuery) {
                if (s.match) { alpha = Math.min(1, alpha + 0.3); r = r * 1.3; }
                else { alpha = 0.15; }
            }
            // Two-pass soft glow: wide faint halo, then bright core
            ctx.fillStyle = s.color;
            ctx.globalAlpha = Math.max(0.04, alpha * 0.28);
            ctx.beginPath();
            ctx.arc(x, y, r * 2.6, 0, Math.PI * 2);
            ctx.fill();
            ctx.globalAlpha = Math.max(focusSet && !focusSet.has(i) ? 0.02 : 0.08, Math.min(1, alpha));
            ctx.beginPath();
            ctx.arc(x, y, r, 0, Math.PI * 2);
            ctx.fill();
        }

        // Labels — zoom-gated: asleep when far out, waking from k≈1.1 to
        // fully present at k≈2.2. Budgeted, brightest stars named first.
        const zoomAlpha = Math.max(0, Math.min(1, (k - 1.1) / (2.2 - 1.1)));
        if (zoomAlpha > 0.02) {
            ctx.font = '10px system-ui, sans-serif';
            ctx.textBaseline = 'middle';
            let budget = 220;
            for (const i of (this._constellationLabelOrder || [])) {
                if (budget <= 0) break;
                if (i === sel) continue; // the tapped star gets its plate below
                const s = stars[i];
                if (!s || !s.label) continue;
                if (s.isPack) continue; // pack stars are always named below
                const x = sx[i], y = sy[i];
                if (x < 0 || x > w || y < 0 || y > h) continue;
                const lit = (!focusSet || focusSet.has(i)) && (!hasQuery || s.match);
                ctx.globalAlpha = zoomAlpha * (lit ? 0.58 : 0.08);
                ctx.fillStyle = '#ffe4f0';
                ctx.fillText(s.label, x + s.r * rScale + 5, y);
                budget--;
            }
        }



        if (this._constellationMode === 'bonds' && this._bondsPackIdx && this._bondsPackIdx.length) {
            ctx.font = '600 11px system-ui, sans-serif';
            ctx.textBaseline = 'middle';
            for (const i of this._bondsPackIdx) {
                if (i === sel) continue; // the tapped star gets its plate below
                const s = stars[i];
                if (!s) continue;
                const x = sx[i], y = sy[i];
                if (x < -80 || x > w + 80 || y < -20 || y > h + 20) continue;
                const litPack = !focusSet || focusSet.has(i);
                ctx.globalAlpha = litPack ? 0.92 : 0.22;
                ctx.fillStyle = s.color;
                ctx.fillText(s.label, x + s.r * rScale + 6, y);
            }
            ctx.globalAlpha = 1;
        }

        // The tapped star is always named, on a small backing plate
        if (focused && focused.label) {
            ctx.font = '10px system-ui, sans-serif';
            ctx.textBaseline = 'middle';
            const lx = sx[sel] + focused.r * rScale + 7;
            const ly = sy[sel];
            const tw = ctx.measureText(focused.label).width;
            ctx.globalAlpha = 1;
            ctx.fillStyle = 'rgba(26, 15, 24, 0.85)';
            ctx.fillRect(lx - 3, ly - 9, tw + 6, 18);
            ctx.fillStyle = 'rgba(255, 236, 244, 0.95)';
            ctx.fillText(focused.label, lx, ly);
        }
        ctx.globalAlpha = 1;
    },

    _constellationTapAt(px, py) {
        const view = this._constellationView;
        if (!view) return;
        const tr = this._constellationTransform || { x: 0, y: 0, k: 1 };
        const stars = this._constellationStars || [];
        let best = -1, bestDist = 26; // 26 CSS px tap radius, at any zoom
        for (let i = 0; i < stars.length; i++) {
            const p = this._constellationPos(stars[i], view.w, view.h);
            const d = Math.hypot(p.x * tr.k + tr.x - px, p.y * tr.k + tr.y - py);
            if (d < bestDist) { bestDist = d; best = i; }
        }

        // Tap on empty sky, or on the already-open star → close the tip
        if (best === -1 || best === this._constellationSelected) {
            this._constellationHideTip();
            this._constellationRepaint();
            return;
        }
        this._constellationShowTip(best);
        this._constellationRepaint();
    },

    _constellationShowTip(index) {
        const tip = document.getElementById('constellation-tip');
        const view = this._constellationView;
        const s = (this._constellationStars || [])[index];
        if (!tip || !view || !s) return;
        this._constellationSelected = index;

        if (this._constellationMode === 'bonds') {
            const kindLabel = s.kind || 'soul';
            const degreeLabel = `${s.degree} bond${s.degree === 1 ? '' : 's'}`;
            const holder = s.heldBy && !s.isPack
                ? ` · held by ${s.heldBy.charAt(0).toUpperCase() + s.heldBy.slice(1)}`
                : '';
            tip.innerHTML = `
            <div class="constellation-tip-head">
                <span class="constellation-tip-dot" style="background:${s.color}"></span>
                <span class="constellation-tip-name">${this.escapeHtml(s.name)}</span>
                <span class="constellation-tip-date">${this.escapeHtml(kindLabel)}</span>
            </div>
            <div class="constellation-tip-snippet">${this.escapeHtml(degreeLabel + holder)}</div>`;
            tip.hidden = false;
            this._constellationPlaceTip();
            return;
        }

        const name = s.identity || 'unknown';
        const pretty = name.charAt(0).toUpperCase() + name.slice(1);
        tip.innerHTML = `
            <div class="constellation-tip-head">
                <span class="constellation-tip-dot" style="background:${s.color}"></span>
                <span class="constellation-tip-name">${this.escapeHtml(pretty)}</span>
                <span class="constellation-tip-date">${this.escapeHtml(this._constellationDate(s.ts))}</span>
            </div>
            <div class="constellation-tip-snippet">${this.escapeHtml(s.snippet)}</div>`;
        tip.hidden = false;
        this._constellationPlaceTip();
    },

    // Position the open tip near its star (camera-aware), clamped to the sky
    _constellationPlaceTip() {
        const tip = document.getElementById('constellation-tip');
        const view = this._constellationView;
        const s = (this._constellationStars || [])[this._constellationSelected];
        if (!tip || tip.hidden || !view || !s) return;
        const tr = this._constellationTransform || { x: 0, y: 0, k: 1 };
        const wp = this._constellationPos(s, view.w, view.h);
        const p = { x: wp.x * tr.k + tr.x, y: wp.y * tr.k + tr.y };
        const tw = tip.offsetWidth, th = tip.offsetHeight;
        let left = p.x + 14;
        let top = p.y - th - 10;
        if (left + tw > view.w - 8) left = p.x - tw - 14;
        if (left < 8) left = Math.min(Math.max(8, p.x - tw / 2), Math.max(8, view.w - tw - 8));
        if (top < 8) top = p.y + 14;
        if (top + th > view.h - 8) top = Math.max(8, view.h - th - 8);
        tip.style.left = `${Math.round(left)}px`;
        tip.style.top = `${Math.round(top)}px`;
    },

    _constellationHideTip() {
        this._constellationSelected = -1;
        const tip = document.getElementById('constellation-tip');
        if (tip) tip.hidden = true;
    },

    _constellationDate(ts) {
        if (!ts) return '';
        try {
            const d = new Date(String(ts).replace(' ', 'T'));
            if (isNaN(d.getTime())) return String(ts).slice(0, 10);
            return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
        } catch (err) {
            return String(ts).slice(0, 10);
        }
    },

    // =========================================================================
    // BONDS SKY — the Qualia bond graph as constellations (Stars tab toggle).
    // Rides the SAME camera, gestures, labels, tip, and rAF loop as the
    // memories sky; only the data, layout (d3-force), and edges differ.
    // =========================================================================

    _constellationSetMode(mode) {
        this._constellationMode = mode;
        document.querySelectorAll('.constellation-mode-btn').forEach((b) => {
            b.classList.toggle('active', (b.dataset.sky === 'bonds' ? 'bonds' : 'memories') === mode);
        });
        const subtitle = document.getElementById('constellation-subtitle');
        if (subtitle) {
            subtitle.textContent = mode === 'bonds'
                ? 'every star is a soul we hold — every line a bond'
                : 'every star is a memory — tap one';
        }
        // A different sky: fresh camera, no stale selection or star arrays
        this._bondsStopSim();
        this._constellationHideTip();
        cancelAnimationFrame(this._constellationFly);
        this._constellationTransform = null;
        this._constellationUserMoved = false;
        this._constellationSX = null;
        this._constellationSY = null;
        this._constellationStars = [];
        this._constellationLabelOrder = [];
        this._bondsEdges = null;
        this._bondsPackIdx = null;
        this.loadConstellation();
    },

    async _bondsLoad() {
        const fresh = this._bondsData
            && (Date.now() - this._bondsAt) < 10 * 60 * 1000;
        let fetched = false;
        if (!fresh) {
            try {
                // The first build walks Qualia from every seed — give it room
                const data = await fetchJson('/api/hub/constellation/graph', { timeoutMs: 45000 });
                this._bondsData = (data && Array.isArray(data.nodes)) ? data : { nodes: [], edges: [] };
                this._bondsAt = Date.now();
                fetched = true;
            } catch (err) {
                // Keep whatever sky we already have; empty sky if none
                if (!this._bondsData) this._bondsData = { nodes: [], edges: [] };
            }
        }
        // Nothing new and a settled sky already up — don't reshuffle it
        if (!fetched && this._bondsEdges) return;
        this._bondsBuild();
    },

    _bondsStopSim() {
        if (this._bondsSettleRaf) {
            cancelAnimationFrame(this._bondsSettleRaf);
            this._bondsSettleRaf = 0;
        }
        if (this._bondsSim) {
            try { this._bondsSim.stop(); } catch (err) { /* fine */ }
            this._bondsSim = null;
        }
    },

    // Which palette family a soul belongs to (Friend's starKindOf, extended for
    // the qualia person_type vocabulary: ai / pack_member read as people).
    _bondsKindOf(kind) {
        const k = String(kind || '').toLowerCase();
        if (/(person|people|human|partner|family|friend|being|companion|pet|sibling|ai|pack)/.test(k)) return 'person';
        if (/(project|product|tool|build|work)/.test(k)) return 'project';
        if (/(place|location|home|room|city)/.test(k)) return 'place';
        return 'concept';
    },

    // Reduce a pack accent's saturation for the ordinary stars a boy holds —
    // his color, but quieter than his own star. Mixes each channel toward luma.
    _bondsSoften(hex) {
        const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || '').trim());
        if (!m) return this._constellationDefaultColor;
        const v = parseInt(m[1], 16);
        const r = (v >> 16) & 255, g = (v >> 8) & 255, b = v & 255;
        const luma = 0.299 * r + 0.587 * g + 0.114 * b;
        const mix = (c) => Math.round(c + (luma - c) * 0.45);
        return `rgb(${mix(r)}, ${mix(g)}, ${mix(b)})`;
    },

    // prepareSky (ported from Friend's skyData.ts): dedupe by id, degree over the
    // FULL edge set, radius = min(9, 1.6 + sqrt(deg) * 0.9), kind palette —
    // with the pack rendered as large named stars in their own accents.
    _bondsPrepareSky(data) {
        const rawNodes = Array.isArray(data && data.nodes) ? data.nodes : [];
        const rawEdges = Array.isArray(data && data.edges) ? data.edges : [];

        // Dedupe by id (first occurrence wins); drop id-less nodes.
        const seen = new Map();
        for (const n of rawNodes) {
            const id = String((n && n.id) || '').trim();
            if (!id || seen.has(id)) continue;
            seen.set(id, n);
        }

        // Degree over the FULL valid edge set — a star's brightness is its
        // true connectedness. Self-loops and dangling edges dropped.
        const degree = new Map();
        const validEdges = [];
        for (const e of rawEdges) {
            const from = String((e && e.from) || '');
            const to = String((e && e.to) || '');
            if (from === to || !seen.has(from) || !seen.has(to)) continue;
            validEdges.push({ from, to, type: e && e.type ? String(e.type) : '' });
            degree.set(from, (degree.get(from) || 0) + 1);
            degree.set(to, (degree.get(to) || 0) + 1);
        }

        const idToIndex = new Map();
        const stars = [];
        const packIdx = [];
        let i = 0;
        for (const [id, origin] of seen) {
            const name = String(origin.name || id);
            const nameKey = name.trim().toLowerCase();
            const heldBy = String(origin.held_by || '').trim().toLowerCase();
            const deg = degree.get(id) || 0;
            const packColor = this._bondsPackColors[nameKey];
            const isPack = !!packColor;
            const isOwner = nameKey === 'owner';
            const starKind = this._bondsKindOf(origin.kind);
            let color;
            if (isPack) {
                color = packColor;
            } else if (this._bondsPackColors[heldBy]) {
                color = this._bondsSoften(this._bondsPackColors[heldBy]);
            } else {
                color = this._bondsKindColors[starKind] || this._constellationDefaultColor;
            }
            const star = {
                id,
                name,
                kind: String(origin.kind || '') || starKind,
                starKind,
                heldBy,
                degree: deg,

                r: Math.min(9, 1.6 + Math.sqrt(deg) * 0.9) + (isPack ? (isOwner ? 4 : 3) : 0),
                color,
                isPack,
                phase: i * 2.4,
                label: name.length > 26 ? name.slice(0, 25) + '…' : name,
                search: `${name} ${origin.kind || ''} ${heldBy}`.toLowerCase(),
                match: true,
                nn: [], // adjacency as star indices — focus lighting reads this
            };
            idToIndex.set(id, i);
            stars.push(star);
            if (isPack) packIdx.push(i);
            i += 1;
        }

        // Links (for the sim), flat index pairs (for the batched edge draw),
        // adjacency (for focus lighting).
        const links = [];
        const edgeIdx = [];
        for (const e of validEdges) {
            const a = idToIndex.get(e.from);
            const b = idToIndex.get(e.to);
            links.push({ source: e.from, target: e.to, type: e.type });
            edgeIdx.push(a, b);
            stars[a].nn.push(b);
            stars[b].nn.push(a);
        }
        for (const s of stars) s.nn = [...new Set(s.nn)];

        return { stars, links, edgeIdx, packIdx };
    },

    _bondsBuild() {
        const sky = document.getElementById('constellation-sky');
        const canvas = document.getElementById('constellation-canvas');
        const empty = document.getElementById('constellation-empty');
        if (!sky || !canvas) return;
        this._bondsStopSim();

        const prepared = this._bondsPrepareSky(this._bondsData || {});
        const q = this._constellationQuery;
        prepared.stars.forEach((s) => { s.match = !q || s.search.includes(q); });

        this._constellationStars = prepared.stars;
        this._bondsEdges = prepared.edgeIdx;
        this._bondsPackIdx = prepared.packIdx;
        // Label budget order — brightest (biggest) stars get named first
        this._constellationLabelOrder = prepared.stars
            .map((s, i) => i)
            .sort((a, b) => prepared.stars[b].r - prepared.stars[a].r);
        this._constellationHideTip();
        this._constellationSX = null;
        this._constellationSY = null;

        if (empty) empty.hidden = prepared.stars.length !== 0;

        this._constellationWire();
        this._constellationResize();
        if (prepared.stars.length) this._bondsStartSim(prepared.stars, prepared.links);
        this._constellationCorner();
        this._constellationSyncLoop();
        this._constellationRepaint();
    },

    // Deterministic phyllotaxis — the sky still renders if d3-force is missing
    _bondsFallbackLayout(stars) {
        const golden = Math.PI * (3 - Math.sqrt(5));
        stars.forEach((s, i) => {
            const rad = 12 * Math.sqrt(i + 0.5);
            const ang = i * golden;
            s.x = rad * Math.cos(ang);
            s.y = rad * Math.sin(ang);
        });
    },

    _bondsStartSim(stars, links) {
        const d3f = window.d3;
        if (!d3f || typeof d3f.forceSimulation !== 'function') {
            this._bondsFallbackLayout(stars);
            this._constellationFitToView(true);
            return;
        }

        // Friend's exact sim config (ConstellationView.tsx)
        const sim = d3f.forceSimulation(stars)
            .force('link', d3f.forceLink(links)
                .id((d) => d.id)
                .distance(32)
                .strength((l) => 1 / Math.min(8, Math.max(1, Math.min(l.source.degree, l.target.degree)))))
            .force('charge', d3f.forceManyBody().strength(-42).theta(0.9).distanceMax(600))
            .force('x', d3f.forceX(0).strength(0.04))
            .force('y', d3f.forceY(0).strength(0.04))
            .force('collide', d3f.forceCollide((n) => n.r + 1.5).iterations(1))
            .alphaDecay(0.035);
        this._bondsSim = sim;
        // Frame the phyllotaxis seed so the settle is watchable
        this._constellationFitToView(true);

        if (this._constellationReducedMotion()) {
            // No drift on screen: settle offscreen in rAF batches (~40 ticks
            // each), then present one honest frozen frame (Friend's pattern).
            sim.stop();
            this._constellationRepaint(); // the seeded sky immediately — honest, not blank
            const settle = () => {
                let i = 0;
                while (sim.alpha() > sim.alphaMin() && i < 40) {
                    sim.tick();
                    i += 1;
                }
                if (sim.alpha() > sim.alphaMin()) {
                    this._bondsSettleRaf = requestAnimationFrame(settle);
                } else {
                    this._bondsSettleRaf = 0;
                    if (!this._constellationUserMoved) this._constellationFitToView(true);
                    this._constellationRepaint();
                }
            };
            this._bondsSettleRaf = requestAnimationFrame(settle);
            return;
        }

        // Normal motion: the sim drifts live — the tab's rAF loop presents
        // each frame; when it settles, gently re-frame (unless she moved).
        sim.on('end', () => {
            if (!this._constellationUserMoved) this._constellationFitToView();
            this._constellationRepaint();
        });
    },

    // =========================================================================
    // HELPERS
    // =========================================================================

    escapeHtml(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    },

    truncate(str, max) {
        if (!str || str.length <= max) return str;
        return str.substring(0, max) + '...';
    },
};

document.addEventListener('DOMContentLoaded', () => Hub.init());
