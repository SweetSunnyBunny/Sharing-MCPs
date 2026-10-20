// Easel — frontend. Vanilla JS, single file, tight.

import { icon } from '/icons.js?v=2';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function hydrateIcons(root = document) {
  root.querySelectorAll('[data-icon]').forEach((el) => {
    if (el.dataset.hydrated === '1') return;
    el.insertAdjacentHTML('afterbegin', icon(el.dataset.icon));
    el.dataset.hydrated = '1';
  });
}

// ---- State ----
const state = {
  view: 'media', // media | favorites | trash | folder | presets | detail
  folderId: null,
  detailId: null,
  tasks: [],
  presets: [],
  folders: [],
  settings: {
    aspect: '2:3',
    variants: 2,
    quality: 'medium',
    characterIds: [],      // ordered list of character preset ids
    styleId: '',           // single style preset id
    referencePath: null,   // direct single-image attach from prompt bar
  },
};

const ASPECTS = ['1:1', '3:2', '2:3', '16:9', '9:16'];
const VARIANTS = [1, 2, 3, 4];
const QUALITIES = ['low', 'medium', 'high'];

// ---- API ----
async function api(path, opts = {}) {
  const resp = await fetch(path, {
    headers: { 'content-type': 'application/json' },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ error: resp.statusText }));
    throw new Error(err.error || 'Request failed');
  }
  return resp.json();
}

// ---- Toast ----
function toast(message, kind = 'ok') {
  const el = document.createElement('div');
  el.className = 'toast' + (kind === 'err' ? ' err' : '');
  el.textContent = message;
  $('#toast-root').appendChild(el);
  setTimeout(() => el.remove(), 3600);
}

// ---- Popover ----
// Single-select: onPick(value) fires on click, popover closes.
// Multi-select (multi: true): each click calls onToggle(value), popover stays open until Done or backdrop.
function popover(anchor, title, options, onPick, opts = {}) {
  closePopover();
  const backdrop = document.createElement('div');
  backdrop.className = 'popover-backdrop';
  backdrop.id = 'active-popover';
  backdrop.addEventListener('click', closePopover);

  const pop = document.createElement('div');
  pop.className = 'popover';
  pop.addEventListener('click', (e) => e.stopPropagation());

  const header = document.createElement('div');
  header.className = 'popover-title';
  header.textContent = title;
  pop.appendChild(header);

  const renderRows = () => {
    // Remove existing option rows before re-rendering
    pop.querySelectorAll('.popover-option').forEach((el) => el.remove());
    const footer = pop.querySelector('.popover-footer');
    for (const opt of options) {
      const row = document.createElement('div');
      row.className = 'popover-option' + (opt.active ? ' active' : '');
      row.innerHTML = `<span>${escapeHtml(opt.label)}</span>${opt.active ? '<span class="check">✓</span>' : ''}`;
      row.addEventListener('click', (e) => {
        e.stopPropagation();
        if (opts.multi) {
          opt.active = !opt.active;
          onPick(opt.value, opt.active);
          renderRows();
        } else {
          onPick(opt.value);
          closePopover();
        }
      });
      if (footer) pop.insertBefore(row, footer);
      else pop.appendChild(row);
    }
  };

  if (opts.multi) {
    const footer = document.createElement('div');
    footer.className = 'popover-footer';
    const done = document.createElement('button');
    done.type = 'button';
    done.textContent = 'Done';
    done.addEventListener('click', (e) => {
      e.stopPropagation();
      closePopover();
    });
    footer.appendChild(done);
    pop.appendChild(footer);
  }

  renderRows();

  backdrop.appendChild(pop);
  document.body.appendChild(backdrop);

  // Position menu above the anchor, pinned to its right edge
  const rect = anchor.getBoundingClientRect();
  const menuWidth = pop.offsetWidth;
  const menuHeight = pop.offsetHeight;
  const left = Math.max(
    16,
    Math.min(rect.right - menuWidth, window.innerWidth - menuWidth - 16),
  );
  const top = Math.max(16, rect.top - menuHeight - 8);
  pop.style.left = `${left}px`;
  pop.style.top = `${top}px`;
}
function closePopover() {
  document.getElementById('active-popover')?.remove();
}

// ---- Data loaders ----
async function loadFolders() {
  state.folders = await api('/api/folders');
  renderFolders();
}
async function loadPresets() {
  state.presets = await api('/api/presets');
}
async function loadTasks() {
  const params = new URLSearchParams();
  if (state.view === 'favorites') params.set('view', 'favorites');
  else if (state.view === 'trash') params.set('view', 'trash');
  else if (state.view === 'folder') {
    params.set('view', 'folder');
    if (state.folderId) params.set('folder_id', state.folderId);
  } else params.set('view', 'media');
  state.tasks = await api('/api/tasks?' + params.toString());
}

// ---- Render: sidebar ----
function renderFolders() {
  const root = $('#folder-list');
  root.innerHTML = '';
  for (const f of state.folders) {
    const el = document.createElement('a');
    el.className = 'nav-item' + (state.view === 'folder' && state.folderId === f.id ? ' active' : '');
    el.href = `#folder/${f.id}`;
    el.dataset.folderId = f.id;
    el.innerHTML = `<span class="nav-icon">${icon('folder', { size: 16 })}</span><span>${escapeHtml(f.name)}</span>`;
    el.onclick = (ev) => {
      ev.preventDefault();
      goto('folder', { folderId: f.id });
    };
    root.appendChild(el);
  }
  if (state.folders.length === 0) {
    const empty = document.createElement('div');
    empty.style.cssText = 'font-size: 0.72rem; color: var(--text-muted); padding: 4px 10px; font-style: italic;';
    empty.textContent = 'No folders yet';
    root.appendChild(empty);
  }
}

function updateActiveNav() {
  $$('.nav-item').forEach((el) => el.classList.remove('active'));
  if (state.view === 'folder' && state.folderId) {
    $(`.nav-item[data-folder-id="${state.folderId}"]`)?.classList.add('active');
  } else {
    $(`.nav-item[data-view="${state.view}"]`)?.classList.add('active');
  }
}

