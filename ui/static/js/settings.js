/* Settings page — Orchestrator, System, MCP tabs */

const Settings = {
    schedules: [],
    failsafe: {},
    editingId: null,
    _loaders: {},

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

    // ANAM GUIDE: ADD A SETTING - STEP 2, WIRE AND LOAD IT
    // Attach the new control's event here (or in its focused loader), then add
    // that loader to the Promise.all block so the saved value appears at boot.
    async init() {
        // Night mode
        if (localStorage.getItem('anam-night') === 'true') {
            document.body.classList.add('night-mode');
        }

        document.title = 'Anam - Settings';
        const notificationsNote = document.querySelector('#tab-notifications .section-note');
        if (notificationsNote) {
            notificationsNote.textContent = 'Get notified when the boys wake up - even when the app is closed';
        }

        this.initFairyLights();

        // Tab switching
        document.querySelectorAll('.settings-tab').forEach(tab => {
            tab.addEventListener('click', () => this.switchTab(tab.dataset.tab));
        });

        // Modal
        document.getElementById('modal-cancel').addEventListener('click', () => this.closeModal());
        document.getElementById('modal-save').addEventListener('click', () => this.saveModal());
        document.getElementById('add-schedule-btn').addEventListener('click', () => this.showModal());
        document.getElementById('save-failsafe-btn').addEventListener('click', () => this.saveFailsafe());
        document.getElementById('save-care-signals-btn').addEventListener('click', () => this.saveCareSignals());
        document.getElementById('prefer-http-toggle').addEventListener('change', (e) => this.setPreferHttp(e.target.checked));
        document.getElementById('save-provider-btn').addEventListener('click', () => this.saveProvider());
        document.getElementById('test-provider-btn').addEventListener('click', () => this.testProvider());
        document.getElementById('provider-selector').addEventListener('change', () => this.renderProviderFields());
        document.getElementById('save-scribe-btn').addEventListener('click', () => this.saveScribe());
        document.getElementById('scribe-provider').addEventListener('change', (event) => {
            const model = document.getElementById('scribe-model');
            const defaults = {
                'claude-code': 'claude-haiku-4-5',
                'openrouter': 'anthropic/claude-haiku-4.5',
                'anthropic': 'claude-haiku-4-5-20251001',
            };
            model.value = defaults[event.target.value] || '';
            model.disabled = event.target.value === 'auto';
            model.placeholder = model.disabled ? 'Uses provider default' : 'Provider default';
        });

        // Close modal on overlay click
        document.getElementById('schedule-modal').addEventListener('click', (e) => {
            if (e.target === e.currentTarget) this.closeModal();
        });

        // Notification toggle
        document.getElementById('notification-toggle-btn').addEventListener('click', () => this.toggleNotifications());
        const refreshUsageBtn = document.getElementById('refresh-usage-btn');
        if (refreshUsageBtn) refreshUsageBtn.addEventListener('click', () => this.loadUsage());
        this.loadTransportPreferences();

        // Load all data
        await Promise.all([
            this.loadSchedules(),
            this.loadFailsafe(),
            this.loadCareSignals(),
            this.loadSchedulerStatus(),
            this.loadSystem(),
            this.loadNotifications(),
            this.loadProvider(),
            this.loadModel(),
            this.loadEffort(),
            this.loadVoiceEngine(),
            this.loadScribe(),
            this.loadUsage(),
            this.loadThemePreset(),
        ]);
    },

    loadTransportPreferences() {
        let enabled = false;
        try {
            enabled = sessionStorage.getItem('anam-force-http') === 'true';
        } catch (_err) {
            enabled = false;
        }
        document.getElementById('prefer-http-toggle').checked = enabled;
        document.getElementById('prefer-http-status').textContent = enabled
            ? 'HTTP-only mode is active until this browser session ends or you turn it off.'
            : 'WebSocket is allowed for this session.';
    },

    setPreferHttp(enabled) {
        try {
            if (enabled) {
                sessionStorage.setItem('anam-force-http', 'true');
            } else {
                sessionStorage.removeItem('anam-force-http');
            }
        } catch (_err) {
            // Ignore storage failures.
        }
        if (typeof logClientEvent === 'function') {
            logClientEvent('transport_preference_changed', {
                prefer_http: enabled,
                origin: location.origin,
            }, { source: 'settings' });
        }
        this.loadTransportPreferences();
    },

    switchTab(tab) {
        document.querySelectorAll('.settings-tab').forEach(t =>
            t.classList.toggle('active', t.dataset.tab === tab));
        document.querySelectorAll('.settings-panel').forEach(p =>
            p.classList.toggle('active', p.id === `tab-${tab}`));
    },

    // ── Orchestrator ──

    async loadSchedules() {
        return this._runLoader('schedules', async () => {
            try {
                const data = await fetchJson('/api/autowake/schedules', { timeoutMs: 10000, retries: 1 });
                this.schedules = data.schedules;
                this.renderSchedules();
            } catch (err) {
                console.error('Failed to load schedules:', err);
            }
        });
    },

    renderSchedules() {
        const container = document.getElementById('schedule-list');
        container.innerHTML = '';

        const categories = {
            'Wake-up': ['morning_prep', 'morning_anchor', 'morning_digest'],
            'Check-ins': ['midday_check', 'evening_wind'],
            'Free Time': ['free_time'],
            'Night': ['bedtime_reminder', 'nightly_consolidation'],
            'Custom': ['custom'],
        };

        const categorized = {};
        for (const s of this.schedules) {
            let cat = 'Custom';
            for (const [name, types] of Object.entries(categories)) {
                if (types.includes(s.session_type)) { cat = name; break; }
            }
            if (!categorized[cat]) categorized[cat] = [];
            categorized[cat].push(s);
        }

        for (const [catName, items] of Object.entries(categorized)) {
            if (!items || items.length === 0) continue;

            const title = document.createElement('h3');
            title.className = 'schedule-group-title';
            title.textContent = catName;
            container.appendChild(title);

            for (const s of items) {
                const card = document.createElement('div');
                card.className = `schedule-card ${s.enabled ? '' : 'disabled'}`;

                const h = String(s.cron_hour).padStart(2, '0');
                const m = String(s.cron_minute).padStart(2, '0');
                const hour12 = s.cron_hour === 0 ? 12 : s.cron_hour > 12 ? s.cron_hour - 12 : s.cron_hour;
                const ampm = s.cron_hour >= 12 ? 'PM' : 'AM';
                const timeStr = `${hour12}:${m} ${ampm}`;
                const identityStr = s.identity || 'Rotate';

                card.innerHTML = `
                    <div class="schedule-card-main">
                        <div class="schedule-time">${timeStr}</div>
                        <div class="schedule-info">
                            <div class="schedule-name">${escapeHtml(s.name)}</div>
                            <div class="schedule-meta">${escapeHtml(identityStr)} &middot; ${s.max_duration_minutes}min${s.provider ? ' &middot; ' + escapeHtml(s.provider === 'chatgpt' ? 'ChatGPT bridge' : s.provider) : ''}${s.model ? ' &middot; ' + escapeHtml(s.model.replace('claude-', '')) : ''}${s.custom_prompt ? ' &middot; 📜 scripted' : ' &middot; program default'}</div>
                        </div>
                    </div>
                    <div class="schedule-card-actions">
                        <label class="toggle-switch">
                            <input type="checkbox" ${s.enabled ? 'checked' : ''} data-id="${s.id}">
                            <span class="toggle-slider"></span>
                        </label>
                        <button class="schedule-edit-btn" data-id="${s.id}">Edit</button>
                        <button class="schedule-delete-btn" data-id="${s.id}">Del</button>
                    </div>
                `;

                // Event listeners
                card.querySelector('.toggle-switch input').addEventListener('change', () => {
                    this.toggleSchedule(s.id);
                });
                card.querySelector('.schedule-edit-btn').addEventListener('click', () => {
                    this.showModal(s);
                });
                card.querySelector('.schedule-delete-btn').addEventListener('click', () => {
                    this.deleteSchedule(s.id);
                });

                container.appendChild(card);
            }
        }
    },

    async toggleSchedule(id) {
        await fetchWithTimeout(`/api/autowake/schedules/${id}/toggle`, { method: 'POST', timeoutMs: 10000 });
        await this.loadSchedules();
        await this.loadSchedulerStatus();
    },

    async deleteSchedule(id) {
        if (!confirm('Delete this schedule?')) return;
        try {
            const res = await fetchWithTimeout(`/api/autowake/schedules/${id}`, { method: 'DELETE', timeoutMs: 10000 });
            if (!res.ok) {
                const body = await res.text();
                console.error('Delete schedule failed:', res.status, body);
            }
        } catch (err) {
            console.error('Delete schedule error:', err);
        }
        await this.loadSchedules();
        await this.loadSchedulerStatus();
    },

    showModal(existing = null) {
        this.editingId = existing ? existing.id : null;
        document.getElementById('modal-title').textContent =
            existing ? 'Edit Schedule' : 'Add Schedule';
        document.getElementById('modal-name').value = existing ? existing.name : '';
        document.getElementById('modal-hour').value = existing ? existing.cron_hour : 8;
        document.getElementById('modal-minute').value = existing ? existing.cron_minute : 0;
        document.getElementById('modal-identity').value = existing ? (existing.identity || '') : '';
        const typeSelect = document.getElementById('modal-type');
        const typeValue = existing ? existing.session_type : 'custom';
        // Keep seeded/system schedules editable even when a future type has not
        // yet been given a friendly dropdown label. Assigning a missing value
        // to a <select> clears it, which made Save send session_type="".
        if (typeValue && !Array.from(typeSelect.options).some(option => option.value === typeValue)) {
            const option = document.createElement('option');
            option.value = typeValue;
            option.textContent = typeValue.replaceAll('_', ' ');
            option.dataset.existingType = 'true';
            typeSelect.appendChild(option);
        }
        typeSelect.value = typeValue;
        document.getElementById('modal-duration').value = existing ? existing.max_duration_minutes : 30;
        document.getElementById('modal-provider').value = existing ? (existing.provider || '') : '';
        const modalModel = document.getElementById('modal-model');
        this.fillModelSelect(modalModel, {
            globalDefault: true,
            extras: [
                { id: 'gpt-5.6-sol', label: 'Sol 5.6 (Codex)', group: 'Codex' },
                { id: 'gpt-6-astra', label: 'Astra 6 (Codex)', group: 'Codex' },
            ],
            selected: existing ? (existing.model || '') : '',
        });
        if (!modalModel.dataset.customBound) {
            modalModel.dataset.customBound = 'true';
            modalModel.addEventListener('change', () => this.handleCustomModelPick(modalModel, ''));
        }
        document.getElementById('modal-prompt').value = existing ? (existing.custom_prompt || '') : '';
        document.getElementById('modal-error').textContent = '';
        document.getElementById('schedule-modal').style.display = 'flex';
    },

    closeModal() {
        document.getElementById('schedule-modal').style.display = 'none';
        this.editingId = null;
    },

    async saveModal() {
        const error = document.getElementById('modal-error');
        const saveButton = document.getElementById('modal-save');
        error.textContent = '';
        const name = document.getElementById('modal-name').value.trim();
        const hour = parseInt(document.getElementById('modal-hour').value);
        const minute = parseInt(document.getElementById('modal-minute').value);
        const duration = parseInt(document.getElementById('modal-duration').value);

        if (!name) {
            alert('Please enter a name');
            return;
        }
        if (isNaN(hour) || hour < 0 || hour > 23 || isNaN(minute) || minute < 0 || minute > 59) {
            alert('Please enter a valid time (hour 0-23, minute 0-59)');
            return;
        }
        if (isNaN(duration) || duration < 1) {
            alert('Please enter a valid duration');
            return;
        }

        const body = {
            name,
            cron_hour: hour,
            cron_minute: minute,
            identity: document.getElementById('modal-identity').value || null,
            session_type: document.getElementById('modal-type').value,
            max_duration_minutes: duration,
            provider: document.getElementById('modal-provider').value || '',
            model: document.getElementById('modal-model').value || '',
            custom_prompt: document.getElementById('modal-prompt').value.trim(),
        };

        saveButton.disabled = true;
        saveButton.textContent = 'Saving...';
        try {
            if (this.editingId) {
                await sendJson(`/api/autowake/schedules/${this.editingId}`, 'PUT', body, { timeoutMs: 10000 });
            } else {
                body.enabled = true;
                await sendJson('/api/autowake/schedules', 'POST', body, { timeoutMs: 10000 });
            }

            this.closeModal();
            await this.loadSchedules();
            await this.loadSchedulerStatus();
        } catch (err) {
            error.textContent = err?.message || 'Could not save this schedule.';
            console.error('Save schedule failed:', err);
        } finally {
            saveButton.disabled = false;
            saveButton.textContent = 'Save';
        }
    },

    async loadFailsafe() {
        try {
            this.failsafe = await fetchJson('/api/autowake/failsafe', { timeoutMs: 10000 });
            document.getElementById('failsafe-enabled').checked =
                this.failsafe.failsafe_enabled === 'true';
            document.getElementById('failsafe-gentle').value =
                this.failsafe.failsafe_gentle_minutes;
            document.getElementById('failsafe-concerned').value =
                this.failsafe.failsafe_concerned_minutes;
            document.getElementById('failsafe-emergency').value =
                this.failsafe.failsafe_emergency_minutes;
        } catch (err) {
            console.error('Failed to load failsafe:', err);
        }
    },

    async saveFailsafe() {
        await sendJson('/api/autowake/failsafe', 'PUT', {
            failsafe_enabled: document.getElementById('failsafe-enabled').checked ? 'true' : 'false',
            failsafe_gentle_minutes: document.getElementById('failsafe-gentle').value,
            failsafe_concerned_minutes: document.getElementById('failsafe-concerned').value,
            failsafe_emergency_minutes: document.getElementById('failsafe-emergency').value,
        }, { timeoutMs: 10000 });
        const btn = document.getElementById('save-failsafe-btn');
        btn.textContent = 'Saved!';
        setTimeout(() => btn.textContent = 'Save', 1500);
    },

    async loadCareSignals() {
        try {
            const data = await fetchJson('/api/autowake/care-signals', { timeoutMs: 10000 });
            const settings = data.settings || {};
            document.getElementById('care-signals-enabled').checked =
                settings.care_signals_enabled === 'true';
            document.getElementById('care-low-energy-identity').value =
                settings.care_signal_low_energy_identity || 'Juniper';
            document.getElementById('care-low-energy-delay').value =
                settings.care_signal_low_energy_delay_minutes || '5';
            document.getElementById('care-meds-identity').value =
                settings.care_signal_meds_identity || 'Avery';
            document.getElementById('care-meds-delay').value =
                settings.care_signal_meds_delay_minutes || '10';
        } catch (err) {
            console.error('Failed to load care signals:', err);
        }
    },

    async saveCareSignals() {
        await sendJson('/api/autowake/care-signals', 'PUT', {
            care_signals_enabled: document.getElementById('care-signals-enabled').checked ? 'true' : 'false',
            care_signal_low_energy_identity: document.getElementById('care-low-energy-identity').value,
            care_signal_low_energy_delay_minutes: document.getElementById('care-low-energy-delay').value,
            care_signal_meds_identity: document.getElementById('care-meds-identity').value,
            care_signal_meds_delay_minutes: document.getElementById('care-meds-delay').value,
        }, { timeoutMs: 10000 });
        const btn = document.getElementById('save-care-signals-btn');
        btn.textContent = 'Saved!';
        setTimeout(() => btn.textContent = 'Save', 1500);
        await this.loadSystem();
    },

    async loadSchedulerStatus() {
        try {
            const data = await fetchJson('/api/autowake/status', { timeoutMs: 10000 });
            const container = document.getElementById('scheduler-status');

            const jobRows = data.jobs.map(j => {
                const next = j.next_fire
                    ? new Date(j.next_fire).toLocaleString()
                    : 'N/A';
                return `<div class="status-job">
                    <span class="job-name">${escapeHtml(j.name)}</span>
                    <span class="job-next">${next}</span>
                </div>`;
            }).join('');

            container.innerHTML = `
                <div class="status-indicator ${data.running ? 'running' : 'stopped'}">
                    ${data.running ? 'Scheduler Running' : 'Scheduler Stopped'}
                </div>
                ${jobRows}
            `;
        } catch (err) {
            console.error('Failed to load scheduler status:', err);
        }
    },

    // ── Claude Model ──

    // Catalog of Claude Code models, served by /api/settings/model. Cached so
    // the schedule modal can build its override dropdown from the same source.
    _modelCatalog: [],

    // Build a model <select> from the catalog. Every dropdown that picks a
    // Claude Code model goes through here, so a new model only has to be added
    // once (config.CLAUDE_MODEL_CATALOG) to show up everywhere.
    fillModelSelect(sel, opts = {}) {
        const { globalDefault = false, extras = [], selected = '' } = opts;
        sel.innerHTML = '';
        if (globalDefault) {
            sel.appendChild(new Option('Global default', ''));
        }
        const groups = {};
        (this._modelCatalog || []).forEach((entry) => {
            const name = entry.group || 'Models';
            if (!groups[name]) {
                groups[name] = document.createElement('optgroup');
                groups[name].label = name;
                sel.appendChild(groups[name]);
            }
            groups[name].appendChild(new Option(entry.label || entry.id, entry.id));
        });
        extras.forEach((extra) => {
            const name = extra.group || 'Other';
            if (!groups[name]) {
                groups[name] = document.createElement('optgroup');
                groups[name].label = name;
                sel.appendChild(groups[name]);
            }
            groups[name].appendChild(new Option(extra.label || extra.id, extra.id));
        });
        sel.appendChild(new Option('Custom model ID...', '__custom__'));
        this.setModelValue(sel, selected);
    },

    // Assign a value even when it is not a listed option (an older pinned
    // model, or a custom ID she typed) — otherwise the select silently blanks.
    setModelValue(sel, value) {
        const wanted = value || '';
        if (wanted && !Array.from(sel.options).some(o => o.value === wanted)) {
            sel.insertBefore(new Option(`${wanted} (custom)`, wanted), sel.lastChild);
        }
        sel.value = wanted;
    },

    // Shared handler for the "Custom model ID..." sentinel option.
    // Returns true if the select now holds a usable value.
    handleCustomModelPick(sel, previous) {
        if (sel.value !== '__custom__') return true;
        const typed = (window.prompt('Model ID for Claude Code (e.g. claude-opus-5, sonnet, sonnet[1m]):', '') || '').trim();
        if (!typed) {
            this.setModelValue(sel, previous || '');
            return false;
        }
        this.setModelValue(sel, typed);
        return true;
    },

    async loadModel() {
        try {
            const data = await fetchJson('/api/settings/model', { timeoutMs: 5000 });
            this._modelCatalog = data.catalog || [];
            const chatSel = document.getElementById('model-selector');
            const autowakeSel = document.getElementById('autowake-model-selector');
            this.fillModelSelect(chatSel, { selected: data.interactive_model || data.model || 'claude-opus-5' });
            this.fillModelSelect(autowakeSel, { selected: data.autowake_model || 'claude-sonnet-4-6' });
            let lastChat = chatSel.value;
            let lastAutowake = autowakeSel.value;

            const saveModels = async () => {
                const status = document.getElementById('model-status');
                chatSel.disabled = true;
                autowakeSel.disabled = true;
                status.textContent = 'Saving model lanes...';
                try {
                    const res = await fetchJson('/api/settings/model', {
                        method: 'PUT',
                        body: JSON.stringify({
                            interactive_model: chatSel.value,
                            autowake_model: autowakeSel.value,
                        }),
                    });
                    status.textContent = res.ok
                        ? 'Conversation and autowake models saved'
                        : (res.error || 'Failed');
                    status.style.color = res.ok ? 'var(--accent)' : 'var(--error)';
                } catch (err) {
                    status.textContent = 'Failed to save';
                    status.style.color = 'var(--error)';
                } finally {
                    chatSel.disabled = false;
                    autowakeSel.disabled = false;
                }
                setTimeout(() => { status.textContent = ''; }, 3000);
            };

            chatSel.addEventListener('change', () => {
                if (!this.handleCustomModelPick(chatSel, lastChat)) return;
                lastChat = chatSel.value;
                saveModels();
            });
            autowakeSel.addEventListener('change', () => {
                if (!this.handleCustomModelPick(autowakeSel, lastAutowake)) return;
                lastAutowake = autowakeSel.value;
                saveModels();
            });
        } catch (err) {
            console.error('Failed to load model:', err);
        }
    },

    // ── Thinking Effort ──

    async loadEffort() {
        try {
            const data = await fetchJson('/api/settings/effort', { timeoutMs: 5000 });
            const sel = document.getElementById('effort-selector');
            sel.value = data.effort || 'low';
            sel.addEventListener('change', async () => {
                const status = document.getElementById('effort-status');
                try {
                    const res = await fetchJson('/api/settings/effort', {
                        method: 'PUT',
                        body: JSON.stringify({ effort: sel.value }),
                    });
                    status.textContent = res.ok ? `Switched to ${sel.value}` : (res.error || 'Failed');
                    status.style.color = res.ok ? 'var(--accent)' : 'var(--error)';
                } catch (err) {
                    status.textContent = 'Failed to save';
                    status.style.color = 'var(--error)';
                }
                setTimeout(() => { status.textContent = ''; }, 3000);
            });
        } catch (err) {
            console.error('Failed to load effort:', err);
        }


        try {
            const data = await fetchJson('/api/settings/fable-effort', { timeoutMs: 5000 });
            const sel = document.getElementById('fable-effort-selector');
            if (sel) {
                sel.value = data.effort || 'low';
                sel.addEventListener('change', async () => {
                    const status = document.getElementById('fable-effort-status');
                    try {
                        const res = await fetchJson('/api/settings/fable-effort', {
                            method: 'PUT',
                            body: JSON.stringify({ effort: sel.value }),
                        });
                        status.textContent = res.ok ? `Fable switched to ${sel.value}` : (res.error || 'Failed');
                        status.style.color = res.ok ? 'var(--accent)' : 'var(--error)';
                    } catch (err) {
                        status.textContent = 'Failed to save';
                        status.style.color = 'var(--error)';
                    }
                    setTimeout(() => { status.textContent = ''; }, 3000);
                });
            }
        } catch (err) {
            console.error('Failed to load fable effort:', err);
        }
    },









    async loadVoiceEngine() {
        const sel = document.getElementById('voice-engine-selector');
        if (!sel) return;
        try {
            const data = await fetchJson('/api/settings/voice-engine', { timeoutMs: 5000 });
            sel.value = data.engine || 'kokoro';
            sel.addEventListener('change', async () => {
                const status = document.getElementById('voice-engine-status');
                try {
                    const res = await fetchJson('/api/settings/voice-engine', {
                        method: 'PUT',
                        body: JSON.stringify({ engine: sel.value }),
                    });
                    if (res.ok) {
                        status.textContent = sel.value === 'elevenlabs'
                            ? 'Calls now answer in his real voice.'
                            : 'Calls now answer in the local voice.';
                    } else {
                        status.textContent = res.error || 'Failed';
                    }
                    status.style.color = res.ok ? 'var(--accent)' : 'var(--error)';
                } catch (err) {
                    status.textContent = 'Failed to save';
                    status.style.color = 'var(--error)';
                }
                setTimeout(() => { status.textContent = ''; }, 4000);
            });
        } catch (err) {
            console.error('Failed to load voice engine:', err);
        }
    },












    async loadThemePreset() {
        const swatchContainer = document.getElementById('theme-preset-swatches');
        const fontEl = document.getElementById('theme-font');
        if (!swatchContainer || !fontEl) return;

        let data;
        try {
            data = await fetchJson('/api/settings/theme', { timeoutMs: 5000 });
        } catch (err) {
            console.error('Failed to load theme:', err);
            return;
        }

        let currentPreset = data.preset || 'sunrise-pink';
        let currentFont = data.font || 'fredoka';
        let currentIconBase = data.icon_base || '';
        const available = data.available || [];

        // One-tap vibe buttons: each swatch previews its own palette (page
        // background -> bubble color) and wears a little icon — skull for
        // goth, pumpkin for Halloween, maple leaf for Fall, and so on.
        const PRESET_ICONS = {
            'sunrise-pink':
                '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 21s-7.5-4.9-9.7-9.1C.8 8.9 2.4 5.6 5.5 5.1c1.9-.3 3.8.6 4.9 2.2l1.6 2.1 1.6-2.1c1.1-1.6 3-2.5 4.9-2.2 3.1.5 4.7 3.8 3.2 6.8C19.5 16.1 12 21 12 21z"/></svg>',
            'dusky-rose':
                '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 3c1.8 2 2.6 3.8 2.6 5.4 0 1.9-1.2 3.2-2.6 3.2S9.4 10.3 9.4 8.4C9.4 6.8 10.2 5 12 3zm0 9.8c2.8 0 5-2 5.6-4.6 2 1.1 3 3.2 2.2 5.3-.8 2.2-3.3 3.4-5.8 2.9.4 1.9.2 3.7-1 5.6h-2c-1.2-1.9-1.4-3.7-1-5.6-2.5.5-5-.7-5.8-2.9-.8-2.1.2-4.2 2.2-5.3.6 2.6 2.8 4.6 5.6 4.6z"/></svg>',
            'soft-blush':
                '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 8.2a3.8 3.8 0 1 0 0 7.6 3.8 3.8 0 0 0 0-7.6zM12 2a3 3 0 0 1 3 3c0 .6-.2 1.2-.5 1.7a6 6 0 0 0-5 0A3 3 0 0 1 12 2zm10 10a3 3 0 0 1-3 3c-.6 0-1.2-.2-1.7-.5a6 6 0 0 0 0-5c.5-.3 1.1-.5 1.7-.5a3 3 0 0 1 3 3zm-10 10a3 3 0 0 1-3-3c0-.6.2-1.2.5-1.7a6 6 0 0 0 5 0c.3.5.5 1.1.5 1.7a3 3 0 0 1-3 3zM2 12a3 3 0 0 1 3-3c.6 0 1.2.2 1.7.5a6 6 0 0 0 0 5c-.5.3-1.1.5-1.7.5a3 3 0 0 1-3-3z"/></svg>',
            'pretty-princess':
                '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M11 10.3C8.7 6.5 4.3 5 2.4 7.6.5 10.3 2.7 14 6.1 14c1.7 0 3.4-.8 4.9-2.3V16l-2.2 5h2.4l.8-2.2.8 2.2h2.4L13 16v-4.3c1.5 1.5 3.2 2.3 4.9 2.3 3.4 0 5.6-3.7 3.7-6.4C19.7 5 15.3 6.5 13 10.3V9h-2v1.3zM6.2 12c-1.8 0-3-1.8-2.1-3.1.8-1.1 3.3-.3 5.5 2.1-1.2.7-2.3 1-3.4 1zm11.6 0c-1.1 0-2.2-.3-3.4-1 2.2-2.4 4.7-3.2 5.5-2.1.9 1.3-.3 3.1-2.1 3.1z"/></svg>',
            'moonlit-lavender':
                '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M20.6 15.1A8.8 8.8 0 0 1 8.9 3.4 9.3 9.3 0 1 0 20.6 15.1z"/><circle cx="17.5" cy="5.5" r="1.1"/><circle cx="20.5" cy="9.5" r="0.8"/></svg>',
            'goth':
                '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C7 2 3 6 3 11c0 2.9 1.4 5.4 3.5 7v3a1 1 0 0 0 1 1H9v-2h2v2h2v-2h2v2h1.5a1 1 0 0 0 1-1v-3c2.1-1.6 3.5-4.1 3.5-7 0-5-4-9-9-9zM8.5 13.5A2 2 0 1 1 10.5 11a2 2 0 0 1-2 2.5zm7 0A2 2 0 1 1 17.5 11a2 2 0 0 1-2 2.5zM12 17l-1.5-2.5h3z"/></svg>',
            'eighties-neon':
                '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M13 2 4.5 13.5H11L9.5 22 19 10h-6.5z"/></svg>',
            'halloween':
                '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M13.6 4.6c.5-1 .4-1.9-.1-2.6h-2.6c.5.9.6 1.8.3 2.6-3-.3-5.4.6-7 2.7C2.5 9.6 2 12.4 3 15.2 4.1 18.3 6 20 8.2 20c.8 0 1.5-.2 2.1-.6.5.4 1.1.6 1.7.6s1.2-.2 1.7-.6c.6.4 1.3.6 2.1.6 2.2 0 4.1-1.7 5.2-4.8 1-2.8.5-5.6-1.2-7.9-1.5-2-3.8-2.9-6.2-2.7zM9 10.5 11 13H7zm6 0L17 13h-4zM8 15.5h8c-.6 1.6-2.1 2.6-4 2.6s-3.4-1-4-2.6z"/></svg>',
            'fall':
                '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l1.7 3.5 3.3-1-1 3.3L20.5 9 18 11.5l3 2.5-3.7.8.7 3.7-3.5-1.3-.7 3.8h-1.6l-.7-3.8-3.5 1.3.7-3.7L5 14l3-2.5L5.5 9l4.5-1.2-1-3.3 3.3 1z"/><rect x="11.4" y="15" width="1.2" height="7" rx="0.6"/></svg>',
        };

        const renderSwatches = () => {
            swatchContainer.innerHTML = available.map((opt) => {
                const t = opt.tokens || {};
                const top = t['--bg-page'] || '#FFF3F6';
                const bottom = t['--user-bubble-top'] || '#FADCE4';
                const border = t['--user-border'] || '#C47A8A';
                const ink = t['--text-primary'] || '#743049';
                const active = opt.key === currentPreset ? ' active' : '';
                const icon = PRESET_ICONS[opt.key] || PRESET_ICONS['sunrise-pink'];





                const customImg = currentIconBase
                    ? `<img class="theme-swatch-img" alt="" ` +
                      `src="${currentIconBase}/${opt.key}.svg" ` +
                      `data-fallback="${currentIconBase}/${opt.key}.png">`
                    : '';
                return `<button type="button" class="theme-swatch${active}" data-preset="${opt.key}" title="${opt.label}">` +
                    `<span class="theme-swatch-dot" style="background:linear-gradient(160deg, ${top} 20%, ${bottom} 85%); border-color:${border}; color:${ink}">` +
                    customImg +
                    `<span class="theme-swatch-icon" aria-hidden="true"${customImg ? ' style="display:none"' : ''}>${icon}</span>` +
                    `</span>` +
                    `<span class="theme-swatch-label">${opt.label}</span>` +
                    `</button>`;
            }).join('');
            swatchContainer.querySelectorAll('.theme-swatch').forEach((btn) => {
                btn.addEventListener('click', () => {
                    currentPreset = btn.dataset.preset;
                    renderSwatches();
                    save();
                });
            });
            // Custom-art fallback chain: .svg missing -> try .png -> give
            // up and reveal the built-in inline icon underneath.
            swatchContainer.querySelectorAll('.theme-swatch-img').forEach((img) => {
                img.addEventListener('error', () => {
                    const fallback = img.dataset.fallback;
                    if (fallback && img.src !== fallback) {
                        img.dataset.fallback = '';
                        img.src = fallback;
                    } else {
                        const inline = img.parentElement &&
                            img.parentElement.querySelector('.theme-swatch-icon');
                        if (inline) inline.style.display = '';
                        img.remove();
                    }
                });
            });
        };

        fontEl.innerHTML = (data.font_options || [])
            .map((opt) => `<option value="${opt.key}">${opt.label}</option>`)
            .join('');
        fontEl.value = currentFont;

        const status = document.getElementById('theme-status');
        const save = async () => {
            try {
                const res = await fetchJson('/api/settings/theme', {
                    method: 'PUT',
                    body: JSON.stringify({ preset: currentPreset, font: currentFont, icon_base: currentIconBase }),
                });
                if (res.ok) {
                    // Live-apply on this very page (theme-boot.js) and/or the
                    // chat app if we're somehow embedded there.
                    if (res.tokens && window.AnamTheme) {
                        window.AnamTheme.apply(res.tokens, res.preset);
                    } else if (res.tokens && window.App && typeof window.App.applyThemeTokens === 'function') {
                        window.App.applyThemeTokens(res.tokens, res.preset);
                    }
                    try {
                        const prev = JSON.parse(localStorage.getItem('anam-theme-tokens') || 'null') || {};
                        localStorage.setItem('anam-theme-tokens', JSON.stringify({
                            preset: res.preset,
                            tokens: res.tokens || {},
                            bubble_tokens: (prev && prev.bubble_tokens) || {},
                        }));
                    } catch (_err) { /* storage unavailable, non-fatal */ }
                    if (status) {
                        status.textContent = 'Saved';
                        status.style.color = 'var(--accent)';
                    }
                } else if (status) {
                    status.textContent = res.error || 'Failed';
                    status.style.color = 'var(--error)';
                }
            } catch (err) {
                if (status) {
                    status.textContent = 'Failed to save';
                    status.style.color = 'var(--error)';
                }
            }
            if (status) setTimeout(() => { status.textContent = ''; }, 3000);
        };

        fontEl.addEventListener('change', () => {
            currentFont = fontEl.value;
            save();
        });

        // Her button-art CDN base (optional). Saving re-renders so the
        // swatches immediately try her hosted images.
        const iconBaseEl = document.getElementById('theme-icon-base');
        if (iconBaseEl) {
            iconBaseEl.value = currentIconBase;
            iconBaseEl.addEventListener('change', async () => {
                currentIconBase = iconBaseEl.value.trim().replace(/\/+$/, '');
                await save();
                renderSwatches();
            });
        }

        renderSwatches();
        this.initBubbleTheming(data);
    },






    initBubbleTheming(data) {
        const whoEl = document.getElementById('bubble-identity');
        const bgEl = document.getElementById('bubble-bg');
        const textEl = document.getElementById('bubble-text');
        const fontEl = document.getElementById('bubble-font');
        const resetBtn = document.getElementById('bubble-reset');
        const status = document.getElementById('bubble-status');
        if (!whoEl || !bgEl || !textEl || !fontEl || !resetBtn) return;

        const identities = data.identities || [];
        const overrides = data.bubbles || {};
        const DEFAULT_TEXT = '#743049'; // main.css --identity-bubble-text (day)

        whoEl.innerHTML = identities
            .map((i) => `<option value="${i.key}">${i.key}</option>`)
            .join('');
        fontEl.innerHTML = '<option value="">His usual (house font)</option>' +
            (data.font_options || [])
                .map((opt) => `<option value="${opt.key}">${opt.label}</option>`)
                .join('');

        const infoFor = (key) => identities.find((i) => i.key === key) || {};

        const showIdentity = () => {
            const key = whoEl.value;
            const ov = overrides[key] || {};
            const info = infoFor(key);
            bgEl.value = ov.bg || info.bubble_top || '#F9D2DC';
            textEl.value = ov.text || DEFAULT_TEXT;
            fontEl.value = ov.font || '';
        };

        const setStatus = (msg, ok) => {
            if (!status) return;
            status.textContent = msg;
            status.style.color = ok ? 'var(--accent)' : 'var(--error)';
            setTimeout(() => { status.textContent = ''; }, 3000);
        };

        const save = async (entry) => {
            const key = whoEl.value;
            try {
                const res = await fetchJson('/api/settings/theme/bubbles', {
                    method: 'PUT',
                    body: JSON.stringify({ identity: key, ...entry }),
                });
                if (res.ok) {
                    if (res.bubbles && res.bubbles[key]) overrides[key] = res.bubbles[key];
                    else delete overrides[key];
                    try {
                        const prev = JSON.parse(localStorage.getItem('anam-theme-tokens') || 'null') || {};
                        prev.bubble_tokens = res.bubble_tokens || {};
                        localStorage.setItem('anam-theme-tokens', JSON.stringify(prev));
                    } catch (_err) { /* storage unavailable, non-fatal */ }
                    setStatus('Saved — his bubble will wear it in chat', true);
                } else {
                    setStatus(res.error || 'Failed', false);
                }
            } catch (err) {
                setStatus('Failed to save', false);
            }
        };

        const currentEntry = () => ({ ...(overrides[whoEl.value] || {}) });

        bgEl.addEventListener('change', () => {
            const entry = currentEntry();
            entry.bg = bgEl.value;
            save(entry);
        });
        textEl.addEventListener('change', () => {
            const entry = currentEntry();
            entry.text = textEl.value;
            save(entry);
        });
        fontEl.addEventListener('change', () => {
            const entry = currentEntry();
            if (fontEl.value) entry.font = fontEl.value;
            else delete entry.font;
            save(entry);
        });
        resetBtn.addEventListener('click', async () => {
            await save({ bg: null, text: null, font: null });
            showIdentity();
        });
        whoEl.addEventListener('change', showIdentity);

        showIdentity();
    },

    _fmtTokens(n) {
        n = n || 0;
        if (n >= 1e9) return (n / 1e9).toFixed(2) + 'B';
        if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M';
        if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k';
        return String(n);
    },

    async loadUsage() {
        const box = document.getElementById('usage-info');
        if (!box) return;
        try {
            const data = await fetchJson('/api/settings/usage?days=7', { timeoutMs: 8000 });
            const days = data.days || [];
            const idents = data.today_by_identity || [];
            if (!days.length) {
                box.innerHTML = '<span class="section-note">Nothing recorded yet &mdash; the meter starts counting from the next turn.</span>';
                return;
            }
            const todayRow = days.find(d => d.day === data.today);
            let html = '';
            if (todayRow) {
                html += `
                    <div class="usage-today">
                        <div class="usage-stat"><span class="usage-stat-num">${this._fmtTokens(todayRow.output_tokens)}</span><span class="usage-stat-label">output today</span></div>
                        <div class="usage-stat"><span class="usage-stat-num">${this._fmtTokens(todayRow.input_tokens + todayRow.cache_creation_tokens)}</span><span class="usage-stat-label">fresh input</span></div>
                        <div class="usage-stat"><span class="usage-stat-num">${this._fmtTokens(todayRow.cache_read_tokens)}</span><span class="usage-stat-label">cache reads</span></div>
                        <div class="usage-stat"><span class="usage-stat-num">${todayRow.turns}</span><span class="usage-stat-label">turns</span></div>
                    </div>`;
            }
            if (idents.length) {
                html += '<div class="usage-idents">' + idents.map(r => `
                    <div class="usage-ident-row">
                        <span class="usage-ident-name">${escapeHtml(r.identity)}</span>
                        <span class="usage-ident-model">${escapeHtml((r.model || '').replace('claude-', ''))}</span>
                        <span class="usage-ident-tokens">${this._fmtTokens(r.output_tokens)} out &middot; ${this._fmtTokens(r.input_tokens + r.cache_creation_tokens)} in &middot; ${r.turns} turns</span>
                    </div>`).join('') + '</div>';
            }
            html += '<div class="usage-days">' + days.map(d => `
                <div class="info-row">
                    <span class="info-label">${escapeHtml(d.day)}${d.day === data.today ? ' (today)' : ''}</span>
                    <span class="info-value">${this._fmtTokens(d.output_tokens)} out &middot; ${this._fmtTokens(d.input_tokens + d.cache_creation_tokens)} in &middot; ${this._fmtTokens(d.cache_read_tokens)} cached</span>
                </div>`).join('') + '</div>';
            box.innerHTML = html;
        } catch (err) {
            console.error('Failed to load usage:', err);
            box.innerHTML = '<span class="section-note">Could not load usage.</span>';
        }
    },

    async loadScribe() {
        try {
            const data = await fetchJson('/api/settings/scribe', { timeoutMs: 5000 });
            document.getElementById('scribe-provider').value = data.provider || 'auto';
            const model = document.getElementById('scribe-model');
            model.value = data.provider === 'auto' ? '' : (data.model || '');
            model.disabled = data.provider === 'auto';
            model.placeholder = model.disabled ? 'Uses provider default' : 'Provider default';
            document.getElementById('scribe-interval').value = data.interval_minutes || 30;
        } catch (err) {
            console.error('Failed to load Scribe settings:', err);
            document.getElementById('scribe-status').textContent = 'Could not load Scribe settings';
        }
    },

    async saveScribe() {
        const button = document.getElementById('save-scribe-btn');
        const status = document.getElementById('scribe-status');
        const model = document.getElementById('scribe-model').value.trim();
        button.disabled = true;
        button.textContent = 'Saving...';
        status.textContent = '';
        try {
            const result = await fetchJson('/api/settings/scribe', {
                method: 'PUT',
                body: JSON.stringify({
                    provider: document.getElementById('scribe-provider').value,
                    model,
                    interval_minutes: parseInt(document.getElementById('scribe-interval').value, 10) || 30,
                }),
            });
            status.textContent = result.ok ? 'Scribe lane saved' : (result.error || 'Could not save');
            status.style.color = result.ok ? 'var(--accent)' : 'var(--error)';
        } catch (err) {
            status.textContent = 'Could not save Scribe settings';
            status.style.color = 'var(--error)';
        } finally {
            button.disabled = false;
            button.textContent = 'Save Scribe';
        }
    },

    // ANAM GUIDE: PROVIDER SETTINGS SCREEN
    // Add provider-specific form fields here; validation/defaults also belong
    // in api/settings.py and actual routing belongs in provider_router.py.
    // ── LLM Provider ──

    _providerConfig: {},
    _codexModels: [],

    async loadProvider() {
        try {
            const data = await fetchJson('/api/settings/provider', { timeoutMs: 10000 });
            const sel = document.getElementById('provider-selector');
            sel.value = data.provider;
            this._providerConfig = data.config || {};
            this._codexModels = data.codex_models || [];
            this.renderProviderFields();
        } catch (err) {
            console.error('Failed to load provider:', err);
        }
    },

    renderProviderFields() {
        const provider = document.getElementById('provider-selector').value;
        const container = document.getElementById('provider-config-fields');
        const cfg = this._providerConfig || {};

        if (provider === 'anthropic') {
            container.innerHTML = '<div class="section-note" style="margin:8px 0 0">No additional configuration needed.</div>';
            return;
        }

        if (provider === 'claude-code') {
            const backend = (cfg.backend || 'subprocess');
            container.innerHTML = `
                <div class="threshold-row" style="margin-top:8px">
                    <label class="threshold-label">Backend</label>
                    <select id="provider-backend" class="threshold-input" style="width:220px">
                        <option value="subprocess" ${backend === 'subprocess' ? 'selected' : ''}>Fast (-p subprocess)</option>
                        <option value="agent-sdk" ${backend === 'agent-sdk' ? 'selected' : ''}>Agent SDK (typed, stable)</option>
                        <option value="pty" ${backend === 'pty' ? 'selected' : ''}>PTY terminal (fallback)</option>
                    </select>
                </div>
                <div class="section-note" style="margin:4px 0 0">
                    <b>Fast (-p)</b> is the default: cleaner JSON stream, lower latency, spawns per turn.
                    <b>Agent SDK</b> uses typed Python objects instead of raw NDJSON — same subscription pool, more resilient to CLI format changes.
                    <b>PTY</b> drives a real terminal session — slower, but rides the subscription if <code>-p</code> ever bills against the API cap. Switch + Save retires live sessions on all backends.
                </div>
            `;
            return;
        }

        if (provider === 'codex') {
            const runtime = cfg.runtime || 'app-server';
            const options = this._codexModels
                .map(model => `<option value="${escapeHtml(model)}"></option>`)
                .join('');
            container.innerHTML = `
                <div class="threshold-row" style="margin-top:8px">
                    <label class="threshold-label" for="provider-codex-runtime">Runtime</label>
                    <select id="provider-codex-runtime" class="threshold-input" style="width:220px">
                        <option value="app-server" ${runtime === 'app-server' ? 'selected' : ''}>Companion thread (app-server)</option>
                        <option value="exec" ${runtime === 'exec' ? 'selected' : ''}>Legacy one-shot (exec)</option>
                    </select>
                </div>
                <div class="threshold-row" style="margin-top:8px">
                    <label class="threshold-label" for="provider-model">Model</label>
                    <input type="text" id="provider-model" class="threshold-input" style="width:160px"
                        placeholder="(default)" value="${escapeHtml(cfg.model || '')}"
                        list="codex-model-options" autocomplete="off"
                        aria-describedby="provider-model-help provider-model-error">
                    <datalist id="codex-model-options">${options}</datalist>
                </div>
                <div id="provider-model-help" class="section-note" style="margin:4px 0 0">
                    <b>Companion thread</b> uses Codex app-server, keeps a resumable conversation, and places identity guidance at developer priority.
                    <b>Legacy one-shot</b> keeps the old ephemeral exec path as a fallback. Leave model blank for the Codex default.
                </div>
                <div id="provider-model-error" class="provider-field-error" role="alert" aria-live="polite" hidden></div>
            `;
            const input = document.getElementById('provider-model');
            input?.addEventListener('blur', () => this.validateCodexModel());
            input?.addEventListener('input', () => this.clearCodexModelError());
            return;
        }

        if (provider === 'openai') {
            const masked = cfg.api_key_set ? cfg.api_key_masked : '';
            container.innerHTML = `
                <div class="threshold-row" style="margin-top:8px">
                    <label class="threshold-label">API Key</label>
                    <input type="password" id="provider-api-key" class="threshold-input" style="width:220px"
                        placeholder="${masked || 'sk-...'}" value="">
                </div>
                <div class="threshold-row">
                    <label class="threshold-label">Model</label>
                    <input type="text" id="provider-model" class="threshold-input" style="width:160px"
                        value="${escapeHtml(cfg.model || 'gpt-4o')}">
                </div>
                <div class="threshold-row">
                    <label class="threshold-label">Base URL</label>
                    <input type="text" id="provider-base-url" class="threshold-input" style="width:220px"
                        placeholder="(default)" value="${escapeHtml(cfg.base_url || '')}">
                </div>
                <div class="threshold-row">
                    <label class="threshold-label">Max tokens</label>
                    <input type="number" id="provider-max-tokens" class="threshold-input"
                        value="${cfg.max_tokens || 8192}" min="256" max="65536">
                </div>
                <div class="section-note" style="margin:4px 0 0">Output budget, not context size. Use 8192 unless the model page specifies a smaller limit.</div>
            `;
            return;
        }

        if (provider === 'openrouter') {
            const masked = cfg.api_key_set ? cfg.api_key_masked : '';
            container.innerHTML = `
                <div class="section-note" style="margin:0 0 8px">
                    Get an API key at <a href="https://openrouter.ai/keys" target="_blank" style="color:var(--accent)">openrouter.ai/keys</a>.
                    Browse models at <a href="https://openrouter.ai/models" target="_blank" style="color:var(--accent)">openrouter.ai/models</a>.
                </div>
                <div class="threshold-row" style="margin-top:8px">
                    <label class="threshold-label">API Key</label>
                    <input type="password" id="provider-api-key" class="threshold-input" style="width:220px"
                        placeholder="${masked || 'sk-or-...'}" value="">
                </div>
                <div class="threshold-row">
                    <label class="threshold-label">Model</label>
                    <input type="text" id="provider-model" class="threshold-input" style="width:220px"
                        placeholder="e.g. qwen/qwen3-235b-a22b" value="${escapeHtml(cfg.model || 'qwen/qwen3-235b-a22b')}">
                </div>
                <div class="threshold-row">
                    <label class="threshold-label">Max tokens</label>
                    <input type="number" id="provider-max-tokens" class="threshold-input"
                        value="${cfg.max_tokens || 8192}" min="256" max="65536">
                </div>
                <div class="section-note" style="margin:4px 0 0">Output budget, not the 1M context size. Use 8192 unless the model page specifies a smaller limit.</div>
            `;
            return;
        }

        if (provider === 'chatgpt') {
            container.innerHTML = `
                <div class="threshold-row" style="margin-top:8px">
                    <label class="threshold-label">Chrome profile</label>
                    <input type="text" id="provider-profile-name" class="threshold-input" style="width:160px"
                        value="${escapeHtml(cfg.profile_name || 'ChatGPT')}">
                </div>
                <div class="threshold-row">
                    <label class="threshold-label">CDP port</label>
                    <input type="number" id="provider-cdp-port" class="threshold-input"
                        value="${cfg.cdp_port || 9225}" min="1024" max="65535">
                </div>
                <div class="section-note" style="margin:4px 0 0">Continues the current Anam conversation on ChatGPT through this signed-in Chrome profile. The ChatGPT account's memory applies — use the profile whose login you want.</div>
            `;
            return;
        }

        if (provider === 'lmstudio' || provider === 'ollama') {
            const isOllama = provider === 'ollama';
            const defaultUrl = isOllama ? 'http://localhost:11434/v1' : 'http://localhost:1234/v1';
            const defaultCtx = isOllama ? 131072 : 32768;
            const label = isOllama ? 'Ollama' : 'LM Studio';
            const note = isOllama
                ? `Run <code>ollama pull kimi-k2</code> first, then set the model name here. Context length should match the model's capability.`
                : `Match context length to what you set in LM Studio. The identity prompt will be trimmed to fit. 32K+ recommended.`;
            container.innerHTML = `
                <div class="threshold-row" style="margin-top:8px">
                    <label class="threshold-label">Base URL</label>
                    <input type="text" id="provider-base-url" class="threshold-input" style="width:220px"
                        value="${escapeHtml(cfg.base_url || defaultUrl)}">
                </div>
                <div class="threshold-row">
                    <label class="threshold-label">Model</label>
                    <input type="text" id="provider-model" class="threshold-input" style="width:160px"
                        placeholder="${isOllama ? 'e.g. kimi-k2' : '(auto-detect)'}" value="${escapeHtml(cfg.model || '')}">
                </div>
                <div class="threshold-row">
                    <label class="threshold-label">Context length</label>
                    <input type="number" id="provider-context-length" class="threshold-input"
                        value="${cfg.context_length || defaultCtx}" min="2048">
                    <span class="threshold-unit">tokens</span>
                </div>
                <div class="threshold-row">
                    <label class="threshold-label">Max tokens</label>
                    <input type="number" id="provider-max-tokens" class="threshold-input"
                        value="${cfg.max_tokens || 4096}" min="256">
                </div>
                <div class="section-note" style="margin:4px 0 0">${note}</div>
            `;
            return;
        }

        container.innerHTML = '';
    },

    _suggestCodexModel(model) {
        const requested = String(model || '').trim().toLowerCase();
        if (!requested || !this._codexModels.length) return null;
        const prefixed = requested.startsWith('gpt-') ? requested : `gpt-${requested}`;
        if (this._codexModels.includes(prefixed)) return prefixed;

        const family = prefixed.match(/^gpt-(\d+(?:\.\d+)?)/)?.[0];
        if (family) {
            const exactFamily = this._codexModels.find(candidate => candidate === family);
            if (exactFamily) return exactFamily;
            const familyTier = this._codexModels.find(candidate => candidate.startsWith(`${family}-`));
            if (familyTier) return familyTier;
        }

        const requestedVersion = prefixed.match(/^gpt-(\d+(?:\.\d+)?)/)?.[1];
        if (requestedVersion) {
            const requestedNumber = Number(requestedVersion);
            const nearest = this._codexModels
                .map(candidate => ({
                    candidate,
                    version: Number(candidate.match(/^gpt-(\d+(?:\.\d+)?)/)?.[1]),
                }))
                .filter(item => Number.isFinite(item.version))
                .sort((a, b) => Math.abs(a.version - requestedNumber) - Math.abs(b.version - requestedNumber))[0];
            if (nearest) return nearest.candidate;
        }
        return null;
    },

    _normalizeCodexModel(model) {
        const requested = String(model || '').trim().toLowerCase();
        if (!requested || !this._codexModels.length) return requested;
        if (this._codexModels.includes(requested)) return requested;
        const prefixed = requested.startsWith('gpt-') ? requested : `gpt-${requested}`;
        if (this._codexModels.includes(prefixed)) return prefixed;
        const familyTier = this._codexModels.find(candidate => candidate.startsWith(`${prefixed}-`));
        return familyTier || requested;
    },

    clearCodexModelError() {
        const input = document.getElementById('provider-model');
        const error = document.getElementById('provider-model-error');
        if (!input || !error) return;
        input.removeAttribute('aria-invalid');
        input.classList.remove('field-invalid');
        error.hidden = true;
        error.replaceChildren();
    },

    validateCodexModel() {
        const input = document.getElementById('provider-model');
        const error = document.getElementById('provider-model-error');
        if (!input || !error || !this._codexModels.length) return true;

        const model = this._normalizeCodexModel(input.value);
        if (!model || this._codexModels.includes(model)) {
            input.value = model;
            this.clearCodexModelError();
            return true;
        }

        const suggestion = this._suggestCodexModel(model);
        input.setAttribute('aria-invalid', 'true');
        input.classList.add('field-invalid');
        error.replaceChildren();

        const title = document.createElement('strong');
        title.textContent = 'That model is not available to this Codex CLI.';
        const detail = document.createElement('span');
        detail.textContent = suggestion
            ? ` Did you mean ${suggestion}?`
            : ' Choose a suggested model from the field, or leave it blank for the default.';
        error.append(title, detail);

        if (suggestion) {
            const useButton = document.createElement('button');
            useButton.type = 'button';
            useButton.className = 'provider-model-suggestion';
            useButton.textContent = `Use ${suggestion}`;
            useButton.addEventListener('click', () => {
                input.value = suggestion;
                this.clearCodexModelError();
                input.focus();
            });
            error.append(useButton);
        }

        error.hidden = false;
        return false;
    },

    _collectProviderConfig() {
        const provider = document.getElementById('provider-selector').value;
        const config = {};

        if (provider === 'claude-code') {
            config.backend = document.getElementById('provider-backend')?.value || 'subprocess';
        } else if (provider === 'codex') {
            config.runtime = document.getElementById('provider-codex-runtime')?.value || 'app-server';
            config.model = this._normalizeCodexModel(document.getElementById('provider-model')?.value) || '';
        } else if (provider === 'chatgpt') {
            config.profile_name = document.getElementById('provider-profile-name')?.value?.trim() || 'ChatGPT';
            config.cdp_port = parseInt(document.getElementById('provider-cdp-port')?.value) || 9225;
        } else if (provider === 'openai') {
            const apiKey = document.getElementById('provider-api-key')?.value?.trim();
            if (apiKey) config.api_key = apiKey;
            config.model = document.getElementById('provider-model')?.value?.trim() || 'gpt-4o';
            config.base_url = document.getElementById('provider-base-url')?.value?.trim() || '';
            config.max_tokens = parseInt(document.getElementById('provider-max-tokens')?.value) || 4096;
        } else if (provider === 'openrouter') {
            const apiKey = document.getElementById('provider-api-key')?.value?.trim();
            if (apiKey) config.api_key = apiKey;
            config.model = document.getElementById('provider-model')?.value?.trim() || 'qwen/qwen3-235b-a22b';
            config.base_url = 'https://openrouter.ai/api/v1';
            config.max_tokens = parseInt(document.getElementById('provider-max-tokens')?.value) || 4096;
        } else if (provider === 'lmstudio' || provider === 'ollama') {
            const defaultUrl = provider === 'ollama' ? 'http://localhost:11434/v1' : 'http://localhost:1234/v1';
            config.base_url = document.getElementById('provider-base-url')?.value?.trim() || defaultUrl;
            config.model = document.getElementById('provider-model')?.value?.trim() || '';
            config.context_length = parseInt(document.getElementById('provider-context-length')?.value) || (provider === 'ollama' ? 131072 : 32768);
            config.max_tokens = parseInt(document.getElementById('provider-max-tokens')?.value) || 4096;
        }

        return { provider, config };
    },

    async saveProvider() {
        const { provider, config } = this._collectProviderConfig();
        const btn = document.getElementById('save-provider-btn');
        const status = document.getElementById('provider-status');

        if (provider === 'codex' && !this.validateCodexModel()) {
            status.textContent = 'Choose a valid Codex model before saving.';
            status.style.color = 'var(--error)';
            return;
        }

        btn.textContent = 'Saving...';
        btn.disabled = true;

        try {
            const result = await sendJson('/api/settings/provider', 'PUT', { provider, config }, { timeoutMs: 10000 });
            if (result?.ok !== false) {
                btn.textContent = 'Saved!';
                status.textContent = `Provider set to ${provider}`;
                status.style.color = 'var(--success)';
                // Reload to get masked keys
                await this.loadProvider();
            } else {
                btn.textContent = 'Error';
                status.textContent = result?.error || 'Save failed';
                status.style.color = 'var(--error)';
            }
        } catch (err) {
            btn.textContent = 'Error';
            status.textContent = String(err);
            status.style.color = 'var(--error)';
        }

        setTimeout(() => { btn.textContent = 'Save'; btn.disabled = false; }, 2000);
    },

    async testProvider() {
        const { provider, config } = this._collectProviderConfig();
        const btn = document.getElementById('test-provider-btn');
        const status = document.getElementById('provider-status');

        btn.textContent = 'Testing...';
        btn.disabled = true;

        try {
            const result = await sendJson('/api/settings/provider/test', 'POST', { provider, config }, { timeoutMs: 15000 });
            if (result?.ok) {
                status.textContent = result.message || 'Connected!';
                status.style.color = 'var(--success)';
            } else {
                status.textContent = result?.message || 'Connection failed';
                status.style.color = 'var(--error)';
            }
        } catch (err) {
            status.textContent = String(err);
            status.style.color = 'var(--error)';
        }

        btn.textContent = 'Test Connection';
        btn.disabled = false;
    },

    // ── System ──

    async loadSystem() {
        try {
            const [sys, auth, health, care] = await Promise.all([
                fetchJson('/api/settings/system', { timeoutMs: 10000 }),
                fetchJson('/auth/status', { timeoutMs: 10000 }),
                fetchJson('/health', { timeoutMs: 10000 }),
                fetchJson('/api/autowake/care-signals', { timeoutMs: 10000 }),
            ]);

            // Per-session MCP roster captured from the CLI's system/init event.
            // One compact line per live -p session: "qualia-backend ✓ · social-backend ✗ failed".
            const ccSessions = Array.isArray(sys.claude_code_sessions) ? sys.claude_code_sessions : [];
            const mcpRows = ccSessions
                .filter(s => s && Array.isArray(s.mcp_servers) && s.mcp_servers.length)
                .map(s => {
                    const line = s.mcp_servers.map(m => {
                        const name = escapeHtml(m.name || '?');
                        if (m.status === 'connected') {
                            const tools = m.tool_count ? ` title="${m.tool_count} tools"` : '';
                            return `<span class="mcp-chip ok"${tools}>${name} ✓</span>`;
                        }
                        return `<span class="mcp-chip bad">${name} ✗ ${escapeHtml(m.status || 'failed')}</span>`;
                    }).join('<span class="mcp-chip-sep">·</span>');
                    return `
                        <div class="info-row">
                            <span class="info-label">MCP · ${escapeHtml(s.identity || '?')}</span>
                            <span class="info-value mcp-session-line">${line}</span>
                        </div>`;
                }).join('');

            document.getElementById('system-info').innerHTML = `
                <div class="info-row">
                    <span class="info-label">Uptime</span>
                    <span class="info-value">${sys.uptime}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Version</span>
                    <span class="info-value">${sys.version}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">LLM Provider</span>
                    <span class="info-value">${escapeHtml(sys.llm_provider || 'anthropic')}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Timezone</span>
                    <span class="info-value">${sys.timezone}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Chat connections</span>
                    <span class="info-value">${sys.websocket_connections} active</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Integration owner</span>
                    <span class="info-value">${sys.integration_owner?.owner ? 'This instance' : 'Standby'}${sys.integration_owner?.exclusive === false ? ' (shared mode)' : ''}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Owner reason</span>
                    <span class="info-value">${escapeHtml(sys.integration_owner?.reason || 'unknown')}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Auth</span>
                    <span class="info-value">${auth.authenticated ? auth.username : 'Disabled'}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Identities</span>
                    <span class="info-value">${sys.identities.join(', ')}</span>
                </div>
                ${mcpRows || `
                <div class="info-row">
                    <span class="info-label">MCP servers</span>
                    <span class="info-value">no live Claude Code sessions yet</span>
                </div>`}
                <div class="info-row">
                    <span class="info-label">Path overrides</span>
                    <span class="info-value">${Object.values(sys.path_config || {}).filter(p => p.source === 'env').length} env-backed</span>
                </div>
            `;

            document.getElementById('health-info').innerHTML = `
                <div class="info-row">
                    <span class="info-label">Overall</span>
                    <span class="info-value">${escapeHtml(health.status || 'unknown')}</span>
                </div>
                ${Object.entries(health).filter(([key]) => !['status', 'ready', 'uptime_seconds'].includes(key)).map(([key, value]) => `
                    <div class="info-row">
                        <span class="info-label">${escapeHtml(key)}</span>
                        <span class="info-value">${escapeHtml(typeof value === 'object' ? JSON.stringify(value) : String(value))}</span>
                    </div>
                `).join('')}
            `;

            document.getElementById('care-signal-info').innerHTML = `
                <div class="info-row">
                    <span class="info-label">Enabled</span>
                    <span class="info-value">${care.settings?.care_signals_enabled === 'true' ? 'Yes' : 'No'}</span>
                </div>
                ${(care.timers || []).length === 0 ? '<div class="info-row"><span class="info-label">Pending</span><span class="info-value">None</span></div>' : ''}
                ${(care.timers || []).map((timer) => `
                    <div class="info-row">
                        <span class="info-label">${escapeHtml(timer.identity)}</span>
                        <span class="info-value">${escapeHtml(timer.status)} · ${new Date(timer.fire_at).toLocaleString()}</span>
                    </div>
                `).join('')}
                ${Object.entries(sys.path_config || {}).map(([key, info]) => `
                    <div class="info-row">
                        <span class="info-label">${escapeHtml(key)}</span>
                        <span class="info-value">${escapeHtml(info.source)} · ${escapeHtml(info.path)}</span>
                    </div>
                `).join('')}
            `;
        } catch (err) {
            console.error('Failed to load system info:', err);
        }
    },

    // ── Notifications ──

    async loadNotifications() {
        const container = document.getElementById('notification-status');
        const btn = document.getElementById('notification-toggle-btn');

        if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
            container.innerHTML = '<div style="color:var(--text-muted);font-size:0.85rem">Push notifications are not supported in this browser.</div>';
            btn.style.display = 'none';
            return;
        }

        const permission = Notification.permission;
        if (permission === 'denied') {
            container.innerHTML = '<div class="status-indicator stopped">Blocked by browser — check site settings to allow</div>';
            btn.style.display = 'none';
            return;
        }

        try {
            const reg = await navigator.serviceWorker.ready;
            const sub = await reg.pushManager.getSubscription();

            if (sub) {
                container.innerHTML = '<div class="status-indicator running">Notifications active</div>';
                btn.textContent = 'Disable Notifications';
                btn.className = 'action-btn secondary';
            } else {
                container.innerHTML = '<div class="status-indicator stopped">Notifications off</div>';
                btn.textContent = 'Enable Notifications';
                btn.className = 'action-btn';
            }
        } catch (err) {
            console.error('Failed to check notification status:', err);
            container.innerHTML = '<div style="color:var(--text-muted);font-size:0.85rem">Could not check notification status.</div>';
        }
    },

    async toggleNotifications() {
        const btn = document.getElementById('notification-toggle-btn');
        const reg = await navigator.serviceWorker.ready;
        const sub = await reg.pushManager.getSubscription();

        if (sub) {
            // Unsubscribe
            btn.textContent = 'Disabling...';
            btn.disabled = true;
            try {
                await sendJson('/api/notifications/unsubscribe', 'POST', { endpoint: sub.endpoint }, { timeoutMs: 10000 });
                await sub.unsubscribe();
            } catch (err) {
                console.error('Failed to unsubscribe:', err);
            }
            btn.disabled = false;
            await this.loadNotifications();
        } else {
            // Subscribe
            btn.textContent = 'Enabling...';
            btn.disabled = true;
            try {
                // Get VAPID key from server
                const { publicKey } = await fetchJson('/api/notifications/vapid-key', { timeoutMs: 10000 });

                // Convert base64url to Uint8Array
                const padding = '='.repeat((4 - publicKey.length % 4) % 4);
                const raw = atob(publicKey.replace(/-/g, '+').replace(/_/g, '/') + padding);
                const keyArray = new Uint8Array(raw.length);
                for (let i = 0; i < raw.length; i++) keyArray[i] = raw.charCodeAt(i);

                const newSub = await reg.pushManager.subscribe({
                    userVisibleOnly: true,
                    applicationServerKey: keyArray,
                });

                // Send subscription to server
                await sendJson('/api/notifications/subscribe', 'POST', { subscription: newSub.toJSON() }, { timeoutMs: 10000 });
            } catch (err) {
                console.error('Failed to subscribe:', err);
                if (Notification.permission === 'denied') {
                    alert('Notifications were blocked. Check your browser settings to allow them for this site.');
                }
            }
            btn.disabled = false;
            await this.loadNotifications();
        }
    },

                    

                     

                         

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
};

document.addEventListener('DOMContentLoaded', () => Settings.init());
