/* ANAM GUIDE: DIAGNOSTICS PAGE BEHAVIOR
   What: Runs the health checks on the Diagnostics page — pings the server's status endpoints and fills in the result rows and summary line.
   Loaded by: static/diagnostics.html only.
   Edit here when: You want to add a new health check row or change how check results are shown. Page looks live in static/css/diagnostics.css. */

const Diagnostics = {
    async init() {
        if (localStorage.getItem('anam-night') === 'true') {
            document.body.classList.add('night-mode');
        }

        document.getElementById('run-diagnostics-btn').addEventListener('click', () => {
            this.run();
        });

        await this.run();
    },

    setSummary(text, tone = 'neutral') {
        const el = document.getElementById('diag-summary');
        el.textContent = text;
        el.dataset.tone = tone;
    },

    renderRows(targetId, rows) {
        const el = document.getElementById(targetId);
        el.innerHTML = rows.map((row) => {
            const valueClass = row.tone ? `diag-value ${row.tone}` : 'diag-value';
            return `
                <div class="diag-kv">
                    <div class="diag-key">${escapeHtml(row.label)}</div>
                    <div class="${valueClass}">${escapeHtml(row.value)}</div>
                </div>
            `;
        }).join('');
    },

    async fetchJsonWithMeta(url, timeoutMs = 8000) {
        const started = performance.now();
        const resp = await apiFetch(url, {
            method: 'GET',
            cache: 'no-store',
            signal: AbortSignal.timeout(timeoutMs),
        });
        const elapsedMs = Math.round(performance.now() - started);
        const text = await resp.text();
        let body = null;
        try {
            body = text ? JSON.parse(text) : null;
        } catch (_err) {
            body = text;
        }
        return {
            ok: resp.ok,
            status: resp.status,
            elapsedMs,
            body,
        };
    },

    async probeWebSocket(timeoutMs = 8000) {
        const apiBase = (typeof anamApiUrl === 'function') ? anamApiUrl() : '';
        const url = apiBase
            ? apiBase.replace(/^http/, 'ws') + '/ws/chat'
            : `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/chat`;
        const started = performance.now();

        return await new Promise((resolve) => {
            let settled = false;
            let opened = false;
            let ws = null;

            const finish = (result) => {
                if (settled) return;
                settled = true;
                clearTimeout(timer);
                try {
                    if (ws && ws.readyState === WebSocket.OPEN) {
                        ws.close(1000, 'diagnostics-complete');
                    }
                } catch (_err) {
                    // Ignore close failures.
                }
                resolve(result);
            };

            const timer = setTimeout(() => {
                finish({
                    status: 'timeout',
                    detail: `No WebSocket response after ${timeoutMs}ms`,
                    elapsedMs: Math.round(performance.now() - started),
                    tone: 'status-warn',
                });
            }, timeoutMs);

            try {
                ws = new WebSocket(url);
            } catch (err) {
                finish({
                    status: 'construct-failed',
                    detail: err?.message || 'Failed to construct WebSocket',
                    elapsedMs: Math.round(performance.now() - started),
                    tone: 'status-bad',
                });
                return;
            }

            ws.onopen = () => {
                opened = true;
                finish({
                    status: 'open',
                    detail: `Connected in ${Math.round(performance.now() - started)}ms`,
                    elapsedMs: Math.round(performance.now() - started),
                    tone: 'status-ok',
                });
            };

            ws.onerror = () => {
                if (!opened) {
                    finish({
                        status: 'error',
                        detail: 'Browser reported a WebSocket error before open',
                        elapsedMs: Math.round(performance.now() - started),
                        tone: 'status-bad',
                    });
                }
            };

            ws.onclose = (event) => {
                if (opened) return;
                finish({
                    status: 'closed-before-open',
                    detail: `Closed before open (code ${event.code}${event.reason ? `, ${event.reason}` : ''})`,
                    elapsedMs: Math.round(performance.now() - started),
                    tone: event.code === 4001 ? 'status-warn' : 'status-bad',
                });
            };
        });
    },

    async inspectServiceWorker() {
        if (!('serviceWorker' in navigator)) {
            return {
                supported: false,
                controlled: false,
                script: 'Not supported',
                scope: 'Not supported',
            };
        }

        let registration = null;
        try {
            registration = await navigator.serviceWorker.getRegistration();
        } catch (_err) {
            registration = null;
        }

        return {
            supported: true,
            controlled: Boolean(navigator.serviceWorker.controller),
            script: registration?.active?.scriptURL || registration?.waiting?.scriptURL || registration?.installing?.scriptURL || 'None',
            scope: registration?.scope || 'None',
        };
    },

    async run() {
        this.setSummary('Running diagnostics...');

        const clientRows = [
            { label: 'Origin', value: location.origin },
            { label: 'Path', value: `${location.pathname}${location.search}` },
            { label: 'Online', value: navigator.onLine ? 'true' : 'false', tone: navigator.onLine ? 'status-ok' : 'status-bad' },
            { label: 'Public Base', value: window.__ANAM_PUBLIC_BASE_URL__ || '(unset)' },
            { label: 'User Agent', value: navigator.userAgent },
        ];
        this.renderRows('diag-client', clientRows);

        let healthResult = null;
        let authResult = null;
        let wsResult = null;
        let swResult = null;

        try {
            [healthResult, authResult, wsResult, swResult] = await Promise.all([
                this.fetchJsonWithMeta('/health'),
                this.fetchJsonWithMeta('/auth/status'),
                this.probeWebSocket(),
                this.inspectServiceWorker(),
            ]);
        } catch (err) {
            this.setSummary(`Diagnostics failed early: ${err?.message || err}`, 'bad');
        }

        try {
            const runtime = await this.fetchJsonWithMeta('/api/tool-gateway/runtime');
            if (!runtime.ok) throw new Error(`Runtime HTTP ${runtime.status}`);
            const data = runtime.body;
            const rows = (data.codex_processes || []).map(process => ({label:`${process.identity} · ${process.kind}`,
                value:`${process.alive ? 'Connected' : 'Stopped'} · ${process.busy ? 'working' : 'ready'} · PID ${process.pid}`}));
            for (const timing of data.timings?.summary || []) {
                rows.push({label:timing.stage + (timing.reused == null ? '' : timing.reused ? ' · reused' : ' · startup'),
                    value:`Median ${Math.round(timing.median_ms)} ms · p95 ${Math.round(timing.p95_ms)} ms · ${timing.count} samples`});
            }
            const history = data.resources;
            const latest = history?.recent?.at(-1);
            if (latest) {
                rows.push({label:'Physical memory',value:`${latest.memory_percent}% used · ${(latest.available_mb/1024).toFixed(1)} GB available`});
                if (latest.commit_limit_mb) rows.push({label:'Committed memory',value:`${(latest.commit_mb/1024).toFixed(1)} / ${(latest.commit_limit_mb/1024).toFixed(1)} GB`});
                rows.push({label:'Anam processes',value:`${latest.anam_tree_count} · ${latest.codex_messaging} messaging · ${latest.codex_autowake} autowake`});
                rows.push({label:'Resource history',value:`${history.sample_count} samples · every ${history.interval_seconds}s · up to ${history.retention_hours} hours retained across restarts`});
            }
            this.renderRows('diag-runtime', rows.length ? rows : [{label:'Runtime',value:'No measured turns yet.'}]);
            document.getElementById('diag-runtime-json').textContent = JSON.stringify(data,null,2);
        } catch (error) {
            this.renderRows('diag-runtime',[{label:'Runtime',value:error.message,tone:'status-warn'}]);
        }

        const httpRows = [];
        if (healthResult) {
            httpRows.push(
                {
                    label: '/health',
                    value: `HTTP ${healthResult.status} in ${healthResult.elapsedMs}ms`,
                    tone: healthResult.ok ? 'status-ok' : 'status-bad',
                },
                {
                    label: 'Health Status',
                    value: String(healthResult.body?.status || 'unknown'),
                    tone: healthResult.body?.status === 'healthy' ? 'status-ok' : 'status-warn',
                },
            );
            document.getElementById('diag-health-json').textContent = JSON.stringify(healthResult.body, null, 2);
        } else {
            httpRows.push({ label: '/health', value: 'Request failed', tone: 'status-bad' });
            document.getElementById('diag-health-json').textContent = 'Health check failed.';
        }

        if (authResult) {
            httpRows.push({
                label: '/auth/status',
                value: authResult.ok ? `HTTP ${authResult.status} · ${authResult.body?.authenticated ? 'authenticated' : 'not authenticated'}` : `HTTP ${authResult.status}`,
                tone: authResult.ok ? 'status-ok' : 'status-bad',
            });
        }
        this.renderRows('diag-http', httpRows);

        this.renderRows('diag-ws', wsResult ? [
            { label: 'URL', value: `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/chat` },
            { label: 'Result', value: wsResult.status, tone: wsResult.tone },
            { label: 'Detail', value: wsResult.detail, tone: wsResult.tone },
        ] : [
            { label: 'Result', value: 'Probe did not run', tone: 'status-bad' },
        ]);

        this.renderRows('diag-sw', swResult ? [
            { label: 'Supported', value: swResult.supported ? 'true' : 'false', tone: swResult.supported ? 'status-ok' : 'status-warn' },
            { label: 'Controlled', value: swResult.controlled ? 'true' : 'false', tone: swResult.controlled ? 'status-ok' : 'status-warn' },
            { label: 'Script', value: swResult.script },
            { label: 'Scope', value: swResult.scope },
        ] : [
            { label: 'Service Worker', value: 'Inspection failed', tone: 'status-bad' },
        ]);

        if (healthResult?.ok && wsResult?.status === 'open') {
            this.setSummary('HTTP and WebSocket both succeeded on this origin.', 'ok');
        } else if (healthResult?.ok && wsResult?.status !== 'open') {
            this.setSummary('HTTP worked, but WebSocket did not. The app should fall back to HTTP mode here.', 'warn');
        } else if (!healthResult?.ok) {
            this.setSummary('HTTP health check failed on this origin. This is not just a WebSocket issue.', 'bad');
        } else {
            this.setSummary('Diagnostics completed with mixed results.', 'warn');
        }

        if (typeof logClientEvent === 'function') {
            logClientEvent('diagnostics_run', {
                origin: location.origin,
                health_ok: Boolean(healthResult?.ok),
                ws_status: wsResult?.status || 'unknown',
                sw_controlled: Boolean(swResult?.controlled),
            }, { source: 'diagnostics' });
        }
    },
};

window.addEventListener('load', () => {
    Diagnostics.init().catch((err) => {
        console.error('[Diagnostics] Init failed:', err);
    });
});
