                                                                           
                                   
                                                                                                                                                                             
                                                          
                                                                                                                                                                                 

                                                                            
                                                                               
const ROLEPLAY_IDENTITIES = ['Bakugou', 'Dynamight', 'DragonKing', 'Dean', 'Eroan', 'Pack', 'Sans', 'Michael', 'Workshop', 'Daniel', 'Doctor', 'Beckett', 'Harem', 'Isekai'];

const Sidebar = {
    overlay: null,
    panel: null,
    listEl: null,
    conversations: [],
    _contextMenu: null,
    _contextMenuBtn: null,
    _contextMenuJustOpened: false,
    _touchStartX: 0,
    _touchStartY: 0,
    _searchInput: null,
    _searchResults: null,
    _refreshDebounceTimer: null,
    _customProjects: [],

    /* ── Collapse state persistence ── */

    _collapseState: {},

    _loadCollapseState() {
        try {
            const raw = localStorage.getItem('anam-sidebar-collapsed');
            if (raw) this._collapseState = JSON.parse(raw);
        } catch (e) {
            this._collapseState = {};
        }
    },

    _saveCollapseState() {
        try {
            localStorage.setItem('anam-sidebar-collapsed', JSON.stringify(this._collapseState));
        } catch (e) {
            // Ignore storage errors
        }
    },

    _isSectionCollapsed(sectionName) {
        // Active identity defaults to expanded; all others default to collapsed
        if (this._collapseState.hasOwnProperty(sectionName)) {
            return this._collapseState[sectionName];
        }
        return sectionName !== App.currentIdentity;
    },

    _toggleSection(sectionName) {
        const current = this._isSectionCollapsed(sectionName);
        this._collapseState[sectionName] = !current;
        this._saveCollapseState();
    },

    /* ── Custom projects persistence ── */

    _loadProjects() {
        try {
            this._customProjects = JSON.parse(localStorage.getItem('anam-custom-projects') || '[]');
        } catch { this._customProjects = []; }
    },

    _saveProjects() {
        localStorage.setItem('anam-custom-projects', JSON.stringify(this._customProjects));
    },

    /* ── Init ── */

    init() {
        this.overlay = document.getElementById('sidebar-overlay');
        this.panel = document.getElementById('sidebar');
        this.listEl = document.getElementById('sidebar-conversations');

        this._loadCollapseState();
        this._loadProjects();

        // Toggle button
        document.getElementById('sidebar-toggle').addEventListener('click', () => this.open());

        // Close button
        document.getElementById('sidebar-close').addEventListener('click', () => this.close());

        // Overlay tap closes
        this.overlay.addEventListener('click', () => this.close());

        // New chat from sidebar (top-level button)
        document.getElementById('sidebar-new-chat-btn').addEventListener('click', () => {
            App.newConversation();
            this.close();
        });

        // Search input
        this._searchInput = document.getElementById('sidebar-search');
        if (this._searchInput) {
            let _searchDebounce = null;
            this._searchInput.addEventListener('input', () => {
                clearTimeout(_searchDebounce);
                const query = this._searchInput.value.trim();
                if (query.length < 2) {
                    this._searchResults = null;
                    this._render();
                    return;
                }
                _searchDebounce = setTimeout(() => this._runSearch(query), 300);
            });
        }

        // Hide the "Show all" toggle — no longer needed with grouped view
        const showAllToggle = document.querySelector('.sidebar-show-all-toggle');
        if (showAllToggle) showAllToggle.style.display = 'none';

        // Swipe-to-close gesture (with vertical threshold to avoid triggering during scroll)
        this.panel.addEventListener('touchstart', (e) => {
            this._touchStartX = e.touches[0].clientX;
            this._touchStartY = e.touches[0].clientY;
        }, { passive: true });

        this.panel.addEventListener('touchend', (e) => {
            const dx = e.changedTouches[0].clientX - this._touchStartX;
            const dy = Math.abs(e.changedTouches[0].clientY - this._touchStartY);
            // Only close if horizontal swipe left > 60px AND horizontal > vertical (not scrolling)
            if (dx < -60 && Math.abs(dx) > dy * 1.5) this.close();
        }, { passive: true });

        // Close context menu on outside click (skip if menu was just opened this tick)
        document.addEventListener('click', (e) => {
            if (this._contextMenuJustOpened) {
                this._contextMenuJustOpened = false;
                return;
            }
            if (this._contextMenu && !this._contextMenu.contains(e.target)) {
                this._closeContextMenu();
            }
        });

        // Listen for WS events to auto-refresh
        if (App.ws) {
            App.ws.on('new_conversation', () => this._refreshIfOpen());
            App.ws.on('history', () => this._refreshIfOpen());
        }
    },

    /* ── Open / Close ── */

    async open() {
        this._clearSearch();
        await this._fetchAndRender();
        this.panel.classList.add('open');
        this.overlay.classList.add('open');
    },

    close() {
        this.panel.classList.remove('open');
        this.overlay.classList.remove('open');
        this._closeContextMenu();
    },

    /* ── Refresh ── */

    _refreshIfOpen() {
        if (!this.panel.classList.contains('open')) return;
        // Debounce rapid refresh calls (autowake + brother activity can fire many events)
        clearTimeout(this._refreshDebounceTimer);
        this._refreshDebounceTimer = setTimeout(() => this._fetchAndRender(), 500);
    },

    /* ── Fetch & Render ── */

    async _fetchAndRender() {
        try {
            const data = await fetchJson('/api/messages/conversations', {
                timeoutMs: 12000,
                retries: 1,
            });
            this.conversations = data.conversations || [];
        } catch (err) {
            console.error('[Sidebar] Failed to fetch conversations:', err);
            this.conversations = [];
        }
        this._render();
    },

    _render() {
        // If search is active, show search results instead
        if (this._searchResults !== null && this._searchResults !== undefined) {
            this._renderSearchResults();
            return;
        }

        this.listEl.innerHTML = '';

        // Group conversations
        const groups = this._groupConversations(this.conversations);
        const identityNames = Object.keys(App.identities || {});

        // Split: bonded pack (top-level) vs roleplay masks (nested under Roleplay group)
        const packIdentityNames = identityNames.filter(n => !ROLEPLAY_IDENTITIES.includes(n));
        const roleplayIdentityNames = identityNames.filter(n => ROLEPLAY_IDENTITIES.includes(n));

        // 1. Active identity first (if it's a pack identity), then other pack identities
        if (App.currentIdentity && packIdentityNames.includes(App.currentIdentity)) {
            this._renderSection(App.currentIdentity, groups[App.currentIdentity] || [], 'identity');
        }
        packIdentityNames.forEach(name => {
            if (name !== App.currentIdentity) {
                this._renderSection(name, groups[name] || [], 'identity');
            }
        });

        // 2. Pack Night (the shared room — usually a single singleton card)
        this._renderSection('Pack Night', groups['_pack_night'] || [], 'pack-night');

        // 3. Pack Hall
        this._renderSection('Pack Hall', groups['_pack_hall'] || [], 'pack-hall');

        // 4. Roleplay — parent group containing nested mask identities (Bakugou, Dean)
        //    plus any session_type='roleplay' conversations flat at the top of its body.
        this._renderRoleplayGroup(roleplayIdentityNames, groups);

        // 5. D&D
        this._renderSection('D&D', groups['_dnd'] || [], 'dnd');

        // 5. Custom projects
        this._customProjects.forEach(proj => {
            this._renderSection(proj.name, groups['_proj_' + proj.session_type] || [], 'project', proj);
        });

        // 6. "+ Add Project" button
        this._renderAddProjectButton();
    },

    _groupConversations(conversations) {
        const groups = {};
        // Collect custom project session types for matching
        const customTypes = new Set(this._customProjects.map(p => p.session_type));

        conversations.forEach(convo => {
            const st = convo.session_type;
            if (st === 'pack-night') {
                if (!groups['_pack_night']) groups['_pack_night'] = [];
                groups['_pack_night'].push(convo);
            } else if (st === 'brother') {
                if (!groups['_pack_hall']) groups['_pack_hall'] = [];
                groups['_pack_hall'].push(convo);
            } else if (st === 'roleplay') {
                if (!groups['_roleplay']) groups['_roleplay'] = [];
                groups['_roleplay'].push(convo);
            } else if (st === 'dnd') {
                if (!groups['_dnd']) groups['_dnd'] = [];
                groups['_dnd'].push(convo);
            } else if (st && customTypes.has(st)) {
                if (!groups['_proj_' + st]) groups['_proj_' + st] = [];
                groups['_proj_' + st].push(convo);
            } else {
                // Regular chat — group by identity
                const identity = convo.identity || 'Unknown';
                if (!groups[identity]) groups[identity] = [];
                groups[identity].push(convo);
            }
        });

        return groups;
    },

    /* ── Section Rendering ── */

    // type: 'identity' | 'pack-hall' | 'roleplay' | 'dnd' | 'project'
    // extra: project object for 'project' type
    // container: where to append the section element. Defaults to the top-level
    //   sidebar list. Pass a parent section's body to nest (used by Roleplay group).
    // labelText: optional label override (e.g. "Canon" for the Bakugou identity
    //   when it renders inside its mask group). Collapse state + colors still key
    //   off the real sectionName/identity.
    _renderSection(sectionName, convos, type, extra, container = null, labelText = null) {
        const section = document.createElement('div');
        section.className = 'sidebar-section';
        section.dataset.section = sectionName;

        const collapsed = this._isSectionCollapsed(sectionName);
        if (collapsed) section.classList.add('collapsed');

        const hasActive = convos.some(c => c.id === App.conversationId);
        if (hasActive) section.classList.add('has-active');

        // ── Header ──
        const header = document.createElement('div');
        header.className = 'sidebar-section-header';

        if (type === 'identity') {
            header.classList.add('identity-header');
            header.dataset.identity = sectionName;
            const info = App.identities[sectionName];
            if (info) header.style.setProperty('--section-accent-rgb', info.accent_rgb);
            if (sectionName === App.currentIdentity) header.classList.add('active-identity');
        } else if (type === 'pack-night') {
            header.classList.add('pack-night-header');
        } else if (type === 'pack-hall') {
            header.classList.add('pack-hall-header');
        } else if (type === 'roleplay') {
            header.classList.add('roleplay-header');
        } else if (type === 'dnd') {
            header.classList.add('dnd-header');
        } else if (type === 'project' && extra) {
            header.classList.add('project-header');
            header.style.setProperty('--project-color', extra.color);
        }

        // Arrow + label + count
        const arrow = document.createElement('span');
        arrow.className = 'sidebar-section-arrow';
        arrow.textContent = collapsed ? '\u25B6' : '\u25BC';

        const label = document.createElement('span');
        label.className = 'sidebar-section-label';
        label.textContent = labelText || sectionName;

        const count = document.createElement('span');
        count.className = 'sidebar-section-count';
        count.textContent = convos.length > 0 ? `(${convos.length})` : '';

        header.appendChild(arrow);
        header.appendChild(label);
        header.appendChild(count);

        // "+" button (hidden for Pack Hall and Pack Night — both are singletons/aggregates)
        if (type !== 'pack-hall' && type !== 'pack-night') {
            const addBtn = document.createElement('button');
            addBtn.className = 'sidebar-section-add-btn';
            addBtn.textContent = '+';
            addBtn.title = `New ${sectionName} conversation`;
            addBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                if (type === 'identity') {
                    if (sectionName !== App.currentIdentity) {
                        App.switchIdentity(sectionName);
                    } else {
                        App.newConversation();
                    }
                    this.close();
                } else if (type === 'roleplay') {
                    App.newConversation('roleplay');
                    this.close();
                } else if (type === 'dnd') {
                    App.newConversation('dnd');
                    this.close();
                } else if (type === 'project' && extra) {
                    App.newConversation(extra.session_type);
                    this.close();
                }
            });
            header.appendChild(addBtn);
        }

        // Delete button for custom projects
        if (type === 'project' && extra) {
            const delBtn = document.createElement('button');
            delBtn.className = 'sidebar-section-del-btn';
            delBtn.innerHTML = '&#x1F5D1;';
            delBtn.title = `Delete ${sectionName}`;
            delBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this._confirmDeleteProject(extra);
            });
            header.appendChild(delBtn);
        }

        // Toggle collapse on header click
        header.addEventListener('click', () => {
            this._toggleSection(sectionName);
            const isNowCollapsed = this._isSectionCollapsed(sectionName);
            section.classList.toggle('collapsed', isNowCollapsed);
            arrow.textContent = isNowCollapsed ? '\u25B6' : '\u25BC';
        });

        section.appendChild(header);

        // ── Body ──
        const body = document.createElement('div');
        body.className = 'sidebar-section-body';

        if (convos.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'sidebar-section-empty';
            const emptyText = {
                'pack-night': 'Pack night room not ready yet',
                'pack-hall': 'No brother conversations yet',
                'roleplay': 'No roleplay stories yet',
                'dnd': 'No D&D campaigns yet',
            };
            empty.textContent = emptyText[type] || 'No conversations yet';
            body.appendChild(empty);
        } else {
            // Sort pinned conversations to top
            const sorted = [...convos].sort((a, b) => {
                const aPinned = a.pinned ? 1 : 0;
                const bPinned = b.pinned ? 1 : 0;
                return bPinned - aPinned;
            });
            sorted.forEach(convo => {
                body.appendChild(this._renderConvoCard(convo, sectionName));
            });
        }

        section.appendChild(body);
        (container || this.listEl).appendChild(section);
        return section;
    },

    /* ── Roleplay parent group — contains nested mask identity sections ── */
    // roleplayIdentityNames: identity names that are character masks (e.g., Bakugou, Dean)
    // groups: the grouped conversations map (so we can find each mask's chats + any
    //         session_type='roleplay' conversations to render flat in the parent body)
    _renderRoleplayGroup(roleplayIdentityNames, groups) {
        const sectionName = 'Roleplay';
        const sessionConvos = groups['_roleplay'] || [];

        // If a roleplay identity is the currently-active identity, the parent group
        // should default to expanded (so the active mask is visible).
        const activeIsRoleplay = roleplayIdentityNames.includes(App.currentIdentity);

        // Build a faux convo list for header counting: sum of all mask convos + session convos.
        let totalCount = sessionConvos.length;
        roleplayIdentityNames.forEach(name => {
            totalCount += (groups[name] || []).length;
        });

        const section = document.createElement('div');
        section.className = 'sidebar-section roleplay-group';
        section.dataset.section = sectionName;

        // Respect saved collapsed state, but force-expand if active mask is inside
        let collapsed = this._isSectionCollapsed(sectionName);
        if (activeIsRoleplay) collapsed = false;
        if (collapsed) section.classList.add('collapsed');

        // ── Header ──
        const header = document.createElement('div');
        header.className = 'sidebar-section-header roleplay-header';

        const arrow = document.createElement('span');
        arrow.className = 'sidebar-section-arrow';
        arrow.textContent = collapsed ? '▶' : '▼';

        const label = document.createElement('span');
        label.className = 'sidebar-section-label';
        label.textContent = sectionName;

        const count = document.createElement('span');
        count.className = 'sidebar-section-count';
        count.textContent = totalCount > 0 ? `(${totalCount})` : '';

        header.appendChild(arrow);
        header.appendChild(label);
        header.appendChild(count);

        // "+" button on the parent creates a new generic roleplay-session conversation
        const addBtn = document.createElement('button');
        addBtn.className = 'sidebar-section-add-btn';
        addBtn.textContent = '+';
        addBtn.title = 'New roleplay conversation';
        addBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            App.newConversation('roleplay');
            this.close();
        });
        header.appendChild(addBtn);

        header.addEventListener('click', () => {
            this._toggleSection(sectionName);
            const isNowCollapsed = this._isSectionCollapsed(sectionName);
            section.classList.toggle('collapsed', isNowCollapsed);
            arrow.textContent = isNowCollapsed ? '▶' : '▼';
        });

        section.appendChild(header);

        // ── Body ──
        const body = document.createElement('div');
        body.className = 'sidebar-section-body roleplay-group-body';

        // First: any session_type='roleplay' conversations as flat cards
        if (sessionConvos.length > 0) {
            const sorted = [...sessionConvos].sort((a, b) => {
                const aPinned = a.pinned ? 1 : 0;
                const bPinned = b.pinned ? 1 : 0;
                return bPinned - aPinned;
            });
            sorted.forEach(convo => {
                body.appendChild(this._renderConvoCard(convo, sectionName));
            });
        }

        // Then: nested mask identity sub-sections (Bakugou, Dean, etc.).
        // Masks that share a config "group" (e.g. Bakugou / Dynamight / DragonKing
        // all carry group: "Bakugou") cluster under one collapsible group header,
        // with each chat row showing its short group_label ("Canon", "Pro Hero",
        // "Dragon King"). Ungrouped masks render exactly as before.
        if (roleplayIdentityNames.length > 0) {
            // Build render units: solo masks + one unit per mask group.
            const units = [];
            const seenGroups = new Set();
            roleplayIdentityNames.forEach(name => {
                const g = (App.identities[name] || {}).group;
                if (g) {
                    if (!seenGroups.has(g)) {
                        seenGroups.add(g);
                        units.push({
                            group: g,
                            members: roleplayIdentityNames.filter(
                                n => (App.identities[n] || {}).group === g
                            ),
                        });
                    }
                } else {
                    units.push({ name });
                }
            });

            // Active mask (or the group containing it) floats to the top.
            const isActiveUnit = (u) => u.name
                ? u.name === App.currentIdentity
                : u.members.includes(App.currentIdentity);
            units.sort((a, b) => (isActiveUnit(b) ? 1 : 0) - (isActiveUnit(a) ? 1 : 0));

            units.forEach(u => {
                if (u.name) {
                    const nested = this._renderSection(
                        u.name,
                        groups[u.name] || [],
                        'identity',
                        null,
                        body,
                    );
                    if (nested) nested.classList.add('nested');
                } else {
                    this._renderMaskGroup(u.group, u.members, groups, body);
                }
            });
        }

        // Empty state — no session convos AND no mask identities present
        if (sessionConvos.length === 0 && roleplayIdentityNames.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'sidebar-section-empty';
            empty.textContent = 'No roleplay stories yet';
            body.appendChild(empty);
        }

        section.appendChild(body);
        this.listEl.appendChild(section);
    },

    /* ── Mask group — one character with several chat variants (e.g. Bakugou:
       Canon / Pro Hero / Dragon King) nested under a single header inside the
       Roleplay group. Members come from config "group"/"group_label". ── */
    _renderMaskGroup(groupName, memberNames, groups, container) {
        // Collapse-state key is namespaced so it can't collide with an identity
        // section that shares the group's name (e.g. the "Bakugou" identity).
        const stateKey = 'group:' + groupName;
        const activeInside = memberNames.includes(App.currentIdentity);

        let totalCount = 0;
        memberNames.forEach(name => { totalCount += (groups[name] || []).length; });

        const section = document.createElement('div');
        section.className = 'sidebar-section nested mask-group';
        section.dataset.section = stateKey;

        let collapsed = this._isSectionCollapsed(stateKey);
        if (activeInside) collapsed = false;
        if (collapsed) section.classList.add('collapsed');

        const header = document.createElement('div');
        header.className = 'sidebar-section-header roleplay-header mask-group-header';
        // Tint the group header with the lead member's accent (first member).
        const leadInfo = App.identities[memberNames[0]];
        if (leadInfo) header.style.setProperty('--section-accent-rgb', leadInfo.accent_rgb);

        const arrow = document.createElement('span');
        arrow.className = 'sidebar-section-arrow';
        arrow.textContent = collapsed ? '▶' : '▼';

        const label = document.createElement('span');
        label.className = 'sidebar-section-label';
        label.textContent = groupName;

        const count = document.createElement('span');
        count.className = 'sidebar-section-count';
        count.textContent = totalCount > 0 ? `(${totalCount})` : '';

        header.appendChild(arrow);
        header.appendChild(label);
        header.appendChild(count);

        header.addEventListener('click', () => {
            this._toggleSection(stateKey);
            const isNowCollapsed = this._isSectionCollapsed(stateKey);
            section.classList.toggle('collapsed', isNowCollapsed);
            arrow.textContent = isNowCollapsed ? '▶' : '▼';
        });

        section.appendChild(header);

        const body = document.createElement('div');
        body.className = 'sidebar-section-body mask-group-body';

        // Active variant first, then the rest — each labeled with its group_label.
        const ordered = [...memberNames].sort((a, b) =>
            (b === App.currentIdentity ? 1 : 0) - (a === App.currentIdentity ? 1 : 0));
        ordered.forEach(name => {
            const info = App.identities[name] || {};
            const nested = this._renderSection(
                name,
                groups[name] || [],
                'identity',
                null,
                body,
                info.group_label || name,
            );
            if (nested) nested.classList.add('nested');
        });

        section.appendChild(body);
        container.appendChild(section);
    },

    _renderConvoCard(convo, sectionName) {
        const card = document.createElement('div');
        card.className = 'sidebar-convo-card';
        if (convo.id === App.conversationId) card.classList.add('active');

        // Build preview text
        let preview = '';
        if (convo.last_message) {
            const prefix = convo.last_role === 'user' ? 'You: ' : '';
            preview = prefix + convo.last_message;
        }

        const pinIcon = convo.pinned ? '<span class="sidebar-pin-icon" title="Pinned">&#x1F4CC;</span> ' : '';
        card.innerHTML = `
            <div class="sidebar-convo-info">
                <div class="sidebar-convo-title">${pinIcon}${escapeHtml(convo.title || 'Untitled')}</div>
                <div class="sidebar-convo-preview">${escapeHtml(preview)}</div>
            </div>
            <div class="sidebar-convo-right">
                <span class="sidebar-convo-time">${formatRelativeTime(convo.updated_at)}</span>
                <button class="sidebar-convo-menu-btn" title="Options">&#x22EE;</button>
            </div>
        `;
        if (convo.pinned) card.classList.add('pinned');

        // Session type badges
        if (convo.session_type === 'brother') {
            card.querySelector('.sidebar-convo-title').insertAdjacentHTML(
                'afterbegin', '<span class="sidebar-badge b2b">B2B</span> '
            );
        } else if (convo.session_type === 'roleplay') {
            card.querySelector('.sidebar-convo-title').insertAdjacentHTML(
                'afterbegin', '<span class="sidebar-badge rp">RP</span> '
            );
        } else if (convo.session_type === 'dnd') {
            card.querySelector('.sidebar-convo-title').insertAdjacentHTML(
                'afterbegin', '<span class="sidebar-badge dnd">DND</span> '
            );
        }

        // In Pack Hall, show identity badge since multiple identities participate
        if (sectionName === 'Pack Hall' && convo.identity) {
            card.querySelector('.sidebar-convo-title').insertAdjacentHTML(
                'afterbegin', `<span class="sidebar-badge identity">${escapeHtml(convo.identity.split(',')[0].trim().slice(0, 3))}</span> `
            );
        }

        // Click card -> switch conversation
        card.addEventListener('click', (e) => {
            if (e.target.closest('.sidebar-convo-menu-btn')) return;
            this._switchTo(convo.id, convo);
        });

        // Menu button
        const menuBtn = card.querySelector('.sidebar-convo-menu-btn');
        menuBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this._showContextMenu(convo, card, menuBtn);
        });

        return card;
    },

    /* ── Switch conversation ── */

    _switchTo(conversationId, convo) {
        // Save draft for current conversation before switching
        Chat.saveDraft(App.conversationId);
        Chat.parkAttachmentPreview();

        App.conversationId = conversationId;
        App.saveState();

        const isPackNight = convo && convo.session_type === 'pack-night';

        // For brother conversations, use the first identity in the comma-separated list
        let identity = App.currentIdentity;
        if (convo && convo.session_type === 'brother' && convo.identity) {
            identity = convo.identity.split(',')[0].trim();
        } else if (convo && convo.identity) {
            identity = convo.identity;
        }

        // Switch to that identity visually if needed
        if (identity !== App.currentIdentity && App.identities[identity]) {
            App.currentIdentity = identity;
            App.applyIdentityTheme(identity);
            App.updateIdentityLabel();
            App.saveState();
        }

        // Pack-night uses a special placeholder so it's clear the room is active.
        const inputEl = document.getElementById('message-input');
        if (inputEl) {
            inputEl.placeholder = isPackNight
                ? 'Message the pack...'
                : `Message ${identity}...`;
        }

        App.ws.send({
            type: 'load_history',
            identity: identity,
            conversation_id: conversationId,
        });
        this.close();
    },

    /* ── Context menu (keep exactly) ── */

    _showContextMenu(convo, cardEl, btnEl) {
        this._closeContextMenu();

        const menu = document.createElement('div');
        menu.className = 'sidebar-convo-context-menu';

        const pinBtn = document.createElement('button');
        pinBtn.className = 'sidebar-context-action';
        pinBtn.textContent = convo.pinned ? 'Unpin' : 'Pin';
        pinBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this._closeContextMenu();
            this._togglePin(convo);
        });

        const renameBtn = document.createElement('button');
        renameBtn.className = 'sidebar-context-action';
        renameBtn.textContent = 'Rename';
        renameBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this._closeContextMenu();
            this._promptRename(convo);
        });

        const deleteBtn = document.createElement('button');
        deleteBtn.className = 'sidebar-context-action danger';
        deleteBtn.textContent = 'Delete';
        deleteBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this._closeContextMenu();
            this._confirmDelete(convo);
        });

        menu.appendChild(pinBtn);
        menu.appendChild(renameBtn);
        menu.appendChild(deleteBtn);
        cardEl.appendChild(menu);

        btnEl.classList.add('visible');
        this._contextMenu = menu;
        this._contextMenuBtn = btnEl;
        this._contextMenuJustOpened = true;
    },

    _closeContextMenu() {
        if (this._contextMenu) {
            this._contextMenu.remove();
            this._contextMenu = null;
        }
        if (this._contextMenuBtn) {
            this._contextMenuBtn.classList.remove('visible');
            this._contextMenuBtn = null;
        }
    },

    /* ── Pin ── */

    _togglePin(convo) {
        const newPinned = !convo.pinned;
        App.ws.send({
            type: 'pin_conversation',
            conversation_id: convo.id,
            pinned: newPinned,
        });
        // Optimistic update
        convo.pinned = newPinned;
        this._render();
    },

    /* ── Rename (keep exactly) ── */

    async _promptRename(convo) {
        this._showInlineModal({
            title: 'Rename conversation',
            inputValue: convo.title || '',
            confirmLabel: 'Rename',
            onConfirm: async (newTitle) => {
                if (!newTitle || newTitle === convo.title) return;
                try {
                    await apiFetch(`/api/messages/conversations/${convo.id}/rename`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ title: newTitle }),
                    });
                    await this._fetchAndRender();
                } catch (err) {
                    console.error('[Sidebar] Rename failed:', err);
                }
            },
        });
    },

    /* ── Delete (keep exactly) ── */

    async _confirmDelete(convo) {
        this._showInlineModal({
            title: `Delete "${convo.title || 'Untitled'}"?`,
            message: 'This conversation will be removed from your list.',
            confirmLabel: 'Delete',
            danger: true,
            onConfirm: async () => {
                try {
                    await apiFetch(`/api/messages/conversations/${convo.id}`, {
                        method: 'DELETE',
                    });
                    if (convo.id === App.conversationId) {
                        App.newConversation();
                    }
                    await this._fetchAndRender();
                } catch (err) {
                    console.error('[Sidebar] Delete failed:', err);
                }
            },
        });
    },

    /* ── Inline modal (keep exactly) ── */

    _showInlineModal({ title, message, inputValue, confirmLabel, danger, onConfirm, onRender }) {
        const existing = document.querySelector('.sidebar-modal-overlay');
        if (existing) existing.remove();

        const overlay = document.createElement('div');
        overlay.className = 'sidebar-modal-overlay';

        const modal = document.createElement('div');
        modal.className = 'sidebar-modal';

        const heading = document.createElement('div');
        heading.className = 'sidebar-modal-title';
        heading.textContent = title;
        modal.appendChild(heading);

        if (message) {
            const msg = document.createElement('div');
            msg.className = 'sidebar-modal-message';
            msg.textContent = message;
            modal.appendChild(msg);
        }

        let input = null;
        if (inputValue !== undefined) {
            input = document.createElement('input');
            input.className = 'sidebar-modal-input';
            input.type = 'text';
            input.value = inputValue;
            modal.appendChild(input);
        }

        const actions = document.createElement('div');
        actions.className = 'sidebar-modal-actions';

        const cancelBtn = document.createElement('button');
        cancelBtn.className = 'sidebar-modal-btn cancel';
        cancelBtn.textContent = 'Cancel';
        cancelBtn.addEventListener('click', () => overlay.remove());

        actions.appendChild(cancelBtn);

        if (confirmLabel !== null) {
            const confirmBtn = document.createElement('button');
            confirmBtn.className = `sidebar-modal-btn confirm${danger ? ' danger' : ''}`;
            confirmBtn.textContent = confirmLabel || 'OK';
            confirmBtn.addEventListener('click', () => {
                overlay.remove();
                onConfirm(input ? input.value.trim() : undefined);
            });
            actions.appendChild(confirmBtn);
        }

        modal.appendChild(actions);

        if (onRender) onRender(modal);

        overlay.appendChild(modal);

        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) overlay.remove();
        });

        // Escape key closes modal from anywhere
        const _escHandler = (e) => {
            if (e.key === 'Escape') {
                overlay.remove();
                document.removeEventListener('keydown', _escHandler);
            }
        };
        document.addEventListener('keydown', _escHandler);

        document.body.appendChild(overlay);
        if (input) {
            input.focus();
            input.select();
            input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    const cfm = modal.querySelector('.sidebar-modal-btn.confirm');
                    if (cfm) cfm.click();
                }
            });
        }
    },

    /* ── Custom projects ── */

    _renderAddProjectButton() {
        const btn = document.createElement('button');
        btn.className = 'sidebar-add-project-btn';
        btn.textContent = '+ Add Project';
        btn.addEventListener('click', () => this._showAddProjectModal());
        this.listEl.appendChild(btn);
    },

    _showAddProjectModal() {
        const PASTEL_COLORS = [
            '#F2B8C6', '#A8D8D0', '#A8B4D4', '#C5B3D4', '#F0DFA0', '#E8E0D0',
            '#F0B0A0', '#B0D8B0', '#D0C0E0', '#F0D0B0', '#A0D0E8', '#E0C8A0',
        ];

        // Use closure to track selected color across render and confirm
        let selectedColor = PASTEL_COLORS[0];

        this._showInlineModal({
            title: 'New Project',
            message: 'Give your project a name and color.',
            inputValue: '',
            confirmLabel: 'Create',
            onRender: (modal) => {
                const picker = document.createElement('div');
                picker.className = 'sidebar-color-picker';

                PASTEL_COLORS.forEach(color => {
                    const swatch = document.createElement('button');
                    swatch.className = 'sidebar-color-swatch';
                    if (color === selectedColor) swatch.classList.add('selected');
                    swatch.style.background = color;
                    swatch.addEventListener('click', (e) => {
                        e.preventDefault();
                        selectedColor = color;
                        picker.querySelectorAll('.sidebar-color-swatch').forEach(s => s.classList.remove('selected'));
                        swatch.classList.add('selected');
                    });
                    picker.appendChild(swatch);
                });

                const actions = modal.querySelector('.sidebar-modal-actions');
                modal.insertBefore(picker, actions);
            },
            onConfirm: (name) => {
                if (!name) return;
                const sessionType = name.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
                if (['chat', 'brother', 'roleplay', 'dnd'].includes(sessionType)) return;
                this._customProjects.push({
                    id: 'proj_' + Date.now(),
                    name,
                    color: selectedColor,
                    session_type: sessionType,
                });
                this._saveProjects();
                this._fetchAndRender();
            },
        });
    },

    _confirmDeleteProject(project) {
        this._showInlineModal({
            title: `Delete "${project.name}"?`,
            message: 'The project section will be removed. Conversations in it will still exist.',
            confirmLabel: 'Delete',
            danger: true,
            onConfirm: () => {
                this._customProjects = this._customProjects.filter(p => p.id !== project.id);
                this._saveProjects();
                this._fetchAndRender();
            },
        });
    },

    /* ── Search (keep exactly) ── */

    _clearSearch() {
        if (this._searchInput) {
            this._searchInput.value = '';
            this._searchInput.style.display = '';
        }
        this._searchResults = null;
    },

    async _runSearch(query) {
        try {
            const params = new URLSearchParams({ q: query, limit: '15' });
            const res = await apiFetch(`/api/messages/search?${params}`);
            const data = await res.json();
            this._searchResults = data.results || [];
        } catch (err) {
            console.error('[Sidebar] Search failed:', err);
            this._searchResults = [];
        }
        this._render();
    },

    _highlightQuery(text, query) {
        if (!query) return escapeHtml(text);
        // If text already contains FTS5 <mark> tags, pass them through safely
        if (text.includes('<mark>') && text.includes('</mark>')) {
            // Strip everything except <mark> tags, escape the rest
            return text
                .replace(/<mark>/g, '\x00MARK\x00')
                .replace(/<\/mark>/g, '\x00/MARK\x00')
                .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
                .replace(/\x00MARK\x00/g, '<mark class="search-highlight">')
                .replace(/\x00\/MARK\x00/g, '</mark>');
        }
        const escaped = escapeHtml(text);
        const escapedQuery = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        return escaped.replace(
            new RegExp(`(${escapedQuery})`, 'gi'),
            '<mark class="search-highlight">$1</mark>'
        );
    },

    _renderSearchResults() {
        this.listEl.innerHTML = '';
        if (!this._searchResults || this._searchResults.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'sidebar-empty';
            empty.textContent = 'No results found';
            this.listEl.appendChild(empty);
            return;
        }
        const query = this._searchInput ? this._searchInput.value.trim() : '';
        this._searchResults.forEach(result => {
            const card = document.createElement('div');
            card.className = 'sidebar-convo-card';
            if (result.conversation_id === App.conversationId) card.classList.add('active');

            card.innerHTML = `
                <div class="sidebar-convo-info">
                    <div class="sidebar-convo-title">${this._highlightQuery(result.conversation_title || 'Untitled', query)}</div>
                    <div class="sidebar-convo-preview sidebar-search-snippet">${this._highlightQuery(result.snippet || '', query)}</div>
                </div>
                <div class="sidebar-convo-right">
                    <span class="sidebar-convo-time">${formatRelativeTime(result.created_at)}</span>
                </div>
            `;

            if (result.identity) {
                card.querySelector('.sidebar-convo-title').insertAdjacentHTML(
                    'afterbegin', `<span class="sidebar-badge identity">${escapeHtml(result.identity.slice(0, 3))}</span> `
                );
            }

            card.addEventListener('click', () => {
                this._switchTo(result.conversation_id, { identity: result.identity });
            });
            this.listEl.appendChild(card);
        });
    },
};