// ---- Render: gallery ----
function groupTasksByDate(tasks) {
  const groups = new Map();
  for (const t of tasks) {
    const d = new Date(t.created_at);
    const key = d.toDateString();
    const list = groups.get(key) ?? [];
    list.push(t);
    groups.set(key, list);
  }
  return groups;
}

function formatDateHeader(d) {
  const date = new Date(d);
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);
  if (date.toDateString() === today.toDateString()) return 'Today';
  if (date.toDateString() === yesterday.toDateString()) return 'Yesterday';
  return date.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
}

function formatTime(d) {
  return new Date(d).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true }).toLowerCase();
}

function taskCardHtml(task) {
  const count = task.variants?.length || task.variant_count || 1;
  const pending = task.status === 'pending';
  const failed = task.status === 'failed';
  const variantsHtml = pending
    ? `<div class="task-pending-content">
        <div class="task-pending-spinner"></div>
        <div class="task-pending-label">generating…</div>
        <div class="task-pending-elapsed">0s</div>
      </div>`
    : failed
      ? `<div style="color: var(--danger); padding: 20px; text-align: center;">${escapeHtml(task.error || 'failed')}</div>`
      : task.variants
          .map(
            (v) =>
              `<img src="/images/${v.image_path}" alt="variant ${v.idx}" data-variant-id="${v.id}" data-task-id="${task.id}" loading="lazy" />`,
          )
          .join('');

  const presets = task.presets ?? [];
  const presetStackHtml = presets.length
    ? `<div class="task-preset-stack">${presets
        .map(
          (p) =>
            `<span class="task-preset-tag ${p.type}">${p.type === 'style' ? '✦' : '❈'} ${escapeHtml(p.name)} v${p.version}</span>`,
        )
        .join('')}${task.reference_image_path ? '<span class="task-ref-indicator">◎ ref</span>' : ''}</div>`
    : task.reference_image_path
      ? `<div class="task-preset-stack"><span class="task-ref-indicator">◎ ref attached</span></div>`
      : '';

  const favoriteIconSvg = task.favorite ? icon('star-filled', { size: 12 }) : icon('star', { size: 12 });
  const kindLabel = (task.kind || 'image_generation').replace('_', ' ');

  return `
    <div class="task-card ${pending ? 'pending' : ''} ${failed ? 'failed' : ''}" data-task-id="${task.id}">
      <div class="task-variants" data-count="${count}" data-aspect="${task.aspect_ratio || '1:1'}">${variantsHtml}</div>
      <div class="task-meta">
        <div class="task-label">${kindLabel}</div>
        ${presetStackHtml}
        <div class="task-prompt">${escapeHtml(task.prompt)}</div>
      </div>
      <div class="task-footer">
        <span>${formatTime(task.created_at)}</span>
        <div class="task-footer-actions">
          <button class="pill ${task.favorite ? 'active' : ''}" data-action="favorite" title="Favorite">${favoriteIconSvg}</button>
          ${task.trashed
            ? `<button class="pill" data-action="restore" title="Restore">${icon('corner-up-left', { size: 12 })}</button>`
            : `<button class="pill pill-danger" data-action="trash" title="Move to trash">${icon('trash', { size: 12 })}</button>`}
        </div>
      </div>
    </div>`;
}

function renderGallery() {
  const root = $('#view-root');
  if (state.tasks.length === 0) {
    const emptyByView = {
      media: { icon: 'images', title: 'Nothing here yet', hint: 'Describe an image in the bar below to begin. Attach a character preset to anchor identity.' },
      favorites: { icon: 'star', title: 'No favorites', hint: 'Tap the star on any generation to save it here.' },
      trash: { icon: 'trash', title: 'Trash is empty', hint: 'Removed generations land here before they\'re gone forever.' },
      folder: { icon: 'folder', title: 'Folder is empty', hint: 'Generate with this folder open, or move things in from the gallery.' },
    };
    const e = emptyByView[state.view] ?? emptyByView.media;
    root.innerHTML = `<div class="empty-state">
      <div class="empty-state-icon">${icon(e.icon, { size: 48 })}</div>
      <div class="empty-state-title">${e.title}</div>
      <div class="empty-state-hint">${e.hint}</div>
    </div>`;
    return;
  }
  const groups = groupTasksByDate(state.tasks);
  const html = [...groups.entries()]
    .map(
      ([key, tasks]) => `
      <div class="date-group">
        <div class="date-header">${formatDateHeader(key)}</div>
        <div class="task-grid">${tasks.map(taskCardHtml).join('')}</div>
      </div>`,
    )
    .join('');
  root.innerHTML = html;

  // Delegate events for task actions
  root.onclick = async (ev) => {
    const btn = ev.target.closest('button[data-action]');
    if (btn) {
      ev.stopPropagation();
      const card = btn.closest('.task-card');
      const taskId = card.dataset.taskId;
      const action = btn.dataset.action;
      try {
        if (action === 'favorite') {
          const task = state.tasks.find((t) => t.id === taskId);
          await api(`/api/tasks/${taskId}`, { method: 'PATCH', body: { favorite: !task.favorite } });
          await refreshView();
        } else if (action === 'trash') {
          await api(`/api/tasks/${taskId}`, { method: 'PATCH', body: { trashed: true } });
          toast('Moved to trash');
          await refreshView();
        } else if (action === 'restore') {
          await api(`/api/tasks/${taskId}`, { method: 'PATCH', body: { trashed: false } });
          toast('Restored');
          await refreshView();
        }
      } catch (e) {
        toast(e.message, 'err');
      }
      return;
    }
    const img = ev.target.closest('img[data-variant-id]');
    if (img) {
      goto('detail', { detailId: img.dataset.taskId });
    }
  };
}

