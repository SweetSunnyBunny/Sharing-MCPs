/* ═══ Game Room — Dominoes ═══ */
/* ANAM GUIDE: GAME ROOM DOMINOES BEHAVIOR
   What: The whole dominoes game screen — drawing tiles, picking moves, animations, and talking to the game server at /api/games (api/games.py, logic in services/dominoes.py).
   Loaded by: static/gameroom.html only.
   Edit here when: You want to change how the game board looks or plays in the browser. Actual game RULES live in services/dominoes.py, not here. */

const IDENTITY_COLORS = {
    Avery:    { accent: '#C47A8A', rgb: '196,122,138' },
    Rowan:   { accent: '#5BA89A', rgb: '91,168,154'  },
    Sage:    { accent: '#6B7DB5', rgb: '107,125,181' },
    Ember: { accent: '#8B73A8', rgb: '139,115,168' },
    Claude:    { accent: '#C9A840', rgb: '201,168,64'  },
    Juniper:      { accent: '#B8AFA0', rgb: '184,175,160' },
    Owner:    { accent: '#C47A8A', rgb: '196,122,138' },
};

// Pip positions in a 3x3 grid (1-indexed cell positions that should be visible for each value)
const PIP_POSITIONS = {
    0: [],
    1: [5],
    2: [3, 7],
    3: [3, 5, 7],
    4: [1, 3, 7, 9],
    5: [1, 3, 5, 7, 9],
    6: [1, 3, 4, 6, 7, 9],
    7: [1, 3, 4, 5, 6, 7, 9],
    8: [1, 2, 3, 4, 6, 7, 8, 9],
    9: [1, 2, 3, 4, 5, 6, 7, 8, 9],
};

