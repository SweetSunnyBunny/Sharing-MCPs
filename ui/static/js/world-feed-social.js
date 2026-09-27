/* Timeline interactions, draft receipts and navigation continuity. */
(() => {
    const feed = WorldFeed;
    const base = { bindEvents: feed.bindEvents, setView: feed.setView, selectWorld: feed.selectWorld };
    Object.assign(feed, {
        navigationCache: new Map(),
        pendingReactions: new Set(),
        draftKey() { return `world-feed-draft:${this.state.world?.id}`; },
        composeSnapshot() {
            return { body: this.el['wf-post-text'].value, media: this.state.media,
                replyTo: this.state.replyTo, quotePost: this.state.quotePost,
                composerProfileId: this.state.composerProfileId, canon: this.el['wf-post-canon'].value };
        },
        saveDraft() {
            if (!this.state.world) return;
            try { localStorage.setItem(this.draftKey(), JSON.stringify({ ...this.composeSnapshot(), submission: this.submission })); }
            catch (_) { /* Storage may be full or disabled; keep the in-memory draft. */ }
        },
        restoreDraft() {
            let draft = {};
            try { draft = JSON.parse(localStorage.getItem(this.draftKey()) || '{}'); } catch (_) { /* bad old draft */ }
            this.el['wf-post-text'].value = draft.body || '';
            this.state.media = Array.isArray(draft.media) ? draft.media.slice(0, 4) : [];
            this.state.composerProfileId = draft.composerProfileId || '';
            this.el['wf-post-canon'].value = draft.canon || 'ambient';
            this.submission = draft.submission || null;
            this.cancelPostContext();
            if (draft.replyTo) this.beginReply(draft.replyTo, false, false);
            if (draft.quotePost) this.beginQuote(draft.quotePost, false);
            this.renderWorldChrome(); this.renderMediaPreview(); this.updateCharCount();
        },
        bindEvents() {
            base.bindEvents.call(this);
            for (const id of ['wf-post-text', 'wf-post-as', 'wf-post-canon']) {
                this.el[id].addEventListener('input', () => this.saveDraft());
                this.el[id].addEventListener('change', () => this.saveDraft());
            }
            this.el['wf-media-preview'].addEventListener('click', () => this.saveDraft());
            this.el['wf-replying-to'].addEventListener('click', () => this.saveDraft());
            window.addEventListener('pagehide', () => this.saveDraft());
            document.getElementById('wf-search-form').addEventListener('submit', event => {
                event.preventDefault(); this.setView('search', document.getElementById('wf-search-input').value.trim());
            });
            document.getElementById('wf-new-posts').addEventListener('click', async () => {
                try { await this.loadFeed(); this.renderCurrentView(); this.hideNewPosts(); window.scrollTo({ top: 0 }); }
                catch (error) { this.toast(error.message, true); }
            });
            if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
        },
        async selectWorld(id) {
            this.saveDraft();
            this.navigationCache.clear();
            this.state.posts = []; this.state.thread = null; this.state.profile = null; this.state.nextCursor = null;
            this.state.replyTo = null; this.state.quotePost = null; this.state.media = [];
            this.state.composerProfileId = ''; this.el['wf-post-text'].value = '';
            await base.selectWorld.call(this, id);
            if (this.state.world?.id === id) this.restoreDraft();
        },
        routeKey(view = this.state.view, detail = this.state.detailId) {
            return `${this.state.world?.id}:${this.state.activeProfileId}:${view}:${detail}`;
        },
        async setView(view, detail = '', fromHistory = false) {
            const oldKey = this.routeKey(), newKey = this.routeKey(view, detail);
            if (oldKey !== newKey) {
                const saved = {};
                for (const key of ['posts', 'nextCursor', 'thread', 'profile', 'hashtag', 'authorFilter', 'canonFilter']) saved[key] = this.state[key];
                this.navigationCache.set(oldKey, { saved, scroll: window.scrollY || document.body.scrollTop || 0 });
                if (this.navigationCache.size > 12) this.navigationCache.delete(this.navigationCache.keys().next().value);
            }
            const cached = fromHistory && oldKey !== newKey && this.navigationCache.get(newKey);
            const searchForm = document.getElementById('wf-search-form');
            if (searchForm) searchForm.hidden = view !== 'search';
            const searchInput = document.getElementById('wf-search-input');
            if (searchInput && view === 'search') searchInput.value = detail;
            this.hideNewPosts();
            const waiting = base.setView.call(this, view, detail, fromHistory);
            const generation = this.viewGeneration;
            await waiting;
            if (cached && generation === this.viewGeneration) {
                Object.assign(this.state, cached.saved);
                this.renderCurrentView();
                window.scrollTo({ top: cached.scroll, behavior: 'instant' });
                document.body.scrollTop = cached.scroll;
            }
        },
        hideNewPosts() { const button = document.getElementById('wf-new-posts'); if (button) button.hidden = true; },
        async pollActivity() {
            const generation = this.viewGeneration;
            await this.loadNotifications(this.state.view === 'notifications');
            if (generation !== this.viewGeneration || !['home', 'following', 'media', 'profile', 'search'].includes(this.state.view)) return;
            const params = new URLSearchParams({ limit: '1', viewer_profile_id: this.state.activeProfileId || '' });
            if (this.state.view === 'following') params.set('following_only', 'true');
            if (this.state.view === 'media') params.set('media_only', 'true');
            if (this.state.view === 'profile') params.set('author_profile_id', this.state.detailId);
            if (this.state.hashtag) params.set('hashtag', this.state.hashtag);
            if (this.state.view === 'search') params.set('search', this.state.detailId);
            const data = await this.latestRequest('activity', `/api/world-feed/worlds/${encodeURIComponent(this.state.world.id)}/feed?${params}`);
            if (!data || generation !== this.viewGeneration) return;
            const latest = data.posts?.[0];
            if (latest && !this.state.posts.some(post => post.id === latest.id)) {
                const button = document.getElementById('wf-new-posts'); if (button) button.hidden = false;
            }
        },
        async reactPost(id, kind) {
            const profile = this.activeProfile();
            if (!profile) throw new Error('Choose your account first.');
            const key = `${profile.id}:${id}:${kind}`;
            if (this.pendingReactions.has(key)) return;
            const post = [this.state.thread, ...this.state.posts].find(p => p?.id === id);
            if (!post) return;
            const field = { like: 'viewer_liked', bookmark: 'viewer_bookmarked', repost: 'viewer_reposted' }[kind];
            const desired = !post[field], generation = this.worldGeneration;
            this.pendingReactions.add(key);
            try {
                const data = await this.request(`/api/world-feed/posts/${encodeURIComponent(id)}/${kind}`, {
                    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ profile_id: profile.id, active: desired }),
                });
                if (generation !== this.worldGeneration || profile.id !== this.state.activeProfileId) return;
                for (const item of [this.state.thread, ...this.state.posts]) {
                    if (item?.id === id) { item[field] = data.active; if (kind === 'like') item.like_count = data.like_count; }
                }
                for (const cached of this.navigationCache.values()) {
                    for (const item of [cached.saved.thread, ...cached.saved.posts]) {
                        if (item?.id === id) { item[field] = data.active; if (kind === 'like') item.like_count = data.like_count; }
                    }
                }
                // Touch only the matching controls; images, focus and loaded pages stay put.
                this.el['wf-content'].querySelectorAll('[data-action]').forEach(button => {
                    if (button.dataset.post !== id || button.dataset.action !== kind) return;
                    button.classList.toggle('liked', data.active);
                    button.setAttribute('aria-pressed', String(data.active));
                    if (kind === 'like') button.querySelector('span').textContent = data.like_count || '';
                });
                if (kind === 'bookmark' && !desired && this.state.view === 'bookmarks') {
                    this.state.posts = this.state.posts.filter(p => p.id !== id);
                    this.el['wf-content'].querySelectorAll('[data-post-id]').forEach(node => { if (node.dataset.postId === id) node.remove(); });
                }
            } finally { this.pendingReactions.delete(key); }
        },
        async likePost(id) { return this.reactPost(id, 'like'); },
        async submitPost() {
            if (this.submitting || this.uploading) return;
            const author = this.composerProfile();
            if (!author) return this.toast('Choose an account first.', true);
            const snapshot = JSON.stringify(this.composeSnapshot());
            const payload = { author_profile_id: author.id, body: this.el['wf-post-text'].value.trim(),
                parent_post_id: this.state.replyTo?.id || null, quote_post_id: this.state.quotePost?.id || null,
                canon_level: this.el['wf-post-canon'].value, canon_status: 'approved', origin: 'human',
                media: this.state.media, viewer_profile_id: this.state.activeProfileId || null };
            if (!payload.body && !payload.media.length && !payload.quote_post_id) return this.toast('Write something or attach an image.', true);
            const fingerprint = JSON.stringify(payload);
            if (this.submission?.fingerprint !== fingerprint) this.submission = { id: crypto.randomUUID(), fingerprint };
            payload.submission_id = this.submission.id;
            const world = this.state.world.id, generation = this.worldGeneration;
            this.saveDraft(); this.submitting = true; this.el['wf-submit-post'].disabled = true;
            try {
                const post = await this.request(`/api/world-feed/worlds/${encodeURIComponent(world)}/posts`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
                });
                if (generation !== this.worldGeneration) return;
                if (snapshot === JSON.stringify(this.composeSnapshot())) {
                    this.el['wf-post-text'].value = ''; this.state.media = []; this.state.composerProfileId = '';
                    this.submission = null; this.cancelPostContext(); this.renderWorldChrome(); this.renderMediaPreview(); this.updateCharCount(); this.saveDraft();
                }
                // Keep history pages and their scroll anchors when replying from a thread.
                for (const cached of this.navigationCache.values()) {
                    for (const item of [cached.saved.thread, ...cached.saved.posts]) {
                        if (item?.id === post.parent_post_id && post.parent_post) item.reply_count = post.parent_post.reply_count;
                    }
                }
                if ((this.state.view === 'home' && !this.state.hashtag && !this.state.authorFilter) || (this.state.view === 'thread' && post.parent_post_id === this.state.detailId)) {
                    this.state.posts = [post, ...this.state.posts.filter(p => p.id !== post.id)];
                    const top = window.scrollY || 0; this.renderCurrentView(); window.scrollTo({ top, behavior: 'instant' });
                }
                this.toast('Posted to your world.');
            } catch (error) { this.toast(`${error.message} Your draft is still here.`, true); }
            finally { this.submitting = false; this.el['wf-submit-post'].disabled = false; }
        },
        async addPostMedia(fileList) {
            if (this.uploading || this.submitting) return;
            // FileList is live: resetting the input empties it on mobile browsers.
            const files = Array.from(fileList || []).slice(0, Math.max(0, 4 - this.state.media.length));
            if (!files.length) {
                if (this.state.media.length >= 4) this.toast('You can attach up to four images to a post.', true);
                this.el['wf-media-input'].value = '';
                return;
            }
            const generation = this.worldGeneration;
            this.uploading = true; this.el['wf-submit-post'].disabled = true;
            this.el['wf-media-input'].value = '';
            try {
                for (const file of files) {
                    const uploaded = await this.uploadImage(file);
                    if (generation !== this.worldGeneration) return;
                    this.state.media.push({ url: uploaded.url, media_type: 'image', alt_text: file.name, caption: '' });
                    this.renderMediaPreview(); this.saveDraft();
                }
            } catch (error) { this.toast(error.message, true); }
            finally { this.uploading = false; this.el['wf-submit-post'].disabled = false; }
        },
        async markNotificationsRead() {
            const active = this.activeProfile(), world = this.state.world.id;
            if (!active) return;
            const ids = (this.state.notifications || []).filter(n => !n.is_read).map(n => n.id);
            await this.request(`/api/world-feed/worlds/${encodeURIComponent(world)}/notifications/seen`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ profile_id: active.id, ids }),
            });
            if (this.state.world.id === world && this.state.activeProfileId === active.id) await this.loadNotifications(false);
        },
    });
})();
