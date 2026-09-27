/* ANAM GUIDE: CANVAS SIDE PANEL
   What: The slide-out side panel where the boys show rich content (a <canvas title="...">...</canvas> tag in their reply opens it), plus the saved-canvas library.
   Loaded by: static/index.html (the main chat page only). Saves and loads canvases through api/canvases.py.
   Edit here when: You want to change how the canvas panel opens/closes, how its content renders, or how the library list looks and behaves. */

/* Canvas panel — rich content side panel for displaying structured content.
 *
 * The boys can open the canvas by including <canvas title="...">content</canvas>
 * tags in their response. The content is extracted, rendered as markdown, and
 * shown in the side panel. Multiple canvas blocks update the same panel.
 *
 * Frontend can also open it programmatically via Canvas.open(title, html).
 */

const Canvas = {
    panel: null,
    titleEl: null,
    contentEl: null,
    closeBtn: null,
    downloadBtn: null,
    _isOpen: false,
    // Raw markdown source of whatever the panel is currently showing, kept so
    // the download button hands over the ACTUAL text the boy wrote rather than
    // markdown reverse-engineered out of the rendered HTML. Null while the
    // library LIST is showing (a list of titles isn't a downloadable artifact).
    _currentMarkdown: null,
    _currentTitle: null,

    init() {
        this.panel = document.getElementById('canvas-panel');
        this.titleEl = document.getElementById('canvas-title');
        this.contentEl = document.getElementById('canvas-content');
        this.closeBtn = document.getElementById('canvas-close');
        this.downloadBtn = document.getElementById('canvas-download');

        if (!this.panel) return;

        this.closeBtn.addEventListener('click', () => this.close());

        if (this.downloadBtn) {
            this.downloadBtn.addEventListener('click', () => this.download());
        }

        const libraryBtn = document.getElementById('canvas-library-btn');
        if (libraryBtn) libraryBtn.addEventListener('click', () => this.openLibrary());

        // Swipe right to close on mobile
        let touchStartX = 0;
        this.panel.addEventListener('touchstart', (e) => {
            touchStartX = e.touches[0].clientX;
        }, { passive: true });
        this.panel.addEventListener('touchend', (e) => {
            const dx = e.changedTouches[0].clientX - touchStartX;
            if (dx > 80) this.close();
        }, { passive: true });

        // Escape key closes
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this._isOpen) this.close();
        });
    },

    open(title, html, markdown = null) {
        if (!this.panel) return;
        this.titleEl.textContent = title || 'Canvas';
        this.contentEl.innerHTML = html;
        this.panel.style.display = 'flex';
        void this.panel.offsetHeight; // read forces reflow so the CSS transition animates (jshint W030-safe)
        this.panel.classList.add('open');
        document.body.classList.add('canvas-open');
        this._isOpen = true;
        this._setSource(title, markdown);
    },

    /** Point the download button at a specific title + raw markdown, or hide
     *  it entirely by passing markdown = null (library list, empty states). */
    _setSource(title, markdown) {
        this._currentTitle = title || 'Canvas';
        this._currentMarkdown = markdown;
        if (this.downloadBtn) {
            this.downloadBtn.style.display = markdown ? '' : 'none';
        }
    },

    /** Filesystem-safe slug, mirroring canvas_slug() in
     *  services/canvas_store.py so a downloaded file and its Vault archive
     *  carry the same name. */
    _slug(text, limit = 50) {
        const safe = (text || 'canvas')
            .replace(/[^\w \-]/g, '_')
            .trim();
        return (safe || 'canvas').slice(0, limit);
    },

    /** Save the current canvas to the device as a .md file. Pure client-side
     *  Blob + <a download> — works the same for a live canvas and one opened
     *  out of the library, and needs no round trip, so it still works if the
     *  tunnel is having a bad day. */
    download() {
        if (!this._currentMarkdown) return;
        const title = this._currentTitle || 'Canvas';
        const identity = (typeof App !== 'undefined' && App.currentIdentity) || 'Anam';

        // Same shape as render_canvas_markdown() in services/canvas_store.py,
        // deliberately — the file she downloads to her phone and the file that
        // lands in the Vault should be the same artifact, not two cousins.
        // A piece that titles itself keeps its own heading on top; otherwise
        // the panel title becomes the H1. Provenance line either way, because
        // an artifact with no signed date is unreadable a year from now.
        let body = this._currentMarkdown.trim();
        let heading;
        if (body.startsWith('#')) {
            const nl = body.indexOf('\n');
            heading = (nl === -1 ? body : body.slice(0, nl)).trim();
            body = (nl === -1 ? '' : body.slice(nl + 1)).trim();
        } else {
            heading = `# ${title}`;
        }
        const meta = `**Canvas:** ${title} | **By:** ${identity} | **Created:** ${new Date().toISOString()}`;
        const text = `${heading}\n\n${meta}\n\n---\n\n${body}\n`;

        const date = new Date();
        const stamp = [
            date.getFullYear(),
            String(date.getMonth() + 1).padStart(2, '0'),
            String(date.getDate()).padStart(2, '0'),
        ].join('-');
        const filename = `${stamp}_${this._slug(title)}.md`;

        const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        a.style.display = 'none';
        document.body.appendChild(a);
        a.click();
        // Revoke on a delay, not immediately — some Android webviews haven't
        // finished reading the blob by the time the click handler returns, and
        // revoking too early produces a silently empty download.
        setTimeout(() => {
            URL.revokeObjectURL(url);
            a.remove();
        }, 1500);

        if (this.downloadBtn) {
            this.downloadBtn.textContent = '✓';
            this.downloadBtn.classList.add('saved');
            setTimeout(() => {
                this.downloadBtn.textContent = '⬇';
                this.downloadBtn.classList.remove('saved');
            }, 1600);
        }
    },

    update(html, append = false) {
        if (!this.panel || !this._isOpen) return;
        if (append) {
            this.contentEl.innerHTML += html;
        } else {
            this.contentEl.innerHTML = html;
        }
    },

    updateTitle(title) {
        if (this.titleEl) this.titleEl.textContent = title;
    },

    close() {
        if (!this.panel) return;
        this.panel.classList.remove('open');
        document.body.classList.remove('canvas-open');
        this._isOpen = false;
        this._setSource(null, null);
        // Wait for transition to finish before hiding
        setTimeout(() => {
            if (!this._isOpen) {
                this.panel.style.display = 'none';
                this.contentEl.innerHTML = '';
            }
        }, 300);
    },

    /**
     * Mask fenced/inline code-span contents with same-length NUL runs, so a
     * boy explaining the <canvas> feature in prose (e.g. `<canvas
     * title="...">` inside backticks) doesn't get matched as a real block --
     * without this, the regex starts at that literal mention and greedily
     * swallows everything up to the NEXT real </canvas>, splicing his
     * surrounding prose into the extracted content (caught live, 2026-07-08).
     * Same-length replacement keeps character offsets identical to the
     * original, so match indices still slice correctly from the real text.
     */
    _maskCodeSpans(text) {
        return text
            .replace(/```[\s\S]*?```/g, (m) => '\0'.repeat(m.length))
            .replace(/`[^`\n]*`/g, (m) => '\0'.repeat(m.length));
    },

    /**
     * Extract <canvas> tags from a message and display in the panel.
     * Returns the message with canvas tags removed.
     */
    extractAndShow(text) {
        if (!text) return text;

        const masked = this._maskCodeSpans(text);
        const canvasRegex = /<canvas(?:\s+title="([^"]*)")?>([\s\S]*?)<\/canvas>/gid;
        let match;
        let hasCanvas = false;
        const ranges = [];

        while ((match = canvasRegex.exec(masked)) !== null) {
            const title = match[1] || 'Canvas';
            const [contentStart, contentEnd] = match.indices[2];
            const content = text.slice(contentStart, contentEnd).trim();
            if (!content) continue;

            ranges.push(match.indices[0]);

            // Render markdown to HTML (reuse the existing markdown renderer)
            const rendered = typeof renderMarkdown === 'function'
                ? renderMarkdown(content)
                : content.replace(/\n/g, '<br>');

            if (!hasCanvas) {
                this.open(title, rendered, content);
                hasCanvas = true;
            } else {
                this.update('<hr style="margin:16px 0;opacity:0.2">' + rendered, true);
                // Several blocks in one reply share the panel, so the download
                // has to carry all of them — joined with a rule, matching what
                // the eye sees. (Each block still gets its OWN Vault file
                // server-side; that archive is per-canvas, this is per-panel.)
                this._currentMarkdown = `${this._currentMarkdown}\n\n---\n\n${content}`;
            }
        }

        // Strip only the real matched ranges from the displayed message --
        // remove back-to-front so earlier indices stay valid.
        let displayText = text;
        for (let i = ranges.length - 1; i >= 0; i--) {
            const [s, e] = ranges[i];
            displayText = displayText.slice(0, s) + displayText.slice(e);
        }
        return displayText.trim();
    },

    // ── Canvas Library (#32) ──
    // Every <canvas> block now persists server-side (services/canvas_store.py,
    // api/canvases.py). This is the browsable view on top of that: opened via
    // the header library button, reuses the same panel shell as a live
    // canvas -- just swaps #canvas-content between "list" and "single item".

    async openLibrary(offset = 0) {
        if (!this.panel || typeof App === 'undefined' || !App.currentIdentity) return;
        this.updateTitle('Canvas Library');
        // A list of titles isn't a downloadable artifact — hide the button
        // until an actual canvas is on screen.
        this._setSource(null, null);
        this.contentEl.innerHTML = '<p class="canvas-library-loading">Loading…</p>';
        this.panel.style.display = 'flex';
        void this.panel.offsetHeight; // read forces reflow so the CSS transition animates (jshint W030-safe)
        this.panel.classList.add('open');
        document.body.classList.add('canvas-open');
        this._isOpen = true;

        let data;
        try {
            data = await fetchJson(
                `/api/canvases?identity=${encodeURIComponent(App.currentIdentity)}&limit=20&offset=${offset}`,
                { timeoutMs: 8000 },
            );
        } catch (err) {
            this.contentEl.innerHTML = '<p class="canvas-library-error">Couldn\'t load the library.</p>';
            return;
        }
        this._renderLibraryList(data);
    },

    _renderLibraryList(data) {
        const items = data.items || [];
        if (!items.length) {
            this.contentEl.innerHTML = '<p class="canvas-library-empty">Nothing saved yet — canvases you write with &lt;canvas&gt; tags will show up here.</p>';
            return;
        }

        const rows = items.map((item) => {
            const date = new Date(item.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
            const sharedBadge = item.shared_by
                ? `<span class="canvas-library-badge">shared by ${escapeHtml(item.shared_by)}</span>`
                : '';
            const ownerActions = !item.shared_by ? `
                <button type="button" class="canvas-library-action" data-share="${item.id}" title="Share">↗</button>
                <button type="button" class="canvas-library-action" data-delete="${item.id}" title="Delete">🗑</button>
            ` : '';
            return `
                <div class="canvas-library-row" data-open="${item.id}">
                    <div class="canvas-library-row-main">
                        <span class="canvas-library-row-title">${escapeHtml(item.title)}</span>
                        <span class="canvas-library-row-meta">${date}${sharedBadge}</span>
                    </div>
                    <div class="canvas-library-row-actions">${ownerActions}</div>
                </div>
            `;
        }).join('');

        const hasMore = data.offset + items.length < data.total;
        const hasPrev = data.offset > 0;
        const pager = (hasMore || hasPrev) ? `
            <div class="canvas-library-pager">
                <button type="button" class="canvas-library-page-btn" data-page-prev ${hasPrev ? '' : 'disabled'}>&larr; Newer</button>
                <span class="canvas-library-page-count">${data.offset + 1}-${data.offset + items.length} of ${data.total}</span>
                <button type="button" class="canvas-library-page-btn" data-page-next ${hasMore ? '' : 'disabled'}>Older &rarr;</button>
            </div>
        ` : '';

        this.contentEl.innerHTML = `<div class="canvas-library-list">${rows}</div>${pager}`;

        this.contentEl.querySelectorAll('[data-open]').forEach((row) => {
            row.addEventListener('click', (e) => {
                if (e.target.closest('.canvas-library-action')) return;
                this._openSavedCanvas(Number(row.dataset.open));
            });
        });
        this.contentEl.querySelectorAll('[data-share]').forEach((btn) => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                this._shareCanvas(Number(btn.dataset.share));
            });
        });
        this.contentEl.querySelectorAll('[data-delete]').forEach((btn) => {
            // Two-tap inline confirm instead of native confirm() — Android
            // webviews can swallow the dialog, which made the trashcan feel
            // like it needed several taps before anything happened.
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                if (btn.dataset.armed) {
                    this._deleteCanvas(Number(btn.dataset.delete), data.offset);
                    return;
                }
                btn.dataset.armed = '1';
                btn.textContent = 'Sure?';
                btn.classList.add('armed');
                setTimeout(() => {
                    delete btn.dataset.armed;
                    btn.textContent = '🗑';
                    btn.classList.remove('armed');
                }, 3000);
            });
        });
        const prevBtn = this.contentEl.querySelector('[data-page-prev]');
        if (prevBtn) prevBtn.addEventListener('click', () => this.openLibrary(Math.max(0, data.offset - 20)));
        const nextBtn = this.contentEl.querySelector('[data-page-next]');
        if (nextBtn) nextBtn.addEventListener('click', () => this.openLibrary(data.offset + 20));
    },

    async _openSavedCanvas(id) {
        if (typeof App === 'undefined' || !App.currentIdentity) return;
        let item;
        try {
            item = await fetchJson(
                `/api/canvases/${id}?identity=${encodeURIComponent(App.currentIdentity)}`,
                { timeoutMs: 8000 },
            );
        } catch (err) {
            return;
        }
        const rendered = typeof renderMarkdown === 'function'
            ? renderMarkdown(item.content)
            : item.content.replace(/\n/g, '<br>');
        const backLink = '<button type="button" class="canvas-library-back" id="canvas-back-to-library">&larr; Library</button>';
        this.updateTitle(item.title);
        this._setSource(item.title, item.content);
        this.contentEl.innerHTML = backLink + rendered;
        const back = document.getElementById('canvas-back-to-library');
        if (back) back.addEventListener('click', () => this.openLibrary());
    },

    async _shareCanvas(id) {
        if (typeof App === 'undefined' || !App.currentIdentity || !App.identities) return;
        const options = Object.keys(App.identities).filter((name) => name !== App.currentIdentity);
        if (!options.length) return;
        const target = prompt(`Share with which boy?\n${options.join(', ')}`);
        if (!target || !options.includes(target)) return;
        try {
            await fetchJson(`/api/canvases/${id}/share`, {
                method: 'POST',
                body: JSON.stringify({ identity: App.currentIdentity, share_with: target }),
            });
        } catch (err) { /* best-effort */ }
    },

    async _deleteCanvas(id, currentOffset) {
        if (typeof App === 'undefined' || !App.currentIdentity) return;
        try {
            await fetchJson(`/api/canvases/${id}?identity=${encodeURIComponent(App.currentIdentity)}`, {
                method: 'DELETE',
            });
        } catch (err) { /* best-effort */ }
        this.openLibrary(currentOffset);
    },
};
