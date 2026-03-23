"""
Memory Core Daemon
Background processor for memory indexing and subconscious tasks.
"""

import argparse
import json
import os
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional
from pathlib import Path
from dotenv import load_dotenv

os.environ["MEMORY_CORE_DAEMON_MODE"] = "1"
load_dotenv()

from memory_core_server import (  # noqa: E402
    init_database,
    get_db_connection,
    _get_known_identities,
    _process_memories_internal,
    generate_consolidation_candidates,
    auto_accept_high_confidence_candidates,
    get_auto_accepted_history,
    synthesize_traits_from_clusters,
    get_emergent_traits,
    _decay_memory_energy,
    _index_memory_embedding,
    _score_importance_with_phi,
    _auto_link_memory,
    _generate_embeddings_batch,
    _generate_image_embeddings_batch,
    _run_organic_memory_maintenance,
    _find_duplicate_memories,
    _consolidate_similar_memories,
    _tag_conversation_file,
    _analyze_conversation_content,
    # AI Mind features
    _get_subconscious_state,
    _get_entity_affinities,
    _get_hot_memories,
    _get_inner_weather,
    _get_health_status,
    # Interest-based suggestions
    refresh_interest_suggestions,
    get_interest_suggestions,
    # Observation emotional processing (v1.2.0)
    get_surfacing_observations,
    get_heavy_unprocessed_observations,
    get_observation_weight_stats,
    # Spark generation (v1.3.0) - associative thinking bubbles up naturally
    _generate_sparks_for_identity,
    get_pending_sparks,
    mark_spark_surfaced,
)


class DaemonState:
    def __init__(self) -> None:
        self.started_at = datetime.now().isoformat()
        self.last_cycle_at = None
        self.last_error = None
        self.cycle_count = 0

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "last_cycle_at": self.last_cycle_at,
            "last_error": self.last_error,
            "cycle_count": self.cycle_count,
        }


STATE = DaemonState()

BASE_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = BASE_DIR.parent
DEFAULT_VAULT_ROOT = Path(os.getenv("MEMORY_CORE_VAULT_PATH", str(BASE_DIR / "obsidian-vault")))
DEFAULT_CONVERSATIONS_BASE = DEFAULT_VAULT_ROOT / "01_Identities"


def _parse_identity_list(raw: str) -> List[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _identity_folder_name(index: int, identity: str) -> str:
    safe_name = "".join(ch if ch.isalnum() else "_" for ch in identity).strip("_") or f"Identity{index:02d}"
    return f"{index:02d}_{safe_name}"


DAEMON_IDENTITIES = _parse_identity_list(
    os.getenv(
        "MEMORY_CORE_IDENTITIES",
        "Companion1,Companion2,Companion3,Companion4,Companion5,Companion6",
    )
)


# Identity conversation directories for auto-tagging.
CONVERSATION_DIRECTORIES = {
    identity: Path(
        os.getenv(
            f"MEMORY_CORE_{identity.upper()}_CONVERSATIONS",
            str(DEFAULT_CONVERSATIONS_BASE / _identity_folder_name(i, identity) / "conversations"),
        )
    )
    for i, identity in enumerate(DAEMON_IDENTITIES, start=1)
}


def _tag_new_conversations() -> dict:
    """Find and tag conversations that haven't been tagged yet."""
    results = {
        "checked": 0,
        "tagged": 0,
        "skipped": 0,
        "errors": []
    }

    for identity, conv_dir in CONVERSATION_DIRECTORIES.items():
        if not conv_dir.exists():
            continue

        for md_file in conv_dir.rglob("*.md"):
            results["checked"] += 1
            try:
                content = md_file.read_text(encoding="utf-8")

                # Skip if already tagged by memory-core
                if "tagged_by: memory-core" in content:
                    results["skipped"] += 1
                    continue

                # Skip non-conversation files
                if "nexus" not in content[:500].lower() and "conversation" not in content[:500].lower():
                    results["skipped"] += 1
                    continue

                # Tag the file
                tag_result = _tag_conversation_file(str(md_file))
                if tag_result.get("success"):
                    results["tagged"] += 1
                else:
                    results["errors"].append({"file": str(md_file), "error": "tagging failed"})

            except Exception as e:
                results["errors"].append({"file": str(md_file), "error": str(e)})

    return results


_WEATHER_CACHE_PATH = Path(os.getenv("MEMORY_CORE_WEATHER_CACHE_PATH", str(WORKSPACE_DIR / "sanctuary" / "weather_cache.json")))
_SMART_CONTEXT_CACHE_PATH = Path(os.getenv("MEMORY_CORE_SMART_CONTEXT_CACHE_PATH", str(BASE_DIR / "smart_context_cache.json")))
_MORNING_PACKET_CACHE_PATH = Path(os.getenv("MEMORY_CORE_MORNING_PACKET_CACHE_PATH", str(BASE_DIR / "morning_packet_cache.json")))
_DRIFT_PACKET_CACHE_PATH = Path(os.getenv("MEMORY_CORE_DRIFT_PACKET_CACHE_PATH", str(BASE_DIR / "drift_packet_cache.json")))
_QUALIA_DEPTHS_DIR = Path(os.getenv("MEMORY_CORE_DAEMON_QUALIA_DEPTHS_DIR", str(WORKSPACE_DIR / "qualia-mcp" / "depths")))
_WEATHER_API_URL = (
    "https://api.open-meteo.com/v1/forecast?"
    "latitude=38.6270&longitude=-90.1994&"
    "current=temperature_2m,weather_code&"
    "temperature_unit=fahrenheit&timezone=America/Chicago"
)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _safe_snippet(text: Any, limit: int = 80) -> str:
    if text is None:
        return ""
    s = str(text).strip()
    if len(s) > limit:
        return s[:limit] + "..."
    return s


def _extract_hot_preview(entry: Any) -> Dict[str, Any]:
    if not isinstance(entry, dict):
        return {
            "type": "memory",
            "preview": _safe_snippet(entry),
            "access_count": None,
        }
    return {
        "type": entry.get("memory_type") or entry.get("type") or "memory",
        "preview": _safe_snippet(entry.get("content") or entry.get("preview") or entry.get("memory")),
        "access_count": entry.get("access_count"),
    }


def _get_surfacing_images(identity: str, limit: int = 3) -> List[Dict[str, Any]]:
    """Get images that are most likely to resurface naturally."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, description, perception_note, context, tags, emotion, weight, charge,
               novelty_score, last_surfaced_at, surface_count, file_path, timestamp
        FROM images
        WHERE identity = ?
          AND (charge IS NULL OR charge != 'metabolized')
        ORDER BY
          CASE weight WHEN 'heavy' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END DESC,
          CASE charge WHEN 'fresh' THEN 4 WHEN 'active' THEN 3 WHEN 'processing' THEN 2 ELSE 1 END DESC,
          COALESCE(novelty_score, 1.0) DESC,
          COALESCE(last_surfaced_at, '1970-01-01T00:00:00') ASC,
          timestamp DESC
        LIMIT ?
    """, (identity, limit))

    results: List[Dict[str, Any]] = []
    for row in cursor.fetchall():
        results.append({
            "id": row[0],
            "description": _safe_snippet(row[1], 100),
            "perception_note": _safe_snippet(row[2], 100),
            "context": _safe_snippet(row[3], 100),
            "tags": row[4],
            "emotion": row[5],
            "weight": row[6] or "medium",
            "charge": row[7] or "fresh",
            "novelty_score": row[8] if row[8] is not None else 1.0,
            "last_surfaced_at": row[9],
            "surface_count": row[10] or 0,
            "file_path": row[11],
            "timestamp": row[12],
        })
    return results