// ---- Render: presets ----
async function renderPresets() {
  await loadPresets();
  const root = $('#view-root');

  // Preserve selected id across re-renders
  const selectedId = root.dataset.selectedId || state.presets[0]?.id || '';

  root.innerHTML = `
    <div class="presets-layout">
      <div>
        <div class="presets-list-header">
          <span class="input-label">All Presets</span>
          <button id="new-preset-btn">+ New</button>
        </div>
        <div class="presets-list" id="presets-list"></div>
      </div>
      <div id="preset-detail-root"></div>
    </div>`;

  const listRoot = $('#presets-list');
  for (const p of state.presets) {
    const el = document.createElement('div');
    el.className = 'preset-item' + (p.id === selectedId ? ' active' : '');
    el.dataset.presetId = p.id;
    el.innerHTML = `
      <div>
        <span class="preset-item-name">${escapeHtml(p.name)}</span>
        <span class="preset-item-version">v${p.version}</span>
      </div>
      <span class="preset-item-count">${String(p.use_count).padStart(4, '0')}</span>`;
    el.onclick = () => selectPreset(p.id);
    listRoot.appendChild(el);
  }

  if (state.presets.length === 0) {
    listRoot.innerHTML = `<div class="empty-state" style="padding: 30px 10px;">No presets yet. Create one to lock in your style.</div>`;
  }

  $('#new-preset-btn').onclick = () => renderPresetDetail(null, true);

  if (selectedId && state.presets.find((p) => p.id === selectedId)) {
    renderPresetDetail(selectedId, false);
  } else if (state.presets.length === 0) {
    renderPresetDetail(null, true);
  }

  root.dataset.selectedId = selectedId;
}

function selectPreset(id) {
  $('#view-root').dataset.selectedId = id;
  $$('.preset-item').forEach((el) => el.classList.toggle('active', el.dataset.presetId === id));
  renderPresetDetail(id, false);
}

function renderPresetDetail(id, isNew) {
  const root = $('#preset-detail-root');
  const preset = id ? state.presets.find((p) => p.id === id) : null;

  // Working state for the editor — mirrors preset, but lets us change type and ref on the fly.
  const current = {
    type: preset?.type ?? 'character',
    reference_image_path: preset?.reference_image_path ?? null,
  };

  const refImageHtml = () => {
    if (current.type !== 'character') return '';
    const hasRef = !!current.reference_image_path;
    return `
      <div>
        <div class="input-label">Reference image</div>
        <div class="preset-ref-upload">
          ${
            hasRef
              ? `<img src="/refs/${current.reference_image_path}" alt="reference" />`
              : `<div class="empty">no image</div>`
          }
          <div class="preset-ref-upload-info">
            <span class="label">${hasRef ? 'Pinned to this preset' : 'Anchor for identity'}</span>
            <span class="hint">
              ${hasRef
                ? 'Every generation that uses this preset will route through images.edit with this anchor.'
                : 'Drop a clear shot of the character. Front-facing, good lighting, roughly waist-up works best.'}
            </span>
            <div class="preset-ref-upload-actions">
              <button type="button" class="btn-ghost btn" id="preset-upload-btn">${hasRef ? 'Replace' : 'Upload'}</button>
              ${hasRef ? `<button type="button" class="btn-danger btn" id="preset-remove-ref">Remove</button>` : ''}
              <input type="file" id="preset-ref-input" accept="image/png,image/webp,image/jpeg" hidden />
            </div>
          </div>
        </div>
      </div>`;
  };

  const typeToggleHtml = () => `
    <div class="type-toggle" id="preset-type-toggle">
      <button type="button" data-type="character" class="${current.type === 'character' ? 'active' : ''}">Character</button>
      <button type="button" data-type="style" class="${current.type === 'style' ? 'active' : ''}">Style</button>
    </div>`;

  const render = () => {
    root.innerHTML = `
      <div class="preset-detail">
        <div class="preset-detail-header">
          <div class="preset-detail-title">
            <h2>${isNew ? 'New Preset' : escapeHtml(preset?.name || '')}</h2>
            ${preset ? `<span class="preset-detail-version">v${preset.version}</span>` : ''}
          </div>
          <div class="preset-actions">
            ${preset ? `<button class="btn-danger btn" id="archive-preset">Archive</button>` : ''}
          </div>
        </div>

        <div style="display: flex; align-items: center; gap: 12px;">
          <div class="input-label" style="margin: 0;">Type</div>
          ${typeToggleHtml()}
        </div>

        <div>
          <div class="input-label">Name</div>
          <input type="text" id="preset-name" class="text-input" value="${escapeHtml(preset?.name || '')}" placeholder="${current.type === 'character' ? 'e.g. Friend Vale' : 'e.g. Film Noir'}" />
        </div>

        <div>
          <div class="input-label">${current.type === 'character' ? 'Character description' : 'Style description'}</div>
          <textarea id="preset-body" class="text-input" placeholder="${current.type === 'character' ? 'Physical description, distinguishing features, outfit defaults, mannerisms…' : 'Rendering aesthetic, lighting mood, palette, medium, film reference…'}">${escapeHtml(preset?.body || '')}</textarea>
        </div>

        ${refImageHtml()}

        <div style="display: flex; gap: 10px; justify-content: flex-end;">
          <button class="btn" id="save-preset">${isNew || !preset ? 'Create' : 'Save (new version)'}</button>
        </div>
      </div>`;

    // Type toggle
    root.querySelectorAll('#preset-type-toggle button').forEach((btn) => {
      btn.addEventListener('click', () => {
        current.type = btn.dataset.type;
        render();
      });
    });

    // Upload / remove reference (character only)
    const uploadBtn = $('#preset-upload-btn');
    const fileInput = $('#preset-ref-input');
    if (uploadBtn && fileInput) {
      uploadBtn.onclick = () => fileInput.click();
      fileInput.onchange = async () => {
        const file = fileInput.files?.[0];
        if (!file) return;
        try {
          const up = await uploadFile(file);
          current.reference_image_path = up.path;
          render();
        } catch (e) {
          toast(e.message, 'err');
        }
      };
    }
    $('#preset-remove-ref')?.addEventListener('click', () => {
      current.reference_image_path = null;
      render();
    });

    // Save
    $('#save-preset').onclick = async () => {
      const name = $('#preset-name').value.trim();
      const body = $('#preset-body').value.trim();
      if (!name || !body) {
        toast('Name and body are required', 'err');
        return;
      }
      try {
        const payload = {
          name,
          body,
          type: current.type,
          reference_image_path: current.type === 'character' ? current.reference_image_path : null,
          ...(preset ? { fork_from: preset.id } : {}),
        };
        const saved = await api('/api/presets', { method: 'POST', body: payload });
        toast(`${preset ? 'Saved' : 'Created'} ${saved.name} v${saved.version}`);
        await loadPresets();
        $('#view-root').dataset.selectedId = saved.id;
        renderPresets();
      } catch (e) {
        toast(e.message, 'err');
      }
    };

    // Archive
    $('#archive-preset')?.addEventListener('click', async () => {
      if (!preset) return;
      try {
        await api(`/api/presets/${preset.id}`, { method: 'PATCH', body: { archived: true } });
        toast(`Archived ${preset.name}`);
        await loadPresets();
        $('#view-root').dataset.selectedId = '';
        renderPresets();
      } catch (e) {
        toast(e.message, 'err');
      }
    });
  };

  render();
}

