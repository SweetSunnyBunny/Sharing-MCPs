/* Custom emoji support. Configure your own manifest and image assets. */

const CustomEmoji = {
    _byName: new Map(),      // "claude-longing" -> { name, url, group }
    _groups: [],             // [{ group, items: [...] }] in manifest order
    _loaded: false,
    _loadPromise: null,

    // A shortcode needs at least one letter, so clock times (10:30:45) and
    // ratios can never match. The map lookup is the real gate anyway: we only
    // substitute names we actually have art for.
    SHORTCODE_RE: /:([a-z0-9][a-z0-9_-]{0,62}):/gi,

    /** Load the manifest. CDN first (always current), local fallback second. */
    async init() {
        if (this._loadPromise) return this._loadPromise;
        this._loadPromise = this._load();
        return this._loadPromise;
    },

    async _load() {
        const cdn = (window.__anamCdnUrl || '').replace(/\/+$/, '');
        const sources = [];
        if (cdn) sources.push(`${cdn}/emoji/manifest.json`);
        sources.push('/static/emoji/manifest.json');

        for (const src of sources) {
            try {
                const res = await fetch(src, { cache: 'no-cache' });
                if (!res.ok) continue;
                const data = await res.json();
                if (this._ingest(data, cdn)) {
                    this._loaded = true;
                    document.dispatchEvent(new CustomEvent('customemoji:ready'));
                    return true;
                }
            } catch (err) {
                // Offline, blocked, or malformed — try the next source quietly.
                // A missing emoji set must never break the chat.
            }
        }
        console.warn('[CustomEmoji] no manifest reachable — shortcodes stay as text');
        this._loaded = true;
        return false;
    },

    _ingest(data, cdnFallback) {
        const list = data && Array.isArray(data.emoji) ? data.emoji : null;
        if (!list || !list.length) return false;

        const base = (data.base || cdnFallback || '').replace(/\/+$/, '');
        this._byName.clear();
        this._groups = [];
        const groupIndex = new Map();

        for (const raw of list) {
            if (!raw || !raw.name || !raw.url) continue;
            const name = String(raw.name).toLowerCase();
            // An absolute url in the manifest wins; otherwise it's CDN-relative.
            const url = /^https?:\/\//i.test(raw.url)
                ? raw.url
                : `${base}/${String(raw.url).replace(/^\/+/, '')}`;
            const entry = { name, url, group: raw.group || 'Emoji' };

            this._byName.set(name, entry);
            if (!groupIndex.has(entry.group)) {
                const bucket = { group: entry.group, items: [] };
                groupIndex.set(entry.group, bucket);
                this._groups.push(bucket);
            }
            groupIndex.get(entry.group).items.push(entry);
        }
        return this._byName.size > 0;
    },

    isReady() { return this._loaded && this._byName.size > 0; },
    has(name) { return this._byName.has(String(name || '').toLowerCase()); },
    get(name) { return this._byName.get(String(name || '').toLowerCase()) || null; },
    groups() { return this._groups; },
    all() { return Array.from(this._byName.values()); },

    /** The `:name:` form, for inserting into the composer or storing a reaction. */
    codeFor(name) { return `:${name}:`; },

    /** True if a reaction key is one of ours rather than a unicode emoji. */
    isCustomKey(key) {
        if (typeof key !== 'string' || key.length < 3) return false;
        if (key[0] !== ':' || key[key.length - 1] !== ':') return false;
        return this.has(key.slice(1, -1));
    },

    /** <img> for a reaction pill / picker tile. Returns '' if unknown. */
    imgHtml(name, extraClass) {
        const entry = this.get(name);
        if (!entry) return '';
        const cls = extraClass ? `custom-emoji ${extraClass}` : 'custom-emoji';
        return `<img class="${cls}" src="${entry.url}" alt=":${entry.name}:" title=":${entry.name}:" loading="lazy" draggable="false">`;
    },

    /**
     * Swap :shortcodes: for <img> inside an ALREADY HTML-ESCAPED string.
     * Called from markdown.js after code blocks and inline code have been
     * pulled out, so emoji never fire inside a code sample.
     */
    render(html) {
        if (!html || !this._byName.size) return html;
        if (html.indexOf(':') === -1) return html;
        return html.replace(this.SHORTCODE_RE, (match, name) => {
            const entry = this.get(name);
            return entry ? this.imgHtml(entry.name) : match;
        });
    },

    /**
     * Discord's nicest small touch: a message that is nothing but emoji gets
     * them big. Detects that on the rendered HTML and tags the wrapper.
     */
    isEmojiOnly(html) {
        if (!html) return false;
        const stripped = html
            .replace(/<img class="custom-emoji"[^>]*>/g, '')
            .replace(/<\/?(p|br|span|div)[^>]*>/g, '')
            .replace(/&nbsp;/g, ' ')
            .trim();
        if (stripped.length > 0) return false;
        const count = (html.match(/<img class="custom-emoji"/g) || []).length;
        return count > 0 && count <= 6;
    },
};

if (typeof window !== 'undefined') {
    window.CustomEmoji = CustomEmoji;
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => CustomEmoji.init());
    } else {
        CustomEmoji.init();
    }
}