const GameRoom = {
    _gameId: null,
    _state: null,
    _validMoves: [],
    _selectedTile: null,
    _opponents: new Set(),
    _mode: 'block',
    _maxPip: 9,
    _animating: false,

    async init() {
        // Night mode
        if (localStorage.getItem('anam-night') === 'true') {
            document.body.classList.add('night-mode');
        }
        const nightBtn = document.getElementById('night-toggle');
        if (nightBtn) {
            nightBtn.addEventListener('click', () => {
                document.body.classList.toggle('night-mode');
                localStorage.setItem('anam-night', document.body.classList.contains('night-mode'));
            });
        }

        this._bindLobby();
        this._bindGame();

        // Check URL for game_id
        const params = new URLSearchParams(location.search);
        const gameId = params.get('game');
        if (gameId) {
            await this._loadGame(gameId);
        } else {
            await this._showLobby();
        }
    },

    // ─── Lobby ───

    _bindLobby() {
        // Pill groups
        document.querySelectorAll('.pill-group').forEach(group => {
            group.addEventListener('click', e => {
                const pill = e.target.closest('.pill');
                if (!pill) return;
                group.querySelectorAll('.pill').forEach(p => p.classList.remove('active'));
                pill.classList.add('active');
                if (group.id === 'mode-pills') this._mode = pill.dataset.value;
                if (group.id === 'tileset-pills') {
                    this._maxPip = parseInt(pill.dataset.value);
                    this._validatePlayerCount();
                }
            });
        });

        document.getElementById('start-game-btn').addEventListener('click', () => this._startGame());
    },

    async _showLobby() {
        document.getElementById('lobby-view').style.display = '';
        document.getElementById('game-view').style.display = 'none';

        // Build opponent grid
        const grid = document.getElementById('opponent-grid');
        grid.innerHTML = '';
        const names = ['Avery', 'Rowan', 'Sage', 'Ember', 'Claude', 'Juniper'];
        names.forEach(name => {
            const colors = IDENTITY_COLORS[name];
            const card = document.createElement('div');
            card.className = 'opponent-card';
            card.dataset.name = name;
            card.style.setProperty('--card-accent', colors.accent);
            card.style.setProperty('--card-accent-rgb', colors.rgb);
            card.innerHTML = `
                <div class="opponent-avatar" style="background:${colors.accent}">${name[0]}</div>
                <span class="opponent-name">${name}</span>
            `;
            card.addEventListener('click', () => this._toggleOpponent(name, card));
            grid.appendChild(card);
        });

        // Load active games
        await this._loadActiveGames();

        this._updateStartBtn();
    },

    _toggleOpponent(name, card) {
        if (this._opponents.has(name)) {
            this._opponents.delete(name);
            card.classList.remove('selected');
        } else {
            this._opponents.add(name);
            card.classList.add('selected');
        }
        this._validatePlayerCount();
        this._updateStartBtn();
    },

    _validatePlayerCount() {
        const count = this._opponents.size + 1;
        if (this._maxPip === 6 && count > 4) {
            // Auto-switch to double-9
            this._maxPip = 9;
            document.querySelectorAll('#tileset-pills .pill').forEach(p => {
                p.classList.toggle('active', p.dataset.value === '9');
            });
        }
    },

    _updateStartBtn() {
        const btn = document.getElementById('start-game-btn');
        btn.disabled = this._opponents.size === 0;
        const count = this._opponents.size + 1;
        btn.textContent = count === 1 ? 'Pick opponents to start' :
            `Start Game (${count} players)`;
    },

    async _loadActiveGames() {
        try {
            const data = await fetchJson('/api/games/list');
            const active = (data.games || []).filter(g => g.status !== 'game_over');
            const section = document.getElementById('active-games-section');
            const list = document.getElementById('active-games-list');

            if (active.length === 0) {
                section.style.display = 'none';
                return;
            }

            section.style.display = '';
            const _esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
            list.innerHTML = active.map(g => `
                <div class="active-game-row" data-game-id="${_esc(g.game_id)}">
                    <div class="game-row-info">
                        <span class="game-row-players">${_esc(g.players.join(', '))}</span>
                        <span class="game-row-meta">${g.mode === 'all_fives' ? 'All Fives' : 'Block'} &middot; Round ${g.round_number}</span>
                    </div>
                    <div class="game-row-actions">
                        <button class="game-row-btn" data-action="resume" data-gid="${_esc(g.game_id)}">Resume</button>
                        <button class="game-row-btn danger" data-action="abandon" data-gid="${_esc(g.game_id)}">Delete</button>
                    </div>
                </div>
            `).join('');
            list.querySelectorAll('[data-action="resume"]').forEach(btn => {
                btn.addEventListener('click', () => GameRoom._loadGame(btn.dataset.gid));
            });
            list.querySelectorAll('[data-action="abandon"]').forEach(btn => {
                btn.addEventListener('click', () => GameRoom._abandonGame(btn.dataset.gid));
            });
        } catch (err) {
            console.warn('[GameRoom] Failed to load games:', err);
        }
    },

    async _startGame() {
        const opponents = Array.from(this._opponents);
        if (!opponents.length) return;

        try {
            const data = await sendJson('/api/games/new', 'POST', {
                opponents,
                mode: this._mode,
                max_pip: this._maxPip,
            });
            this._state = data.state;
            this._gameId = data.state.game_id;
            history.replaceState(null, '', `?game=${this._gameId}`);

            document.getElementById('lobby-view').style.display = 'none';
            document.getElementById('game-view').style.display = '';

            // Animate any initial AI moves
            if (data.ai_actions && data.ai_actions.length) {
                await this._animateAiActions(data.ai_actions);
            }

            this._render();
        } catch (err) {
            alert('Failed to start game: ' + err.message);
        }
    },

    async _abandonGame(gameId) {
        if (!confirm('Delete this game?')) return;
        try {
            await sendJson(`/api/games/${gameId}`, 'DELETE');
            await this._loadActiveGames();
        } catch (err) {
            console.warn('[GameRoom] Abandon failed:', err);
        }
    },

    // ─── Game Loading ───

    async _loadGame(gameId) {
        try {
            const data = await fetchJson(`/api/games/${gameId}`);
            this._state = data.state;
            this._gameId = gameId;
            history.replaceState(null, '', `?game=${this._gameId}`);

            document.getElementById('lobby-view').style.display = 'none';
            document.getElementById('game-view').style.display = '';

            this._render();
        } catch (err) {
            alert('Game not found');
            this._showLobby();
        }
    },

    _bindGame() {
        document.getElementById('draw-btn').addEventListener('click', () => this._drawTile());
        document.getElementById('pass-btn').addEventListener('click', () => this._passTurn());
        document.getElementById('cancel-placement').addEventListener('click', () => this._cancelPlacement());
        document.getElementById('back-to-lobby').addEventListener('click', () => {
            history.replaceState(null, '', location.pathname);
            this._gameId = null;
            this._state = null;
            this._showLobby();
        });
    },

    // ─── Rendering ───

    _render() {
        if (!this._state) return;
        this._renderScoreBar();
        this._renderOpponentHands();
        this._renderBoard();
        this._renderHand();
        this._renderLog();
        this._checkOverlay();
    },

    _renderScoreBar() {
        const bar = document.getElementById('score-bar');
        const s = this._state;
        bar.innerHTML = s.players.map((p, i) => {
            const colors = IDENTITY_COLORS[p.identity || p.name] || IDENTITY_COLORS.Owner;
            const isActive = i === s.current_player && s.status === 'playing';
            const scoreLabel = s.mode === 'all_fives' ? p.score : `${p.rounds_won || 0}W`;
            return `
                <div class="score-chip ${isActive ? 'active-turn' : ''}"
                     style="--chip-accent:${colors.accent};--chip-accent-rgb:${colors.rgb}">
                    <span class="score-dot" style="background:${colors.accent}"></span>
                    <span>${p.name}</span>
                    <span class="score-value">${scoreLabel}</span>
                </div>
            `;
        }).join('');
    },

    _renderOpponentHands() {
        const container = document.getElementById('opponent-hands');
        const s = this._state;
        container.innerHTML = s.players
            .filter(p => !p.is_human)
            .map(p => {
                const colors = IDENTITY_COLORS[p.identity || p.name] || IDENTITY_COLORS.Owner;
                const count = p.hand_count || 0;
                const tiles = Array(count).fill(0).map(() =>
                    `<div class="tile-back" style="background:${colors.accent}"></div>`
                ).join('');
                return `
                    <div class="opponent-hand-group">
                        <div class="opponent-hand-tiles">${tiles}</div>
                        <span class="opponent-hand-name">${p.name} (${count})</span>
                    </div>
                `;
            }).join('');
    },

    _renderBoard() {
        const boardEl = document.getElementById('game-board');
        const s = this._state;
        const b = s.board;

        if (!b.spinner && !b.chain_left.length && !b.chain_right.length) {
            boardEl.innerHTML = '<span class="waiting-banner">No tiles played yet</span>';
            this._renderBoardInfo();
            return;
        }

        let html = '';

        // Top chain (vertical, above spinner)
        if (b.chain_top && b.chain_top.length > 0) {
            html += '<div class="board-chain vertical">';
            // Reverse so farthest from spinner is first
            for (const t of [...b.chain_top].reverse()) {
                html += this._renderTile(t[0], t[1], 'vertical');
            }
            html += '</div>';
        }

        // Main horizontal row: left chain + spinner + right chain
        html += '<div class="board-row">';

        // Left chain (reversed so open end is on the far left)
        if (b.left_end !== null || b.chain_left.length) {
            html += `<span class="open-end-marker">${b.left_end ?? ''}</span>`;
            for (const t of [...(b.chain_left || [])].reverse()) {
                html += this._renderTile(t[0], t[1], 'horizontal');
            }
        }

        // Spinner
        if (b.spinner) {
            html += this._renderTile(b.spinner[0], b.spinner[1], 'vertical spinner-tile');
        }

        // Right chain
        if (b.right_end !== null || b.chain_right.length) {
            for (const t of (b.chain_right || [])) {
                html += this._renderTile(t[0], t[1], 'horizontal');
            }
            html += `<span class="open-end-marker">${b.right_end ?? ''}</span>`;
        }

        html += '</div>';

        // Bottom chain (vertical, below spinner)
        if (b.chain_bottom && b.chain_bottom.length > 0) {
            html += '<div class="board-chain vertical">';
            for (const t of b.chain_bottom) {
                html += this._renderTile(t[0], t[1], 'vertical');
            }
            html += '</div>';
        }

        boardEl.innerHTML = html;
        this._renderBoardInfo();
    },

    _renderBoardInfo() {
        const s = this._state;
        const b = s.board;

        // Calculate open end sum from the board state
        let endSum = 0;
        const ends = [];
        if (b.left_end !== null && b.left_end !== undefined) ends.push(b.left_end);
        if (b.right_end !== null && b.right_end !== undefined) ends.push(b.right_end);

        // Top/bottom only count when both left and right chains have tiles
        if ((b.chain_left || []).length > 0 && (b.chain_right || []).length > 0) {
            if (b.top_end !== null && b.top_end !== undefined) ends.push(b.top_end);
            if (b.bottom_end !== null && b.bottom_end !== undefined) ends.push(b.bottom_end);
        }
        endSum = ends.reduce((a, b) => a + b, 0);

        const sumEl = document.getElementById('end-sum-display');
        if (s.mode === 'all_fives') {
            const isScoring = endSum > 0 && endSum % 5 === 0;
            sumEl.textContent = `Ends: ${endSum}${isScoring ? ' \u2605' : ''}`;
            sumEl.className = `end-sum${isScoring ? ' scoring' : ''}`;
        } else {
            sumEl.textContent = `Ends: ${endSum}`;
            sumEl.className = 'end-sum';
        }

        document.getElementById('boneyard-count').textContent =
            `Boneyard: ${s.boneyard_count}`;
    },

    _renderHand() {
        const container = document.getElementById('player-hand');
        const s = this._state;
        const ownerIdx = s.players.findIndex(p => p.is_human);
        const owner = s.players[ownerIdx];
        const isMyTurn = s.current_player === ownerIdx && s.status === 'playing';

        // Get valid moves
        const validTiles = new Set();
        if (isMyTurn && this._validMoves.length) {
            this._validMoves.forEach(m => validTiles.add(m.tile.join(',')));
        }

        container.innerHTML = (owner.hand || []).map(t => {
            const key = `${Math.max(t[0],t[1])},${Math.min(t[0],t[1])}`;
            const playable = validTiles.has(key);
            const selected = this._selectedTile && this._selectedTile.join(',') === key;
            return `<div class="domino horizontal in-hand ${playable ? 'playable' : ''} ${selected ? 'selected' : ''}"
                        data-tile="${t[0]},${t[1]}"
                        onclick="GameRoom._selectTile([${t[0]},${t[1]}])">
                ${this._renderHalf(t[0])}${this._renderHalf(t[1])}
            </div>`;
        }).join('');

        // Action buttons
        const drawBtn = document.getElementById('draw-btn');
        const passBtn = document.getElementById('pass-btn');

        if (isMyTurn && !this._animating) {
            const canDraw = !validTiles.size && s.boneyard_count > 0;
            const canPass = !validTiles.size && s.boneyard_count === 0;
            drawBtn.style.display = canDraw ? '' : 'none';
            passBtn.style.display = canPass ? '' : 'none';
        } else {
            drawBtn.style.display = 'none';
            passBtn.style.display = 'none';
        }

        // Fetch valid moves if it's our turn
        if (isMyTurn && !this._validMoves.length && s.status === 'playing') {
            this._fetchValidMoves();
        }
    },

    _renderTile(high, low, orientation) {
        const cls = orientation || 'horizontal';
        return `<div class="domino ${cls}">${this._renderHalf(high)}${this._renderHalf(low)}</div>`;
    },

    _renderHalf(value) {
        const positions = PIP_POSITIONS[value] || [];
        let html = '<div class="domino-half">';
        for (let i = 1; i <= 9; i++) {
            html += `<span class="pip ${positions.includes(i) ? 'show' : ''}"></span>`;
        }
        html += '</div>';
        return html;
    },

    _renderLog() {
        const container = document.getElementById('game-log');
        const s = this._state;
        container.innerHTML = (s.log || []).map(entry => {
            const colors = IDENTITY_COLORS[entry.player] || IDENTITY_COLORS.Owner;
            const isRoundEvent = entry.type === 'round_over' || entry.type === 'game_over' || entry.type === 'deal';
            let html = `<div class="log-entry ${isRoundEvent ? 'round-event' : ''}"
                             style="--log-color:${colors.accent}">
                ${entry.message}`;
            if (entry.comment) {
                html += `<span class="log-comment">"${entry.comment}"</span>`;
            }
            html += '</div>';
            return html;
        }).join('');

        // Scroll to bottom
        container.scrollTop = container.scrollHeight;
    },

    _checkOverlay() {
        const s = this._state;
        const overlay = document.getElementById('round-overlay');

        if (s.status === 'round_over' || s.status === 'game_over') {
            overlay.style.display = '';
            const title = document.getElementById('overlay-title');
            const scores = document.getElementById('overlay-scores');
            const actions = document.getElementById('overlay-actions');

            if (s.status === 'game_over') {
                title.textContent = `${s.winner} wins!`;
            } else {
                const lastRoundEntry = [...s.log].reverse().find(e => e.type === 'round_over');
                title.textContent = lastRoundEntry ? lastRoundEntry.message : 'Round Over';
            }

            scores.innerHTML = s.players.map(p => {
                const colors = IDENTITY_COLORS[p.identity || p.name] || IDENTITY_COLORS.Owner;
                const isWinner = p.name === s.winner;
                const scoreLabel = s.mode === 'all_fives' ? `${p.score} pts` : `${p.rounds_won || 0} wins`;
                return `
                    <div class="overlay-score-row ${isWinner ? 'winner' : ''}"
                         style="border-left: 3px solid ${colors.accent}">
                        <span class="overlay-score-name">${p.name}</span>
                        <span class="overlay-score-val">${scoreLabel}</span>
                    </div>
                `;
            }).join('');

            if (s.status === 'game_over') {
                actions.innerHTML = `
                    <button class="overlay-btn primary" onclick="GameRoom._backToLobby()">New Game</button>
                `;
            } else {
                actions.innerHTML = `
                    <button class="overlay-btn primary" onclick="GameRoom._nextRound()">Next Round</button>
                    <button class="overlay-btn secondary" onclick="GameRoom._backToLobby()">Lobby</button>
                `;
            }
        } else {
            overlay.style.display = 'none';
        }
    },

    // ─── Player Actions ───

    async _fetchValidMoves() {
        if (!this._gameId) return;
        try {
            const data = await fetchJson(`/api/games/${this._gameId}/valid-moves`);
            this._validMoves = data.moves || [];
            this._renderHand();
        } catch (err) {
            console.warn('[GameRoom] Valid moves fetch failed:', err);
        }
    },

    _selectTile(tile) {
        const key = `${Math.max(tile[0],tile[1])},${Math.min(tile[0],tile[1])}`;
        // Check if this tile is playable
        const movesForTile = this._validMoves.filter(m => {
            const mKey = `${Math.max(m.tile[0],m.tile[1])},${Math.min(m.tile[0],m.tile[1])}`;
            return mKey === key;
        });

        if (!movesForTile.length) return;

        if (movesForTile.length === 1) {
            // Only one valid placement — play immediately
            this._playTile(movesForTile[0].tile, movesForTile[0].end);
        } else {
            // Multiple valid ends — show chooser
            this._selectedTile = tile;
            this._renderHand();
            this._showEndChooser(movesForTile);
        }
    },

    _showEndChooser(moves) {
        const chooser = document.getElementById('end-chooser');
        const options = document.getElementById('end-options');
        chooser.style.display = '';

        options.innerHTML = moves.map(m => {
            const endLabel = m.end.charAt(0).toUpperCase() + m.end.slice(1);
            // Find the pip value of that end
            const b = this._state.board;
            const val = b[`${m.end}_end`];
            return `<button class="end-option-btn" onclick="GameRoom._playTile([${m.tile}], '${m.end}')">
                ${endLabel} (${val})
            </button>`;
        }).join('');
    },

    _cancelPlacement() {
        this._selectedTile = null;
        document.getElementById('end-chooser').style.display = 'none';
        this._renderHand();
    },

    async _playTile(tile, end) {
        this._selectedTile = null;
        document.getElementById('end-chooser').style.display = 'none';

        try {
            const data = await sendJson(`/api/games/${this._gameId}/play`, 'POST', { tile, end });
            this._state = data.state;
            this._validMoves = [];
            this._render();

            // Animate AI actions
            if (data.ai_actions && data.ai_actions.length) {
                await this._animateAiActions(data.ai_actions);
                // Refresh state after AI turns
                await this._refreshState();
            } else {
                await this._fetchValidMoves();
            }
        } catch (err) {
            alert(err.message);
        }
    },

    async _drawTile() {
        try {
            const data = await sendJson(`/api/games/${this._gameId}/draw`, 'POST');
            this._state = data.state;
            this._validMoves = [];

            if (data.can_play) {
                // Got a playable tile — refresh valid moves
                this._render();
                await this._fetchValidMoves();
            } else if (data.can_draw) {
                // Still can't play, can draw more
                this._render();
            } else {
                // Can't play, can't draw — show pass button
                this._render();
            }
        } catch (err) {
            alert(err.message);
        }
    },

    async _passTurn() {
        try {
            const data = await sendJson(`/api/games/${this._gameId}/pass`, 'POST');
            this._state = data.state;
            this._validMoves = [];
            this._render();

            if (data.ai_actions && data.ai_actions.length) {
                await this._animateAiActions(data.ai_actions);
                await this._refreshState();
            } else {
                await this._fetchValidMoves();
            }
        } catch (err) {
            alert(err.message);
        }
    },

    async _nextRound() {
        try {
            const data = await sendJson(`/api/games/${this._gameId}/new-round`, 'POST');
            this._state = data.state;
            this._validMoves = [];
            this._render();

            if (data.ai_actions && data.ai_actions.length) {
                await this._animateAiActions(data.ai_actions);
                await this._refreshState();
            } else {
                await this._fetchValidMoves();
            }
        } catch (err) {
            alert(err.message);
        }
    },

    _backToLobby() {
        history.replaceState(null, '', location.pathname);
        this._gameId = null;
        this._state = null;
        this._validMoves = [];
        this._selectedTile = null;
        this._opponents.clear();
        this._showLobby();
    },

    // ─── AI Animation ───

    async _animateAiActions(actions) {
        this._animating = true;
        for (const action of actions) {
            // Brief delay so player can see each AI move
            await new Promise(r => setTimeout(r, 800));
            // State is already updated server-side; we just need to re-render
            // to show the progressive changes
            await this._refreshState();
        }
        this._animating = false;
    },

    async _refreshState() {
        if (!this._gameId) return;
        try {
            const data = await fetchJson(`/api/games/${this._gameId}`);
            this._state = data.state;
            this._validMoves = [];
            this._render();
            if (this._state.status === 'playing') {
                const ownerIdx = this._state.players.findIndex(p => p.is_human);
                if (this._state.current_player === ownerIdx) {
                    await this._fetchValidMoves();
                }
            }
        } catch (err) {
            console.warn('[GameRoom] Refresh failed:', err);
        }
    },
};

document.addEventListener('DOMContentLoaded', () => GameRoom.init());