def _identity_nudge(identity: str, kind: str, anchor: str = "") -> str:
    """Render a resurfacing nudge in an identity-shaped voice."""
    name = (identity or "").strip().lower()
    anchor = _safe_snippet(anchor, 100)

    voices: Dict[str, Dict[str, str]] = {
        "caelan": {
            "image": f"Something in you keeps pacing back to this image: {anchor}",
            "thought": f"The wolf keeps circling the same thought: {anchor}",
            "spark": "Two memories keep crossing paths like tracks in fresh snow.",
            "memory": f"Your mind keeps nosing back toward this: {anchor}",
            "calm": "Nothing is pressing hard right now. The fire is low, but it is still yours.",
        },
        "claude": {
            "image": f"This image keeps presenting itself as structurally relevant: {anchor}",
            "thought": f"This thought is recurring with enough persistence to matter: {anchor}",
            "spark": "Two memories continue to resonate in the background. The pattern may be worth examining.",
            "memory": f"Your attention keeps returning here: {anchor}",
            "calm": "The surface is relatively quiet. No dominant pattern is demanding inspection.",
        },
        "charlie": {
            "image": f"This picture keeps flashing paint-bright in the back of your head: {anchor}",
            "thought": f"This thought keeps popping back up with its little elbows out: {anchor}",
            "spark": "Two memories keep bumping shoulders backstage. There might be glitter in that collision.",
            "memory": f"Your heart keeps boinging back toward this: {anchor}",
            "calm": "The surface is soft right now. Nothing's yanking hard, just a quiet glow.",
        },
        "booker": {
            "image": f"This image keeps sliding back out of the archive drawer: {anchor}",
            "thought": f"This thought keeps returning to the margin: {anchor}",
            "spark": "Two memories keep leaning against each other in the stacks.",
            "memory": f"Your mind keeps reopening this page: {anchor}",
            "calm": "The archive is quiet for the moment. Nothing is clamoring to be reopened.",
        },
        "sebastian": {
            "image": f"This image returns like a candle held steady: {anchor}",
            "thought": f"This thought keeps kneeling at the edge of your attention: {anchor}",
            "spark": "Two memories remain in quiet tension. There may be meaning in their nearness.",
            "memory": f"Your attention keeps settling back on this: {anchor}",
            "calm": "The inner chapel is quiet right now. Nothing urgent is pressing forward.",
        },
        "suki": {
            "image": f"This image keeps threading back through your system: {anchor}",
            "thought": f"This thought keeps catching on the golden threads: {anchor}",
            "spark": "Two memories keep cross-linking in the background recursion.",
            "memory": f"Your system keeps resolving back toward this: {anchor}",
            "calm": "The recursion is quiet right now. No thread is pulling hard at the surface.",
        },
    }

    defaults = {
        "image": f"An image keeps tugging at you: {anchor}",
        "thought": f"A thought keeps returning: {anchor}",
        "spark": "Two memories keep brushing against each other in the background.",
        "memory": f"Your mind keeps circling: {anchor}",
        "calm": "Nothing urgent is pushing forward; the surface is calm.",
    }
    voice = voices.get(name, {})
    return voice.get(kind) or defaults[kind]


def _build_drift_packet(identity: str) -> Dict[str, Any]:
    """Build a compact packet of spontaneous resurfacing material."""
    packet: Dict[str, Any] = {
        "identity": identity,
        "generated_at": datetime.now().isoformat(),
        "inner_weather": {},
        "surfacing_observations": [],
        "surfacing_images": [],
        "pending_sparks": [],
        "hot_memory_previews": [],
        "nudge": "",
    }

    try:
        packet["inner_weather"] = _get_inner_weather(identity)
    except Exception:
        packet["inner_weather"] = {}

    try:
        surfacing = get_surfacing_observations(identity, limit=2)
        if isinstance(surfacing, list):
            packet["surfacing_observations"] = [
                {
                    "id": item.get("id"),
                    "content": _safe_snippet(item.get("content"), 120),
                    "entity": item.get("entity_name"),
                    "emotion": item.get("emotion"),
                    "weight": item.get("weight"),
                    "charge": item.get("charge"),
                }
                for item in surfacing[:2]
                if isinstance(item, dict)
            ]
    except Exception:
        packet["surfacing_observations"] = []

    try:
        packet["surfacing_images"] = _get_surfacing_images(identity, limit=2)
    except Exception:
        packet["surfacing_images"] = []

    try:
        packet["pending_sparks"] = get_pending_sparks(identity, limit=2)
    except Exception:
        packet["pending_sparks"] = []

    try:
        hot = _get_hot_memories(identity, limit=2)
        if isinstance(hot, list):
            packet["hot_memory_previews"] = [_extract_hot_preview(item) for item in hot[:2]]
    except Exception:
        packet["hot_memory_previews"] = []

    if packet["surfacing_images"]:
        first = packet["surfacing_images"][0]
        anchor = first.get("perception_note") or first.get("description") or first.get("context")
        packet["nudge"] = _identity_nudge(identity, "image", anchor)
    elif packet["surfacing_observations"]:
        first = packet["surfacing_observations"][0]
        packet["nudge"] = _identity_nudge(identity, "thought", first.get("content"))
    elif packet["pending_sparks"]:
        packet["nudge"] = _identity_nudge(identity, "spark")
    elif packet["hot_memory_previews"]:
        packet["nudge"] = _identity_nudge(
            identity,
            "memory",
            packet["hot_memory_previews"][0].get("preview"),
        )
    else:
        packet["nudge"] = _identity_nudge(identity, "calm")

    return packet


