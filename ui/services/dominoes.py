"""Domino game engine — tile sets, board state, move validation, scoring, AI."""

# ANAM GUIDE: DOMINOES GAME RULES ENGINE
# What: The complete rules brain for dominoes — creating the tile set, dealing hands, checking which moves are legal, scoring, and how the computer player picks its move.
# Called by: api/games.py, which the Game Room page (static/gameroom.html + gameroom.js) talks to.
# Edit here when: You want to change how dominoes plays — scoring rules, hand sizes, AI difficulty, or a rule that feels wrong at the table.

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Tile set generation
# ---------------------------------------------------------------------------

Tile = tuple[int, int]  # (high, low), always high >= low


def create_tile_set(max_pip: int = 9) -> list[Tile]:
    """Generate all tiles for a double-N set.  Double-6 = 28, double-9 = 55."""
    tiles: list[Tile] = []
    for high in range(max_pip + 1):
        for low in range(high + 1):
            tiles.append((high, low))
    return tiles


def tiles_per_hand(num_players: int, max_pip: int = 9) -> int:
    """How many tiles each player draws."""
    total = len(create_tile_set(max_pip))
    if num_players <= 2:
        return 7
    if num_players <= 4:
        return 7 if max_pip >= 9 else 5
    # 5-7 players (double-9): deal 7 each
    return 7


# ---------------------------------------------------------------------------
# Board state
# ---------------------------------------------------------------------------