// ---- Pending polling + elapsed counter ----
let pendingPollTimer = null;
let elapsedTickerTimer = null;

function anyPending() {
  return state.tasks.some((t) => t.status === 'pending' && String(t.id).startsWith('pending_'));
}

function startPendingPolling() {
  // Start elapsed ticker if not running
  if (!elapsedTickerTimer) {
    elapsedTickerTimer = setInterval(tickElapsed, 1000);
  }
  // Start polling timer if not running
  if (!pendingPollTimer) {
    pendingPollTimer = setInterval(pollPending, 8000);
  }
  tickElapsed();
}

function stopPendingTimers() {
  if (elapsedTickerTimer) {
    clearInterval(elapsedTickerTimer);
    elapsedTickerTimer = null;
  }
  if (pendingPollTimer) {
    clearInterval(pendingPollTimer);
    pendingPollTimer = null;
  }
}

function tickElapsed() {
  if (!anyPending()) {
    stopPendingTimers();
    return;
  }
  // Update elapsed label directly in the DOM — no full re-render
  for (const t of state.tasks) {
    if (t.status !== 'pending' || !t._clientStartMs) continue;
    const el = document.querySelector(`.task-card[data-task-id="${t.id}"] .task-pending-elapsed`);
    if (el) {
      const secs = Math.floor((Date.now() - t._clientStartMs) / 1000);
      el.textContent = secs >= 60 ? `${Math.floor(secs / 60)}m ${secs % 60}s` : `${secs}s`;
    }
  }
}

async function pollPending() {
  if (!anyPending()) {
    stopPendingTimers();
    return;
  }
  // Only poll if the user is on a view where tasks are listed
  if (state.view !== 'media' && state.view !== 'folder' && state.view !== 'favorites') return;

  try {
    const params = new URLSearchParams();
    if (state.view === 'favorites') params.set('view', 'favorites');
    else if (state.view === 'folder') {
      params.set('view', 'folder');
      if (state.folderId) params.set('folder_id', state.folderId);
    } else params.set('view', 'media');

    const serverTasks = await api('/api/tasks?' + params.toString());
    const pendingTasks = state.tasks.filter(
      (t) => t.status === 'pending' && String(t.id).startsWith('pending_'),
    );

    // For each pending task, look for a matching server task (same prompt, server created_at >= client start - 5s)
    const matched = new Set();
    const kept = [];
    for (const p of pendingTasks) {
      const match = serverTasks.find(
        (s) =>
          !matched.has(s.id) &&
          s.prompt === p.prompt &&
          s.created_at >= p._clientStartMs - 5000 &&
          s.status !== 'pending',
      );
      if (match) {
        matched.add(match.id);
      } else {
        kept.push(p);
      }
    }

    if (kept.length !== pendingTasks.length) {
      // At least one pending resolved on the server — rebuild state.tasks with server authoritative + remaining pendings
      state.tasks = [...kept, ...serverTasks];
      renderGallery();
      setViewTitle();
      if (!anyPending()) stopPendingTimers();
    }
  } catch (e) {
    console.warn('[poll]', e.message);
  }
}

// ---- Workshop ----
const SKETCHPAD_KEY = 'easel:sketchpad';

function renderWorkshop() {
  const root = $('#view-root');
  const sketchpadValue = localStorage.getItem(SKETCHPAD_KEY) ?? '';

  root.innerHTML = `
    <div class="workshop">
      <div class="workshop-panels">

        <!-- From an Image -->
        <div class="workshop-card">
          <div class="workshop-card-header">
            <div class="workshop-card-icon">${icon('image-plus', { size: 18 })}</div>
            <div class="workshop-card-titles">
              <span class="workshop-card-title">From an Image</span>
              <span class="workshop-card-hint">Drop in any image and I'll reverse-engineer it into a prompt.</span>
            </div>
          </div>

          <div id="ws-drop" class="workshop-image-drop">
            Click or drop an image here (PNG, JPG, WEBP)
          </div>
          <input type="file" id="ws-image-input" accept="image/png,image/webp,image/jpeg" hidden />

          <div id="ws-image-preview" hidden></div>

          <div id="ws-image-output" class="workshop-output empty">prompt will appear here</div>

          <div class="workshop-actions">
            <button class="btn-ghost btn" id="ws-image-analyze" disabled>${icon('sparkles', { size: 14 })} <span>Analyze</span></button>
            <button class="btn btn-with-icon" id="ws-image-send" disabled>${icon('arrow-up', { size: 14 })} Send to bar</button>
          </div>
        </div>

        <!-- Brain Dump -->
        <div class="workshop-card">
          <div class="workshop-card-header">
            <div class="workshop-card-icon">${icon('lightbulb', { size: 18 })}</div>
            <div class="workshop-card-titles">
              <span class="workshop-card-title">Brain Dump</span>
              <span class="workshop-card-hint">Toss me a fragment — I'll come back with three different reads.</span>
            </div>
          </div>

          <textarea id="ws-dump" class="text-input" placeholder="autumn, quiet, window light, cats somewhere…" style="min-height: 90px; font-family: var(--font-body);"></textarea>

          <div class="workshop-actions">
            <button class="btn btn-with-icon" id="ws-brainstorm-btn">${icon('sparkles', { size: 14 })} Generate ideas</button>
          </div>

          <div id="ws-brainstorm-output"></div>
        </div>
      </div>

      <!-- Sketchpad -->
      <div class="workshop-sketchpad">
        <div class="workshop-card-header" style="padding-bottom: 8px;">
          <div class="workshop-card-icon">${icon('pen-tool', { size: 18 })}</div>
          <div class="workshop-card-titles">
            <span class="workshop-card-title">Sketchpad</span>
            <span class="workshop-card-hint">Freeform notes that stay here across sessions.</span>
          </div>
        </div>
        <textarea id="ws-sketchpad" placeholder="jot ideas, fragments, phrases, references…">${escapeHtml(sketchpadValue)}</textarea>
        <div class="workshop-sketchpad-footer">
          <span id="ws-sketchpad-status">saved</span>
          <span>auto-saves locally</span>
        </div>
      </div>
    </div>`;

  wireWorkshop();
}

