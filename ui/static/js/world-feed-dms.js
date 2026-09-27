/* Private World Feed messages. Polling preserves the composer and reading position. */
(() => {
    const feed = WorldFeed;
    const base = Object.fromEntries(['selectWorld', 'setView', 'renderCurrentView', 'pollActivity', 'handleContentAction', 'profileHeaderHtml'].map(key => [key, feed[key]]));
    const drafts = new Map(), sending = new Set();
    let opening = false;
    const scope = () => JSON.stringify([feed.state.world?.id, feed.state.activeProfileId, feed.state.detailId]);
    const current = token => scope() === token && feed.state.view === 'dms';
    const draft = key => {
        if (!drafts.has(key)) {
            let saved;
            try { saved = JSON.parse(sessionStorage.getItem('wf-dm-draft:' + key)); } catch (_) { /* storage unavailable */ }
            drafts.set(key, saved && typeof saved.text === 'string' ? saved : { text: '' });
        }
        return drafts.get(key);
    };
    const storeDraft = (key, value) => {
        drafts.set(key, value);
        try { sessionStorage.setItem('wf-dm-draft:' + key, JSON.stringify(value)); } catch (_) { /* memory fallback */ }
    };
    const viewport = () => {
        const shell = document.querySelector('.wf-dm-shell');
        if (!shell) return;
        const view = window.visualViewport;
        const bottom = view ? view.offsetTop + view.height : window.innerHeight;
        shell.style.setProperty('--dm-height', `${Math.max(180, bottom - Math.max(0, shell.getBoundingClientRect().top) - 8)}px`);
    };
    window.visualViewport?.addEventListener('resize', viewport);
    window.visualViewport?.addEventListener('scroll', viewport);
    window.addEventListener?.('resize', viewport);

    Object.assign(feed, {
        async selectWorld(id) {
            this.state.dmData = null;
            this.state.dmThread = null;
            await base.selectWorld.call(this, id);
            try { await this.loadDMs(false); } catch (_) { /* next poll retries */ }
        },

        async setView(view, detail = '', fromHistory = false) {
            document.body.classList?.toggle('wf-dm-open', view === 'dms' && Boolean(detail));
            // Cached messages belong to one account and world only.
            this.state.dmThread = null;
            this.state.dmError = '';
            await base.setView.call(this, view, detail, fromHistory);
            const token = scope(), generation = this.state.viewGeneration;
            if (view !== 'dms' || !current(token) || this.state.detailId !== detail) return;
            try {
                await this.loadDMs(false);
                if (!current(token) || generation !== this.state.viewGeneration) return;
                if (detail) await this.loadDMThread(detail, true);
            } catch (error) {
                if (current(token)) this.state.dmError = error.message;
            }
            if (current(token) && generation === this.state.viewGeneration) this.renderDMs();
        },

        renderCurrentView() {
            return this.state.view === 'dms' ? this.renderDMs() : base.renderCurrentView.call(this);
        },

        async pollActivity() {
            // A failed timeline request must not prevent private messages refreshing.
            try { await base.pollActivity.call(this); } catch (_) { /* independently retry next poll */ }
            const token = scope();
            try {
                await this.loadDMs(false);
                if (current(token) && this.state.detailId) await this.loadDMThread(this.state.detailId, true);
                if (current(token)) { this.state.dmError = ''; this.renderDMs(); }
            } catch (_) {
                if (current(token)) { this.state.dmError = 'Connection interrupted. Your draft is safe; reconnecting…'; this.renderDMs(); }
            }
        },

        async loadDMs(render = false) {
            const active = this.activeProfile(), worldId = this.state.world?.id;
            if (!active?.is_user_controlled || !worldId) return;
            const profileId = active.id;
            const data = await this.latestRequest('dms', `/api/world-feed/worlds/${encodeURIComponent(worldId)}/dms?profile_id=${encodeURIComponent(profileId)}`);
            if (!data || this.state.world?.id !== worldId || this.state.activeProfileId !== profileId) return;
            this.state.dmData = data;
            this.state.dmAccount = JSON.stringify([worldId, profileId]);
            for (const id of ['wf-dm-badge', 'wf-dm-mobile-badge']) {
                const badge = document.getElementById(id);
                if (badge) badge.textContent = data.unread_count ? String(data.unread_count) : '';
            }
            if (render && this.state.view === 'dms') this.renderDMs();
        },

        async loadDMThread(threadId, markRead = true, older = false) {
            const active = this.activeProfile(), token = scope(), generation = this.state.viewGeneration;
            if (!active?.is_user_controlled) return;
            const previous = this.state.dmThread;
            const before = older ? previous?.before_rowid : null;
            if (older && !before) return;
            const data = await this.latestRequest(older ? 'dm-older' : 'dm-thread',
                `/api/world-feed/dms/${encodeURIComponent(threadId)}?profile_id=${encodeURIComponent(active.id)}&mark_read=${markRead && !older}${before ? '&before_rowid=' + before : ''}`);
            if (!data || !current(token) || this.state.detailId !== threadId || generation !== this.state.viewGeneration) return;
            // Merge against the current cache: older-page and incoming-message requests can overlap.
            const cache = this.state.dmThread;
            const overlaps = cache?.messages.some(old => data.messages.some(message => message.id === old.id));
            if (cache?.thread.id === threadId && cache.scope === token && (older || overlaps || !data.has_older)) {
                data.messages = [...new Map([...cache.messages, ...data.messages].map(m => [m.id, m])).values()].sort((a, b) => a.message_rowid - b.message_rowid);
                if (!older) { data.has_older = cache.has_older; data.before_rowid = cache.before_rowid; }
                else data.reply_status = cache.reply_status;
            }
            data.scope = token;
            this.state.dmThread = data;
            await this.loadDMs(false);
        },

        profileHeaderHtml(profile) {
            let html = base.profileHeaderHtml.call(this, profile);
            if (this.activeProfile()?.is_user_controlled && profile && profile.id !== this.state.activeProfileId) {
                html = html.replace('<button class="wf-secondary-button" data-action="edit-profile"',
                    `<button class="wf-secondary-button" data-action="dm-profile" data-profile="${this.attr(profile.id)}">Message</button><button class="wf-secondary-button" data-action="edit-profile"`);
            }
            return html;
        },

        dmMessagesHtml(data, active) {
            return data.messages.map(message => `<article class="wf-dm-message ${message.sender.id === active.id ? 'mine' : ''}" data-message="${this.attr(message.id || '')}">${message.sender.id === active.id ? '' : this.avatarHtml(message.sender, 'wf-avatar-small')}<div><small>${this.text(message.sender.display_name)}</small><p>${this.linkify(message.body)}</p><time>${this.text(formatRelativeTime(message.created_at))}</time></div></article>`).join('') || '<div class="wf-dm-placeholder"><span>Say something when you’re ready.</span></div>';
        },

        dmConversationHtml(data, active) {
            const other = data.thread.participants.find(p => p.id !== active.id) || {};
            return `<header class="wf-dm-head"><button class="wf-secondary-button wf-dm-back" data-action="dm-back" aria-label="Back to messages">←</button>${this.avatarHtml(other, 'wf-avatar-small')}<div><strong>${this.text(other.display_name || '')}</strong><small>@${this.text(other.handle || '')}</small></div></header>
                <button id="wf-dm-older" class="wf-secondary-button" data-action="dm-older" ${data.has_older ? '' : 'hidden'}>Earlier messages</button>
                <div class="wf-dm-messages" id="wf-dm-messages" aria-label="Conversation">${this.dmMessagesHtml(data, active)}</div>
                <div id="wf-dm-status" class="wf-dm-status" role="status"></div>
                <form id="wf-dm-compose" class="wf-dm-compose"><textarea id="wf-dm-text" aria-label="Message" maxlength="4000" rows="2" placeholder="Write a message…" required></textarea><button type="submit" class="wf-primary-button">Send</button></form>`;
        },

        renderDMs() {
            document.body.classList?.toggle('wf-dm-open', this.state.view === 'dms' && Boolean(this.state.detailId));
            const active = this.activeProfile(), content = this.el['wf-content'], token = scope();
            if (!active?.is_user_controlled) {
                content.innerHTML = '<div class="wf-empty"><strong>Choose your account first.</strong><button class="wf-primary-button" data-action="open-profiles">Choose account</button></div>';
                return;
            }
            const selected = this.state.dmThread?.scope === token ? this.state.dmThread : null;
            const renderKey = JSON.stringify([token, Boolean(selected)]);
            let shell = document.getElementById('wf-dm-shell');
            if (!shell || shell.dataset.key !== renderKey) {
                const choices = this.state.profiles.filter(p => p.is_active && !p.is_user_controlled).map(p => `<option value="${this.attr(p.id)}">${this.text(p.display_name)} (@${this.text(p.handle)})</option>`).join('');
                content.innerHTML = `<section id="wf-dm-shell" class="wf-dm-shell ${this.state.detailId ? 'has-conversation' : ''}"><aside class="wf-dm-list"><form id="wf-dm-new"><label for="wf-dm-recipient">New message</label><div><select id="wf-dm-recipient" required><option value="">Choose someone…</option>${choices}</select><button class="wf-secondary-button">Open</button></div></form><div id="wf-dm-threads"></div></aside><div class="wf-dm-conversation">${selected ? this.dmConversationHtml(selected, active) : `<div class="wf-dm-placeholder"><button class="wf-secondary-button" data-action="dm-back">← Messages</button><strong>${this.state.detailId ? 'Loading conversation…' : 'Your messages'}</strong><span id="wf-dm-status" role="status">Choose a conversation or start a new one.</span></div>`}</div></section>`;
                shell = document.getElementById('wf-dm-shell');
                if (shell) shell.dataset.key = renderKey;
                document.getElementById('wf-dm-new')?.addEventListener('submit', event => this.startDM(event));
                document.getElementById('wf-dm-compose')?.addEventListener('submit', event => this.sendDM(event));
                const input = document.getElementById('wf-dm-text');
                if (input) {
                    input.value = draft(token).text;
                    input.addEventListener('input', () => storeDraft(token, { ...draft(token), text: input.value }));
                }
                const pane = document.getElementById('wf-dm-messages');
                if (pane) pane.scrollTop = pane.scrollHeight;
            }
            const threads = this.state.dmAccount === JSON.stringify([this.state.world?.id, active.id]) ? this.state.dmData?.threads || [] : [];
            const cards = threads.map(thread => {
                const other = thread.other || {};
                return `<button class="wf-dm-thread ${thread.id === this.state.detailId ? 'active' : ''}" data-action="open-dm" data-thread="${this.attr(thread.id)}">${this.avatarHtml(other, 'wf-avatar-small')}<span><strong>${this.text(other.display_name || 'Conversation')}</strong><small>@${this.text(other.handle || '')}</small><em>${this.text(thread.latest_message?.body || 'Start the conversation.')}</em></span>${thread.unread_count ? `<b>${thread.unread_count}</b>` : ''}</button>`;
            }).join('') || '<p class="wf-muted">No private conversations yet.</p>';
            const list = document.getElementById('wf-dm-threads');
            if (list && list.innerHTML !== cards) list.innerHTML = cards;
            const pane = document.getElementById('wf-dm-messages');
            if (pane && selected) {
                const html = this.dmMessagesHtml(selected, active);
                if (pane.innerHTML !== html) {
                    const atBottom = pane.scrollHeight - pane.scrollTop - pane.clientHeight < 65;
                    const first = pane.firstElementChild, oldTop = first?.getBoundingClientRect().top;
                    const id = first?.dataset.message, oldScroll = pane.scrollTop;
                    pane.innerHTML = html;
                    const anchor = id && [...pane.children].find(el => el.dataset.message === id);
                    pane.scrollTop = atBottom && !this.state.dmPrepending ? pane.scrollHeight : oldScroll + (anchor ? anchor.getBoundingClientRect().top - oldTop : 0);
                }
                const older = document.getElementById('wf-dm-older');
                if (older) older.hidden = !selected.has_older;
            }
            const status = document.getElementById('wf-dm-status');
            if (status) status.innerHTML = this.state.dmError ? `${this.text(this.state.dmError)} <button class="wf-secondary-button" data-action="dm-refresh">Reconnect</button>` : selected?.reply_status === 'failed' ? 'The reply couldn’t be sent. <button class="wf-secondary-button" data-action="dm-retry">Retry reply</button>' : ['queued', 'running'].includes(selected?.reply_status) ? 'Waiting for a reply…' : '';
            const send = document.querySelector('#wf-dm-compose button');
            if (send) send.disabled = sending.has(token);
            viewport();
        },

        async startDM(event, profileId = '') {
            event?.preventDefault();
            const active = this.activeProfile(), token = scope(), generation = this.state.viewGeneration;
            const other = profileId || document.getElementById('wf-dm-recipient')?.value;
            if (!active?.is_user_controlled || !other) return this.toast('Choose someone to message.', true);
            if (opening) return;
            opening = true;
            try {
                const thread = await this.request(`/api/world-feed/worlds/${encodeURIComponent(this.state.world.id)}/dms`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ profile_id: active.id, other_profile_id: other }),
                });
                if (scope() === token && generation === this.state.viewGeneration) await this.setView('dms', thread.id);
            } catch (error) { this.toast(error.message, true); }
            finally { opening = false; }
        },

        async sendDM(event) {
            event.preventDefault();
            const input = document.getElementById('wf-dm-text'), body = input?.value.trim();
            const token = scope(), threadId = this.state.detailId, profileId = this.state.activeProfileId;
            if (!body || sending.has(token)) return;
            const saved = draft(token);
            const receipt = saved.receipt?.body === body ? saved.receipt : { body, id: crypto.randomUUID().replaceAll('-', '') };
            storeDraft(token, { text: input.value, receipt });
            sending.add(token);
            const button = event.submitter;
            if (button) button.disabled = true;
            try {
                await this.request(`/api/world-feed/dms/${encodeURIComponent(threadId)}/messages`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ profile_id: profileId, body, submission_id: receipt.id }),
                });
                // Don't erase something newly typed while the request was in flight.
                if (draft(token).text.trim() === body) {
                    storeDraft(token, { text: '' });
                    if (current(token)) {
                        const visible = document.getElementById('wf-dm-text');
                        if (visible?.value.trim() === body) visible.value = '';
                    }
                }
                if (current(token)) { await this.loadDMThread(threadId, true); this.renderDMs(); }
            } catch (error) { this.toast(error.message, true); }
            finally {
                sending.delete(token);
                if (button) button.disabled = false;
                if (current(token)) { const visible = document.querySelector('#wf-dm-compose button'); if (visible) visible.disabled = false; }
            }
        },

        async handleContentAction(event) {
            const action = event.target.closest('[data-action]');
            if (!action) return base.handleContentAction.call(this, event);
            const kind = action.dataset.action;
            if (!['open-dm', 'dm-profile', 'open-profiles', 'dm-back', 'dm-older', 'dm-refresh', 'dm-retry'].includes(kind)) return base.handleContentAction.call(this, event);
            event.preventDefault();
            if (kind === 'open-dm') return this.setView('dms', action.dataset.thread);
            if (kind === 'dm-profile') return this.startDM(null, action.dataset.profile);
            if (kind === 'open-profiles') return this.setView('profiles');
            if (kind === 'dm-back') return this.setView('dms');
            const token = scope();
            try {
                if (kind === 'dm-retry') await this.request(`/api/world-feed/dms/${encodeURIComponent(this.state.detailId)}/retry`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ profile_id: this.state.activeProfileId }) });
                if (!current(token)) return;
                this.state.dmPrepending = kind === 'dm-older';
                await this.loadDMThread(this.state.detailId, kind !== 'dm-older', kind === 'dm-older');
                if (current(token)) { this.state.dmError = ''; this.renderDMs(); }
            } catch (error) { this.toast(error.message, true); }
            finally { this.state.dmPrepending = false; }
        },
    });
})();
