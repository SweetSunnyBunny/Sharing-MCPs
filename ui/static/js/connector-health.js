/* Redacted status only: never insert connector-supplied HTML or raw errors. */
(() => {
    const grid = document.getElementById('connectors');
    const summary = document.getElementById('summary');
    const filter = document.getElementById('filter');
    const labels = {healthy:'Working',connected_unchecked:'Connected · not checked',not_connected:'No shared connection',auth_required:'Login needed',permission_denied:'Permission denied',rate_limited:'Rate limited',missing_tool:'Tool missing',invalid_arguments:'Arguments need updating',stale_connection:'Stale connection',unavailable:'Unavailable'};
    let rows = [];
    const element = (tag, text, cls) => {
        const node = document.createElement(tag); node.textContent = text;
        if (cls) node.className = cls; return node;
    };
    function render() {
        grid.replaceChildren();
        const visible = rows.filter(row => row.server.toLowerCase().includes(filter.value.toLowerCase()));
        for (const row of visible) {
            const card = element('article', '', 'card');
            card.append(element('h2', row.server));
            const good = row.status === 'healthy';
            const bad = !good && !['connected_unchecked','not_connected'].includes(row.status);
            card.append(element('span', labels[row.status] || row.status, 'badge' + (good ? ' good' : bad ? ' bad' : '')));
            card.append(element('p', row.hint));
            if (row.checked_at) card.append(element('small', 'Last observation: ' + new Date(row.checked_at).toLocaleString()));
            if (row.last_failure) {
                const detail = document.createElement('details');
                detail.append(element('summary', 'Previous failure'));
                detail.append(element('p', (labels[row.last_failure.status] || row.last_failure.status) + ' · ' + new Date(row.last_failure.checked_at).toLocaleString()));
                detail.append(element('p', row.last_failure.hint)); card.append(detail);
            }
            const check = element('button', 'Check catalog');
            check.addEventListener('click', async () => {
                check.disabled = true; check.textContent = 'Checking…';
                try {
                    const data = await get('/api/computer/connectors?probe=true&server=' + encodeURIComponent(row.server));
                    rows = rows.map(r => r.server === row.server ? data.servers[0] : r); render();
                    summary.textContent = 'Checked ' + row.server + '.';
                } catch { summary.textContent = 'The check could not reach Anam. Try again when the connection returns.'; check.disabled = false; check.textContent = 'Check catalog'; }
            });
            card.append(check); grid.append(card);
        }
        if (!visible.length) grid.append(element('p', 'No connectors match that search.'));
    }
    async function get(url) {
        const response = await fetch(url, {credentials:'same-origin'});
        if (!response.ok) throw new Error('Request failed');
        return response.json();
    }
    async function load() {
        const button = document.getElementById('refresh'); button.disabled = true;
        try { const data = await get('/api/computer/connectors'); rows = data.servers; render(); summary.textContent = rows.length + ' configured connectors. Select Check catalog to verify one now.'; }
        catch { summary.textContent = 'Couldn’t load connector health. Check that Anam is running and you’re signed in.'; }
        finally { button.disabled = false; }
    }
    filter.addEventListener('input', render);
    document.getElementById('refresh').addEventListener('click', load);
    load();
})();