function wireWorkshop() {
  // --- Image to prompt ---
  const drop = $('#ws-drop');
  const fileInput = $('#ws-image-input');
  const preview = $('#ws-image-preview');
  const output = $('#ws-image-output');
  const analyzeBtn = $('#ws-image-analyze');
  const sendBtn = $('#ws-image-send');
  let selectedFile = null;
  let generatedPrompt = '';

  const pickFile = () => fileInput.click();
  drop.onclick = pickFile;

  ['dragover', 'dragenter'].forEach((evt) =>
    drop.addEventListener(evt, (e) => {
      e.preventDefault();
      drop.classList.add('dragover');
    }),
  );
  ['dragleave', 'drop'].forEach((evt) =>
    drop.addEventListener(evt, (e) => {
      e.preventDefault();
      drop.classList.remove('dragover');
    }),
  );
  drop.addEventListener('drop', (e) => {
    const f = e.dataTransfer?.files?.[0];
    if (f) handleFile(f);
  });

  fileInput.onchange = () => {
    const f = fileInput.files?.[0];
    if (f) handleFile(f);
  };

  function handleFile(f) {
    if (!f.type.startsWith('image/')) {
      toast('Not an image file', 'err');
      return;
    }
    selectedFile = f;
    const url = URL.createObjectURL(f);
    preview.hidden = false;
    preview.innerHTML = `<div class="workshop-image-preview"><img src="${url}" alt="preview" /><div style="color: var(--text-secondary); font-size: 0.78rem;">${escapeHtml(f.name)}</div></div>`;
    analyzeBtn.disabled = false;
    output.className = 'workshop-output empty';
    output.textContent = 'ready to analyze';
    generatedPrompt = '';
    sendBtn.disabled = true;
  }

  analyzeBtn.onclick = async () => {
    if (!selectedFile) return;
    const origHtml = analyzeBtn.innerHTML;
    analyzeBtn.disabled = true;
    analyzeBtn.innerHTML = '<span class="workshop-spinner"></span> <span>Analyzing…</span>';
    output.className = 'workshop-output empty';
    output.textContent = 'thinking…';
    try {
      const fd = new FormData();
      fd.append('file', selectedFile);
      const resp = await fetch('/api/workshop/from-image', { method: 'POST', body: fd });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ error: resp.statusText }));
        throw new Error(err.error || 'Failed');
      }
      const data = await resp.json();
      generatedPrompt = data.prompt || '';
      output.className = 'workshop-output';
      output.textContent = generatedPrompt;
      sendBtn.disabled = !generatedPrompt;
    } catch (e) {
      output.className = 'workshop-output empty';
      output.textContent = 'failed';
      toast(e.message, 'err');
    } finally {
      analyzeBtn.innerHTML = origHtml;
      analyzeBtn.disabled = false;
    }
  };

  sendBtn.onclick = () => {
    if (!generatedPrompt) return;
    sendPromptToBar(generatedPrompt);
  };

  // --- Brain dump ---
  const dumpInput = $('#ws-dump');
  const brainstormBtn = $('#ws-brainstorm-btn');
  const brainstormOut = $('#ws-brainstorm-output');

  brainstormBtn.onclick = async () => {
    const dump = dumpInput.value.trim();
    if (!dump) {
      toast('Write something first', 'err');
      return;
    }
    const origHtml = brainstormBtn.innerHTML;
    brainstormBtn.disabled = true;
    brainstormBtn.innerHTML = '<span class="workshop-spinner"></span> <span>Generating…</span>';
    brainstormOut.innerHTML = '<div class="workshop-output empty">three reads coming…</div>';
    try {
      const data = await api('/api/workshop/brainstorm', { method: 'POST', body: { dump } });
      const options = data.options || [];
      if (options.length === 0) {
        brainstormOut.innerHTML = '<div class="workshop-output empty">no options returned</div>';
      } else {
        brainstormOut.innerHTML = options
          .map(
            (opt, i) => `
          <div class="workshop-brainstorm-option" data-idx="${i}">
            <span class="workshop-brainstorm-option-title">${escapeHtml(opt.title || `Option ${i + 1}`)}</span>
            <div class="workshop-brainstorm-option-body">${escapeHtml(opt.prompt || '')}</div>
            <div class="workshop-brainstorm-option-actions">
              <button class="btn btn-with-icon" data-action="send-option" data-idx="${i}">${icon('arrow-up', { size: 14 })} Send to bar</button>
              <button class="btn-ghost btn btn-with-icon" data-action="copy-option" data-idx="${i}">${icon('copy', { size: 14 })} Copy</button>
            </div>
          </div>`,
          )
          .join('');

        brainstormOut.querySelectorAll('button[data-action]').forEach((btn) => {
          btn.addEventListener('click', async () => {
            const idx = Number(btn.dataset.idx);
            const opt = options[idx];
            if (btn.dataset.action === 'send-option') {
              sendPromptToBar(opt.prompt);
            } else if (btn.dataset.action === 'copy-option') {
              try {
                await navigator.clipboard.writeText(opt.prompt);
                toast('Copied');
              } catch (e) {
                toast(e.message, 'err');
              }
            }
          });
        });
      }
    } catch (e) {
      brainstormOut.innerHTML = '<div class="workshop-output empty">failed</div>';
      toast(e.message, 'err');
    } finally {
      brainstormBtn.innerHTML = origHtml;
      brainstormBtn.disabled = false;
    }
  };

  // --- Sketchpad (localStorage persistence) ---
  const sketchpad = $('#ws-sketchpad');
  const sketchpadStatus = $('#ws-sketchpad-status');
  let sketchpadTimer = null;
  sketchpad.addEventListener('input', () => {
    sketchpadStatus.textContent = 'saving…';
    clearTimeout(sketchpadTimer);
    sketchpadTimer = setTimeout(() => {
      localStorage.setItem(SKETCHPAD_KEY, sketchpad.value);
      sketchpadStatus.textContent = 'saved';
    }, 400);
  });
}

