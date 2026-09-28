/* World Feed — private, persistent story-social UI. */

const WorldFeed = {
    state: {
        worlds: [],
        world: null,
        profiles: [],
        relationships: [],
        posts: [],
        trends: [],
        activeProfileId: localStorage.getItem('world-feed-active-profile') || '',
        view: 'home',
        canonFilter: 'all',
        hashtag: '',
        authorFilter: '',
        replyTo: null,
        quotePost: null,
        media: [],
        nextCursor: null,
        detailId: '',
        profile: null,
        thread: null,
        connections: [],
        connectionCursor: null,
        profileSearch: '',
        photoData: null,
        photoRequest: null,
        composerProfileId: '',
        dmData: null,
        dmThread: null,
    },

    el: {},
    requests: {},
    worldGeneration: 0,
    viewGeneration: 0,
    worldModalGeneration: 0,
    editingWorldId: '',

    async latestRequest(key, url) {
        const sequence = this.requests[key] = (this.requests[key] || 0) + 1;
        const world = this.worldGeneration;
        const current = () => this.requests[key] === sequence && this.worldGeneration === world;
        try {
            const data = await this.request(url);
            return current() ? data : null;
        } catch (error) {
            if (current()) throw error;
            return null;
        }
    },

    async init() {
        this.cacheElements();
        this.bindEvents();
        try {
            const data = await this.request('/api/world-feed/worlds');
            this.state.worlds = data.worlds || [];
            if (!this.state.worlds.length) throw new Error('No story worlds exist yet.');
            this.renderWorldPicker();
            const wanted = localStorage.getItem('world-feed-world');
            const world = this.state.worlds.find(item => item.id === wanted) || this.state.worlds[0];
            await this.selectWorld(world.id);
            window.setInterval(async () => {
                if (document.hidden || this.photoPollBusy) return;
                this.photoPollBusy = true;
                try {
                    if (this.state.view === 'photos') { await this.loadPhotos(); this.renderPhotoJobs(); }
                    await this.pollActivity();
                }
                catch (_error) { /* Manual refresh can surface a persistent connection error. */ }
                finally { this.photoPollBusy = false; }
            }, 15000);
        } catch (error) {
            this.renderFatal(error);
        }
    },

    cacheElements() {
        const ids = [
            'wf-world-select', 'wf-new-world', 'wf-view-title', 'wf-view-subtitle', 'wf-refresh',
            'wf-clock-label', 'wf-world-description', 'wf-world-settings', 'wf-composer',
            'wf-composer-avatar', 'wf-post-text', 'wf-char-count', 'wf-media-input', 'wf-post-as',
            'wf-add-media', 'wf-insert-hashtag', 'wf-post-canon', 'wf-submit-post',
            'wf-media-preview', 'wf-replying-to', 'wf-filter-bar', 'wf-filter-context',
            'wf-content', 'wf-trends', 'wf-profile-preview', 'wf-active-avatar',
            'wf-active-name', 'wf-active-handle', 'wf-account-switcher', 'wf-notice-badge',
            'wf-new-profile', 'wf-see-profiles', 'wf-manage-relations', 'wf-compose-nav',
            'wf-mobile-compose', 'wf-mobile-menu', 'wf-left-nav', 'wf-nav-overlay',
            'wf-modal-backdrop', 'wf-profile-modal', 'wf-profile-modal-title',
            'wf-profile-form', 'wf-profile-id', 'wf-profile-name', 'wf-profile-handle',
            'wf-profile-type', 'wf-profile-color', 'wf-profile-bio', 'wf-profile-location',
            'wf-profile-website', 'wf-profile-style', 'wf-profile-notes',
            'wf-profile-controlled', 'wf-profile-verified', 'wf-avatar-url', 'wf-header-url',
            'wf-avatar-preview', 'wf-header-preview', 'wf-upload-avatar', 'wf-upload-header',
            'wf-avatar-input', 'wf-header-input', 'wf-relationship-modal',
            'wf-relationship-form', 'wf-rel-from', 'wf-rel-to', 'wf-rel-type',
            'wf-rel-public', 'wf-rel-private', 'wf-world-modal', 'wf-world-form',
            'wf-world-name', 'wf-world-description-input', 'wf-world-now', 'wf-world-clock',
            'wf-world-title', 'wf-world-submit', 'wf-world-identity', 'wf-world-branch',
            'wf-world-photo-style',
            'wf-world-slug', 'wf-world-slug-field', 'wf-world-creation-note', 'wf-world-activity-controls',
            'wf-world-posting-enabled', 'wf-image-lightbox', 'wf-toast-stack',
        ];
        ids.forEach(id => { this.el[id] = document.getElementById(id); });
    },

    bindEvents() {
        this.el['wf-world-select'].addEventListener('change', event => this.selectWorld(event.target.value));
        this.el['wf-new-world'].addEventListener('click', () => this.openWorldModal(true));
        this.el['wf-refresh'].addEventListener('click', () => this.refreshCurrentView());
        this.el['wf-post-text'].addEventListener('input', () => this.updateCharCount());
        this.el['wf-post-as'].addEventListener('change', event => {
            this.state.composerProfileId = event.target.value;
            this.renderAvatarInto(this.el['wf-composer-avatar'], this.composerProfile());
        });
        this.el['wf-submit-post'].addEventListener('click', () => this.submitPost());
        this.el['wf-add-media'].addEventListener('click', () => this.el['wf-media-input'].click());
        this.el['wf-media-input'].addEventListener('change', event => this.addPostMedia(event.target.files));
        this.el['wf-insert-hashtag'].addEventListener('click', () => this.insertAtCursor('#'));
        this.el['wf-new-profile'].addEventListener('click', () => this.openProfileModal());
        this.el['wf-see-profiles'].addEventListener('click', () => this.setView('profiles'));
        this.el['wf-manage-relations'].addEventListener('click', () => this.openRelationshipModal());
        this.el['wf-world-settings'].addEventListener('click', () => this.openWorldModal());
        this.el['wf-compose-nav'].addEventListener('click', () => this.focusComposer());
        this.el['wf-mobile-compose'].addEventListener('click', () => this.focusComposer());
        this.el['wf-account-switcher'].addEventListener('click', () => this.setView('profiles'));
        this.el['wf-mobile-menu'].addEventListener('click', () => this.toggleMobileNav(true));
        this.el['wf-nav-overlay'].addEventListener('click', () => this.toggleMobileNav(false));

        document.querySelectorAll('.wf-nav-item').forEach(button => {
            button.addEventListener('click', () => this.setView(button.dataset.view));
        });
        this.el['wf-filter-bar'].addEventListener('click', event => {
            const button = event.target.closest('[data-filter]');
            if (!button) return;
            this.state.canonFilter = button.dataset.filter;
            this.el['wf-filter-bar'].querySelectorAll('[data-filter]').forEach(item => item.classList.toggle('active', item === button));
            this.renderFeed();
        });
        this.el['wf-content'].addEventListener('click', event => this.handleContentAction(event));
        this.el['wf-content'].addEventListener('input', event => {
            if (event.target.id !== 'wf-profile-search') return;
            this.state.profileSearch = event.target.value;
            const query = event.target.value.toLocaleLowerCase();
            this.el['wf-content'].querySelectorAll('[data-search]').forEach(card => { card.hidden = !card.dataset.search.includes(query); });
        });
        window.addEventListener('popstate', () => this.readRoute());
        this.el['wf-trends'].addEventListener('click', event => {
            const trend = event.target.closest('[data-hashtag]');
            if (trend) this.filterHashtag(trend.dataset.hashtag);
        });
        this.el['wf-profile-preview'].addEventListener('click', event => this.handleContentAction(event));

        document.querySelectorAll('.wf-modal-close').forEach(button => button.addEventListener('click', () => this.closeModals()));
        this.el['wf-modal-backdrop'].addEventListener('click', event => {
            if (event.target === this.el['wf-modal-backdrop']) this.closeModals();
        });
        document.addEventListener('keydown', event => {
            if (event.key === 'Escape') {
                this.closeModals();
                this.closeLightbox();
                this.toggleMobileNav(false);
            }
        });
        this.el['wf-profile-form'].addEventListener('submit', event => this.saveProfile(event));
        this.el['wf-relationship-form'].addEventListener('submit', event => this.saveRelationship(event));
        this.el['wf-world-form'].addEventListener('submit', event => this.saveWorld(event));
        this.el['wf-upload-avatar'].addEventListener('click', () => this.el['wf-avatar-input'].click());
        this.el['wf-upload-header'].addEventListener('click', () => this.el['wf-header-input'].click());
        this.el['wf-avatar-input'].addEventListener('change', event => this.uploadProfileArt(event, 'avatar'));
        this.el['wf-header-input'].addEventListener('change', event => this.uploadProfileArt(event, 'header'));
        this.el['wf-image-lightbox'].addEventListener('click', event => {
            if (event.target.tagName !== 'IMG') this.closeLightbox();
        });
    },

    async request(url, options = {}) {
        const response = await apiFetch(url, options);
        let data = null;
        try { data = await response.json(); } catch (_error) { /* no body */ }
        if (!response.ok) throw new Error(data?.detail || data?.error || `Request failed (${response.status})`);
        return data;
    },

    async selectWorld(worldId) {
        const generation = ++this.worldGeneration;
        ++this.viewGeneration;
        localStorage.setItem('world-feed-world', worldId);
        this.el['wf-world-select'].value = worldId;
        this.el['wf-content'].innerHTML = '<div class="wf-loading">Opening the world…</div>';
        try {
            const data = await this.request(`/api/world-feed/worlds/${encodeURIComponent(worldId)}`);
            if (generation !== this.worldGeneration) return;
            this.state.world = data.world;
            this.state.profiles = data.profiles || [];
            this.state.hashtag = '';
            this.state.authorFilter = '';
            this.ensureActiveProfile();
            this.renderWorldChrome();
            await Promise.all([this.loadTrends(), this.loadRelationships(), this.loadNotifications(false)]);
            if (generation !== this.worldGeneration) return;
            // A mobile navigation tap can land while this first load is still
            // in flight. Keep the view the person chose instead of bouncing
            // them back Home when the world payload finishes.
            await this.readRoute();
        } catch (error) {
            if (generation === this.worldGeneration) this.renderFatal(error);
        }
    },

    renderWorldPicker() {
        this.el['wf-world-select'].innerHTML = this.state.worlds.map(world =>
            `<option value="${this.attr(world.id)}">${this.text(world.name)}</option>`
        ).join('');
    },

    ensureActiveProfile() {
        const controlled = this.state.profiles.filter(profile => profile.is_user_controlled && profile.is_active);
        const chosen = controlled.find(profile => profile.id === this.state.activeProfileId) || controlled[0] || null;
        this.state.activeProfileId = chosen?.id || '';
        if (chosen) localStorage.setItem('world-feed-active-profile', chosen.id);
        else localStorage.removeItem('world-feed-active-profile');
    },

    activeProfile() {
        return this.state.profiles.find(profile => profile.id === this.state.activeProfileId) || null;
    },

    composerProfile() {
        return this.state.profiles.find(profile => profile.id === this.state.composerProfileId && profile.is_active) || this.activeProfile();
    },

    renderWorldChrome() {
        const world = this.state.world;
        const active = this.activeProfile();
        this.el['wf-clock-label'].textContent = world.clock_label || world.fictional_now || 'Story time';
        this.el['wf-world-description'].textContent = world.description || '';
        this.el['wf-composer'].classList.toggle('disabled', !active);
        this.el['wf-active-name'].textContent = active?.display_name || 'Choose your account';
        this.el['wf-active-handle'].textContent = active ? `@${active.handle}` : 'Create a user-controlled profile';
        this.renderAvatarInto(this.el['wf-active-avatar'], active);
        const composer = this.composerProfile();
        this.el['wf-post-as'].innerHTML = this.state.profiles.filter(p => p.is_active).map(p => `<option value="${this.attr(p.id)}" ${p.id === composer?.id ? 'selected' : ''}>${this.text(p.display_name)} (@${this.text(p.handle)})</option>`).join('');
        this.renderAvatarInto(this.el['wf-composer-avatar'], composer);
        this.renderProfilePreview();
    },

    async loadFeed(append = false) {
        if (!this.state.world) return;
        const params = new URLSearchParams();
        if (this.state.activeProfileId) params.set('viewer_profile_id', this.state.activeProfileId);
        if (this.state.hashtag) params.set('hashtag', this.state.hashtag);
        if (this.state.authorFilter) params.set('author_profile_id', this.state.authorFilter);
        if (this.state.view === 'profile') params.set('author_profile_id', this.state.detailId);
        if (this.state.view === 'bookmarks') params.set('bookmarks_only', 'true');
        if (this.state.view === 'search') params.set('search', this.state.detailId);
        if (this.state.view === 'following') params.set('following_only', 'true');
        if (this.state.view === 'media') params.set('media_only', 'true');
        if (this.state.view === 'review') params.set('canon_status', 'draft');
        if (append && this.state.nextCursor) {
            for (const [key, value] of Object.entries(this.state.nextCursor)) params.set(key, value);
        }
        const url = this.state.view === 'thread'
            ? `/api/world-feed/posts/${encodeURIComponent(this.state.detailId)}/thread?${params}`
            : `/api/world-feed/worlds/${encodeURIComponent(this.state.world.id)}/feed?${params}`;
        const data = await this.latestRequest('feed', url);
        if (!data) return;
        if (data.post) {
            this.state.thread = data.post;
            if (!append && !this.el['wf-post-text'].value.trim() && !this.state.quotePost) this.beginReply(data.post, false, false);
        }
        const posts = append ? [...this.state.posts, ...(data.posts || [])] : (data.posts || []);
        this.state.posts = [...new Map(posts.map(post => [post.id, post])).values()];
        this.state.nextCursor = data.next_cursor || null;
    },

    async loadTrends() {
        const data = await this.latestRequest('trends', `/api/world-feed/worlds/${encodeURIComponent(this.state.world.id)}/trends`);
        if (!data) return;
        this.state.trends = data.trends || [];
        this.renderTrends();
    },

    async loadRelationships() {
        const data = await this.latestRequest('relationships', `/api/world-feed/worlds/${encodeURIComponent(this.state.world.id)}/relationships`);
        if (!data) return;
        this.state.relationships = data.relationships || [];
    },

    renderNotificationBadge(unread) {
        this.el['wf-notice-badge'].textContent = unread ? String(unread) : '';
        const mobile = document.getElementById('wf-notice-mobile-badge');
        if (mobile) mobile.textContent = unread ? String(unread) : '';
        document.querySelectorAll('[data-view="notifications"]').forEach(button => {
            button.setAttribute('aria-label', unread ? `Open notifications, ${unread} unread` : 'Open notifications');
        });
    },

    async loadNotifications(render = true) {
        const active = this.activeProfile();
        if (!active) {
            this.state.notifications = [];
            this.renderNotificationBadge(0);
            return;
        }
        const data = await this.latestRequest('notifications', `/api/world-feed/worlds/${encodeURIComponent(this.state.world.id)}/notifications?profile_id=${encodeURIComponent(active.id)}`);
        if (!data || this.state.activeProfileId !== active.id) return;
        this.state.notifications = data.notifications || [];
        const unread = data.unread_count ?? this.state.notifications.filter(item => !item.is_read).length;
        this.renderNotificationBadge(unread);
        if (render && this.state.view === 'notifications') this.renderNotifications();
    },

    async refreshCurrentView() {
        this.el['wf-refresh'].disabled = true;
        try {
            await Promise.all([this.loadTrends(), this.loadRelationships(), this.loadNotifications(false), this.reloadProfiles()]);
            await this.setView(this.state.view, this.state.detailId, true);
        } catch (error) { this.toast(error.message, true); }
        finally { this.el['wf-refresh'].disabled = false; }
    },

    readRoute() {
        const [view, id = ''] = location.hash.slice(1).split('/');
        const valid = ['home', 'following', 'media', 'bookmarks', 'search', 'photos', 'review', 'notifications', 'dms', 'profiles', 'relationships', 'profile', 'thread', 'followers', 'following-list'];
        let decoded = '';
        try { decoded = decodeURIComponent(id); } catch (_error) { return this.setView('home', '', true); }
        return this.setView(valid.includes(view) ? view : 'home', decoded, true);
    },

    async setView(view, detailId = '', fromHistory = false) {
        const changed = this.state.view !== view || this.state.detailId !== detailId;
        const generation = ++this.viewGeneration;
        this.requests.feed = (this.requests.feed || 0) + 1;
        this.requests.detail = (this.requests.detail || 0) + 1;
        this.requests.connections = (this.requests.connections || 0) + 1;
        this.state.view = view;
        this.state.detailId = detailId;
        this.state.nextCursor = null;
        this.state.profile = null;
        this.state.thread = null;
        if (['profile', 'thread', 'followers', 'following-list'].includes(view)) {
            this.state.hashtag = '';
            this.state.authorFilter = '';
        }
        if (!fromHistory) history.pushState(null, '', `#${view}${detailId ? '/' + encodeURIComponent(detailId) : ''}`);
        this.toggleMobileNav(false);
        document.querySelectorAll('.wf-nav-item').forEach(button => button.classList.toggle('active', button.dataset.view === view));
        const labels = {
            bookmarks: ['Bookmarks', 'Posts saved privately to your account.'],
            search: ['Search', this.state.detailId ? `Results for “${this.state.detailId}”` : 'Search posts, names, and handles.'],
            home: ['Home', 'The world continuing around the story.'],
            following: ['Following', 'Your accounts, plus your own posts. Replies can arrive from anyone.'],
            profile: ['Account', 'Posts, replies, and people.'],
            thread: ['Conversation', 'Replies to this post, newest first.'],
            followers: ['Followers', 'Following does not automatically go both ways.'],
            'following-list': ['Following', 'Accounts this person follows.'],
            media: ['Media', 'Images shared across this world.'],
            photos: ['Photo studio', 'Little snapshots from a world with people in it.'],
            notifications: ['Notifications', 'Mentions, replies, likes, and follows.'],
            dms: ['Messages', 'Private conversations inside this story world.'],
            profiles: ['Profiles', 'Every person, institution, villain, fan, and rando.'],
            relationships: ['Relationships', 'The history begins where your story actually is.'],
            review: ['Canon review', 'Generated photos and proposed story events wait here for your word.'],
        };
        this.el['wf-view-title'].textContent = labels[view]?.[0] || 'World Feed';
        this.el['wf-view-subtitle'].textContent = labels[view]?.[1] || '';
        this.el['wf-composer'].classList.toggle('disabled', !this.activeProfile() || !['home', 'following', 'media', 'thread'].includes(view));
        this.el['wf-filter-bar'].style.display = ['home', 'following', 'media', 'review'].includes(view) ? '' : 'none';
        if (!this.state.world) return;
        try {
            this.el['wf-content'].innerHTML = '<div class="wf-loading">Loading…</div>';
            if (['profile', 'followers', 'following-list'].includes(view)) {
                const profile = await this.latestRequest('detail', `/api/world-feed/profiles/${encodeURIComponent(detailId)}?viewer_profile_id=${encodeURIComponent(this.state.activeProfileId || '')}`);
                if (generation !== this.viewGeneration || !profile) return;
                if (profile.world_id !== this.state.world.id) throw new Error('This account belongs to a different story world.');
                this.state.profile = profile;
                this.el['wf-view-title'].textContent = profile.display_name;
            }
            if (['home', 'following', 'media', 'bookmarks', 'search', 'review', 'profile', 'thread'].includes(view)) await this.loadFeed();
            if (['followers', 'following-list'].includes(view)) await this.loadConnections();
            if (view === 'photos') await this.loadPhotos();
            if (view === 'notifications') {
                await this.loadNotifications(false);
                if (generation !== this.viewGeneration) return;
                await this.markNotificationsRead();
            }
            if (generation === this.viewGeneration) {
                this.renderCurrentView();
                if (changed) {
                    window.scrollTo({ top: 0, behavior: 'instant' });
                    document.body.scrollTop = 0;
                }
            }
        } catch (error) { if (generation === this.viewGeneration) this.renderError(error); }
    },

    renderCurrentView() {
        if (['home', 'following', 'media', 'bookmarks', 'search', 'review', 'profile', 'thread'].includes(this.state.view)) this.renderFeed();
        else if (['followers', 'following-list'].includes(this.state.view)) this.renderConnections();
        else if (this.state.view === 'profiles') this.renderProfiles();
        else if (this.state.view === 'relationships') this.renderRelationships();
        else if (this.state.view === 'notifications') this.renderNotifications();
        else if (this.state.view === 'photos') this.renderPhotos();
    },

    async loadPhotos() {
        const data = await this.latestRequest('photos', `/api/world-feed/worlds/${encodeURIComponent(this.state.world.id)}/photos`);
        if (data) this.state.photoData = data;
    },

    renderPhotos() {
        const config = this.state.photoData?.settings || { enabled: false, daily_limit: 1, profile_ids: [] };
        const references = this.state.photoData?.visual_references || [];
        const profiles = this.state.profiles.filter(p => !p.is_user_controlled && p.is_active);
        const options = profiles.map(p => `<option value="${this.attr(p.id)}">${this.text(p.display_name)} (@${this.text(p.handle)})</option>`).join('');
        this.el['wf-content'].innerHTML = `<div class="wf-photo-studio">
            <p>Generate a character's picture and caption. Images use paid image credits. Every result goes to <a href="#review" id="wf-photo-review">Canon review</a> before it appears in the feed.</p>
            <p class="wf-muted">Pictures you deliberately request here are not limited by the unattended background-photo budget.</p>
            ${references.map(ref => `<details class="wf-photo-reference"><summary>${this.text(ref.display_name)} · visual reference</summary>
                <p>Her reference is included automatically when you request her in a picture or an established story moment supports it. Her dialogue and decisions stay yours.</p>
                <a href="${this.attr(this.safeImage(ref.reference_url))}" target="_blank" rel="noopener"><img src="${this.attr(this.safeImage(ref.reference_url))}" alt="${this.attr(ref.display_name)} appearance reference" style="max-width:100%;width:280px;height:auto" loading="lazy"></a>
                <p>${this.text(ref.description)}</p><p>${this.text(ref.wardrobe)}</p></details>`).join('')}
            <form id="wf-photo-form" class="wf-form-grid">
                <label class="wf-span-2">Account<select id="wf-photo-author" required>${options}</select></label>
                <label class="wf-span-2">An idea (optional)<textarea id="wf-photo-idea" rows="3" maxlength="1500" placeholder="Mina's new dance sneakers, Ochako's lunch, a public hero-agency photo…"></textarea></label>
                <button class="wf-primary-button" type="submit" ${profiles.length ? '' : 'disabled'}>Generate photo post</button>
            </form>
            <details class="wf-photo-settings"><summary>Occasional background photos</summary>
                <form id="wf-photo-settings-form">
                    <p>Choose an allowance of up to ${config.max_daily_limit || 20} automatic photo attempts per rolling 24 hours. Photos are spaced across the day, with no catch-up batches. Failed attempts count; unused allowance is not carried forward. Deliberate Photo Studio requests are additional.</p>
                    <p>Pause when unfinished or unreviewed photos reach the daily allowance (at least two). Every image still waits in Canon review. This is separate from general character pulses. No automatic retries.</p>
                    <label><input type="checkbox" id="wf-photos-enabled" ${config.enabled ? 'checked' : ''}> Enable occasional photo drafts</label>
                    <label class="wf-photo-limit">Automatic photos per rolling 24 hours<select id="wf-photo-limit">${Array.from({ length: config.max_daily_limit || 20 }, (_, i) => i + 1).map(n => `<option value="${n}" ${n === config.daily_limit ? 'selected' : ''}>${n}</option>`).join('')}</select></label>
                    <div class="wf-photo-accounts">${profiles.map(p => `<label><input type="checkbox" name="photo-profile" value="${this.attr(p.id)}" ${config.profile_ids.includes(p.id) ? 'checked' : ''}> ${this.text(p.display_name)}</label>`).join('')}</div>
                    <button class="wf-secondary-button" type="submit">Save photo settings</button>
                </form>
            </details>
            <h2>Photo requests</h2><p class="wf-muted">The worker checks once a minute; rendering can take several minutes. This list refreshes while you stay here.</p>
            <div id="wf-photo-jobs"></div>
        </div>`;
        document.getElementById('wf-photo-review').addEventListener('click', event => { event.preventDefault(); this.setView('review'); });
        document.getElementById('wf-photo-form').addEventListener('submit', event => this.submitPhoto(event));
        document.getElementById('wf-photo-settings-form').addEventListener('submit', event => this.savePhotoSettings(event));
        this.renderPhotoJobs();
    },

    renderPhotoJobs() {
        if (this.state.view !== 'photos') return;
        const target = document.getElementById('wf-photo-jobs');
        if (!target) return;
        const jobs = this.state.photoData?.jobs || [];
        target.innerHTML = jobs.length ? jobs.map(job => {
            const profile = this.state.profiles.find(p => p.id === job.profile_id);
            const status = job.status === 'ready' ? ({ draft: 'Waiting in Canon review', approved: 'Published', rejected: 'Not published' }[job.canon_status] || 'Image saved') : job.status;
            return `<article class="wf-photo-job"><strong>${this.text(profile?.display_name || 'Account')}</strong> <span>${this.text(status)}</span>${job.image_url ? this.mediaHtml([{ url: job.image_url, alt_text: 'Generated photo awaiting review' }]) : ''}${job.error ? `<p>${this.text(job.error)}</p>` : ''}</article>`;
        }).join('') : '<p class="wf-muted">No photo requests yet.</p>';
    },

    async submitPhoto(event) {
        event.preventDefault();
        const worldId = this.state.world.id;
        const profileId = document.getElementById('wf-photo-author').value;
        const idea = document.getElementById('wf-photo-idea').value.trim();
        const key = JSON.stringify([worldId, profileId, idea]);
        // Keep the same request id after a lost HTTP response: no duplicate paid jobs.
        if (this.state.photoRequest?.key !== key) this.state.photoRequest = { key, id: crypto.randomUUID().replaceAll('-', '') };
        const button = event.target.querySelector('button[type="submit"]');
        button.disabled = true;
        try {
            await this.request(`/api/world-feed/worlds/${encodeURIComponent(worldId)}/photos`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ profile_id: profileId, idea, job_id: this.state.photoRequest.id }),
            });
            this.state.photoRequest = null;
            this.toast('Photo queued. It will appear in Canon review when ready.');
            if (this.state.world.id === worldId) { await this.loadPhotos(); this.renderPhotoJobs(); }
        } catch (error) { this.toast(error.message, true); }
        finally { button.disabled = false; }
    },

    async savePhotoSettings(event) {
        event.preventDefault();
        const worldId = this.state.world.id;
        const form = event.target;
        const button = form.querySelector('button[type="submit"]');
        button.disabled = true;
        try {
            const world = await this.request(`/api/world-feed/worlds/${encodeURIComponent(worldId)}/photos/settings`, {
                method: 'PUT', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled: document.getElementById('wf-photos-enabled').checked,
                    daily_limit: Number(document.getElementById('wf-photo-limit').value),
                    profile_ids: [...form.querySelectorAll('input[name="photo-profile"]:checked')].map(input => input.value) }),
            });
            if (this.state.world.id === worldId) { this.state.world = world; await this.loadPhotos(); }
            this.toast('Photo settings saved. Generated posts wait for your review.');
        } catch (error) { this.toast(error.message, true); }
        finally { button.disabled = false; }
    },

    renderFeed() {
        let posts = this.state.posts;
        if (!['profile', 'thread'].includes(this.state.view) && this.state.canonFilter !== 'all') posts = posts.filter(post => post.canon_level === this.state.canonFilter);
        this.el['wf-filter-context'].innerHTML = '';
        if (this.state.hashtag) {
            this.el['wf-filter-context'].innerHTML = `#${this.text(this.state.hashtag)} <button data-clear-filter>×</button>`;
            this.el['wf-filter-context'].querySelector('button').addEventListener('click', () => this.clearFeedFilters());
        }
        const more = this.state.nextCursor ? '<button class="wf-secondary-button" data-action="load-more">Load older posts</button>' : '';
        const prefix = this.state.view === 'profile' ? this.profileHeaderHtml(this.state.profile)
            : this.state.view === 'thread' && this.state.thread ? `${this.state.thread.parent_post ? `<a class="wf-thread-parent" href="#thread/${this.attr(this.state.thread.parent_post.id)}" data-open-post="${this.attr(this.state.thread.parent_post.id)}">↑ View parent post</a>` : ''}${this.postHtml(this.state.thread)}<h3 class="wf-section-label">Replies</h3>` : '';
        if (!posts.length) {
            const noProfiles = !this.state.profiles.length;
            this.el['wf-content'].innerHTML = `<div class="wf-empty"><strong>${noProfiles ? 'This world is waiting for its cast.' : 'The timeline is quiet.'}</strong><span>${noProfiles ? 'Build the first profile, then the world can begin speaking.' : 'No posts match this view yet.'}</span>${noProfiles ? '<button class="wf-primary-button" data-action="new-profile">Create first profile</button>' : ''}</div>`;
            this.el['wf-content'].innerHTML = prefix + this.el['wf-content'].innerHTML + more;
            this.applyBackgrounds();
            return;
        }
        this.el['wf-content'].innerHTML = prefix + posts.map(post => this.postHtml(post)).join('') + more;
        this.applyBackgrounds();
    },

    postHtml(post) {
        const author = post.author;
        const media = this.mediaHtml(post.media || []);
        const context = post.parent_post
            ? `<div class="wf-post-context">Replying to <a href="#" data-profile="${this.attr(post.parent_post.author.id)}">@${this.text(post.parent_post.author.handle)}</a></div>`
            : '';
        const embedded = post.quoted_post ? this.embeddedPostHtml(post.quoted_post) : '';
        const draft = post.canon_status === 'draft' ? '<span class="wf-draft-marker">· awaiting approval</span>' : '';
        const review = this.state.view === 'review' ? `<div class="wf-review-actions"><button class="wf-secondary-button" data-action="reject" data-post="${this.attr(post.id)}">Reject</button><button class="wf-primary-button" data-action="approve" data-post="${this.attr(post.id)}">Approve</button></div>` : '';
        return `<article class="wf-post" data-post-id="${this.attr(post.id)}">
            ${this.profileLink(author, this.avatarHtml(author))}
            <div class="wf-post-main">
                ${context}
                <div class="wf-post-head">${this.profileLink(author, `<strong>${this.text(author.display_name)}</strong>`)}${author.is_verified ? '<span class="wf-verified" title="Verified">●</span>' : ''}${this.profileLink(author, `<span class="wf-handle">@${this.text(author.handle)}</span>`)}<a class="wf-post-time" href="#thread/${this.attr(post.id)}" data-open-post="${this.attr(post.id)}">· ${this.text(post.fictional_at || formatRelativeTime(post.created_at))}</a></div>
                <div class="wf-post-body">${post.post_type === 'repost' ? '<small>↻ Reposted</small>' : this.linkify(post.body)}</div>
                ${media}${embedded}
                <div class="wf-post-actions">
                    <button class="wf-post-action" data-action="reply" data-post="${this.attr(post.id)}">↩ <span>${post.reply_count || ''}</span></button>
                    <button class="wf-post-action" data-action="quote" data-post="${this.attr(post.id)}">❝</button>
                    <button class="wf-post-action ${post.viewer_liked ? 'liked' : ''}" data-action="like" data-post="${this.attr(post.id)}">♥ <span>${post.like_count || ''}</span></button>
                    <button class="wf-post-action ${post.viewer_reposted ? 'liked' : ''}" aria-label="Repost" aria-pressed="${!!post.viewer_reposted}" data-action="repost" data-post="${this.attr(post.id)}" ${post.post_type === 'repost' ? 'hidden' : ''}>↻</button>
                    <button class="wf-post-action ${post.viewer_bookmarked ? 'liked' : ''}" aria-label="Bookmark" aria-pressed="${!!post.viewer_bookmarked}" data-action="bookmark" data-post="${this.attr(post.id)}">⚑</button>
                    <button class="wf-post-action" data-action="canon" data-post="${this.attr(post.id)}"><span class="wf-canon-badge wf-canon-${this.attr(post.canon_level)}">${this.text(post.canon_level)}</span>${draft}</button>
                </div>${review}
            </div>
        </article>`;
    },

    embeddedPostHtml(post) {
        return `<div class="wf-embedded-post" data-open-post="${this.attr(post.id)}">${this.profileLink(post.author, `<strong>${this.text(post.author.display_name)}</strong>`)} <small>@${this.text(post.author.handle)}</small><div>${this.linkify(post.body)}</div>${this.mediaHtml((post.media || []).slice(0, 1))}</div>`;
    },

    mediaHtml(items) {
        if (!items?.length) return '';
        const countClass = items.length === 1 ? ' one' : '';
        return `<div class="wf-post-media${countClass}">${items.map(item => {
            const url = this.safeImage(item.url);
            const preview = /^\/api\/images\/file\/[^/?#]+$/.test(url) ? `${url}?width=640` : url;
            return url ? `<img src="${this.attr(preview)}" alt="${this.attr(item.alt_text || item.caption || 'Shared image')}" loading="lazy" decoding="async" width="640" height="640" data-lightbox="${this.attr(url)}">` : '';
        }).join('')}</div>`;
    },

    profileLink(profile, content) {
        return profile?.id ? `<a class="wf-account-link" href="#profile/${this.attr(profile.id)}" data-profile="${this.attr(profile.id)}">${content}</a>` : content;
    },

    profileHeaderHtml(profile) {
        if (!profile) return '';
        const header = this.safeImage(profile.header_url);
        const follow = this.activeProfile() && profile.id !== this.state.activeProfileId ? `<button class="wf-secondary-button" data-action="follow" data-profile="${this.attr(profile.id)}">${profile.viewer_follows ? 'Unfollow' : 'Follow'}</button>` : '';
        return `<section class="wf-account-header" style="--profile-accent:${this.attr(profile.accent_color)}">
            <div class="wf-profile-header" ${header ? `data-bg="${this.attr(header)}"` : ''}></div>
            <div class="wf-profile-info">${this.avatarHtml(profile, 'wf-avatar-large')}
            <div class="wf-profile-card-actions">${follow}<button class="wf-secondary-button" data-action="edit-profile" data-profile="${this.attr(profile.id)}">Edit profile</button></div>
            <h2>${this.profileLink(profile, this.text(profile.display_name))} ${profile.is_verified ? '<span class="wf-verified">●</span>' : ''}</h2>
            <div class="wf-handle">@${this.text(profile.handle)} ${profile.follows_viewer ? '<span class="wf-follows-you">Follows you</span>' : ''}</div>
            <p>${this.text(profile.bio)}</p><div class="wf-profile-meta">${this.text(profile.location)}${profile.website ? ' · ' + this.text(profile.website) : ''}</div>
            <div class="wf-account-counts"><button data-action="connections" data-direction="following-list" data-profile="${this.attr(profile.id)}"><strong>${profile.following}</strong> Following</button><button data-action="connections" data-direction="followers" data-profile="${this.attr(profile.id)}"><strong>${profile.followers}</strong> Followers</button><span>${profile.posts} posts & replies</span></div>
            </div></section>`;
    },

    applyBackgrounds() {
        this.el['wf-content'].querySelectorAll('[data-bg]').forEach(element => { element.style.backgroundImage = `url("${element.dataset.bg.replaceAll('"', '%22')}")`; });
    },

    async loadConnections(append = false) {
        const params = new URLSearchParams({ direction: this.state.view === 'followers' ? 'followers' : 'following' });
        if (append && this.state.connectionCursor) params.set('after_id', this.state.connectionCursor);
        const data = await this.latestRequest('connections', `/api/world-feed/profiles/${encodeURIComponent(this.state.detailId)}/connections?${params}`);
        if (!data) return;
        this.state.connections = append ? [...this.state.connections, ...data.profiles] : data.profiles;
        this.state.connectionCursor = data.next_cursor;
    },

    renderConnections() {
        const followers = this.state.view === 'followers';
        const options = this.state.profiles.filter(p => p.id !== this.state.detailId).map(p => `<option value="${this.attr(p.id)}">${this.text(p.display_name)} (@${this.text(p.handle)})</option>`).join('');
        const editor = `<div class="wf-connection-editor"><label for="wf-connection-choice">Story editor: add ${followers ? 'someone who follows this account' : 'an account this person follows'}</label><select id="wf-connection-choice"><option value="">Choose an account…</option>${options}</select><button class="wf-secondary-button" data-action="add-connection">Add ${followers ? 'follower' : 'follow'}</button><small>One direction only. No automatic follow-back.</small></div>`;
        this.el['wf-content'].innerHTML = this.profileHeaderHtml(this.state.profile) + `<h3 class="wf-section-label">${followers ? 'Followers' : 'Following'}</h3>` + editor + this.state.connections.map(profile => `<article class="wf-connection">${this.profileLink(profile, this.avatarHtml(profile))}<div>${this.profileLink(profile, `<strong>${this.text(profile.display_name)}</strong><div class="wf-handle">@${this.text(profile.handle)}</div>`)}<p>${this.text(profile.bio)}</p><button class="wf-secondary-button" data-action="remove-connection" data-profile="${this.attr(profile.id)}">Remove ${followers ? 'follower' : 'follow'}</button></div></article>`).join('') + (!this.state.connections.length ? '<div class="wf-empty">No accounts here yet.</div>' : '') + (this.state.connectionCursor ? '<button class="wf-secondary-button" data-action="more-connections">Show more accounts</button>' : '');
        this.applyBackgrounds();
    },

    async editConnection(otherId, active) {
        if (!otherId) return this.toast('Choose an account first.');
        const followers = this.state.view === 'followers';
        const follower = followers ? otherId : this.state.detailId;
        const followed = followers ? this.state.detailId : otherId;
        await this.request(`/api/world-feed/worlds/${encodeURIComponent(this.state.world.id)}/follow/${encodeURIComponent(followed)}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ profile_id: follower, active }) });
        await this.setView(this.state.view, this.state.detailId, true);
    },

    async followProfile(profileId) {
        if (!this.activeProfile()) return this.toast('Choose your account first.', true);
        await this.request(`/api/world-feed/worlds/${encodeURIComponent(this.state.world.id)}/follow/${encodeURIComponent(profileId)}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ profile_id: this.state.activeProfileId }) });
        await this.setView(this.state.view, this.state.detailId, true);
    },

    renderProfiles() {
        if (!this.state.profiles.length) {
            this.el['wf-content'].innerHTML = '<div class="wf-empty"><strong>No profiles yet.</strong><span>Create a profile you control, then add your fictional cast.</span><button class="wf-primary-button" data-action="new-profile">Create first profile</button></div>';
            return;
        }
        this.el['wf-content'].innerHTML = `<div class="wf-profile-page-actions"><input id="wf-profile-search" type="search" aria-label="Search accounts" placeholder="Search name, handle, or group…" value="${this.attr(this.state.profileSearch)}"><button class="wf-primary-button" data-action="new-profile">＋ New profile</button></div><div class="wf-profile-grid">${this.state.profiles.map(profile => {
            const header = this.safeImage(profile.header_url);
            const style = `--profile-accent:${this.attr(profile.accent_color || '#D9485F')}`;
            const use = profile.is_user_controlled ? `<button class="wf-secondary-button" data-action="use-profile" data-profile="${this.attr(profile.id)}">${profile.id === this.state.activeProfileId ? 'Active account' : 'Use as me'}</button>` : '';
            const search = [profile.display_name, profile.handle, profile.bio, profile.metadata?.roster_group || ''].join(' ').toLocaleLowerCase();
            return `<article class="wf-profile-card" style="${style}" data-search="${this.attr(search)}" ${search.includes(this.state.profileSearch.toLocaleLowerCase()) ? '' : 'hidden'}>
                <div class="wf-profile-header" ${header ? `data-bg="${this.attr(header)}"` : ''}></div>
                <div class="wf-profile-info">${this.profileLink(profile, this.avatarHtml(profile))}<div class="wf-profile-card-actions">${use} <button class="wf-secondary-button" data-action="edit-profile" data-profile="${this.attr(profile.id)}">Edit</button></div>
                <h3>${this.profileLink(profile, this.text(profile.display_name))} ${profile.is_verified ? '<span class="wf-verified">●</span>' : ''}</h3><div class="wf-handle">@${this.text(profile.handle)} · ${this.text(profile.account_type)}</div>
                <p>${this.text(profile.bio || 'No bio yet.')}</p><div class="wf-profile-meta">${this.text(profile.location || '')}${profile.website ? ` · ${this.text(profile.website)}` : ''}</div></div>
            </article>`;
        }).join('')}</div>`;
        this.el['wf-content'].querySelectorAll('[data-bg]').forEach(element => { element.style.backgroundImage = `url("${element.dataset.bg.replaceAll('"', '%22')}")`; });
    },

    renderRelationships() {
        if (!this.state.relationships.length) {
            this.el['wf-content'].innerHTML = '<div class="wf-empty"><strong>No relationships described yet.</strong><span>You do not have to rebuild anyone from strangers.</span><button class="wf-primary-button" data-action="new-relationship">Describe the first bond</button></div>';
            return;
        }
        this.el['wf-content'].innerHTML = `<div class="wf-relationship-list">${this.state.relationships.map(rel => `<article class="wf-relationship-card"><h3>${this.text(rel.from_profile.display_name)} <span class="wf-rel-arrow">→</span> ${this.text(rel.to_profile.display_name)}</h3><strong>${this.text(rel.relationship_type || 'Relationship')}</strong><p>${this.text(rel.public_summary || 'No public summary.')}</p><div class="wf-relationship-private"><small>Private story truth</small><br>${this.text(rel.private_context || 'Not described yet.')}</div></article>`).join('')}</div>`;
    },

    renderNotifications() {
        const notices = this.state.notifications || [];
        if (!this.activeProfile()) {
            this.el['wf-content'].innerHTML = '<div class="wf-empty"><strong>No active account.</strong><span>Create or choose the profile you control.</span></div>';
            return;
        }
        if (!notices.length) {
            this.el['wf-content'].innerHTML = '<div class="wf-empty"><strong>All quiet.</strong><span>Mentions, replies, likes, and follows will gather here.</span></div>';
            return;
        }
        const verbs = { mention: 'mentioned you', reply: 'replied to you', quote: 'quoted your post', repost: 'reposted your post', like: 'liked your post', follow: 'followed you' };
        this.el['wf-content'].innerHTML = `<div class="wf-notification-list">${notices.map(item => `<article class="wf-notification ${item.is_read ? '' : 'unread'}" ${item.post_id ? `data-open-post="${this.attr(item.post_id)}"` : ''}>${this.profileLink(item.actor, this.avatarHtml(item.actor))}<div><p>${this.profileLink(item.actor, `<strong>${this.text(item.actor?.display_name || 'Someone')}</strong>`)} ${this.text(verbs[item.event_type] || item.event_type)}</p>${item.post_id ? `<a href="#thread/${this.attr(item.post_id)}" data-open-post="${this.attr(item.post_id)}">View conversation</a> · ` : ''}<small>${this.text(formatRelativeTime(item.created_at))}</small></div></article>`).join('')}</div>`;
    },

    renderTrends() {
        this.el['wf-trends'].innerHTML = this.state.trends.length ? this.state.trends.map((trend, index) => `<button class="wf-trend" data-hashtag="${this.attr(trend.normalized)}"><small>${index + 1} · Trending in ${this.text(this.state.world.name)}</small><strong>#${this.text(trend.tag)}</strong><small>${trend.post_count} post${trend.post_count === 1 ? '' : 's'}</small></button>`).join('') : '<p class="wf-muted" style="padding:0 15px 14px">Hashtags will gather here.</p>';
    },

    renderProfilePreview() {
        const profiles = this.state.profiles.slice(0, 5);
        this.el['wf-profile-preview'].innerHTML = profiles.length ? profiles.map(profile => `<button class="wf-profile-mini" data-profile="${this.attr(profile.id)}">${this.avatarHtml(profile, 'wf-avatar-small')}<span><strong>${this.text(profile.display_name)}</strong><small>@${this.text(profile.handle)}</small></span></button>`).join('') : '<p class="wf-muted" style="padding:0 15px 14px">The cast has not arrived yet.</p>';
    },

    async handleContentAction(event) {
        const lightbox = event.target.closest('[data-lightbox]');
        if (lightbox) return this.openLightbox(lightbox.dataset.lightbox, lightbox.alt);
        const hashtag = event.target.closest('[data-hashtag]');
        if (hashtag) { event.preventDefault(); return this.filterHashtag(hashtag.dataset.hashtag); }
        const action = event.target.closest('[data-action]');
        if (!action) {
            const profile = event.target.closest('[data-profile]');
            const mention = event.target.closest('[data-mention]');
            const target = profile?.dataset.profile || (mention && this.state.profiles.find(item => item.handle.toLocaleLowerCase() === mention.dataset.mention)?.id);
            if (profile || mention) {
                event.preventDefault();
                return target ? this.setView('profile', target) : this.toast('That account has not been created yet.');
            }
            const postLink = event.target.closest('[data-open-post], [data-post-id]');
            if (postLink && !window.getSelection()?.toString()) {
                event.preventDefault();
                return this.setView('thread', postLink.dataset.openPost || postLink.dataset.postId);
            }
            return;
        }
        event.preventDefault();
        const post = [this.state.thread, ...this.state.posts].find(item => item?.id === action.dataset.post);
        try {
            if (action.dataset.action === 'load-more') {
                action.disabled = true;
                try { await this.loadFeed(true); this.renderCurrentView(); }
                finally { action.disabled = false; }
            }
            else if (action.dataset.action === 'new-profile') this.openProfileModal();
            else if (action.dataset.action === 'new-relationship') this.openRelationshipModal();
            else if (action.dataset.action === 'edit-profile') this.openProfileModal(this.state.profiles.find(item => item.id === action.dataset.profile));
            else if (action.dataset.action === 'use-profile') this.useProfile(action.dataset.profile);
            else if (action.dataset.action === 'follow') {
                action.disabled = true;
                try { await this.followProfile(action.dataset.profile); } finally { action.disabled = false; }
            }
            else if (action.dataset.action === 'connections') await this.setView(action.dataset.direction, action.dataset.profile);
            else if (action.dataset.action === 'add-connection' || action.dataset.action === 'remove-connection') {
                action.disabled = true;
                try { await this.editConnection(action.dataset.profile || document.getElementById('wf-connection-choice').value, action.dataset.action === 'add-connection'); }
                finally { action.disabled = false; }
            }
            else if (action.dataset.action === 'more-connections') {
                action.disabled = true;
                try { await this.loadConnections(true); this.renderConnections(); } finally { action.disabled = false; }
            }
            else if (action.dataset.action === 'reply') this.beginReply(post);
            else if (action.dataset.action === 'quote') this.beginQuote(post);
            else if (['like', 'bookmark', 'repost'].includes(action.dataset.action)) await this.reactPost(post.id, action.dataset.action);
            else if (action.dataset.action === 'canon') await this.changeCanon(post);
            else if (action.dataset.action === 'approve') await this.updateCanon(post.id, { canon_status: 'approved' });
            else if (action.dataset.action === 'reject') await this.updateCanon(post.id, { canon_status: 'rejected' });
        } catch (error) { this.toast(error.message, true); }
    },

    beginReply(post, focus = true, prefill = true) {
        if (!this.activeProfile()) return this.toast('Choose your user-controlled account first.', true);
        this.state.replyTo = post;
        this.state.quotePost = null;
        this.el['wf-replying-to'].hidden = false;
        this.el['wf-replying-to'].innerHTML = `Replying to <strong>@${this.text(post.author.handle)}</strong> <button data-cancel-reply>×</button>`;
        this.el['wf-replying-to'].querySelector('button').addEventListener('click', () => this.cancelPostContext());
        if (prefill && !this.el['wf-post-text'].value.trim()) this.el['wf-post-text'].value = `@${post.author.handle} `;
        this.updateCharCount();
        if (focus) this.focusComposer();
        this.saveDraft?.();
    },

    beginQuote(post, focus = true) {
        if (!this.activeProfile()) return this.toast('Choose your user-controlled account first.', true);
        this.state.quotePost = post;
        this.state.replyTo = null;
        this.el['wf-replying-to'].hidden = false;
        this.el['wf-replying-to'].innerHTML = `Quoting <strong>@${this.text(post.author.handle)}</strong> <button data-cancel-reply>×</button>`;
        this.el['wf-replying-to'].querySelector('button').addEventListener('click', () => this.cancelPostContext());
        if (focus) this.focusComposer();
        this.saveDraft?.();
    },

    cancelPostContext() {
        this.state.replyTo = null;
        this.state.quotePost = null;
        this.el['wf-replying-to'].hidden = true;
        this.el['wf-replying-to'].innerHTML = '';
    },

    renderMediaPreview() {
        this.el['wf-media-preview'].innerHTML = this.state.media.map((item, index) => `<figure><img src="${this.attr(this.safeImage(item.url))}" alt="${this.attr(item.alt_text || 'Attached image')}"><button data-remove-media="${index}" aria-label="Remove image">×</button></figure>`).join('');
        this.el['wf-media-preview'].querySelectorAll('[data-remove-media]').forEach(button => button.addEventListener('click', () => {
            this.state.media.splice(Number(button.dataset.removeMedia), 1);
            this.renderMediaPreview();
        }));
    },

    async uploadImage(file) {
        const data = new FormData();
        data.append('file', file);
        data.append('identity', 'world-feed');
        return this.request('/api/images/upload', { method: 'POST', body: data });
    },

    async changeCanon(post) {
        const order = ['ambient', 'interaction', 'major'];
        const next = order[(order.indexOf(post.canon_level) + 1) % order.length];
        await this.updateCanon(post.id, { canon_level: next });
    },

    async updateCanon(postId, payload) {
        await this.request(`/api/world-feed/posts/${encodeURIComponent(postId)}/canon`, {
            method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        });
        await this.loadFeed();
        this.renderCurrentView();
    },

    filterHashtag(tag) {
        this.state.hashtag = String(tag || '').replace(/^#/, '').toLowerCase();
        this.setView('home');
    },

    clearFeedFilters() {
        this.state.hashtag = '';
        this.state.authorFilter = '';
        this.setView('home');
    },

    useProfile(profileId) {
        const profile = this.state.profiles.find(item => item.id === profileId);
        if (!profile?.is_user_controlled) return this.toast('Only profiles you control can be used as your account.', true);
        this.state.activeProfileId = profileId;
        localStorage.setItem('world-feed-active-profile', profileId);
        this.renderWorldChrome();
        this.toast(`Posting as ${profile.display_name}.`);
        this.setView('home');
    },

    openProfileModal(profile = null) {
        const form = this.el['wf-profile-form'];
        form.reset();
        this.el['wf-profile-id'].value = profile?.id || '';
        this.el['wf-profile-modal-title'].textContent = profile ? `Edit ${profile.display_name}` : 'Create profile';
        this.el['wf-profile-name'].value = profile?.display_name || '';
        this.el['wf-profile-handle'].value = profile?.handle || '';
        this.el['wf-profile-type'].value = profile?.account_type || 'character';
        this.el['wf-profile-color'].value = /^#[0-9a-f]{6}$/i.test(profile?.accent_color || '') ? profile.accent_color : '#D9485F';
        this.el['wf-profile-bio'].value = profile?.bio || '';
        this.el['wf-profile-location'].value = profile?.location || '';
        this.el['wf-profile-website'].value = profile?.website || '';
        this.el['wf-profile-style'].value = profile?.posting_style || '';
        this.el['wf-profile-notes'].value = profile?.prompt_notes || '';
        this.el['wf-profile-controlled'].checked = !!profile?.is_user_controlled;
        this.el['wf-profile-verified'].checked = !!profile?.is_verified;
        this.el['wf-avatar-url'].value = profile?.avatar_url || '';
        this.el['wf-header-url'].value = profile?.header_url || '';
        this.updateProfileArtPreview();
        this.openModal('wf-profile-modal');
        setTimeout(() => this.el['wf-profile-name'].focus(), 40);
    },

    async saveProfile(event) {
        event.preventDefault();
        const id = this.el['wf-profile-id'].value;
        const payload = {
            display_name: this.el['wf-profile-name'].value,
            handle: this.el['wf-profile-handle'].value,
            account_type: this.el['wf-profile-type'].value,
            accent_color: this.el['wf-profile-color'].value,
            bio: this.el['wf-profile-bio'].value,
            location: this.el['wf-profile-location'].value,
            website: this.el['wf-profile-website'].value,
            posting_style: this.el['wf-profile-style'].value,
            prompt_notes: this.el['wf-profile-notes'].value,
            is_user_controlled: this.el['wf-profile-controlled'].checked,
            is_verified: this.el['wf-profile-verified'].checked,
            avatar_url: this.el['wf-avatar-url'].value,
            header_url: this.el['wf-header-url'].value,
        };
        try {
            const saved = await this.request(id ? `/api/world-feed/profiles/${encodeURIComponent(id)}` : `/api/world-feed/worlds/${encodeURIComponent(this.state.world.id)}/profiles`, {
                method: id ? 'PATCH' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
            });
            await this.reloadProfiles();
            if (saved.is_user_controlled && !this.state.activeProfileId) this.useProfile(saved.id);
            this.closeModals();
            await this.setView(this.state.view, this.state.detailId, true);
            this.toast(`${saved.display_name}'s profile is ready.`);
        } catch (error) { this.toast(error.message, true); }
    },

    async reloadProfiles() {
        const data = await this.request(`/api/world-feed/worlds/${encodeURIComponent(this.state.world.id)}`);
        this.state.world = data.world;
        this.state.profiles = data.profiles || [];
        this.ensureActiveProfile();
        this.renderWorldChrome();
    },

    async uploadProfileArt(event, kind) {
        const file = event.target.files?.[0];
        event.target.value = '';
        if (!file) return;
        try {
            const uploaded = await this.uploadImage(file);
            this.el[kind === 'avatar' ? 'wf-avatar-url' : 'wf-header-url'].value = uploaded.url;
            this.updateProfileArtPreview();
        } catch (error) { this.toast(error.message, true); }
    },

    updateProfileArtPreview() {
        const avatar = this.safeImage(this.el['wf-avatar-url'].value);
        const header = this.safeImage(this.el['wf-header-url'].value);
        this.el['wf-avatar-preview'].style.backgroundImage = avatar ? `url("${avatar.replaceAll('"', '%22')}")` : '';
        this.el['wf-header-preview'].style.backgroundImage = header ? `url("${header.replaceAll('"', '%22')}")` : '';
        this.el['wf-upload-avatar'].textContent = avatar ? 'Change icon' : 'Choose icon';
        this.el['wf-upload-header'].textContent = header ? 'Change header' : 'Choose header image';
    },

    openRelationshipModal() {
        if (this.state.profiles.length < 2) return this.toast('Create at least two profiles first.', true);
        const options = this.state.profiles.map(profile => `<option value="${this.attr(profile.id)}">${this.text(profile.display_name)} (@${this.text(profile.handle)})</option>`).join('');
        this.el['wf-rel-from'].innerHTML = options;
        this.el['wf-rel-to'].innerHTML = options;
        this.el['wf-rel-to'].selectedIndex = Math.min(1, this.state.profiles.length - 1);
        this.el['wf-rel-type'].value = '';
        this.el['wf-rel-public'].value = '';
        this.el['wf-rel-private'].value = '';
        this.openModal('wf-relationship-modal');
    },

    async saveRelationship(event) {
        event.preventDefault();
        try {
            await this.request(`/api/world-feed/worlds/${encodeURIComponent(this.state.world.id)}/relationships`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
                    from_profile_id: this.el['wf-rel-from'].value,
                    to_profile_id: this.el['wf-rel-to'].value,
                    relationship_type: this.el['wf-rel-type'].value,
                    public_summary: this.el['wf-rel-public'].value,
                    private_context: this.el['wf-rel-private'].value,
                    visibility: 'private', status: 'active',
                }),
            });
            await this.loadRelationships();
            this.closeModals();
            this.renderRelationships();
            this.toast('Relationship saved from the story’s real starting point.');
        } catch (error) { this.toast(error.message, true); }
    },

    async openWorldModal(create = false) {
        const generation = ++this.worldModalGeneration;
        const world = create ? {} : this.state.world;
        if (!world) return;
        this.editingWorldId = create ? '' : world.id;
        this.el['wf-world-title'].textContent = create ? 'New world' : 'World settings';
        this.el['wf-world-submit'].textContent = create ? 'Create world' : 'Save world';
        this.el['wf-world-slug-field'].hidden = !create;
        this.el['wf-world-slug'].value = '';
        this.el['wf-world-creation-note'].hidden = !create;
        this.el['wf-world-activity-controls'].hidden = create;
        this.el['wf-world-name'].value = world.name || '';
        this.el['wf-world-description-input'].value = world.description || '';
        this.el['wf-world-identity'].value = world.story_identity || 'Avery';
        this.el['wf-world-branch'].value = world.story_branch || '';
        this.el['wf-world-photo-style'].value = world.metadata?.photo_style || '';
        this.el['wf-world-now'].value = world.fictional_now || (create ? 'Story opening' : '');
        this.el['wf-world-clock'].value = world.clock_label || (create ? 'Story time' : '');
        this.el['wf-world-posting-enabled'].checked = !!world.posting_enabled;
        this.openModal('wf-world-modal');
        const worldId = world.id;
        document.getElementById('wf-activity-limit').value = world.metadata?.activity?.daily_limit || 12;
        document.getElementById('wf-activity-publish').checked = world.metadata?.activity?.auto_publish === true;
        document.getElementById('wf-activity-scenes').checked = world.metadata?.activity?.scene_reactions === true;
        document.getElementById('wf-activity-status').textContent = '';
        if (create) return;
        try {
            const status = await this.request(`/api/world-feed/worlds/${encodeURIComponent(worldId)}/activity`);
            if (generation !== this.worldModalGeneration || this.state.world.id !== worldId) return;
            const latest = status.runs[0];
            document.getElementById('wf-activity-status').textContent = `${status.background_attempts_today || 0} / ${status.settings.daily_limit} background attempts in the last 24 hours · ${status.response_attempts_today || 0} personal response attempts (not counted). ` + (latest ? `Last activity: ${latest.status}${latest.error ? ' · ' + latest.error : ''}` : 'No character activity yet.');
        } catch (error) {
            if (generation === this.worldModalGeneration) document.getElementById('wf-activity-status').textContent = error.message;
        }
    },

    async saveWorld(event) {
        event.preventDefault();
        if (this.worldSaveBusy) return;
        const worldId = this.editingWorldId;
        const creating = !worldId;
        const name = this.el['wf-world-name'].value.trim();
        const identity = this.el['wf-world-identity'].value.trim();
        if (!name || !identity) return this.toast('Enter a world name and a configured narrator identity.', true);
        const dailyLimit = Number(document.getElementById('wf-activity-limit').value);
        if (!creating && (!Number.isInteger(dailyLimit) || dailyLimit < 1 || dailyLimit > 48)) return this.toast('Choose 1–48 activity attempts per day.', true);
        const data = {
            name,
            description: this.el['wf-world-description-input'].value,
            story_identity: identity,
            story_branch: this.el['wf-world-branch'].value.trim(),
            fictional_now: this.el['wf-world-now'].value,
            clock_label: this.el['wf-world-clock'].value,
            metadata: { photo_style: this.el['wf-world-photo-style'].value.trim() },
        };
        if (creating) {
            data.slug = this.el['wf-world-slug'].value.trim() || name.toLowerCase().normalize('NFKD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 64).replace(/-+$/g, '') || `world-${Date.now()}`;
            data.posting_enabled = false;
        } else {
            Object.assign(data, {
                posting_enabled: this.el['wf-world-posting-enabled'].checked,
                activity_daily_limit: dailyLimit,
                activity_auto_publish: document.getElementById('wf-activity-publish').checked,
                activity_scene_reactions: document.getElementById('wf-activity-scenes').checked,
            });
        }
        this.worldSaveBusy = true;
        this.el['wf-world-submit'].disabled = true;
        try {
            const saved = await this.request(creating ? '/api/world-feed/worlds' : `/api/world-feed/worlds/${encodeURIComponent(worldId)}`, {
                method: creating ? 'POST' : 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
            });
            const index = this.state.worlds.findIndex(item => item.id === saved.id);
            if (index === -1) this.state.worlds.push(saved);
            else this.state.worlds[index] = saved;
            this.renderWorldPicker();
            this.closeModals();
            if (creating) {
                history.replaceState(null, '', '#home');
                await this.selectWorld(saved.id);
                this.toast('World created. Add a profile you control, then your fictional cast.');
            } else {
                if (this.state.world?.id === worldId) this.state.world = saved;
                this.el['wf-world-select'].value = this.state.world.id;
                this.renderWorldChrome();
                this.toast('World settings saved.');
            }
        } catch (error) { this.toast(error.message, true); }
        finally {
            this.worldSaveBusy = false;
            this.el['wf-world-submit'].disabled = false;
        }
    },

    openModal(id) {
        this.el['wf-modal-backdrop'].hidden = false;
        ['wf-profile-modal', 'wf-relationship-modal', 'wf-world-modal'].forEach(key => { this.el[key].hidden = key !== id; });
        document.body.style.overflow = 'hidden';
    },

    closeModals() {
        this.el['wf-modal-backdrop'].hidden = true;
        ['wf-profile-modal', 'wf-relationship-modal', 'wf-world-modal'].forEach(key => { this.el[key].hidden = true; });
        document.body.style.overflow = '';
    },

    focusComposer() {
        if (!this.activeProfile()) {
            this.setView('profiles');
            return this.toast('Create or choose the profile you control first.');
        }
        const ready = this.state.view === 'thread' ? Promise.resolve() : this.setView('home');
        ready.then(() => {
            this.el['wf-post-text'].focus();
            this.el['wf-composer'].scrollIntoView({ behavior: 'smooth', block: 'center' });
        });
    },

    toggleMobileNav(open) {
        this.el['wf-left-nav'].classList.toggle('open', open);
        this.el['wf-nav-overlay'].classList.toggle('open', open);
    },

    insertAtCursor(text) {
        const input = this.el['wf-post-text'];
        const start = input.selectionStart;
        input.value = input.value.slice(0, start) + text + input.value.slice(input.selectionEnd);
        input.selectionStart = input.selectionEnd = start + text.length;
        input.focus();
        this.updateCharCount();
    },

    updateCharCount() {
        this.el['wf-char-count'].textContent = `${this.el['wf-post-text'].value.length} / 4000`;
    },

    avatarHtml(profile, extra = '') {
        const initial = profile?.display_name?.trim()?.[0]?.toUpperCase() || '?';
        const image = this.safeImage(profile?.avatar_url || '');
        const accent = this.attr(profile?.accent_color || '#596074');
        return `<span class="wf-avatar ${extra}" style="--avatar-accent:${accent}">${image ? `<img src="${this.attr(image)}" alt="${this.attr(profile.display_name)}">` : this.text(initial)}</span>`;
    },

    renderAvatarInto(element, profile) {
        if (!element) return;
        element.style.setProperty('--avatar-accent', profile?.accent_color || '#596074');
        const image = this.safeImage(profile?.avatar_url || '');
        element.innerHTML = image ? `<img src="${this.attr(image)}" alt="${this.attr(profile.display_name)}">` : this.text(profile?.display_name?.[0]?.toUpperCase() || '?');
    },

    linkify(value) {
        const input = String(value || '');
        const pattern = /([#@][\p{L}\p{N}_]+)/gu;
        let result = '';
        let last = 0;
        for (const match of input.matchAll(pattern)) {
            result += this.text(input.slice(last, match.index));
            const token = match[0];
            if (token.startsWith('#')) result += `<a href="#" data-hashtag="${this.attr(token.slice(1).toLowerCase())}">${this.text(token)}</a>`;
            else result += `<a href="#" data-mention="${this.attr(token.slice(1).toLowerCase())}">${this.text(token)}</a>`;
            last = match.index + token.length;
        }
        return result + this.text(input.slice(last));
    },

    safeImage(url) {
        const value = String(url || '').trim();
        if (!value) return '';
        if (value.startsWith('/')) return apiPath(value);
        if (/^https?:\/\//i.test(value)) return value;
        return '';
    },

    text(value) { return escapeHtml(String(value ?? '')); },
    attr(value) { return String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char])); },

    openLightbox(url, alt) {
        const image = this.el['wf-image-lightbox'].querySelector('img');
        image.src = url;
        image.alt = alt || 'Expanded post image';
        this.el['wf-image-lightbox'].hidden = false;
    },

    closeLightbox() {
        this.el['wf-image-lightbox'].hidden = true;
        this.el['wf-image-lightbox'].querySelector('img').removeAttribute('src');
    },

    toast(message, error = false) {
        const toast = document.createElement('div');
        toast.className = `wf-toast${error ? ' error' : ''}`;
        toast.textContent = message;
        this.el['wf-toast-stack'].appendChild(toast);
        setTimeout(() => toast.remove(), 3800);
    },

    renderFatal(error) {
        this.el['wf-content'].innerHTML = `<div class="wf-empty"><strong>The world could not open.</strong><span>${this.text(error.message)}</span><button class="wf-secondary-button" onclick="location.reload()">Try again</button></div>`;
    },

    renderError(error) {
        this.el['wf-content'].innerHTML = `<div class="wf-empty"><strong>That part of the world did not load.</strong><span>${this.text(error.message)}</span></div>`;
    },
};

document.addEventListener('DOMContentLoaded', () => WorldFeed.init());