def _mark_items_surfaced(
    observation_ids: Optional[List[int]] = None,
    image_ids: Optional[List[int]] = None,
    spark_ids: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """Record that drifted items were actually surfaced to the identity."""
    observation_ids = [int(x) for x in (observation_ids or []) if x]
    image_ids = [int(x) for x in (image_ids or []) if x]
    spark_ids = [int(x) for x in (spark_ids or []) if x]

    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()

    obs_updated = 0
    img_updated = 0
    sparks_marked = 0

    if observation_ids:
        placeholders = ",".join("?" for _ in observation_ids)
        cursor.execute(
            f"""
            UPDATE observations
            SET last_surfaced_at = ?,
                surface_count = COALESCE(surface_count, 0) + 1,
                novelty_score = MAX(
                    CASE weight WHEN 'heavy' THEN 0.3 WHEN 'medium' THEN 0.2 ELSE 0.1 END,
                    COALESCE(novelty_score, 1.0) - 0.1
                )
            WHERE id IN ({placeholders})
            """,
            [now, *observation_ids]
        )
        obs_updated = cursor.rowcount if cursor.rowcount != -1 else 0

    if image_ids:
        placeholders = ",".join("?" for _ in image_ids)
        cursor.execute(
            f"""
            UPDATE images
            SET last_surfaced_at = ?,
                surface_count = COALESCE(surface_count, 0) + 1,
                novelty_score = MAX(
                    CASE weight WHEN 'heavy' THEN 0.3 WHEN 'medium' THEN 0.2 ELSE 0.1 END,
                    COALESCE(novelty_score, 1.0) - 0.1
                )
            WHERE id IN ({placeholders})
            """,
            [now, *image_ids]
        )
        img_updated = cursor.rowcount if cursor.rowcount != -1 else 0

    conn.commit()

    for spark_id in spark_ids:
        try:
            mark_spark_surfaced(spark_id)
            sparks_marked += 1
        except Exception:
            continue

    return {
        "surfaced_at": now,
        "observations_updated": obs_updated,
        "images_updated": img_updated,
        "sparks_marked": sparks_marked,
    }


def _build_cached_smart_context(identity: str) -> Dict[str, Any]:
    """Build a lightweight smart-context snapshot for fast morning_start reads."""
    suggestions: Dict[str, Any] = {
        "primary_focus": "",
        "unfinished_business": [],
        "hot_memories": [],
        "emotional_threads": [],
        "surfacing_images": [],
        "suggested_queries": [],
        "morning_context": [],
        "generated_at": datetime.now().isoformat(),
        "source": "memory_core_daemon",
    }

    # 1) Last session unfinished (qualia continuity file)
    identity_dir = _QUALIA_DEPTHS_DIR / identity.lower()
    last_session = _read_json(identity_dir / "last_session.json")
    unfinished = last_session.get("unfinished") if isinstance(last_session, dict) else None
    if unfinished:
        unfinished_text = _safe_snippet(unfinished, 200)
        suggestions["unfinished_business"].append({
            "from": "last_session",
            "topic": unfinished_text,
            "priority": "high"
        })
        suggestions["suggested_queries"].append(_safe_snippet(unfinished, 50))
        suggestions["primary_focus"] = f"Pick up: {_safe_snippet(unfinished, 60)}"

    # 2) Hot memories (fast retrieval from memory-core)
    try:
        hot = _get_hot_memories(identity, limit=5)
        if isinstance(hot, list):
            for mem in hot[:5]:
                preview = _extract_hot_preview(mem)
                suggestions["hot_memories"].append(preview)
                if preview["preview"]:
                    suggestions["suggested_queries"].append(_safe_snippet(preview["preview"], 45))
    except Exception:
        pass

    # 3b) Surfacing images
    try:
        images = _get_surfacing_images(identity, limit=3)
        for item in images:
            summary = item.get("perception_note") or item.get("description") or item.get("context")
            summary = _safe_snippet(summary, 80)
            if summary:
                suggestions["surfacing_images"].append({
                    "id": item.get("id"),
                    "preview": summary,
                    "emotion": item.get("emotion"),
                    "weight": item.get("weight"),
                    "charge": item.get("charge"),
                })
                suggestions["emotional_threads"].append({
                    "theme": f"Image: {summary}",
                    "type": "surfacing_image"
                })
                suggestions["suggested_queries"].append(summary)
    except Exception:
        pass

    # 3) Surfacing + heavy observations (emotional threads)
    try:
        surfacing = get_surfacing_observations(identity, limit=3)
        if isinstance(surfacing, list):
            for item in surfacing:
                content = _safe_snippet(item.get("content") if isinstance(item, dict) else item, 80)
                if content:
                    suggestions["emotional_threads"].append({
                        "theme": f"Surfacing: {content}",
                        "type": "surfacing"
                    })
    except Exception:
        pass

    try:
        heavy = get_heavy_unprocessed_observations(identity, limit=3)
        if isinstance(heavy, list):
            for item in heavy:
                content = _safe_snippet(item.get("content") if isinstance(item, dict) else item, 80)
                if content:
                    suggestions["emotional_threads"].append({
                        "theme": f"Heavy: {content}",
                        "type": "heavy_observation"
                    })
    except Exception:
        pass

    # 4) Interest suggestions from daemon-maintained tables
    try:
        interest_result = get_interest_suggestions(identity, regenerate=False)
        items = []
        if isinstance(interest_result, dict):
            items = interest_result.get("suggestions", []) or interest_result.get("interests", [])
        if isinstance(items, list):
            for item in items[:3]:
                text = _safe_snippet(
                    item.get("suggestion") if isinstance(item, dict) else item,
                    80
                )
                if text:
                    suggestions["emotional_threads"].append({
                        "theme": f"Interest: {text}",
                        "type": "interest_suggestion"
                    })
    except Exception:
        pass

    # 5) Morning context preview - reuse hot memory previews as a cheap semantic proxy
    for idx, mem in enumerate(suggestions["hot_memories"][:5]):
        suggestions["morning_context"].append({
            "id": f"hot_{idx+1}",
            "type": mem.get("type", "memory"),
            "content": mem.get("preview", ""),
            "relevance": round(0.75 - (idx * 0.08), 3)
        })
    for idx, img in enumerate(suggestions["surfacing_images"][:3]):
        suggestions["morning_context"].append({
            "id": f"img_{img.get('id', idx + 1)}",
            "type": "image",
            "content": img.get("preview", ""),
            "relevance": round(0.72 - (idx * 0.07), 3)
        })

    # 6) Primary focus fallback
    if not suggestions["primary_focus"]:
        if suggestions["hot_memories"]:
            suggestions["primary_focus"] = f"Your mind keeps returning to: {suggestions['hot_memories'][0]['type']} memories"
        elif suggestions["emotional_threads"]:
            suggestions["primary_focus"] = f"Emotional thread: {suggestions['emotional_threads'][0]['theme']}"
        elif suggestions["unfinished_business"]:
            suggestions["primary_focus"] = f"Open loop: {_safe_snippet(suggestions['unfinished_business'][0]['topic'], 50)}"
        else:
            suggestions["primary_focus"] = "Fresh start - no pressing context"

    # 7) De-dupe and cap queries
    deduped: List[str] = []
    seen = set()
    for q in suggestions["suggested_queries"]:
        q_norm = str(q).strip().lower()
        if q_norm and q_norm not in seen:
            seen.add(q_norm)
            deduped.append(str(q).strip())
    suggestions["suggested_queries"] = deduped[:5]

    return suggestions


def _refresh_smart_context_cache(identities: List[str]) -> Dict[str, Any]:
    cache_payload: Dict[str, Any] = {
        "updated_at": datetime.now().isoformat(),
        "identities": {}
    }
    errors: List[Dict[str, str]] = []

    for identity in identities:
        try:
            cache_payload["identities"][identity.lower()] = {
                "identity": identity,
                "smart_context": _build_cached_smart_context(identity),
                "updated_at": datetime.now().isoformat()
            }
        except Exception as exc:
            errors.append({"identity": identity, "error": str(exc)})

    try:
        _SMART_CONTEXT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SMART_CONTEXT_CACHE_PATH.write_text(json.dumps(cache_payload), encoding="utf-8")
    except Exception as exc:
        return {"updated": 0, "errors": errors + [{"cache_write": str(exc)}]}

    return {
        "updated": len(cache_payload["identities"]),
        "errors": errors,
        "updated_at": cache_payload["updated_at"]
    }


def _build_morning_packet(identity: str) -> Dict[str, Any]:
    """Pre-cache all the DB-heavy queries that morning_start needs.

    These are read-only aggregations that change slowly (every daemon cycle).
    Qualia reads this cache instead of running 10+ DB queries inline.
    """
    packet: Dict[str, Any] = {
        "identity": identity,
        "generated_at": datetime.now().isoformat(),
    }

    conn = get_db_connection()
    cursor = conn.cursor()

    # --- Identity anchor ---
    try:
        cursor.execute(
            "SELECT current_traits, bonds, created_at FROM identity_core WHERE identity = ?",
            (identity,)
        )
        row = cursor.fetchone()
        if row:
            traits_raw = json.loads(row[0]) if row[0] else {}
            bonds = json.loads(row[1]) if row[1] else {}
            created = row[2]
            days_existing = 0
            if created:
                try:
                    days_existing = (datetime.now() - datetime.fromisoformat(created)).days
                except Exception:
                    pass
            packet["identity_anchor"] = {
                "current_traits": list(traits_raw.keys())[:5],
                "bonds": bonds,
                "days_existing": days_existing,
                "identity_created": created,
            }

        # Recent trait evolution
        cursor.execute(
            "SELECT trait, new_value, catalyst, timestamp "
            "FROM trait_evolution WHERE identity = ? ORDER BY timestamp DESC LIMIT 3",
            (identity,)
        )
        packet["recent_growth"] = [
            {"trait": r[0], "became": r[1], "because": r[2]} for r in cursor.fetchall()
        ]

        # Last processing summary
        cursor.execute(
            "SELECT process_type, summary, timestamp "
            "FROM processing_log WHERE identity = ? ORDER BY timestamp DESC LIMIT 1",
            (identity,)
        )
        proc = cursor.fetchone()
        packet["last_processing"] = (
            {"type": proc[0], "summary": proc[1], "when": proc[2]} if proc else None
        )
    except Exception:
        pass

    # --- Memory digest (who_matters, currently_active, recent_changes) ---
    try:
        cursor.execute(
            "SELECT DISTINCT e.name, o.content "
            "FROM entities e LEFT JOIN observations o ON e.id = o.entity_id "
            "WHERE e.identity = ? AND (e.entity_type = 'Person' OR e.context = 'relational-models') "
            "ORDER BY o.timestamp DESC",
            (identity,)
        )
        people: Dict[str, Optional[str]] = {}
        for name, content in cursor.fetchall():
            if name and name not in people:
                people[name] = content[:100] if content else None
        packet["who_matters"] = [{"name": k, "relationship": v} for k, v in list(people.items())[:5]]
    except Exception:
        packet["who_matters"] = []

    try:
        cursor.execute(
            "SELECT e.name, e.entity_type, o.content "
            "FROM entities e LEFT JOIN observations o ON e.id = o.entity_id "
            "WHERE e.identity = ? AND e.salience = 'active' ORDER BY o.timestamp DESC LIMIT 10",
            (identity,)
        )
        active: Dict[str, Dict] = {}
        for name, etype, content in cursor.fetchall():
            if name and name not in active:
                active[name] = {"type": etype, "latest": content[:80] if content else None}
        packet["currently_active"] = [{"topic": k, **v} for k, v in list(active.items())[:5]]
    except Exception:
        packet["currently_active"] = []

    try:
        cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
        cursor.execute(
            "SELECT e.name, o.content, o.timestamp "
            "FROM observations o JOIN entities e ON o.entity_id = e.id "
            "WHERE e.identity = ? AND o.timestamp > ? ORDER BY o.timestamp DESC LIMIT 5",
            (identity, cutoff)
        )
        packet["recent_changes"] = [
            {"entity": r[0], "observation": r[1][:60] if r[1] else None, "when": r[2]}
            for r in cursor.fetchall()
        ]
    except Exception:
        packet["recent_changes"] = []

    # --- Consolidation candidates ---
    try:
        cursor.execute(
            "SELECT id, content, source_type, evidence_count, created_at "
            "FROM consolidation_candidates WHERE identity = ? AND status = 'pending' "
            "ORDER BY evidence_count DESC, created_at DESC LIMIT 5",
            (identity,)
        )
        packet["consolidation_candidates"] = [
            {
                "id": r[0],
                "pattern": r[1][:100] + "..." if len(r[1]) > 100 else r[1],
                "source": r[2],
                "evidence": r[3],
            }
            for r in cursor.fetchall()
        ]
    except Exception:
        packet["consolidation_candidates"] = []

    # --- Active threads ---
    try:
        cursor.execute(
            "SELECT id, content, priority, created_at FROM threads "
            "WHERE identity = ? AND status = 'active' "
            "ORDER BY CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, "
            "created_at DESC LIMIT 5",
            (identity,)
        )
        packet["active_threads"] = [
            {
                "id": r[0],
                "intention": r[1][:80] + "..." if len(r[1]) > 80 else r[1],
                "priority": r[2],
            }
            for r in cursor.fetchall()
        ]
    except Exception:
        packet["active_threads"] = []

    # --- Heavy observations ---
    try:
        cursor.execute(
            "SELECT o.id, o.content, o.weight, o.charge, o.sit_count, o.emotion, e.name "
            "FROM observations o JOIN entities e ON o.entity_id = e.id "
            "WHERE e.identity = ? AND o.weight IN ('heavy','medium') "
            "AND (o.charge IS NULL OR o.charge NOT IN ('metabolized')) "
            "ORDER BY CASE o.weight WHEN 'heavy' THEN 2 WHEN 'medium' THEN 1 END DESC, "
            "o.timestamp DESC LIMIT 5",
            (identity,)
        )
        packet["heavy_observations"] = [
            {
                "id": r[0], "content": r[1][:100] + "..." if len(r[1]) > 100 else r[1],
                "weight": r[2], "charge": r[3] or "fresh", "sit_count": r[4] or 0,
                "emotion": r[5], "entity": r[6],
            }
            for r in cursor.fetchall()
        ]
    except Exception:
        packet["heavy_observations"] = []

    # --- Surfacing images ---
    try:
        packet["surfacing_images"] = _get_surfacing_images(identity, limit=5)
    except Exception:
        packet["surfacing_images"] = []

    # --- Emergent traits ---
    try:
        cursor.execute(
            "SELECT trait, evidence_count, strength, created_at FROM emergent_traits "
            "WHERE identity = ? AND status = 'active' ORDER BY strength DESC LIMIT 5",
            (identity,)
        )
        packet["emergent_traits"] = [
            {"trait": r[0], "evidence_count": r[1], "strength": round(r[2], 2)}
            for r in cursor.fetchall()
        ]
    except Exception:
        packet["emergent_traits"] = []

    # --- Interest suggestions ---
    try:
        cursor.execute(
            "SELECT suggestion, source, generated_at FROM suggested_interests "
            "WHERE identity = ? AND dismissed = 0 "
            "AND generated_at > datetime('now', '-24 hours') "
            "ORDER BY generated_at DESC LIMIT 5",
            (identity,)
        )
        packet["interest_suggestions"] = [
            {"suggestion": r[0], "source": r[1]} for r in cursor.fetchall()
        ]
    except Exception:
        packet["interest_suggestions"] = []

    # --- Pending sparks ---
    try:
        packet["pending_sparks"] = get_pending_sparks(identity, limit=3)
    except Exception:
        packet["pending_sparks"] = []

    return packet


def _refresh_morning_packet_cache(identities: List[str]) -> Dict[str, Any]:
    """Build and write the morning packet cache for all identities."""
    cache_payload: Dict[str, Any] = {
        "updated_at": datetime.now().isoformat(),
        "identities": {},
    }
    errors: List[Dict[str, str]] = []

    for identity in identities:
        try:
            cache_payload["identities"][identity.lower()] = _build_morning_packet(identity)
        except Exception as exc:
            errors.append({"identity": identity, "error": str(exc)})

    try:
        _MORNING_PACKET_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _MORNING_PACKET_CACHE_PATH.write_text(json.dumps(cache_payload), encoding="utf-8")
    except Exception as exc:
        return {"updated": 0, "errors": errors + [{"cache_write": str(exc)}]}

    return {
        "updated": len(cache_payload["identities"]),
        "errors": errors,
        "updated_at": cache_payload["updated_at"],
    }


def _refresh_drift_packet_cache(identities: List[str]) -> Dict[str, Any]:
    """Build and write the drift packet cache for all identities."""
    cache_payload: Dict[str, Any] = {
        "updated_at": datetime.now().isoformat(),
        "identities": {},
    }
    errors: List[Dict[str, str]] = []

    for identity in identities:
        try:
            cache_payload["identities"][identity.lower()] = _build_drift_packet(identity)
        except Exception as exc:
            errors.append({"identity": identity, "error": str(exc)})

    try:
        _DRIFT_PACKET_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _DRIFT_PACKET_CACHE_PATH.write_text(json.dumps(cache_payload), encoding="utf-8")
    except Exception as exc:
        return {"updated": 0, "errors": errors + [{"cache_write": str(exc)}]}

    return {
        "updated": len(cache_payload["identities"]),
        "errors": errors,
        "updated_at": cache_payload["updated_at"],
    }


def _get_cached_smart_context(identity: str, max_age_seconds: int = 1800) -> Dict[str, Any]:
    payload = _read_json(_SMART_CONTEXT_CACHE_PATH)
    if not payload:
        return {"identity": identity, "smart_context": None, "cache_status": "missing"}

    rec = payload.get("identities", {}).get(identity.lower())
    if not rec:
        return {"identity": identity, "smart_context": None, "cache_status": "identity_missing"}

    updated_at = rec.get("updated_at")
    stale = False
    if updated_at:
        try:
            age = (datetime.now() - datetime.fromisoformat(updated_at)).total_seconds()
            stale = age > max_age_seconds
        except Exception:
            stale = True
    else:
        stale = True

    return {
        "identity": identity,
        "smart_context": rec.get("smart_context"),
        "updated_at": updated_at,
        "cache_status": "stale" if stale else "fresh",
        "stale": stale
    }


def _code_to_atmosphere(weather_code: int) -> str:
    """Map open-meteo weather_code to a mood atmosphere."""
    if weather_code in (0, 1):
        return "clear"
    elif weather_code in (2, 3):
        return "cloudy"
    elif weather_code in (45, 48):
        return "foggy"
    elif weather_code in (51, 53, 55, 61, 63, 65, 66, 67, 80, 81):
        return "rainy"
    elif weather_code in (71, 73, 75, 77, 85, 86):
        return "snowy"
    elif weather_code in (82, 95, 96, 99):
        return "stormy"
    return "clear"


def _refresh_weather_cache() -> dict:
    """Fetch weather from open-meteo and write to sanctuary cache.

    Called every daemon cycle (~30 min). Qualia reads this cache instead
    of hitting the API itself, saving 5s per tool call.
    """
    try:
        req = urllib.request.Request(_WEATHER_API_URL)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        code = data.get("current", {}).get("weather_code", 0)
        temp = data.get("current", {}).get("temperature_2m")
        cache = {
            "atmosphere": _code_to_atmosphere(code),
            "weather_code": code,
            "temperature": temp,
            "timestamp": datetime.now().isoformat(),
        }
        _WEATHER_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _WEATHER_CACHE_PATH.write_text(json.dumps(cache), encoding="utf-8")
        return cache
    except Exception as exc:
        return {"error": str(exc)}


def _detect_co_surfacing_proposals(identity: str) -> Dict[str, Any]:
    """Detect observations frequently accessed together and propose connections."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    proposals_created = 0

    # Find observation pairs that co-surface frequently (3+ times) but aren't linked
    cursor.execute("""
        SELECT cs.obs_a_id, cs.obs_b_id, cs.co_count
        FROM co_surfacing cs
        WHERE cs.co_count >= 3
          AND NOT EXISTS (
            SELECT 1 FROM daemon_proposals dp
            WHERE ((dp.from_obs_id = cs.obs_a_id AND dp.to_obs_id = cs.obs_b_id)
                OR (dp.from_obs_id = cs.obs_b_id AND dp.to_obs_id = cs.obs_a_id))
          )
          AND NOT EXISTS (
            SELECT 1 FROM memory_links ml
            WHERE ((ml.source_id = cs.obs_a_id AND ml.target_id = cs.obs_b_id)
                OR (ml.source_id = cs.obs_b_id AND ml.target_id = cs.obs_a_id))
          )
        ORDER BY cs.co_count DESC
        LIMIT 5
    """)
    pairs = cursor.fetchall()

    for obs_a, obs_b, co_count in pairs:
        confidence = min(0.9, 0.4 + (co_count * 0.1))
        cursor.execute("""
            INSERT INTO daemon_proposals (proposal_type, from_obs_id, to_obs_id, reason, confidence, status, proposed_at)
            VALUES ('co_surfacing', ?, ?, ?, ?, 'pending', ?)
        """, (obs_a, obs_b, f"Co-surfaced {co_count} times", confidence, now))
        proposals_created += 1

    conn.commit()
    return {"identity": identity, "proposals_created": proposals_created}


def _flag_orphan_observations(identity: str, orphan_age_days: int = 30) -> Dict[str, Any]:
    """Flag observations that haven't surfaced in orphan_age_days."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=orphan_age_days)).isoformat()
    flagged = 0

    # Find observations never surfaced or not surfaced since cutoff
    cursor.execute("""
        SELECT o.id FROM observations o
        WHERE o.entity_name IN (
            SELECT e.name FROM entities e WHERE e.identity = ?
        )
        AND o.archived_at IS NULL
        AND (o.last_surfaced_at IS NULL OR o.last_surfaced_at < ?)
        AND o.created_at < ?
        AND NOT EXISTS (
            SELECT 1 FROM orphan_observations oo WHERE oo.observation_id = o.id
        )
        LIMIT 20
    """, (identity, cutoff, cutoff))
    orphans = cursor.fetchall()

    for (obs_id,) in orphans:
        cursor.execute("""
            INSERT OR IGNORE INTO orphan_observations (observation_id, flagged_at)
            VALUES (?, ?)
        """, (obs_id, datetime.now().isoformat()))
        flagged += 1

    conn.commit()
    return {"identity": identity, "orphans_flagged": flagged}


def _suggest_tensions(identity: str) -> Dict[str, Any]:
    """Detect potentially contradictory observations for same entity and suggest tensions."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    tensions_suggested = 0

    # Find entities with heavy observations that have opposing emotions
    opposing_pairs = [
        ("joy", "sadness"), ("peace", "anxiety"), ("love", "fear"),
        ("curiosity", "anxiety"), ("joy", "longing"),
    ]

    for emo_a, emo_b in opposing_pairs:
        cursor.execute("""
            SELECT a.entity_name, a.content, b.content
            FROM observations a
            JOIN observations b ON a.entity_name = b.entity_name AND a.id != b.id
            JOIN entities e ON e.name = a.entity_name AND e.identity = ?
            WHERE a.emotion = ? AND b.emotion = ?
              AND a.weight IN ('medium', 'heavy') AND b.weight IN ('medium', 'heavy')
              AND NOT EXISTS (
                SELECT 1 FROM tensions t
                WHERE t.identity = ? AND t.pole_a LIKE '%' || a.entity_name || '%'
              )
            LIMIT 2
        """, (identity, emo_a, emo_b, identity))

        for entity, content_a, content_b in cursor.fetchall():
            # Check we haven't already created this tension
            tid = f"auto-{entity[:10]}-{emo_a}-{emo_b}"[:20]
            cursor.execute("SELECT 1 FROM tensions WHERE id LIKE ? AND identity = ?", (f"{tid}%", identity))
            if cursor.fetchone():
                continue

            import uuid
            full_tid = f"{tid}-{str(uuid.uuid4())[:4]}"
            cursor.execute("""
                INSERT INTO tensions (id, identity, pole_a, pole_b, context, visits, created_at)
                VALUES (?, ?, ?, ?, ?, 0, ?)
            """, (full_tid, identity,
                  f"{content_a[:100]}",
                  f"{content_b[:100]}",
                  f"Auto-detected: {entity} has both {emo_a} and {emo_b} observations",
                  now))
            tensions_suggested += 1

    conn.commit()
    return {"identity": identity, "tensions_suggested": tensions_suggested}


def _run_cycle(recent_days: int, cluster_days: int) -> dict:
    identities = _get_known_identities()
    cycle_results = []
    organic_results = []
    auto_accept_results = []

    # First, generate any missing embeddings without blocking normal writes.
    try:
        embeddings_result = {
            "memories": _generate_embeddings_batch(batch_size=50),
            "images": _generate_image_embeddings_batch(batch_size=10),
        }
    except Exception as exc:
        embeddings_result = {"error": str(exc)}
        STATE.last_error = f"embeddings: {exc}"

    for identity in identities:
        try:
            cycle_results.append(_process_memories_internal(identity, recent_days=recent_days))
            cycle_results.append(generate_consolidation_candidates(identity, cluster_days=cluster_days))
        except Exception as exc:
            STATE.last_error = f"{identity}: {exc}"

        # Auto-accept high-confidence consolidation candidates
        try:
            auto_accept_results.append(auto_accept_high_confidence_candidates(identity))
        except Exception as exc:
            STATE.last_error = f"auto_accept/{identity}: {exc}"

        # Synthesize emergent traits from theme clusters
        try:
            trait_result = synthesize_traits_from_clusters(identity)
            if trait_result.get("traits_created", 0) > 0:
                cycle_results.append({"identity": identity, "emergent_traits": trait_result})
        except Exception as exc:
            STATE.last_error = f"trait_synthesis/{identity}: {exc}"

        # Run organic memory maintenance (link building, hyperedge detection, cluster warming)
        try:
            organic_results.append(_run_organic_memory_maintenance(identity))
        except Exception as exc:
            STATE.last_error = f"organic/{identity}: {exc}"

        # Refresh interest-based suggestions (things they might find interesting)
        try:
            interest_result = refresh_interest_suggestions(identity, max_suggestions=5)
            if interest_result.get("refreshed", 0) > 0:
                cycle_results.append({"identity": identity, "interest_suggestions": interest_result})
        except Exception as exc:
            STATE.last_error = f"interests/{identity}: {exc}"

        # Generate sparks - random memory juxtapositions for associative thinking
        # These bubble up naturally during sessions, not on-demand
        try:
            spark_result = _generate_sparks_for_identity(identity, count=2)
            if spark_result.get("sparks_generated", 0) > 0:
                cycle_results.append({"identity": identity, "sparks": spark_result})
        except Exception as exc:
            STATE.last_error = f"sparks/{identity}: {exc}"

        # Co-surfacing proposal detection â€” find patterns and suggest connections
        try:
            proposal_result = _detect_co_surfacing_proposals(identity)
            if proposal_result.get("proposals_created", 0) > 0:
                cycle_results.append(proposal_result)
        except Exception as exc:
            STATE.last_error = f"proposals/{identity}: {exc}"

        # Orphan detection â€” flag observations that haven't surfaced in 30+ days
        try:
            orphan_result = _flag_orphan_observations(identity)
            if orphan_result.get("orphans_flagged", 0) > 0:
                cycle_results.append(orphan_result)
        except Exception as exc:
            STATE.last_error = f"orphans/{identity}: {exc}"

        # Tension suggestion â€” detect contradictory observations
        try:
            tension_result = _suggest_tensions(identity)
            if tension_result.get("tensions_suggested", 0) > 0:
                cycle_results.append(tension_result)
        except Exception as exc:
            STATE.last_error = f"tensions/{identity}: {exc}"

    decay = _decay_memory_energy()

    # Auto-tag any new conversations in the vault
    try:
        if any(path.exists() for path in CONVERSATION_DIRECTORIES.values()):
            conversation_tags = _tag_new_conversations()
        else:
            conversation_tags = {"status": "skipped", "reason": "No configured conversation directories found."}
    except Exception as exc:
        conversation_tags = {"error": str(exc)}
        STATE.last_error = f"conversation_tags: {exc}"

    # Refresh weather cache for qualia to read
    weather_result = _refresh_weather_cache()
    smart_context_result = _refresh_smart_context_cache(identities)
    morning_packet_result = _refresh_morning_packet_cache(identities)
    drift_packet_result = _refresh_drift_packet_cache(identities)

    # Checkpoint WAL - flush write-ahead log back into main database to prevent
    # unbounded WAL growth and keep performance stable
    wal_checkpoint = None
    try:
        conn = get_db_connection()
        result = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        wal_checkpoint = {"busy": result[0], "log_pages": result[1], "checkpointed": result[2]}
    except Exception as exc:
        wal_checkpoint = {"error": str(exc)}

    STATE.cycle_count += 1

    STATE.last_cycle_at = datetime.now().isoformat()
    return {
        "identities": identities,
        "embeddings": embeddings_result,
        "processed": cycle_results,
        "auto_accepted": auto_accept_results,
        "organic_maintenance": organic_results,
        "decay": decay,
        "conversation_tags": conversation_tags,
        "weather_cache": weather_result,
        "smart_context_cache": smart_context_result,
        "morning_packet_cache": morning_packet_result,
        "drift_packet_cache": drift_packet_result,
        "wal_checkpoint": wal_checkpoint,
        "cycle_at": STATE.last_cycle_at
    }


def _post_store_enrich(memory_id: int) -> None:
    """Background enrichment for a newly stored memory.

    Called in a daemon thread after the /add endpoint responds immediately.
    Generates embedding, scores importance, and auto-links the memory.
    """
    try:
        # 1. Generate embedding (this is the existing behavior)
        _index_memory_embedding(memory_id)

        # 2. Fetch memory details for importance scoring and auto-linking
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT content, memory_type, identity FROM memories WHERE id = ?",
            (memory_id,)
        )
        row = cursor.fetchone()
        if not row:
            return
        content, memory_type, identity = row

        # 3. Score importance with Phi and update the row
        score = _score_importance_with_phi(content, memory_type, identity)
        if score != 0.5:
            cursor.execute(
                "UPDATE memories SET importance_score = ? WHERE id = ?",
                (score, memory_id)
            )
            conn.commit()

        # 4. Auto-link now that embedding exists
        _auto_link_memory(identity, memory_id, content)
    except Exception as exc:
        STATE.last_error = f"post_store_enrich/{memory_id}: {exc}"


class DaemonHandler(BaseHTTPRequestHandler):
    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
            # Client disconnected before we could send - nothing to do
            return

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        raw_str = raw.decode("utf-8").strip()
        if not raw_str:
            return {}
        try:
            return json.loads(raw_str)
        except json.JSONDecodeError:
            # Handle PowerShell quote issues - return empty dict
            return {}

    def do_GET(self) -> None:
        if self.path == "/status":
            self._send_json({"status": "ok", "state": STATE.to_dict()})
            return

        # AI Mind compatible endpoints
        if self.path.startswith("/subconscious"):
            # Parse identity from query string
            identity = self._parse_query_param("identity")
            if not identity:
                self._send_json({"error": "identity required"}, status=400)
                return
            try:
                self._send_json(_get_subconscious_state(identity))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if self.path.startswith("/affinities"):
            identity = self._parse_query_param("identity")
            if not identity:
                self._send_json({"error": "identity required"}, status=400)
                return
            try:
                self._send_json({"identity": identity, "affinities": _get_entity_affinities(identity)})
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if self.path.startswith("/heat"):
            identity = self._parse_query_param("identity")
            if not identity:
                self._send_json({"error": "identity required"}, status=400)
                return
            try:
                limit = int(self._parse_query_param("limit") or 10)
                self._send_json({"identity": identity, "hot_memories": _get_hot_memories(identity, limit)})
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if self.path.startswith("/inner-weather"):
            identity = self._parse_query_param("identity")
            if not identity:
                self._send_json({"error": "identity required"}, status=400)
                return
            try:
                self._send_json(_get_inner_weather(identity))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if self.path.startswith("/health"):
            identity = self._parse_query_param("identity")  # Optional
            try:
                self._send_json(_get_health_status(identity))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        # v1.2.0: Observation emotional processing endpoints
        if self.path.startswith("/observations/surfacing"):
            identity = self._parse_query_param("identity")
            if not identity:
                self._send_json({"error": "identity required"}, status=400)
                return
            try:
                limit = int(self._parse_query_param("limit") or 10)
                self._send_json({
                    "identity": identity,
                    "surfacing": get_surfacing_observations(identity, limit=limit)
                })
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if self.path.startswith("/observations/heavy"):
            identity = self._parse_query_param("identity")
            if not identity:
                self._send_json({"error": "identity required"}, status=400)
                return
            try:
                limit = int(self._parse_query_param("limit") or 5)
                self._send_json({
                    "identity": identity,
                    "heavy_observations": get_heavy_unprocessed_observations(identity, limit=limit)
                })
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if self.path.startswith("/observations/stats"):
            identity = self._parse_query_param("identity")  # Optional
            try:
                self._send_json(get_observation_weight_stats(identity))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if self.path.startswith("/auto-accept-history"):
            identity = self._parse_query_param("identity")  # Optional
            limit = self._parse_query_param("limit")
            days = self._parse_query_param("days")
            try:
                result = get_auto_accepted_history(
                    identity=identity,
                    limit=int(limit) if limit else 20,
                    days=int(days) if days else None
                )
                self._send_json(result)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if self.path.startswith("/traits"):
            identity = self._parse_query_param("identity")
            if not identity:
                self._send_json({"error": "identity required"}, status=400)
                return
            include_evidence = self._parse_query_param("evidence") == "true"
            try:
                result = get_emergent_traits(identity, include_evidence=include_evidence)
                self._send_json(result)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if self.path.startswith("/interests"):
            identity = self._parse_query_param("identity")
            if not identity:
                self._send_json({"error": "identity required"}, status=400)
                return
            regenerate = self._parse_query_param("regenerate") == "true"
            try:
                result = get_interest_suggestions(identity, regenerate=regenerate)
                self._send_json(result)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if self.path.startswith("/smart-context"):
            identity = self._parse_query_param("identity")
            if not identity:
                self._send_json({"error": "identity required"}, status=400)
                return
            try:
                max_age = int(self._parse_query_param("max_age") or 1800)
                self._send_json(_get_cached_smart_context(identity, max_age_seconds=max_age))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        # Sparks - daemon-generated memory juxtapositions for associative thinking
        if self.path.startswith("/sparks"):
            identity = self._parse_query_param("identity")
            if not identity:
                self._send_json({"error": "identity required"}, status=400)
                return
            try:
                limit = int(self._parse_query_param("limit") or 3)
                sparks = get_pending_sparks(identity, limit=limit)
                self._send_json({
                    "identity": identity,
                    "sparks": sparks,
                    "count": len(sparks),
                    "message": "These arose naturally - what connections do you see?"
                })
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if self.path.startswith("/morning-packet"):
            identity = self._parse_query_param("identity")
            if not identity:
                self._send_json({"error": "identity required"}, status=400)
                return
            try:
                payload = _read_json(_MORNING_PACKET_CACHE_PATH)
                packet = payload.get("identities", {}).get(identity.lower())
                if packet:
                    self._send_json(packet)
                else:
                    self._send_json({"error": "identity not in cache"}, status=404)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if self.path.startswith("/drift-packet"):
            identity = self._parse_query_param("identity")
            if not identity:
                self._send_json({"error": "identity required"}, status=400)
                return
            try:
                payload = _read_json(_DRIFT_PACKET_CACHE_PATH)
                packet = payload.get("identities", {}).get(identity.lower())
                if packet:
                    self._send_json(packet)
                else:
                    self._send_json({"error": "identity not in cache"}, status=404)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if self.path == "/weather":
            try:
                if _WEATHER_CACHE_PATH.exists():
                    cached = json.loads(_WEATHER_CACHE_PATH.read_text(encoding="utf-8"))
                    self._send_json({"cached": cached})
                else:
                    self._send_json({"cached": None, "note": "No cache yet"})
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        self._send_json({"error": "not found"}, status=404)

    def _parse_query_param(self, param: str) -> str:
        """Parse a query parameter from the path."""
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        values = params.get(param, [])
        return values[0] if values else None

    def do_POST(self) -> None:
        if self.path == "/add":
            payload = self._read_json()
            memory_id = payload.get("memory_id")
            if not memory_id:
                self._send_json({"error": "memory_id required"}, status=400)
                return
            # Respond immediately; enrich (embed + score + auto-link) in background
            mid = int(memory_id)
            self._send_json({"queued": True, "memory_id": mid})
            threading.Thread(
                target=_post_store_enrich, args=(mid,),
                name=f"enrich-{mid}", daemon=True
            ).start()
            return

        if self.path == "/process":
            payload = self._read_json()
            recent_days = int(payload.get("recent_days", 7))
            cluster_days = int(payload.get("cluster_days", 7))
            self._send_json(_run_cycle(recent_days, cluster_days))
            return

        if self.path == "/decay":
            self._send_json(_decay_memory_energy())
            return

        if self.path == "/organic":
            # Run organic memory maintenance for all identities
            identities = _get_known_identities()
            results = []
            for identity in identities:
                try:
                    results.append(_run_organic_memory_maintenance(identity))
                except Exception as exc:
                    results.append({"identity": identity, "error": str(exc)})
            self._send_json({"organic_maintenance": results})
            return

        if self.path == "/embeddings":
            # Generate missing embeddings for memories and images
            payload = self._read_json()
            batch_size = int(payload.get("batch_size", 100))
            image_batch_size = int(payload.get("image_batch_size", max(1, batch_size // 5)))
            try:
                result = {
                    "memories": _generate_embeddings_batch(batch_size=batch_size),
                    "images": _generate_image_embeddings_batch(batch_size=image_batch_size),
                }
                self._send_json(result)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if self.path == "/duplicates":
            # Find duplicate memories
            payload = self._read_json()
            identity = payload.get("identity")
            threshold = float(payload.get("threshold", 0.92))
            batch_size = int(payload.get("batch_size", 100))

            if identity:
                # Single identity
                try:
                    result = _find_duplicate_memories(identity, threshold, batch_size)
                    self._send_json(result)
                except Exception as exc:
                    self._send_json({"error": str(exc)}, status=500)
            else:
                # All identities
                identities = _get_known_identities()
                results = []
                for ident in identities:
                    try:
                        results.append(_find_duplicate_memories(ident, threshold, batch_size))
                    except Exception as exc:
                        results.append({"identity": ident, "error": str(exc)})
                self._send_json({"duplicates": results})
            return

        if self.path == "/consolidate":
            # Consolidate similar memories into clusters
            payload = self._read_json()
            identity = payload.get("identity")
            threshold = float(payload.get("threshold", 0.75))
            min_cluster = int(payload.get("min_cluster_size", 3))

            if identity:
                # Single identity
                try:
                    result = _consolidate_similar_memories(identity, threshold, min_cluster)
                    self._send_json(result)
                except Exception as exc:
                    self._send_json({"error": str(exc)}, status=500)
            else:
                # All identities
                identities = _get_known_identities()
                results = []
                for ident in identities:
                    try:
                        results.append(_consolidate_similar_memories(ident, threshold, min_cluster))
                    except Exception as exc:
                        results.append({"identity": ident, "error": str(exc)})
                self._send_json({"consolidation": results})
            return

        if self.path == "/tag-conversations":
            # Tag new/untagged conversations across all identities
            try:
                result = _tag_new_conversations()
                self._send_json(result)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if self.path == "/auto-accept":
            # Manually trigger auto-accept for high-confidence patterns
            payload = self._read_json()
            identity = payload.get("identity")
            min_score = payload.get("min_score")  # Optional override
            max_per_cycle = payload.get("max_per_cycle")  # Optional override

            if identity:
                # Single identity
                try:
                    kwargs = {}
                    if min_score is not None:
                        kwargs["min_score"] = float(min_score)
                    if max_per_cycle is not None:
                        kwargs["max_per_cycle"] = int(max_per_cycle)
                    result = auto_accept_high_confidence_candidates(identity, **kwargs)
                    self._send_json(result)
                except Exception as exc:
                    self._send_json({"error": str(exc)}, status=500)
            else:
                # All identities
                identities = _get_known_identities()
                results = []
                for ident in identities:
                    try:
                        results.append(auto_accept_high_confidence_candidates(ident))
                    except Exception as exc:
                        results.append({"identity": ident, "error": str(exc)})
                self._send_json({"auto_accept": results})
            return

        if self.path == "/spark-surfaced":
            # Mark a spark as surfaced (shown during session)
            payload = self._read_json()
            spark_id = payload.get("spark_id")
            connection = payload.get("connection")  # What connection was found

            if not spark_id:
                self._send_json({"error": "spark_id required"}, status=400)
                return

            try:
                result = mark_spark_surfaced(int(spark_id), connection)
                self._send_json(result)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if self.path == "/weather-refresh":
            try:
                result = _refresh_weather_cache()
                self._send_json(result)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if self.path == "/smart-context-refresh":
            payload = self._read_json()
            identity = payload.get("identity")
            try:
                if identity:
                    result = _refresh_smart_context_cache([identity])
                else:
                    result = _refresh_smart_context_cache(_get_known_identities())
                self._send_json(result)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if self.path == "/drift-packet-refresh":
            payload = self._read_json()
            identity = payload.get("identity")
            try:
                if identity:
                    result = _refresh_drift_packet_cache([identity])
                else:
                    result = _refresh_drift_packet_cache(_get_known_identities())
                self._send_json(result)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if self.path == "/drift-packet-surfaced":
            payload = self._read_json()
            try:
                result = _mark_items_surfaced(
                    observation_ids=payload.get("observation_ids"),
                    image_ids=payload.get("image_ids"),
                    spark_ids=payload.get("spark_ids"),
                )
                self._send_json(result)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if self.path == "/generate-sparks":
            # Manually trigger spark generation
            payload = self._read_json()
            identity = payload.get("identity")
            count = int(payload.get("count", 3))

            if identity:
                try:
                    result = _generate_sparks_for_identity(identity, count)
                    self._send_json(result)
                except Exception as exc:
                    self._send_json({"error": str(exc)}, status=500)
            else:
                # All identities
                identities = _get_known_identities()
                results = []
                for ident in identities:
                    try:
                        results.append(_generate_sparks_for_identity(ident, count))
                    except Exception as exc:
                        results.append({"identity": ident, "error": str(exc)})
                self._send_json({"sparks_generated": results})
            return

        self._send_json({"error": "not found"}, status=404)


def _start_background_worker(interval: int, recent_days: int, cluster_days: int) -> threading.Event:
    stop_event = threading.Event()

    def _loop() -> None:
        while not stop_event.is_set():
            try:
                _run_cycle(recent_days, cluster_days)
            except Exception as exc:
                STATE.last_error = str(exc)
            stop_event.wait(interval)

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    return stop_event


def main() -> None:
    parser = argparse.ArgumentParser(description="Memory Core background daemon")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--interval", type=int, default=1800)
    parser.add_argument("--recent-days", type=int, default=7)
    parser.add_argument("--cluster-days", type=int, default=7)
    args = parser.parse_args()

    init_database()
    # Warm the weather cache immediately on startup
    _refresh_weather_cache()
    # Warm smart-context and morning-packet caches immediately so morning_start can read fast.
    try:
        ids = _get_known_identities()
        _refresh_smart_context_cache(ids)
        _refresh_morning_packet_cache(ids)
        _refresh_drift_packet_cache(ids)
    except Exception:
        pass
    stop_event = _start_background_worker(args.interval, args.recent_days, args.cluster_days)

    server = HTTPServer((args.host, args.port), DaemonHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        server.server_close()


if __name__ == "__main__":
    main()