function sendPromptToBar(promptText) {
  $('#prompt-input').value = promptText;
  autoResizePrompt();
  goto('media').then(() => $('#prompt-input').focus());
  toast('Loaded into prompt bar');
}

// ---- Upload helper ----
async function uploadFile(file) {
  const fd = new FormData();
  fd.append('file', file);
  const resp = await fetch('/api/upload', { method: 'POST', body: fd });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ error: resp.statusText }));
    throw new Error(err.error || 'Upload failed');
  }
  return resp.json();
}

// ---- Render: detail ----
async function renderDetail(taskId) {
  const root = $('#view-root');
  root.innerHTML = `<div class="empty-state">Loading…</div>`;
  let task;
  try {
    task = await api(`/api/tasks/${taskId}`);
  } catch (e) {
    root.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
    return;
  }

  const presets = task.presets ?? [];
  const characterPresetsInTask = presets.filter((p) => p.type === 'character');
  const stylePresetInTask = presets.find((p) => p.type === 'style') ?? null;

  const presetBlock = presets.length
    ? `<div class="task-preset-stack" style="gap: 8px;">${presets
        .map(
          (p) =>
            `<span class="task-preset-tag ${p.type}" style="font-size: 0.66rem; padding: 4px 10px;">${p.type === 'style' ? '✦' : '❈'} ${escapeHtml(p.name)} v${p.version}</span>`,
        )
        .join('')}</div>`
    : '';

  const refBlock = task.reference_image_path
    ? `<div>
        <div class="input-label">Direct reference</div>
        <img src="/refs/${task.reference_image_path}" alt="reference" style="max-width: 180px; border-radius: 8px; border: 1px solid rgba(196, 168, 114, 0.25); margin-top: 6px;" />
       </div>`
    : '';

  root.innerHTML = `
    <div class="detail-view">
      <div class="detail-toolbar">
        <button class="btn-ghost btn btn-with-icon" id="back-btn">${icon('arrow-left', { size: 14 })} Back</button>
        <div class="detail-toolbar-meta">
          ${new Date(task.created_at).toLocaleString()} · ${task.aspect_ratio} · ${task.quality}
        </div>
        <div class="detail-toolbar-actions">
          <button class="btn btn-with-icon" id="reuse-prompt" title="Load prompt and settings into the bar">${icon('refresh-cw', { size: 14 })} Reuse</button>
          ${task.variants?.length ? `<button class="btn-ghost btn btn-with-icon" id="remix-btn" title="Use first variant as reference for a new gen">${icon('circle-dot', { size: 14 })} Remix</button>` : ''}
          ${task.variants?.length ? `<button class="btn-ghost btn btn-with-icon" id="download-first" title="Download first variant">${icon('download', { size: 14 })} Download</button>` : ''}
        </div>
      </div>
      <div class="detail-variants">
        ${(task.variants || [])
          .map(
            (v, idx) => `
            <div class="detail-variant-wrap" style="position: relative;">
              <img src="/images/${v.image_path}" alt="variant ${v.idx}" data-variant-path="${v.image_path}" />
              <a class="detail-copy-btn" href="/images/${v.image_path}" download="${task.id}-${idx}.png" title="Download variant ${idx + 1}">${icon('download', { size: 14 })}</a>
            </div>`,
          )
          .join('')}
      </div>
      ${presetBlock}
      ${refBlock}
      <div class="detail-copy-wrap">
        <div class="detail-prompt-block">${escapeHtml(task.prompt)}</div>
        <button class="detail-copy-btn" id="copy-prompt" title="Copy prompt to clipboard">${icon('copy', { size: 14 })}</button>
      </div>
    </div>`;

  $('#back-btn').onclick = () => history.back();
  $('#reuse-prompt').onclick = () => {
    $('#prompt-input').value = task.prompt;
    state.settings.aspect = task.aspect_ratio;
    state.settings.variants = task.variant_count;
    state.settings.quality = task.quality;
    state.settings.characterIds = characterPresetsInTask.map((p) => p.id);
    state.settings.styleId = stylePresetInTask?.id ?? '';
    state.settings.referencePath = task.reference_image_path;
    syncChips();
    renderRefStrip();
    goto('media');
    $('#prompt-input').focus();
  };

  $('#copy-prompt')?.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(task.prompt);
      toast('Prompt copied');
    } catch (e) {
      toast('Copy failed: ' + e.message, 'err');
    }
  });

  $('#download-first')?.addEventListener('click', () => {
    const v = task.variants?.[0];
    if (!v) return;
    const a = document.createElement('a');
    a.href = `/images/${v.image_path}`;
    a.download = `${task.id}-0.png`;
    a.click();
  });

  $('#remix-btn')?.addEventListener('click', async () => {
    try {
      const first = task.variants[0];
      const resp = await fetch(`/images/${first.image_path}`);
      if (!resp.ok) throw new Error('Could not load source image');
      const blob = await resp.blob();
      const file = new File([blob], 'remix.png', { type: blob.type || 'image/png' });
      const up = await uploadFile(file);
      state.settings.referencePath = up.path;
      state.settings.aspect = task.aspect_ratio;
      state.settings.variants = task.variant_count;
      state.settings.quality = task.quality;
      state.settings.characterIds = characterPresetsInTask.map((p) => p.id);
      state.settings.styleId = stylePresetInTask?.id ?? '';
      $('#prompt-input').value = '';
      renderRefStrip();
      syncChips();
      toast('Remix reference attached — describe the change and hit send');
      await goto('media');
      $('#prompt-input').focus();
    } catch (e) {
      toast(e.message, 'err');
    }
  });
}

// ---- Views ----
async function refreshView() {
  if (state.view === 'presets') return renderPresets();
  if (state.view === 'detail') return renderDetail(state.detailId);
  if (state.view === 'workshop') return renderWorkshop();
  await loadTasks();
  renderGallery();
}

