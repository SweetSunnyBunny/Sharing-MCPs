/* Simple markdown renderer — converts markdown text to HTML */
/* ANAM GUIDE: MARKDOWN RENDERER
   What: Turns the boys' markdown text (**bold**, lists, tables, code blocks) into the HTML you see in chat bubbles and the canvas panel.
   Loaded by: static/index.html (main chat page); chat.js and canvas.js call renderMarkdown().
   Edit here when: Some markdown renders wrong in a bubble, or you want to support a new bit of formatting. */

function renderMarkdown(text) {
    if (!text) return '';
    try { return _renderMarkdownInner(text); }
    catch (e) { console.error('Markdown render error:', e); return escapeHtml(text).replace(/\n/g, '<br>'); }
}

function _renderMarkdownInner(text) {

    let html = escapeHtml(text);

    // Extract code blocks FIRST to protect them from other rules
    const codeBlocks = [];
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
        codeBlocks.push(`<pre><code class="lang-${lang}">${code.trim()}</code></pre>`);
        return `\x00CODEBLOCK${codeBlocks.length - 1}\x00`;
    });

    // Extract inline code
    const inlineCode = [];
    html = html.replace(/`([^`]+)`/g, (_, code) => {
        inlineCode.push(`<code>${code}</code>`);
        return `\x00INLINE${inlineCode.length - 1}\x00`;
    });

    // Custom emoji (:claude-longing:) — AFTER code extraction so a shortcode
    // inside a code sample stays literal, and BEFORE every rule that emits raw
    // HTML, so we're only ever substituting into escaped plain text.
    if (typeof CustomEmoji !== 'undefined') {
        html = CustomEmoji.render(html);
    }

    // Tables (GitHub-style pipe tables) — extracted BEFORE line/paragraph rules
    // so the newlines inside the table aren't turned into <br>. Restored at end.
    const tables = [];
    const _splitRow = (row) => {
        let s = row.trim();
        if (s.startsWith('|')) s = s.slice(1);
        if (s.endsWith('|')) s = s.slice(0, -1);
        return s.split('|').map(c => c.trim());
    };
    const _fmtCell = (s) => s
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        .replace(/~~(.+?)~~/g, '<del>$1</del>')
        .trim();
    {
        const lines = html.split('\n');
        const out = [];
        const sepRe = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/;
        let i = 0;
        while (i < lines.length) {
            const header = lines[i];
            const sep = lines[i + 1];
            if (header && sep && header.indexOf('|') !== -1 && sepRe.test(sep)) {
                const headerCells = _splitRow(header);
                const aligns = _splitRow(sep).map((c) => {
                    const t = c.trim();
                    const l = t.startsWith(':'), r = t.endsWith(':');
                    if (l && r) return 'center';
                    if (r) return 'right';
                    if (l) return 'left';
                    return '';
                });
                i += 2;
                const body = [];
                while (i < lines.length && lines[i].indexOf('|') !== -1 && lines[i].trim() !== '') {
                    body.push(_splitRow(lines[i]));
                    i++;
                }
                let t = '<table class="md-table"><thead><tr>';
                headerCells.forEach((c, idx) => {
                    const a = aligns[idx] ? ` style="text-align:${aligns[idx]}"` : '';
                    t += `<th${a}>${_fmtCell(c)}</th>`;
                });
                t += '</tr></thead><tbody>';
                body.forEach((r) => {
                    t += '<tr>';
                    headerCells.forEach((_, idx) => {
                        const a = aligns[idx] ? ` style="text-align:${aligns[idx]}"` : '';
                        t += `<td${a}>${_fmtCell(r[idx] != null ? r[idx] : '')}</td>`;
                    });
                    t += '</tr>';
                });
                t += '</tbody></table>';
                tables.push(t);
                out.push(`\x00TABLE${tables.length - 1}\x00`);
            } else {
                out.push(header);
                i++;
            }
        }
        html = out.join('\n');
    }

    // Horizontal rules (---, ***, ___) — before bold/italic so *** isn't eaten.
    // Styled in anam.css as a little row of flowers.
    html = html.replace(/^\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*$/gm, '<hr class="md-hr">');

    // Bold
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

    // Italic
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');

    // Strikethrough
    html = html.replace(/~~(.+?)~~/g, '<del>$1</del>');

    // Headers (### etc)
    html = html.replace(/^#{4,6} (.+)$/gm, '<h5>$1</h5>');
    html = html.replace(/^### (.+)$/gm, '<h4>$1</h4>');
    html = html.replace(/^## (.+)$/gm, '<h3>$1</h3>');
    html = html.replace(/^# (.+)$/gm, '<h2>$1</h2>');

    // Blockquotes
    html = html.replace(/^&gt; (.+)$/gm, '<blockquote>$1</blockquote>');

    // Ordered lists (must come before unordered to use distinct markers)
    html = html.replace(/^\d+\. (.+)$/gm, '\x00OLI$1\x00OLI');
    html = html.replace(/(\x00OLI[\s\S]*?\x00OLI\n?)+/g, (match) => {
        const items = match.replace(/\x00OLI/g, '').trim().split('\n').filter(Boolean);
        return '<ol>' + items.map(i => `<li>${i.trim()}</li>`).join('') + '</ol>';
    });

    // Unordered lists
    html = html.replace(/^[\-\*] (.+)$/gm, '<li>$1</li>');
    html = html.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');

    // Images ![alt](url) — must come before links
    html = html.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (match, alt, url) => {
        const serveUrl = convertImagePath(url);
        return `<img class="message-image" src="${serveUrl}" alt="${escapeHtml(alt)}" loading="lazy" onclick="Chat.viewFullImage(this.src)" onerror="this.onerror=null;this.classList.add('image-broken');this.alt='Image failed to load';this.style.cursor='default';this.onclick=null">`;
    });

    // Links [text](url)
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (match, label, url) => {
        const linkUrl = convertDocumentPath(url);
        return `<a href="${linkUrl}" target="_blank" rel="noopener">${label}</a>`;
    });

    // Line breaks: double newline = paragraph break
    html = html.replace(/\n\n/g, '</p><p>');
    // Single newlines within paragraphs
    html = html.replace(/\n/g, '<br>');

    // Wrap in paragraph if not already wrapped
    if (!html.startsWith('<')) {
        html = '<p>' + html + '</p>';
    }

    // Restore tables first (their cells may contain inline-code placeholders),
    // then inline code, then code blocks.
    tables.forEach((tbl, i) => {
        html = html.replace(`\x00TABLE${i}\x00`, tbl);
    });

    // Restore code blocks and inline code
    inlineCode.forEach((code, i) => {
        html = html.replace(`\x00INLINE${i}\x00`, code);
    });
    codeBlocks.forEach((block, i) => {
        html = html.replace(`\x00CODEBLOCK${i}\x00`, block);
    });

    // A message that is nothing but custom emoji gets them big, the way Discord
    // does it. Purely cosmetic; never changes what was said.
    if (typeof CustomEmoji !== 'undefined' && CustomEmoji.isEmojiOnly(html)) {
        html = html.replace(/<img class="custom-emoji"/g, '<img class="custom-emoji custom-emoji-jumbo"');
    }

    return html;
}