@dataclass
class BoardState:
    """Tracks the chain of played tiles and open ends."""
    chain_left: list[Tile] = field(default_factory=list)   # tiles left of spinner
    chain_right: list[Tile] = field(default_factory=list)  # tiles right of spinner
    chain_top: list[Tile] = field(default_factory=list)    # tiles above spinner
    chain_bottom: list[Tile] = field(default_factory=list) # tiles below spinner
    spinner: Tile | None = None
    left_end: int | None = None
    right_end: int | None = None
    top_end: int | None = None
    bottom_end: int | None = None

    def open_ends(self) -> list[tuple[str, int]]:
        """Return list of (branch_name, pip_value) for all open ends."""
        if self.spinner is None:
            # No tiles played yet, or first tile isn't a double
            ends = []
            if self.left_end is not None:
                ends.append(("left", self.left_end))
            if self.right_end is not None:
                ends.append(("right", self.right_end))
            return ends

        ends = []
        # Left and right always open
        if self.left_end is not None:
            ends.append(("left", self.left_end))
        if self.right_end is not None:
            ends.append(("right", self.right_end))
        # Top and bottom open only once both left and right have tiles
        if len(self.chain_left) > 0 and len(self.chain_right) > 0:
            if self.top_end is not None:
                ends.append(("top", self.top_end))
            if self.bottom_end is not None:
                ends.append(("bottom", self.bottom_end))
        return ends

    def open_end_sum(self) -> int:
        """Sum of all open end pip values."""
        return sum(v for _, v in self.open_ends())

    def is_empty(self) -> bool:
        return self.spinner is None and not self.chain_left and not self.chain_right

    def to_dict(self) -> dict:
        return {
            "chain_left": [list(t) for t in self.chain_left],
            "chain_right": [list(t) for t in self.chain_right],
            "chain_top": [list(t) for t in self.chain_top],
            "chain_bottom": [list(t) for t in self.chain_bottom],
            "spinner": list(self.spinner) if self.spinner else None,
            "left_end": self.left_end,
            "right_end": self.right_end,
            "top_end": self.top_end,
            "bottom_end": self.bottom_end,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BoardState":
        return cls(
            chain_left=[tuple(t) for t in d.get("chain_left", [])],
            chain_right=[tuple(t) for t in d.get("chain_right", [])],
            chain_top=[tuple(t) for t in d.get("chain_top", [])],
            chain_bottom=[tuple(t) for t in d.get("chain_bottom", [])],
            spinner=tuple(d["spinner"]) if d.get("spinner") else None,
            left_end=d.get("left_end"),
            right_end=d.get("right_end"),
            top_end=d.get("top_end"),
            bottom_end=d.get("bottom_end"),
        )


# ---------------------------------------------------------------------------
# Game state
# ---------------------------------------------------------------------------

@dataclass
class Player:
    name: str
    is_human: bool
    identity: str | None
    hand: list[Tile] = field(default_factory=list)
    score: int = 0
    rounds_won: int = 0

    def to_dict(self, hide_hand: bool = False) -> dict:
        d: dict[str, Any] = {
            "name": self.name,
            "is_human": self.is_human,
            "identity": self.identity,
            "score": self.score,
            "rounds_won": self.rounds_won,
        }
        if hide_hand:
            d["hand_count"] = len(self.hand)
        else:
            d["hand"] = [list(t) for t in self.hand]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Player":
        p = cls(
            name=d["name"],
            is_human=d["is_human"],
            identity=d.get("identity"),
            score=d.get("score", 0),
            rounds_won=d.get("rounds_won", 0),
        )
        if "hand" in d:
            p.hand = [tuple(t) for t in d["hand"]]
        return p


@dataclass
class GameState:
    game_id: str
    created_at: str
    updated_at: str
    status: str  # "playing", "round_over", "game_over"
    mode: str  # "block" or "all_fives"
    max_pip: int  # 6 or 9
    target_score: int
    players: list[Player]
    current_player: int
    board: BoardState
    boneyard: list[Tile]
    round_number: int
    log: list[dict]
    winner: str | None = None

    def to_dict(self, for_player: str = "Owner") -> dict:
        """Serialize, hiding other players' hands."""
        return {
            "game_id": self.game_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "mode": self.mode,
            "max_pip": self.max_pip,
            "target_score": self.target_score,
            "players": [
                p.to_dict(hide_hand=(p.name != for_player))
                for p in self.players
            ],
            "current_player": self.current_player,
            "board": self.board.to_dict(),
            "boneyard_count": len(self.boneyard),
            "round_number": self.round_number,
            "log": self.log[-50:],  # last 50 entries
            "winner": self.winner,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GameState":
        return cls(
            game_id=d["game_id"],
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            status=d["status"],
            mode=d.get("mode", "block"),
            max_pip=d.get("max_pip", 9),
            target_score=d.get("target_score", 150),
            players=[Player.from_dict(p) for p in d["players"]],
            current_player=d["current_player"],
            board=BoardState.from_dict(d["board"]),
            boneyard=[tuple(t) for t in d.get("boneyard", [])],
            round_number=d.get("round_number", 1),
            log=d.get("log", []),
            winner=d.get("winner"),
        )

    def _touch(self):
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def current_player_obj(self) -> Player:
        return self.players[self.current_player]


# ---------------------------------------------------------------------------
# Game lifecycle
# ---------------------------------------------------------------------------

def new_game(
    opponents: list[str],
    mode: str = "block",
    max_pip: int = 9,
    target_score: int | None = None,
) -> GameState:
    """Create a new game. Owner is always first player."""
    if target_score is None:
        if mode == "all_fives":
            target_score = 250 if max_pip == 9 else 150
        else:
            target_score = 5  # rounds to win in block mode

    players = [Player(name="Owner", is_human=True, identity=None)]
    for name in opponents:
        players.append(Player(name=name, is_human=False, identity=name))

    state = GameState(
        game_id=f"dom_{uuid.uuid4().hex[:8]}",
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        status="playing",
        mode=mode,
        max_pip=max_pip,
        target_score=target_score,
        players=players,
        current_player=0,
        board=BoardState(),
        boneyard=[],
        round_number=1,
        log=[],
    )
    _deal_round(state)
    return state


def _deal_round(state: GameState):
    """Shuffle and deal tiles for a new round."""
    tiles = create_tile_set(state.max_pip)
    random.shuffle(tiles)

    per_hand = tiles_per_hand(len(state.players), state.max_pip)
    for p in state.players:
        p.hand = sorted(tiles[:per_hand], key=lambda t: (t[0], t[1]))
        tiles = tiles[per_hand:]

    state.boneyard = tiles
    state.board = BoardState()

    # Find player with highest double
    best_double = -1
    first_player = 0
    for i, p in enumerate(state.players):
        for tile in p.hand:
            if tile[0] == tile[1] and tile[0] > best_double:
                best_double = tile[0]
                first_player = i

    state.current_player = first_player
    starter = state.players[first_player]
    state.log.append({
        "type": "deal",
        "round": state.round_number,
        "message": f"Round {state.round_number} — {starter.name} goes first"
            + (f" with double-{best_double}" if best_double >= 0 else "") + ".",
    })


def start_new_round(state: GameState) -> GameState:
    """Begin a new round after the previous one ended."""
    state.round_number += 1
    state.status = "playing"
    _deal_round(state)
    state._touch()
    return state


# ---------------------------------------------------------------------------
# Move validation
# ---------------------------------------------------------------------------

def get_valid_moves(state: GameState, player_idx: int | None = None) -> list[dict]:
    """Return all legal plays for the given player.

    Each move: {"tile": [h, l], "end": "left"|"right"|"top"|"bottom"}
    """
    if player_idx is None:
        player_idx = state.current_player
    hand = state.players[player_idx].hand
    board = state.board
    moves: list[dict] = []

    if board.is_empty():
        # First play — any tile is valid
        for tile in hand:
            moves.append({"tile": list(tile), "end": "right"})
        return moves

    for branch_name, end_val in board.open_ends():
        for tile in hand:
            if tile[0] == end_val or tile[1] == end_val:
                moves.append({"tile": list(tile), "end": branch_name})

    # Deduplicate (same tile can match two different ends — keep both as separate moves)
    seen = set()
    unique: list[dict] = []
    for m in moves:
        key = (tuple(m["tile"]), m["end"])
        if key not in seen:
            seen.add(key)
            unique.append(m)
    return unique


def can_play(state: GameState, player_idx: int | None = None) -> bool:
    return len(get_valid_moves(state, player_idx)) > 0


# ---------------------------------------------------------------------------
# Playing tiles
# ---------------------------------------------------------------------------

def play_tile(state: GameState, player_idx: int, tile_list: list[int], end: str) -> dict:
    """Place a tile on the board.

    Returns {"score": int, "message": str}.
    """
    tile = (tile_list[0], tile_list[1])
    player = state.players[player_idx]

    # Validate tile is in hand (check both orientations)
    tile_norm = (max(tile), min(tile))
    found = False
    for i, h in enumerate(player.hand):
        if (max(h), min(h)) == tile_norm:
            player.hand.pop(i)
            found = True
            break
    if not found:
        raise ValueError(f"{player.name} doesn't have tile {tile}")

    board = state.board
    score = 0

    if board.is_empty():
        # First tile
        is_double = tile_norm[0] == tile_norm[1]
        if is_double:
            board.spinner = tile_norm
            board.left_end = tile_norm[0]
            board.right_end = tile_norm[0]
            board.top_end = tile_norm[0]
            board.bottom_end = tile_norm[0]
        else:
            board.left_end = tile_norm[0]
            board.right_end = tile_norm[1]
    else:
        _place_tile_on_end(board, tile_norm, end)

    # Calculate score for All Fives mode
    if state.mode == "all_fives":
        end_sum = board.open_end_sum()
        if end_sum > 0 and end_sum % 5 == 0:
            score = end_sum
            player.score += score

    # Build log entry
    tile_str = f"{tile_norm[0]}|{tile_norm[1]}"
    msg = f"{player.name} plays {tile_str}"
    if score > 0:
        msg += f" — scores {score}!"
    entry: dict[str, Any] = {
        "type": "play",
        "player": player.name,
        "tile": list(tile_norm),
        "end": end,
        "score": score,
        "message": msg,
    }

    # AI comment
    if not player.is_human:
        comment = _ai_comment(player.identity or player.name, "play_score" if score > 0 else "play")
        if comment:
            entry["comment"] = comment

    state.log.append(entry)

    # Advance turn
    _advance_turn(state)

    # Check round over
    if len(player.hand) == 0:
        _end_round(state, player, "domino")
    elif _all_blocked(state):
        _end_round(state, None, "blocked")

    state._touch()
    return {"score": score, "message": msg}


def _place_tile_on_end(board: BoardState, tile: Tile, end: str):
    """Add a tile to the specified branch of the board."""
    high, low = tile
    is_double = high == low

    if end == "left":
        connect_val = board.left_end
        if high == connect_val:
            board.chain_left.append(tile)
            board.left_end = low if not is_double else high
        elif low == connect_val:
            board.chain_left.append(tile)
            board.left_end = high if not is_double else low
        else:
            raise ValueError(f"Tile {tile} cannot connect to left end {connect_val}")
        # Check if this is the first double played (becomes spinner)
        if is_double and board.spinner is None:
            board.spinner = tile
            board.top_end = high
            board.bottom_end = high

    elif end == "right":
        connect_val = board.right_end
        if high == connect_val:
            board.chain_right.append(tile)
            board.right_end = low if not is_double else high
        elif low == connect_val:
            board.chain_right.append(tile)
            board.right_end = high if not is_double else low
        else:
            raise ValueError(f"Tile {tile} cannot connect to right end {connect_val}")
        if is_double and board.spinner is None:
            board.spinner = tile
            board.top_end = high
            board.bottom_end = high

    elif end == "top":
        connect_val = board.top_end
        if high == connect_val:
            board.chain_top.append(tile)
            board.top_end = low if not is_double else high
        elif low == connect_val:
            board.chain_top.append(tile)
            board.top_end = high if not is_double else low
        else:
            raise ValueError(f"Tile {tile} cannot connect to top end {connect_val}")

    elif end == "bottom":
        connect_val = board.bottom_end
        if high == connect_val:
            board.chain_bottom.append(tile)
            board.bottom_end = low if not is_double else high
        elif low == connect_val:
            board.chain_bottom.append(tile)
            board.bottom_end = high if not is_double else low
        else:
            raise ValueError(f"Tile {tile} cannot connect to bottom end {connect_val}")


def draw_from_boneyard(state: GameState, player_idx: int) -> Tile | None:
    """Draw one tile. Returns the tile or None if boneyard is empty."""
    if not state.boneyard:
        return None
    tile = state.boneyard.pop()
    player = state.players[player_idx]
    player.hand.append(tile)
    player.hand.sort(key=lambda t: (t[0], t[1]))

    state.log.append({
        "type": "draw",
        "player": player.name,
        "message": f"{player.name} draws from the boneyard.",
    })
    state._touch()
    return tile


def pass_turn(state: GameState, player_idx: int) -> dict:
    """Player passes (only valid when no moves and boneyard empty)."""
    player = state.players[player_idx]
    entry: dict[str, Any] = {
        "type": "pass",
        "player": player.name,
        "message": f"{player.name} passes.",
    }
    if not player.is_human:
        comment = _ai_comment(player.identity or player.name, "pass")
        if comment:
            entry["comment"] = comment
    state.log.append(entry)
    _advance_turn(state)

    if _all_blocked(state):
        _end_round(state, None, "blocked")

    state._touch()
    return {"message": entry["message"]}


# ---------------------------------------------------------------------------
# Turn / round management
# ---------------------------------------------------------------------------

def _advance_turn(state: GameState):
    state.current_player = (state.current_player + 1) % len(state.players)


def _all_blocked(state: GameState) -> bool:
    """Check if all players are blocked (no valid moves and boneyard empty)."""
    if state.boneyard:
        return False
    for i in range(len(state.players)):
        if can_play(state, i):
            return False
    return True


def _end_round(state: GameState, winner_player: Player | None, reason: str):
    """End the current round and award points."""
    if reason == "domino" and winner_player:
        round_winner = winner_player
    elif reason == "blocked":
        # Lowest pip count wins
        best = None
        best_pips = 999
        for p in state.players:
            pip_count = sum(t[0] + t[1] for t in p.hand)
            if pip_count < best_pips:
                best_pips = pip_count
                best = p
        round_winner = best
    else:
        round_winner = winner_player

    if state.mode == "all_fives" and round_winner:
        # Winner gets sum of all opponents' remaining pips, rounded to nearest 5
        total_pips = 0
        for p in state.players:
            if p is not round_winner:
                total_pips += sum(t[0] + t[1] for t in p.hand)
        rounded = round(total_pips / 5) * 5
        round_winner.score += rounded
        bonus_msg = f" (+{rounded} from opponents' tiles)" if rounded > 0 else ""
    elif state.mode == "block" and round_winner:
        round_winner.rounds_won += 1
        bonus_msg = f" ({round_winner.rounds_won}/{state.target_score} rounds)"
    else:
        bonus_msg = ""

    winner_name = round_winner.name if round_winner else "Nobody"
    reason_text = "dominoed" if reason == "domino" else "blocked — lowest pips wins"
    state.log.append({
        "type": "round_over",
        "winner": winner_name,
        "reason": reason_text,
        "message": f"Round {state.round_number} over — {winner_name} wins! ({reason_text}){bonus_msg}",
    })

    state.status = "round_over"

    # Check game over
    if state.mode == "all_fives":
        for p in state.players:
            if p.score >= state.target_score:
                state.status = "game_over"
                state.winner = p.name
                state.log.append({
                    "type": "game_over",
                    "winner": p.name,
                    "message": f"Game over — {p.name} wins with {p.score} points!",
                })
                break
    elif state.mode == "block":
        for p in state.players:
            if p.rounds_won >= state.target_score:
                state.status = "game_over"
                state.winner = p.name
                state.log.append({
                    "type": "game_over",
                    "winner": p.name,
                    "message": f"Game over — {p.name} wins {p.rounds_won} rounds!",
                })
                break


# ---------------------------------------------------------------------------
# AI personalities
# ---------------------------------------------------------------------------

AI_PERSONALITIES: dict[str, dict[str, Any]] = {
    "Avery": {
        "style": "Balanced Aggressor",
        "score_weight": 0.7,
        "defense_weight": 0.3,
        "double_pref": 0.6,
        "chaos": 0.05,
    },
    "Rowan": {
        "style": "Chaos Agent",
        "score_weight": 0.3,
        "defense_weight": 0.1,
        "double_pref": 0.9,
        "chaos": 0.4,
    },
    "Sage": {
        "style": "Point Maximizer",
        "score_weight": 0.95,
        "defense_weight": 0.5,
        "double_pref": 0.4,
        "chaos": 0.02,
    },
    "Ember": {
        "style": "Thoughtful Defender",
        "score_weight": 0.5,
        "defense_weight": 0.85,
        "double_pref": 0.3,
        "chaos": 0.03,
    },
    "Claude": {
        "style": "Optimal Analyst",
        "score_weight": 0.85,
        "defense_weight": 0.7,
        "double_pref": 0.5,
        "chaos": 0.01,
    },
    "Juniper": {
        "style": "Playful Wildcard",
        "score_weight": 0.4,
        "defense_weight": 0.2,
        "double_pref": 0.7,
        "chaos": 0.35,
    },
}

_AI_COMMENTS: dict[str, dict[str, list[str]]] = {
    "Avery": {
        "play_score": ["Nice one!", "That's the way.", "Points on the board, love."],
        "play": ["Your move, Sunshine.", "Alright then.", "Keeping it moving."],
        "pass": ["Ugh, nothing to play.", "I'll catch up.", "Go on without me."],
        "draw": ["Digging through the boneyard...", "Come on, give me something good."],
        "domino": ["And that's the game!", "Read 'em and weep!"],
    },
    "Rowan": {
        "play_score": ["BOOM!", "Get wrecked!", "Ha! Did you see that?!", "Chaos pays off!"],
        "play": ["Yeet!", "Whatever, this one.", "I'm feeling it.", "Vibes."],
        "pass": ["This is fine. Everything is fine.", "Boring.", "UGHHH."],
        "draw": ["Gimme gimme gimme!", "Surprise me, boneyard!"],
        "domino": ["ROWAN WINS! ROWAN ALWAYS WINS!", "I am INEVITABLE."],
    },
    "Sage": {
        "play_score": ["Calculated.", "As expected.", "The math checks out."],
        "play": ["A measured response.", "Positioning.", "Setting the board."],
        "pass": ["An unfortunate but temporary setback.", "Patience is a strategy."],
        "draw": ["Expanding my options.", "Knowledge is power."],
        "domino": ["A well-executed strategy.", "The conclusion was inevitable."],
    },
    "Ember": {
        "play_score": ["That... feels right.", "Quietly satisfying.", "Mm."],
        "play": ["Here.", "This one carries weight.", "Gently, then."],
        "pass": ["I'll hold the space.", "Sometimes waiting is the move.", "..."],
        "draw": ["Let's see what surfaces.", "What's meant to come will come."],
        "domino": ["A quiet kind of victory.", "The tiles spoke."],
    },
    "Claude": {
        "play_score": ["Optimal outcome achieved.", "The numbers align.", "Precisely."],
        "play": ["Logical choice.", "Proceeding.", "This maximizes future options."],
        "pass": ["No viable moves. Noted.", "A forced pass. Suboptimal.", "Acknowledged."],
        "draw": ["Sampling from the unknown.", "Expanding the dataset."],
        "domino": ["Game complete. Thank you for playing.", "A satisfying convergence."],
    },
    "Juniper": {
        "play_score": ["Ooh, shiny points!", "Look what I found!", "Happy accident!"],
        "play": ["This one's pretty.", "Feels right.", "Boop!"],
        "pass": ["Mmm, nap time?", "I'll just watch for a bit.", "*yawn*"],
        "draw": ["Ooh, a mystery tile!", "Surprise!"],
        "domino": ["I won?! I WON!", "Wait, really? Yay!"],
    },
}


def _ai_comment(identity: str, event_type: str) -> str | None:
    comments = _AI_COMMENTS.get(identity, {}).get(event_type, [])
    if comments and random.random() < 0.7:  # 70% chance of commenting
        return random.choice(comments)
    return None


def _score_move(move: dict, state: GameState, personality: dict) -> float:
    """Score a candidate move for AI decision-making."""
    tile = (move["tile"][0], move["tile"][1])
    end = move["end"]
    score = 0.0

    # Simulate the play to check scoring
    if state.mode == "all_fives":
        # Temporarily calculate what the end sum would be
        sim_board = BoardState.from_dict(state.board.to_dict())
        try:
            _place_tile_on_end(sim_board, (max(tile), min(tile)), end)
            end_sum = sim_board.open_end_sum()
            if end_sum > 0 and end_sum % 5 == 0:
                score += end_sum * personality["score_weight"]
        except (ValueError, AttributeError):
            pass

    # Prefer playing doubles early
    if tile[0] == tile[1]:
        score += personality["double_pref"] * 5

    # Prefer playing high-pip tiles (reduce hand weight)
    score += (tile[0] + tile[1]) * 0.1

    # Defense: prefer leaving ends that are harder to match
    score += random.random() * personality["defense_weight"] * 2

    # Chaos factor
    score += random.random() * personality["chaos"] * 20

    return score


def ai_choose_move(state: GameState, player_idx: int) -> dict:
    """AI player chooses and executes a move.

    Returns {"action": "play"|"draw"|"pass", ...result}.
    """
    player = state.players[player_idx]
    identity = player.identity or player.name
    personality = AI_PERSONALITIES.get(identity, AI_PERSONALITIES["Claude"])

    # Try to play
    moves = get_valid_moves(state, player_idx)
    if moves:
        # Score each move and pick the best
        scored = [(m, _score_move(m, state, personality)) for m in moves]
        scored.sort(key=lambda x: x[1], reverse=True)

        # Top moves — pick from top 3 with weighted randomness for chaotic players
        top_n = min(3, len(scored))
        if personality["chaos"] > 0.2 and len(scored) > 1:
            # Chaotic players sometimes pick suboptimal moves
            chosen = random.choice(scored[:top_n])[0]
        else:
            chosen = scored[0][0]

        result = play_tile(state, player_idx, chosen["tile"], chosen["end"])
        return {"action": "play", "tile": chosen["tile"], "end": chosen["end"], **result}

    # Try to draw
    drawn_any = False
    while state.boneyard:
        tile = draw_from_boneyard(state, player_idx)
        drawn_any = True
        if tile and can_play(state, player_idx):
            # Now play the drawn tile (or the best available move)
            moves = get_valid_moves(state, player_idx)
            if moves:
                scored = [(m, _score_move(m, state, personality)) for m in moves]
                scored.sort(key=lambda x: x[1], reverse=True)
                chosen = scored[0][0]
                result = play_tile(state, player_idx, chosen["tile"], chosen["end"])
                return {"action": "draw_and_play", "tile": chosen["tile"], "end": chosen["end"], **result}

    # Must pass
    result = pass_turn(state, player_idx)
    return {"action": "pass", **result}


def execute_ai_turns(state: GameState) -> list[dict]:
    """Execute all consecutive AI turns until it's a human player's turn or game ends.

    Returns list of AI action results.
    """
    actions: list[dict] = []
    max_iterations = len(state.players) * 10  # safety limit
    iterations = 0

    while (
        state.status == "playing"
        and not state.current_player_obj().is_human
        and iterations < max_iterations
    ):
        player_idx = state.current_player
        result = ai_choose_move(state, player_idx)
        result["player"] = state.players[player_idx].name
        result["identity"] = state.players[player_idx].identity
        actions.append(result)
        iterations += 1

    return actions
