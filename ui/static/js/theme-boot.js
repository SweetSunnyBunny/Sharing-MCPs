                                              
                                                                                                                                                                             
                                                                                                     
                                                                                                                                                               

                                                                                
                                                                          
                                                                               
                                                                            
                                                          
  
                                                                             
                                                
(function () {
    'use strict';

    var DEFAULT_PRESET = 'sunrise-pink';

    function apply(tokens, preset) {
        if (!tokens) return;
        // Same body-scope trick as App.applyThemeTokens in app.js (keep in
        // sync): body.night-mode redefines the color tokens at body scope,
        // which beats inline vars on <html> for everything inside body. A
        // chosen non-default preset is pinned inline on <body> so it wins;
        // the default preset stays <html>-only so night mode keeps working.
        var isDefault = !preset || preset === DEFAULT_PRESET;
        var root = document.documentElement;
        var body = document.body;
        Object.keys(tokens).forEach(function (key) {
            root.style.setProperty(key, tokens[key]);
            if (body) {
                if (isDefault) body.style.removeProperty(key);
                else body.style.setProperty(key, tokens[key]);
            }
        });
        // Dark vibes (Goth/Halloween/Fall — the ones that repaint the
        // assistant bubbles) also flag the body so stylesheets can swap
        // day-pastel art (sidebar ginghams) for their night variants.
        // Keep in sync with App.applyThemeTokens in app.js.
        if (body) {
            body.classList.toggle('preset-dark',
                tokens['--assistant-bubble-mode'] === 'preset');
        }
        syncMetaThemeColor(tokens, isDefault);
    }

    // Android paints the status bar + gesture-nav bar from
    // <meta name="theme-color"> — follow the preset (keep in sync with
    // App._syncMetaThemeColor in app.js). Default preset restores the
    // page's own original hex.
    function syncMetaThemeColor(tokens, isDefault) {
        var meta = document.querySelector('meta[name="theme-color"]');
        if (!meta) {
            meta = document.createElement('meta');
            meta.setAttribute('name', 'theme-color');
            document.head.appendChild(meta);
        }
        if (meta.dataset.defaultContent === undefined) {
            meta.dataset.defaultContent = meta.getAttribute('content') || '';
        }
        var presetColor = tokens && tokens['--meta-theme-color'];
        if (!isDefault && presetColor) meta.setAttribute('content', presetColor);
        else if (meta.dataset.defaultContent) meta.setAttribute('content', meta.dataset.defaultContent);
    }

    // Instant paint from the shared cache (same key app.js writes).
    try {
        var cached = JSON.parse(localStorage.getItem('anam-theme-tokens') || 'null');
        if (cached && cached.tokens) apply(cached.tokens, cached.preset);
        else if (cached) apply(cached, null); // legacy bare-token cache shape
    } catch (_err) { /* corrupt cache, ignore */ }

    // Then refresh from the server (source of truth) and re-cache.
    fetch('/api/settings/theme')
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
            if (!data || !data.tokens) return;
            apply(data.tokens, data.preset);
            try {
                localStorage.setItem('anam-theme-tokens', JSON.stringify({
                    preset: data.preset,
                    tokens: data.tokens,
                    bubble_tokens: data.bubble_tokens || {},
                }));
            } catch (_err) { /* storage unavailable, non-fatal */ }
        })
        .catch(function () { /* offline / server restarting — cache already painted */ });

    window.AnamTheme = { apply: apply };
})();
