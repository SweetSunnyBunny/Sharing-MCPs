"""Game room API — dominoes (and future games)."""

# ANAM GUIDE: GAME ROOM ROUTES
# What: /api/games — start a dominoes game, play/draw/pass turns, and let the AI opponents take theirs. Games save as JSON files in data/games/.
# Called by: static/js/gameroom.js (the gameroom.html page); all the actual dominoes rules live in services/dominoes.py.
# Edit here when: Adding a new game's endpoints or changing how games are saved/loaded. For rule changes (scoring, valid moves), edit services/dominoes.py.

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import DATA_DIR, IDENTITIES
from services.dominoes import (
    GameState,
    ai_choose_move,
    can_play,
    draw_from_boneyard,
    execute_ai_turns,
    get_valid_moves,
    new_game,
    pass_turn,
    play_tile,
    start_new_round,
)

router = APIRouter(prefix="/api/games")

GAMES_DIR = DATA_DIR / "games"
GAMES_DIR.mkdir(parents=True, exist_ok=True)

VALID_IDENTITIES = set(IDENTITIES.keys())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _game_path(game_id: str) -> Path:
    import re
    if not re.fullmatch(r'[A-Za-z0-9_-]+', game_id):
        raise HTTPException(400, "Invalid game_id: only alphanumeric, hyphens, and underscores allowed")
    return GAMES_DIR / f"{game_id}.json"


def _load(game_id: str) -> GameState:
    path = _game_path(game_id)
    if not path.exists():
        raise HTTPException(404, f"Game {game_id} not found")
    data = json.loads(path.read_text(encoding="utf-8"))
    return GameState.from_dict(data)