function setViewTitle() {
  const titles = {
    media: 'My media',
    favorites: 'Favorites',
    trash: 'Trash',
    folder: state.folders.find((f) => f.id === state.folderId)?.name || 'Folder',
    presets: 'Presets',
    workshop: 'Workshop',
    detail: 'Detail',
  };
  $('#view-title').textContent = titles[state.view] || 'Easel';
  const counts = { media: state.tasks.length, favorites: state.tasks.length, trash: state.tasks.length };
  const c = counts[state.view];
  $('#view-meta').textContent = typeof c === 'number' ? `${c} ${c === 1 ? 'generation' : 'generations'}` : '';
}

async function goto(view, opts = {}) {
  state.view = view;
  state.folderId = opts.folderId ?? null;
  state.detailId = opts.detailId ?? null;
  updateActiveNav();
  const hash =
    view === 'folder' ? `#folder/${state.folderId}` :
    view === 'detail' ? `#gen/${state.detailId}` :
    `#${view}`;
  if (location.hash !== hash) history.pushState({}, '', hash);
  await refreshView();
  setViewTitle();
}

// ---- Chips (prompt controls) ----
function characterPresets() {
  return state.presets.filter((p) => (p.type ?? 'character') === 'character');
}
function stylePresets() {
  return state.presets.filter((p) => p.type === 'style');
}

function syncChips() {
  $('#chip-aspect .chip-value').textContent = state.settings.aspect;
  $('#chip-aspect').dataset.value = state.settings.aspect;
  $('#chip-variants .chip-value').textContent = `${state.settings.variants}v`;
  $('#chip-variants').dataset.value = String(state.settings.variants);
  $('#chip-quality .chip-value').textContent = state.settings.quality;
  $('#chip-quality').dataset.value = state.settings.quality;

  const chosenChars = state.settings.characterIds
    .map((id) => state.presets.find((p) => p.id === id))
    .filter(Boolean);
  const charLabel =
    chosenChars.length === 0
      ? 'No character'
      : chosenChars.length === 1
        ? `${chosenChars[0].name} v${chosenChars[0].version}`
        : `${chosenChars.map((p) => p.name).join(' + ')}`;
  $('#chip-character .chip-value').textContent = charLabel;
  $('#chip-character').classList.toggle('has-value', chosenChars.length > 0);

  const style = stylePresets().find((p) => p.id === state.settings.styleId);
  $('#chip-style .chip-value').textContent = style ? `${style.name} v${style.version}` : 'No style';
  $('#chip-style').classList.toggle('has-value', !!style);
}

function wireChips() {
  $('#chip-aspect').onclick = (ev) => {
    popover(
      ev.currentTarget,
      'Aspect ratio',
      ASPECTS.map((a) => ({ value: a, label: a, active: a === state.settings.aspect })),
      (v) => {
        state.settings.aspect = v;
        syncChips();
      },
    );
  };
  $('#chip-variants').onclick = (ev) => {
    popover(
      ev.currentTarget,
      'Variants',
      VARIANTS.map((n) => ({ value: n, label: `${n} ${n === 1 ? 'image' : 'images'}`, active: n === state.settings.variants })),
      (v) => {
        state.settings.variants = v;
        syncChips();
      },
    );
  };
  $('#chip-quality').onclick = (ev) => {
    popover(
      ev.currentTarget,
      'Quality',
      QUALITIES.map((q) => ({ value: q, label: q, active: q === state.settings.quality })),
      (v) => {
        state.settings.quality = v;
        syncChips();
      },
    );
  };

  $('#chip-character').onclick = (ev) => {
    const chars = characterPresets();
    if (chars.length === 0) {
      toast('No character presets yet — create one in the Presets page', 'err');
      return;
    }
    const options = chars.map((p) => ({
      value: p.id,
      label: `${p.name} v${p.version}${p.reference_image_path ? ' · ref' : ''}`,
      active: state.settings.characterIds.includes(p.id),
    }));
    popover(
      ev.currentTarget,
      'Characters',
      options,
      (id, active) => {
        if (active) {
          if (!state.settings.characterIds.includes(id)) state.settings.characterIds.push(id);
        } else {
          state.settings.characterIds = state.settings.characterIds.filter((x) => x !== id);
        }
        syncChips();
      },
      { multi: true },
    );
  };

  $('#chip-style').onclick = (ev) => {
    const styles = stylePresets();
    const options = [
      { value: '', label: 'No style', active: state.settings.styleId === '' },
      ...styles.map((p) => ({
        value: p.id,
        label: `${p.name} v${p.version}`,
        active: p.id === state.settings.styleId,
      })),
    ];
    popover(ev.currentTarget, 'Style', options, (v) => {
      state.settings.styleId = v;
      syncChips();
    });
  };
}

// ---- Reference attach (prompt-bar direct single image) ----
function renderRefStrip() {
  const strip = $('#ref-strip');
  strip.innerHTML = '';
  if (!state.settings.referencePath) {
    strip.hidden = true;
    return;
  }
  strip.hidden = false;
  const thumb = document.createElement('div');
  thumb.className = 'ref-thumb';
  thumb.innerHTML = `
    <img src="/refs/${state.settings.referencePath}" alt="reference" />
    <button type="button" class="remove" title="Remove">×</button>
    <span class="ref-thumb-label">Ref</span>`;
  thumb.querySelector('.remove').onclick = () => {
    state.settings.referencePath = null;
    renderRefStrip();
  };
  strip.appendChild(thumb);
}

function wireAttach() {
  const btn = $('#attach-btn');
  const input = $('#attach-input');
  btn.onclick = () => input.click();
  input.onchange = async () => {
    const file = input.files?.[0];
    if (!file) return;
    try {
      const up = await uploadFile(file);
      state.settings.referencePath = up.path;
      renderRefStrip();
      toast('Reference attached');
    } catch (e) {
      toast(e.message, 'err');
    } finally {
      input.value = ''; // reset so same file can be re-picked
    }
  };
}