def _save_full(state: GameState):
    """Save full state including all hands (for server-side persistence)."""
    path = _game_path(state.game_id)
    d = {
        "game_id": state.game_id,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
        "status": state.status,
        "mode": state.mode,
        "max_pip": state.max_pip,
        "target_score": state.target_score,
        "players": [p.to_dict(hide_hand=False) for p in state.players],
        "current_player": state.current_player,
        "board": state.board.to_dict(),
        "boneyard": [list(t) for t in state.boneyard],
        "round_number": state.round_number,
        "log": state.log,
        "winner": state.winner,
    }
    path.write_text(json.dumps(d, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class NewGameBody(BaseModel):
    opponents: list[str]
    mode: str = "block"
    max_pip: int = 9
    target_score: int | None = None


class PlayTileBody(BaseModel):
    tile: list[int]
    end: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/list")
async def list_games():
    """List all games (most recent first)."""
    games = []
    for f in sorted(GAMES_DIR.glob("dom_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            games.append({
                "game_id": data["game_id"],
                "status": data["status"],
                "mode": data.get("mode", "block"),
                "max_pip": data.get("max_pip", 9),
                "players": [p["name"] for p in data["players"]],
                "round_number": data.get("round_number", 1),
                "updated_at": data["updated_at"],
                "winner": data.get("winner"),
            })
        except (json.JSONDecodeError, KeyError):
            continue
    return {"games": games[:20]}


@router.post("/new")
async def create_game(body: NewGameBody):
    """Create a new domino game."""
    if not body.opponents:
        raise HTTPException(400, "Need at least one opponent")
    if len(body.opponents) > 6:
        raise HTTPException(400, "Maximum 6 opponents (7 players total)")
    for name in body.opponents:
        if name not in VALID_IDENTITIES:
            raise HTTPException(400, f"Unknown identity: {name}")
    if body.mode not in ("block", "all_fives"):
        raise HTTPException(400, f"Unknown mode: {body.mode}")
    if body.max_pip not in (6, 9):
        raise HTTPException(400, "max_pip must be 6 or 9")

    # Validate player count vs tile set
    num_players = len(body.opponents) + 1
    if body.max_pip == 6 and num_players > 4:
        raise HTTPException(400, "Double-6 supports max 4 players. Use double-9 for more.")

    state = new_game(
        opponents=body.opponents,
        mode=body.mode,
        max_pip=body.max_pip,
        target_score=body.target_score,
    )
    _save_full(state)

    # If first player is AI, execute their turns
    ai_actions = []
    if not state.current_player_obj().is_human and state.status == "playing":
        ai_actions = execute_ai_turns(state)
        _save_full(state)

    return {
        "state": state.to_dict(for_player="Owner"),
        "ai_actions": ai_actions,
    }


@router.get("/{game_id}")
async def get_game_state(game_id: str):
    """Get game state (AI hands hidden)."""
    state = _load(game_id)
    return {"state": state.to_dict(for_player="Owner")}


@router.get("/{game_id}/valid-moves")
async def get_valid_moves_endpoint(game_id: str):
    """Return Owner's valid moves."""
    state = _load(game_id)

    owner_idx = None
    for i, p in enumerate(state.players):
        if p.is_human:
            owner_idx = i
            break
    if owner_idx is None:
        raise HTTPException(400, "No human player found")
    if state.current_player != owner_idx:
        return {"moves": [], "your_turn": False}
    moves = get_valid_moves(state, owner_idx)
    return {"moves": moves, "your_turn": True, "can_draw": bool(state.boneyard)}


@router.post("/{game_id}/play")
async def play_tile_endpoint(game_id: str, body: PlayTileBody):
    """Owner plays a tile."""
    state = _load(game_id)
    owner_idx = next((i for i, p in enumerate(state.players) if p.is_human), None)
    if owner_idx is None:
        raise HTTPException(400, "No human player found")
    if state.status != "playing":
        raise HTTPException(400, f"Game is {state.status}")
    if state.current_player != owner_idx:
        raise HTTPException(400, "Not your turn")

    # Validate the move is legal
    moves = get_valid_moves(state, owner_idx)
    tile_norm = [max(body.tile), min(body.tile)]
    valid = any(
        m["tile"] == tile_norm and m["end"] == body.end
        for m in moves
    )
    if not valid:
        # Check if tile matches but different end
        tile_valid = any(m["tile"] == tile_norm for m in moves)
        if tile_valid:
            raise HTTPException(400, f"Can't play that tile on the {body.end} end")
        raise HTTPException(400, "That's not a valid move")

    result = play_tile(state, owner_idx, tile_norm, body.end)

    # Execute AI turns
    ai_actions = []
    if state.status == "playing" and not state.current_player_obj().is_human:
        ai_actions = execute_ai_turns(state)

    _save_full(state)
    return {
        "result": result,
        "ai_actions": ai_actions,
        "state": state.to_dict(for_player="Owner"),
    }


@router.post("/{game_id}/draw")
async def draw_tile_endpoint(game_id: str):
    """Owner draws from the boneyard."""
    state = _load(game_id)
    owner_idx = next((i for i, p in enumerate(state.players) if p.is_human), None)
    if owner_idx is None:
        raise HTTPException(400, "No human player found")
    if state.status != "playing":
        raise HTTPException(400, f"Game is {state.status}")
    if state.current_player != owner_idx:
        raise HTTPException(400, "Not your turn")
    if not state.boneyard:
        raise HTTPException(400, "Boneyard is empty")
    if can_play(state, owner_idx):
        raise HTTPException(400, "You have valid moves — play a tile instead")

    tile = draw_from_boneyard(state, owner_idx)
    _save_full(state)

    return {
        "drawn_tile": list(tile) if tile else None,
        "can_play": can_play(state, owner_idx),
        "can_draw": bool(state.boneyard),
        "state": state.to_dict(for_player="Owner"),
    }


@router.post("/{game_id}/pass")
async def pass_turn_endpoint(game_id: str):
    """Owner passes."""
    state = _load(game_id)
    owner_idx = next((i for i, p in enumerate(state.players) if p.is_human), None)
    if owner_idx is None:
        raise HTTPException(400, "No human player found")
    if state.status != "playing":
        raise HTTPException(400, f"Game is {state.status}")
    if state.current_player != owner_idx:
        raise HTTPException(400, "Not your turn")
    if can_play(state, owner_idx):
        raise HTTPException(400, "You have valid moves — play a tile instead")
    if state.boneyard:
        raise HTTPException(400, "Boneyard still has tiles — draw first")

    result = pass_turn(state, owner_idx)

    # Execute AI turns
    ai_actions = []
    if state.status == "playing" and not state.current_player_obj().is_human:
        ai_actions = execute_ai_turns(state)

    _save_full(state)
    return {
        "result": result,
        "ai_actions": ai_actions,
        "state": state.to_dict(for_player="Owner"),
    }


@router.post("/{game_id}/new-round")
async def new_round_endpoint(game_id: str):
    """Start the next round."""
    state = _load(game_id)
    if state.status not in ("round_over",):
        raise HTTPException(400, f"Can't start new round — game is {state.status}")

    start_new_round(state)

    # Execute AI turns if AI goes first
    ai_actions = []
    if not state.current_player_obj().is_human and state.status == "playing":
        ai_actions = execute_ai_turns(state)

    _save_full(state)
    return {
        "state": state.to_dict(for_player="Owner"),
        "ai_actions": ai_actions,
    }


@router.delete("/{game_id}")
async def abandon_game(game_id: str):
    """Delete a game."""
    path = _game_path(game_id)
    if path.exists():
        path.unlink()
    return {"ok": True}