// ---- Generate ----
async function handleGenerate(ev) {
  if (ev) ev.preventDefault();
  const input = $('#prompt-input');
  const prompt = input.value.trim();
  if (!prompt) return;

  const send = $('#send-btn');
  send.disabled = true;
  send.textContent = '…';

  const characterIds = [...state.settings.characterIds];
  const styleId = state.settings.styleId || null;
  const referencePath = state.settings.referencePath;
  const submittedPrompt = prompt;
  const submittedAspect = state.settings.aspect;
  const submittedVariants = state.settings.variants;
  const submittedQuality = state.settings.quality;
  const submittedFolderId = state.view === 'folder' ? state.folderId : null;

  // Build the preset stack we'll render optimistically
  const optimisticPresets = [];
  let pos = 0;
  for (const id of characterIds) {
    const p = state.presets.find((pp) => pp.id === id);
    if (p) optimisticPresets.push({ id: p.id, name: p.name, version: p.version, type: 'character', reference_image_path: p.reference_image_path, position: pos++ });
  }
  if (styleId) {
    const p = state.presets.find((pp) => pp.id === styleId);
    if (p) optimisticPresets.push({ id: p.id, name: p.name, version: p.version, type: 'style', reference_image_path: null, position: pos++ });
  }

  const pendingId = 'pending_' + Math.random().toString(36).slice(2, 10);
  const clientStart = Date.now();
  const pendingTask = {
    id: pendingId,
    kind: 'image_generation',
    prompt: submittedPrompt,
    preset_id: null,
    parent_task_id: null,
    aspect_ratio: submittedAspect,
    variant_count: submittedVariants,
    quality: submittedQuality,
    reference_image_path: referencePath,
    folder_id: submittedFolderId,
    favorite: 0,
    trashed: 0,
    status: 'pending',
    error: null,
    created_at: clientStart,
    _clientStartMs: clientStart, // for elapsed counter + polling match window
    variants: [],
    presets: optimisticPresets,
  };

  input.value = '';
  autoResizePrompt();

  const navigatedHome = state.view !== 'media' && state.view !== 'folder';
  if (navigatedHome) await goto('media');

  state.tasks = [pendingTask, ...state.tasks];
  renderGallery();
  setViewTitle();
  startPendingPolling();

  try {
    const task = await api('/api/generate', {
      method: 'POST',
      body: {
        prompt: submittedPrompt,
        character_preset_ids: characterIds,
        style_preset_id: styleId,
        reference_image_path: referencePath,
        aspect: submittedAspect,
        n: submittedVariants,
        quality: submittedQuality,
        folder_id: submittedFolderId,
      },
    });
    // Replace pending with real (if polling didn't already swap it out)
    const stillPending = state.tasks.some((t) => t.id === pendingId);
    if (stillPending) {
      state.tasks = state.tasks.map((t) => (t.id === pendingId ? task : t));
      renderGallery();
      setViewTitle();
      toast(`Generated ${task.variants.length} image${task.variants.length === 1 ? '' : 's'}`);
    }
    // Clear the direct reference after a successful gen — character/style persist
    state.settings.referencePath = null;
    renderRefStrip();
  } catch (e) {
    // If polling already resolved the pending task, swallow — the user already sees the result.
    const stillPending = state.tasks.some((t) => t.id === pendingId);
    if (stillPending) {
      state.tasks = state.tasks.map((t) =>
        t.id === pendingId ? { ...t, status: 'failed', error: e.message } : t,
      );
      renderGallery();
      toast(e.message, 'err');
    }
  } finally {
    send.disabled = false;
    send.textContent = '→';
    input.focus();
  }
}

function autoResizePrompt() {
  const el = $('#prompt-input');
  if (!el) return;
  el.style.height = 'auto';
  el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
}

// ---- Util ----
function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ---- Boot ----
async function boot() {
  // Wire sidebar nav
  $$('.nav-item[data-view]').forEach((el) => {
    el.onclick = (ev) => {
      ev.preventDefault();
      goto(el.dataset.view);
    };
  });

  $('#new-folder-btn').onclick = async () => {
    const name = prompt('Folder name?'); // native prompt, simple
    if (!name) return;
    try {
      await api('/api/folders', { method: 'POST', body: { name } });
      await loadFolders();
    } catch (e) {
      toast(e.message, 'err');
    }
  };

  $('#prompt-form').addEventListener('submit', handleGenerate);

  const promptInput = $('#prompt-input');
  promptInput.addEventListener('input', autoResizePrompt);
  promptInput.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && !ev.shiftKey) {
      ev.preventDefault();
      handleGenerate();
    }
  });
  autoResizePrompt();

  wireChips();
  wireAttach();
  wireKeyboard();

  window.addEventListener('popstate', () => routeFromHash());

  await Promise.all([loadFolders(), loadPresets()]);
  syncChips();
  renderRefStrip();
  hydrateIcons();

  routeFromHash();
}

function wireKeyboard() {
  document.addEventListener('keydown', (ev) => {
    const tag = (ev.target?.tagName ?? '').toLowerCase();
    const inField = tag === 'input' || tag === 'textarea' || ev.target?.isContentEditable;

    // Esc — close popover, or go back from detail/presets
    if (ev.key === 'Escape') {
      if (document.getElementById('active-popover')) {
        closePopover();
        return;
      }
      if (!inField && (state.view === 'detail' || state.view === 'presets')) {
        history.back();
      }
      return;
    }

    if (inField) return;

    // "/" — focus prompt
    if (ev.key === '/') {
      ev.preventDefault();
      $('#prompt-input').focus();
      return;
    }

    // "n" — new preset when on presets page; new folder otherwise
    if (ev.key === 'n' || ev.key === 'N') {
      if (state.view === 'presets') {
        ev.preventDefault();
        $('#new-preset-btn')?.click();
      }
    }
  });
}

function routeFromHash() {
  const h = location.hash.slice(1) || 'media';
  if (h.startsWith('folder/')) {
    goto('folder', { folderId: h.split('/')[1] });
  } else if (h.startsWith('gen/')) {
    goto('detail', { detailId: h.split('/')[1] });
  } else {
    goto(h);
  }
}

boot().catch((e) => {
  console.error('[easel] boot failed', e);
  toast('Boot failed: ' + e.message, 'err');
});
