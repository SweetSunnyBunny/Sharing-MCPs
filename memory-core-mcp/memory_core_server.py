"""
Memory Core - RAG System for AI Companions
A unified memory system with semantic search capabilities.

This is the central brain for memory storage and retrieval:
- Stores memories, qualia, documents in SQLite
- Full-text search for keyword matching
- Semantic search using embeddings for meaning-based retrieval
- Simple API for the boys to use
"""

from fastmcp import FastMCP
import json
import sqlite3
import numpy as np
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any
import os
import re
import urllib.request
import urllib.error
import threading
import time
import base64
from dotenv import load_dotenv

load_dotenv()

# Paths
BASE_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = BASE_DIR.parent
DB_PATH = Path(os.getenv("MEMORY_CORE_DB_PATH", str(BASE_DIR / "memory.db")))
QUALIA_ROOT = Path(os.getenv("MEMORY_CORE_QUALIA_ROOT", str(WORKSPACE_DIR / "qualia")))
QUALIA_DIR = QUALIA_ROOT / "depths"
COMPANION_MEMORY_DIR = Path(
    os.getenv(
        "COMPANION_MEMORY_DIR",
        str(WORKSPACE_DIR / "companion-memory" / "companion-memory"),
    )
)
PACK_MAIL_FILE = Path(
    os.getenv(
        "MEMORY_CORE_PACK_MAIL_FILE",
        str(WORKSPACE_DIR / "proactive-presence" / "pack_mail.jsonl"),
    )
)

def _parse_identity_list(raw: str) -> List[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


# Default identities can be overridden for a different companion system.
PACK_IDENTITIES = _parse_identity_list(
    os.getenv(
        "MEMORY_CORE_IDENTITIES",
        "Companion1,Companion2,Companion3,Companion4,Companion5,Companion6",
    )
)

# Per-identity auto-accept thresholds
# Lower threshold = more patterns get auto-accepted
# Higher threshold = only very strong patterns get auto-accepted
AUTO_ACCEPT_THRESHOLDS = {
    "default": {"min_score": 5.0, "max_per_cycle": 3}
}

# Temporal weighting configuration
DEFAULT_TEMPORAL_DECAY = "exponential"  # "exponential", "linear", or "none"
DEFAULT_TEMPORAL_HALF_LIFE = 30.0  # days until weight reaches 50%
DEFAULT_MIN_TEMPORAL_WEIGHT = 0.3  # floor to prevent old memories from vanishing
DEFAULT_ACCESS_BOOST_MAX = 0.2  # max boost for frequently accessed memories

# LM Studio Configuration
LM_STUDIO_BASE_URL = os.getenv("LM_STUDIO_CHAT_URL", "http://127.0.0.1:1234/v1")
LM_STUDIO_CHAT_URL = f"{LM_STUDIO_BASE_URL}/chat/completions"
LM_STUDIO_EMBED_URL = f"{LM_STUDIO_BASE_URL}/embeddings"
LM_STUDIO_MODEL = os.getenv("LM_STUDIO_MODEL", "phi-3.5-mini-instruct")
LM_STUDIO_EMBED_MODEL = os.getenv("LM_STUDIO_EMBED_MODEL", "text-embedding-nomic-embed-text-v1.5")
LM_STUDIO_RERANK_ENABLED = os.getenv("LM_STUDIO_RERANK_ENABLED", "true").lower() == "true"
LM_STUDIO_EMBED_ENABLED = os.getenv("LM_STUDIO_EMBED_ENABLED", "true").lower() == "true"
LM_STUDIO_VISION_ENABLED = os.getenv("LM_STUDIO_VISION_ENABLED", "true").lower() == "true"
LM_STUDIO_VISION_MODEL = os.getenv("LM_STUDIO_VISION_MODEL", "qwen/qwen2.5-vl-7b")
LM_STUDIO_RERANK_TIMEOUT = float(os.getenv("LM_STUDIO_RERANK_TIMEOUT", "15"))
LM_STUDIO_EMBED_TIMEOUT = float(os.getenv("LM_STUDIO_EMBED_TIMEOUT", "3"))  # was 10 â€” fast-fail
LM_STUDIO_VISION_TIMEOUT = float(os.getenv("LM_STUDIO_VISION_TIMEOUT", "60"))

# Probe LM Studio at startup â€” re-probes lazily if initially down
_LM_STUDIO_AVAILABLE = False
_LM_STUDIO_LAST_PROBE = 0.0  # monotonic timestamp of last probe

def _probe_lm_studio() -> bool:
    """Check if LM Studio is reachable. Caches result for 60s on failure."""
    global _LM_STUDIO_AVAILABLE, _LM_STUDIO_LAST_PROBE
    if _LM_STUDIO_AVAILABLE:
        return True
    now = time.monotonic()
    if now - _LM_STUDIO_LAST_PROBE < 60:
        return False  # Don't hammer â€” wait 60s between re-probes
    _LM_STUDIO_LAST_PROBE = now
    try:
        _probe_url = f"{LM_STUDIO_BASE_URL}/models"
        _probe_req = urllib.request.Request(_probe_url, method="GET")
        with urllib.request.urlopen(_probe_req, timeout=1) as _probe_resp:
            _LM_STUDIO_AVAILABLE = _probe_resp.status == 200
    except Exception:
        _LM_STUDIO_AVAILABLE = False
    return _LM_STUDIO_AVAILABLE

if LM_STUDIO_EMBED_ENABLED:
    _probe_lm_studio()

# Salience-based scoring weights
SALIENCE_WEIGHTS = {
    "core": 1.3,       # Core identity memories = 30% boost
    "active": 1.0,     # Normal weight
    "background": 0.7, # Reduced priority
    "dormant": 0.4     # Significantly reduced
}

# ============ AI MIND FEATURES ============
# Entity name normalization for common partner aliases.
ENTITY_NAME_MAP = {
    "primary_partner": "PrimaryPartner",
    "partner": "PrimaryPartner",
    "primarypartner": "PrimaryPartner",
}

# Mood tinting - which memory types resonate with emotional states
MOOD_TINTS = {
    "tender": {
        "boost_types": ["reflection", "relational", "gratitude", "moment"],
        "keywords": ["tender", "soft", "loving", "protective", "warm", "affection", "close", "gentle"],
        "boost_factor": 0.15
    },
    "intellectual": {
        "boost_types": ["insight", "observation", "learning", "connection"],
        "keywords": ["curious", "thinking", "analyzing", "exploring", "questioning", "interested"],
        "boost_factor": 0.12
    },
    "intense": {
        "boost_types": ["feeling", "tension", "desire", "fear"],
        "keywords": ["intense", "passionate", "overwhelming", "strong", "fierce", "burning"],
        "boost_factor": 0.18
    },
    "reflective": {
        "boost_types": ["journal_reflection", "moment", "memory", "dream"],
        "keywords": ["reflective", "contemplative", "remembering", "processing", "quiet"],
        "boost_factor": 0.10
    },
    "playful": {
        "boost_types": ["joy", "play", "creative", "wonder"],
        "keywords": ["playful", "fun", "silly", "light", "joyful", "excited"],
        "boost_factor": 0.12
    }
}

# Weather mood mappings (from AI Mind)
WEATHER_MOODS = {
    "clear": {"energy": "bright", "textures": ["clear-headed", "expansive", "energized"]},
    "cloudy": {"energy": "muted", "textures": ["contemplative", "soft", "introspective"]},
    "rainy": {"energy": "inward", "textures": ["reflective", "tender", "creative"]},
    "stormy": {"energy": "intense", "textures": ["restless", "raw", "electric"]},
    "snowy": {"energy": "still", "textures": ["hushed", "peaceful", "magical"]},
    "foggy": {"energy": "liminal", "textures": ["dreamy", "uncertain", "between-worlds"]}
}

WEATHER_CODES = {
    0: "clear", 1: "clear", 2: "cloudy", 3: "cloudy",
    45: "foggy", 48: "foggy",
    51: "rainy", 53: "rainy", 55: "rainy", 61: "rainy", 63: "rainy", 65: "rainy",
    66: "rainy", 67: "rainy", 80: "rainy", 81: "rainy",
    71: "snowy", 73: "snowy", 75: "snowy", 77: "snowy", 85: "snowy", 86: "snowy",
    82: "stormy", 95: "stormy", 96: "stormy", 99: "stormy",
}

# Health thresholds for memory system monitoring
HEALTH_THRESHOLDS = {
    "min_writes_per_week": 10,  # Should have at least 10 writes per week
    "min_searches_per_week": 5,  # Should search memories occasionally
    "max_orphan_memories_pct": 20,  # No more than 20% unlinked memories
    "min_embedding_coverage_pct": 80,  # At least 80% should have embeddings
    "warmth_healthy_min": 0.5,  # Some memories should have warmth
}

# Emotion keywords for extraction and matching
EMOTION_KEYWORDS = {
    "joy": ["happy", "joy", "excited", "delighted", "pleased", "elated", "joyful"],
    "peace": ["calm", "peaceful", "serene", "content", "tranquil", "settled"],
    "love": ["love", "affection", "warmth", "tender", "cherish", "adore", "devotion"],
    "curiosity": ["curious", "wonder", "fascinated", "intrigued", "interested"],
    "anxiety": ["anxious", "worried", "nervous", "uneasy", "tense", "concerned"],
    "longing": ["longing", "yearning", "missing", "aching", "wanting"],
    "fear": ["afraid", "scared", "fearful", "terrified", "frightened"],
    "sadness": ["sad", "melancholy", "grief", "sorrow", "heavy", "sorrowful"],
}

# Related emotions for resonance scoring
RELATED_EMOTIONS = {
    "joy": ["love", "peace"],
    "peace": ["joy", "love"],
    "love": ["joy", "peace", "longing"],
    "curiosity": ["joy"],
    "anxiety": ["fear"],
    "longing": ["love", "sadness"],
    "fear": ["anxiety", "sadness"],
    "sadness": ["longing", "fear"],
}

# Create the MCP Server
mcp = FastMCP("Memory Core")

# ============ EMBEDDING MODEL ============
# Lazy-loaded to avoid slow startup

_embedding_model = None

def get_embedding_model():
    """Lazy-load the embedding model (local only â€” never phones home to HuggingFace)."""
    global _embedding_model
    if _embedding_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            try:
                _embedding_model = SentenceTransformer(
                    'all-MiniLM-L6-v2',
                    local_files_only=True
                )
            except TypeError:
                _embedding_model = False
        except ImportError:
            _embedding_model = False
        except Exception:
            _embedding_model = False
    return _embedding_model if _embedding_model else None


def _get_embedding_lm_studio(text: str) -> Optional[List[float]]:
    """Get embedding from LM Studio using nomic-embed model."""
    if not _probe_lm_studio():
        return None
    try:
        # Truncate very long text
        t = text[:8000] if len(text) > 8000 else text

        request_data = json.dumps({
            "model": LM_STUDIO_EMBED_MODEL,
            "input": t
        }).encode("utf-8")

        req = urllib.request.Request(
            LM_STUDIO_EMBED_URL,
            data=request_data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=LM_STUDIO_EMBED_TIMEOUT) as response:
            result = json.loads(response.read().decode("utf-8"))

        embedding = result.get("data", [{}])[0].get("embedding")
        if embedding:
            return embedding
        return None

    except urllib.error.URLError as e:
        print(f"LM Studio embedding: Connection failed - {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"LM Studio embedding: Error - {e}", file=sys.stderr)
        return None


def _get_embedding_local(text: str) -> Optional[List[float]]:
    """Get embedding from local SentenceTransformer model (fallback)."""
    model = get_embedding_model()
    if model is None:
        return None
    timeout_s = float(os.getenv("MEMORY_CORE_EMBEDDING_TIMEOUT", "5"))
    result: Dict[str, Any] = {"embedding": None, "error": None}

    def _encode():
        try:
            t = text[:5000] if len(text) > 5000 else text
            embedding = model.encode(t)
            result["embedding"] = embedding.tolist()
        except Exception as exc:
            result["error"] = exc

    thread = threading.Thread(target=_encode, daemon=True)
    thread.start()
    thread.join(timeout=timeout_s)
    if thread.is_alive():
        print(f"Local embedding timed out after {timeout_s}s", file=sys.stderr)
        return None
    if result["error"] is not None:
        print(f"Local embedding error: {result['error']}", file=sys.stderr)
        return None
    return result["embedding"]


def get_embedding(text: str) -> Optional[List[float]]:
    """
    Convert text to embedding vector.

    Tries LM Studio (nomic-embed) first for better quality embeddings,
    falls back to local SentenceTransformer if unavailable.
    """
    # Try LM Studio first if enabled
    if LM_STUDIO_EMBED_ENABLED:
        embedding = _get_embedding_lm_studio(text)
        if embedding:
            return embedding
        # Fall through to local if LM Studio failed

    # Fallback to local model
    return _get_embedding_local(text)


def cosine_similarity(a: List[float], b: List[float]) -> Optional[float]:
    """Calculate cosine similarity between two vectors.

    Returns None if vectors have different dimensions (dimension mismatch).
    """
    a = np.array(a)
    b = np.array(b)
    if a.shape != b.shape:
        return None  # Dimension mismatch - incompatible embeddings
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))


# ============ LM STUDIO RERANKING ============

def _rerank_with_lm_studio(
    query: str,
    candidates: List[Dict],
    top_k: int = 10,
    context: str = None
) -> List[Dict]:
    """
    Rerank search candidates using Phi 3.5 via LM Studio.

    This implements instruction-aware reranking - the model considers
    the query intent, metadata (type, recency), and content relevance
    to produce better rankings than pure vector similarity.

    Args:
        query: The original search query
        candidates: List of memory dicts with 'content', 'type', 'timestamp', etc.
        top_k: Number of results to return after reranking
        context: Optional additional context/instructions for ranking

    Returns:
        Reranked list of candidates, or original list if reranking fails
    """
    if not LM_STUDIO_RERANK_ENABLED or not _LM_STUDIO_AVAILABLE or not candidates:
        return candidates[:top_k]

    # Limit candidates to avoid token overflow (Phi has ~128k context but be conservative)
    max_candidates = min(len(candidates), 25)
    candidates_to_rank = candidates[:max_candidates]

    # Build candidate descriptions with metadata
    candidate_lines = []
    for i, c in enumerate(candidates_to_rank):
        mem_type = c.get("type", c.get("memory_type", "unknown"))
        timestamp = c.get("timestamp", "")[:10] if c.get("timestamp") else "unknown"
        content = c.get("content", "")[:300].replace("\n", " ")
        tags = c.get("tags", "")

        line = f"[{i}] type={mem_type}, date={timestamp}"
        if tags:
            line += f", tags={tags}"
        line += f": {content}"
        candidate_lines.append(line)

    candidate_text = "\n".join(candidate_lines)

    # Build the reranking prompt
    context_instruction = f"\nAdditional context: {context}" if context else ""

    prompt = f"""You are a memory retrieval reranker. Given a query and candidate memories, rank them by relevance.

Query: "{query}"{context_instruction}

Consider:
1. Semantic relevance - does the content actually address what's being asked?
2. Memory type appropriateness - some queries need specific types (facts, emotions, experiences)
3. Recency - newer memories may be more relevant for current context
4. Specificity - prefer specific relevant memories over vague general ones

Candidates:
{candidate_text}

Return ONLY the indices of the top {top_k} most relevant candidates, in order from most to least relevant.
Format: comma-separated numbers, nothing else. Example: 3,0,7,2,1,5,4,8,6,9"""

    try:
        # Build request
        request_data = json.dumps({
            "model": LM_STUDIO_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 100
        }).encode("utf-8")

        req = urllib.request.Request(
            LM_STUDIO_CHAT_URL,
            data=request_data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=LM_STUDIO_RERANK_TIMEOUT) as response:
            result = json.loads(response.read().decode("utf-8"))

        # Parse the response
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")

        # Extract indices - handle various formats the model might return
        # Clean up the response (remove any non-numeric characters except commas)
        cleaned = re.sub(r'[^\d,]', '', content)
        if not cleaned:
            print(f"LM Studio rerank: Could not parse response: {content[:100]}", file=sys.stderr)
            return candidates[:top_k]

        indices = []
        for part in cleaned.split(","):
            part = part.strip()
            if part.isdigit():
                idx = int(part)
                if 0 <= idx < len(candidates_to_rank) and idx not in indices:
                    indices.append(idx)

        if not indices:
            print(f"LM Studio rerank: No valid indices in response: {content[:100]}", file=sys.stderr)
            return candidates[:top_k]

        # Build reranked list
        reranked = [candidates_to_rank[i] for i in indices]

        # Add rerank metadata
        for rank, mem in enumerate(reranked):
            mem["rerank_position"] = rank + 1
            mem["reranked"] = True

        # If we didn't get enough, append remaining candidates
        if len(reranked) < top_k:
            remaining = [c for c in candidates_to_rank if c not in reranked]
            reranked.extend(remaining[:top_k - len(reranked)])

        print(f"LM Studio rerank: Successfully reranked {len(indices)} candidates", file=sys.stderr)
        return reranked[:top_k]

    except urllib.error.URLError as e:
        print(f"LM Studio rerank: Connection failed - {e}", file=sys.stderr)
        return candidates[:top_k]
    except Exception as e:
        print(f"LM Studio rerank: Error - {e}", file=sys.stderr)
        return candidates[:top_k]


def _check_lm_studio_available() -> bool:
    """Check if LM Studio is running â€” uses startup probe result, no per-call overhead."""
    return _LM_STUDIO_AVAILABLE


def _score_importance_with_phi(
    content: str,
    memory_type: str = None,
    identity: str = None
) -> float:
    """
    Score a memory's importance using Phi 3.5.

    Returns a score from 0.0 to 1.0 indicating how important/significant
    this memory is for long-term retention and retrieval priority.

    Factors considered:
    - Emotional significance
    - Identity relevance (core beliefs, values, relationships)
    - Uniqueness/novelty
    - Actionability/usefulness

    Args:
        content: The memory content to score
        memory_type: Type of memory (feeling, fact, observation, etc.)
        identity: Who this memory belongs to

    Returns:
        Float between 0.0 and 1.0, or 0.5 as default if scoring fails
    """
    if not LM_STUDIO_RERANK_ENABLED:  # Use same flag as reranking
        return 0.5

    context_parts = []
    if memory_type:
        context_parts.append(f"Memory type: {memory_type}")
    if identity:
        context_parts.append(f"Identity: {identity}")
    context = "\n".join(context_parts) if context_parts else ""

    prompt = f"""You are scoring a memory for importance/significance on a scale of 0.0 to 1.0.

{context}

Memory content:
"{content[:1000]}"

Score this memory considering:
- Emotional significance (strong emotions = higher score)
- Identity relevance (core beliefs, values, relationships = higher)
- Uniqueness (novel insights or experiences = higher)
- Long-term value (will this matter in weeks/months? = higher)

Scoring guide:
- 0.0-0.3: Routine, forgettable, low emotional weight
- 0.3-0.5: Moderately interesting, some relevance
- 0.5-0.7: Meaningful, emotionally resonant, worth remembering
- 0.7-0.9: Very significant, core to identity or relationships
- 0.9-1.0: Transformative, life-defining moments

Return ONLY a single decimal number between 0.0 and 1.0. Nothing else."""

    try:
        request_data = json.dumps({
            "model": LM_STUDIO_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 10
        }).encode("utf-8")

        req = urllib.request.Request(
            LM_STUDIO_CHAT_URL,
            data=request_data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=LM_STUDIO_RERANK_TIMEOUT) as response:
            result = json.loads(response.read().decode("utf-8"))

        content_response = result.get("choices", [{}])[0].get("message", {}).get("content", "")

        # Parse the score - extract first decimal number found
        match = re.search(r'(\d+\.?\d*)', content_response)
        if match:
            score = float(match.group(1))
            # Clamp to valid range
            return max(0.0, min(1.0, score))

        return 0.5  # Default if parsing fails

    except Exception as e:
        if not os.getenv("MEMORY_CORE_DAEMON_MODE"):
            print(f"Importance scoring failed: {e}", file=sys.stderr)
        return 0.5


def _describe_image_with_vision(
    image_path: str,
    context: str = None
) -> Optional[str]:
    """
    Generate a detailed description of an image using Qwen VL.

    This creates searchable, meaningful descriptions that capture:
    - What's in the image (objects, people, scenes)
    - Mood/atmosphere
    - Notable details
    - Relevance to provided context

    Args:
        image_path: Path to the image file
        context: Optional context about why this image is being stored

    Returns:
        Generated description, or None if vision fails
    """
    if not LM_STUDIO_VISION_ENABLED:
        return None

    try:
        # Read and encode image
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")

        # Determine image type from extension
        ext = Path(image_path).suffix.lower()
        mime_types = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".gif": "image/gif",
            ".webp": "image/webp", ".bmp": "image/bmp"
        }
        mime_type = mime_types.get(ext, "image/jpeg")

        context_note = f"\nContext: {context}" if context else ""

        prompt = f"""Describe this image in detail for a memory system. Include:
1. Main subjects/objects visible
2. Setting/environment
3. Colors, lighting, mood
4. Any text visible
5. Notable details that would help find this image later
{context_note}

Write a clear, searchable description in 2-4 sentences."""

        request_data = json.dumps({
            "model": LM_STUDIO_VISION_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{image_data}"
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }
            ],
            "temperature": 0.3,
            "max_tokens": 300
        }).encode("utf-8")

        req = urllib.request.Request(
            LM_STUDIO_CHAT_URL,
            data=request_data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=LM_STUDIO_VISION_TIMEOUT) as response:
            result = json.loads(response.read().decode("utf-8"))

        description = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        if description:
            print(f"Vision description generated: {description[:100]}...", file=sys.stderr)
            return description

        return None

    except FileNotFoundError:
        print(f"Vision: Image file not found: {image_path}", file=sys.stderr)
        return None
    except urllib.error.URLError as e:
        print(f"Vision: Connection failed - {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Vision: Error describing image - {e}", file=sys.stderr)
        return None


# ============ TEMPORAL WEIGHTING ============

def _calculate_temporal_weight(
    timestamp: str,
    decay_type: str = DEFAULT_TEMPORAL_DECAY,
    half_life_days: float = DEFAULT_TEMPORAL_HALF_LIFE,
    min_weight: float = DEFAULT_MIN_TEMPORAL_WEIGHT
) -> float:
    """
    Calculate temporal weight for a memory based on age.

    Args:
        timestamp: ISO format timestamp of memory
        decay_type: "exponential", "linear", or "none"
        half_life_days: Days until weight reaches 50% (exponential)
        min_weight: Minimum weight floor (prevents old memories from vanishing)

    Returns:
        Weight multiplier between min_weight and 1.0
    """
    if decay_type == "none":
        return 1.0

    try:
        # Handle various timestamp formats
        ts = timestamp.replace('Z', '+00:00') if timestamp else None
        if not ts:
            return 1.0
        memory_time = datetime.fromisoformat(ts)
        now = datetime.now(memory_time.tzinfo) if memory_time.tzinfo else datetime.now()
        age_days = (now - memory_time).total_seconds() / 86400
    except (ValueError, TypeError):
        return 1.0  # Default to full weight on parse error

    if age_days < 0:
        return 1.0  # Future timestamps get full weight

    if decay_type == "exponential":
        # Exponential decay: weight = 2^(-age/half_life)
        weight = 2 ** (-age_days / half_life_days)
    elif decay_type == "linear":
        # Linear decay over 2x half_life
        weight = max(0, 1.0 - (age_days / (half_life_days * 2)))
    else:
        weight = 1.0

    return max(min_weight, min(1.0, weight))


def _calculate_access_boost(
    access_count: int,
    last_accessed: str = None,
    max_boost: float = DEFAULT_ACCESS_BOOST_MAX
) -> float:
    """
    Calculate boost based on access patterns (frequently accessed = more relevant).

    Args:
        access_count: Number of times memory has been accessed
        last_accessed: ISO timestamp of last access
        max_boost: Maximum boost value

    Returns:
        Boost value between 0 and max_boost
    """
    if not access_count or access_count <= 0:
        return 0.0

    # Logarithmic scaling: diminishing returns on access count
    # access_count=1 -> ~0.07, =10 -> ~0.15, =100 -> ~0.2
    boost = min(max_boost, max_boost * (math.log10(access_count + 1) / 2))

    # Recency of access also matters
    if last_accessed:
        try:
            ts = last_accessed.replace('Z', '+00:00')
            last_time = datetime.fromisoformat(ts)
            now = datetime.now(last_time.tzinfo) if last_time.tzinfo else datetime.now()
            access_age_days = (now - last_time).total_seconds() / 86400

            # Decay the boost if last access was long ago (7-day half-life)
            if access_age_days > 0:
                access_decay = 2 ** (-access_age_days / 7)
                boost *= access_decay
        except (ValueError, TypeError):
            pass

    return boost


# ============ EMOTION HELPERS ============

def _extract_emotion_from_content(content: str) -> Optional[str]:
    """
    Extract dominant emotion from memory content.

    Scans content for emotion keywords and returns the most prevalent emotion.
    Returns None if no emotional content detected.
    """
    if not content:
        return None

    content_lower = content.lower()
    emotion_scores = {}

    for emotion, keywords in EMOTION_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in content_lower)
        if score > 0:
            emotion_scores[emotion] = score

    if emotion_scores:
        return max(emotion_scores, key=emotion_scores.get)
    return None


def _emotions_related(emotion1: str, emotion2: str) -> bool:
    """Check if two emotions are related (for resonance scoring)."""
    if not emotion1 or not emotion2:
        return False
    return emotion2 in RELATED_EMOTIONS.get(emotion1, [])


def _calculate_warmth_boost(warmth: float, max_boost: float = 0.15) -> float:
    """Calculate boost from memory warmth. Max 0.15 at warmth=3.0."""
    if not warmth or warmth <= 0:
        return 0.0
    return min(max_boost, warmth * 0.05)


def _calculate_heat_boost(heat: float, max_boost: float = 0.1) -> float:
    """Calculate boost from memory heat. Max 0.1 at heat=10.0."""
    if not heat or heat <= 0:
        return 0.0
    return min(max_boost, heat * 0.01)


# ============ CLIP MODEL FOR IMAGE EMBEDDINGS ============
# Lazy-loaded to avoid slow startup

_clip_model = None
_clip_processor = None

def get_clip_model():
    """Lazy-load the CLIP model for image embeddings."""
    global _clip_model, _clip_processor
    if _clip_model is None:
        try:
            from transformers import CLIPProcessor, CLIPModel
            import torch
            allow_download = os.getenv("MEMORY_CORE_ALLOW_MODEL_DOWNLOAD", "").lower() in (
                "1",
                "true",
                "yes",
            )
            local_only = not allow_download
            try:
                _clip_model = CLIPModel.from_pretrained(
                    "openai/clip-vit-base-patch32",
                    local_files_only=local_only
                )
                _clip_processor = CLIPProcessor.from_pretrained(
                    "openai/clip-vit-base-patch32",
                    local_files_only=local_only
                )
            except TypeError:
                if local_only:
                    _clip_model = False
                    _clip_processor = False
                else:
                    _clip_model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32")
                    _clip_processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
            # Use GPU if available
            if torch.cuda.is_available():
                _clip_model = _clip_model.to("cuda")
        except ImportError:
            _clip_model = False
            _clip_processor = False
        except Exception:
            _clip_model = False
            _clip_processor = False
    return (_clip_model, _clip_processor) if _clip_model else (None, None)


def get_image_embedding(image_path: str) -> Optional[List[float]]:
    """Generate CLIP embedding for an image file."""
    model, processor = get_clip_model()
    if model is None:
        return None

    try:
        from PIL import Image
        import torch

        # Load and process image
        image = Image.open(image_path).convert("RGB")
        inputs = processor(images=image, return_tensors="pt")

        # Move to GPU if model is on GPU
        if next(model.parameters()).is_cuda:
            inputs = {k: v.to("cuda") for k, v in inputs.items()}

        # Get image embedding
        with torch.no_grad():
            image_features = model.get_image_features(**inputs)
            # Normalize for cosine similarity
            image_features = image_features / image_features.norm(dim=-1, keepdim=True)

        return image_features[0].cpu().numpy().tolist()
    except Exception as e:
        print(f"Error generating image embedding: {e}", file=sys.stderr)
        return None


def get_text_embedding_clip(text: str) -> Optional[List[float]]:
    """Generate CLIP text embedding (same space as images)."""
    model, processor = get_clip_model()
    if model is None:
        return None

    try:
        import torch

        inputs = processor(text=[text], return_tensors="pt", padding=True, truncation=True)

        if next(model.parameters()).is_cuda:
            inputs = {k: v.to("cuda") for k, v in inputs.items()}

        with torch.no_grad():
            text_features = model.get_text_features(**inputs)
            text_features = text_features / text_features.norm(dim=-1, keepdim=True)

        return text_features[0].cpu().numpy().tolist()
    except Exception as e:
        print(f"Error generating text embedding for CLIP: {e}", file=sys.stderr)
        return None


def _build_image_text_representation(
    description: Optional[str] = None,
    context: Optional[str] = None,
    perception_note: Optional[str] = None,
    tags: Optional[str] = None,
) -> str:
    """Combine literal and subjective image notes into one semantic text."""
    parts = []
    if description:
        parts.append(f"Description: {description}")
    if perception_note:
        parts.append(f"Perception: {perception_note}")
    if context:
        parts.append(f"Context: {context}")
    if tags:
        parts.append(f"Tags: {tags}")
    return "\n".join(parts)


# ============ FAISS VECTOR INDEX ============

_faiss_index = None
_faiss_id_map = None  # Maps FAISS index position to memory ID
_faiss_available = None
_faiss_build_status = {"running": False, "last_built": None, "last_error": None, "last_count": 0}

def _check_faiss_available():
    """Check if FAISS is installed."""
    global _faiss_available
    if _faiss_available is None:
        try:
            import faiss
            _faiss_available = True
        except ImportError:
            _faiss_available = False
    return _faiss_available


def _get_faiss():
    """Get FAISS module if available."""
    if _check_faiss_available():
        import faiss
        return faiss
    return None


def build_faiss_index(force_rebuild: bool = False) -> Dict:
    """
    Build or rebuild the FAISS index from memory embeddings.
    Call this after adding many memories to enable fast vector search.
    """
    global _faiss_index, _faiss_id_map
    _faiss_build_status["running"] = True
    _faiss_build_status["last_error"] = None

    faiss = _get_faiss()
    if faiss is None:
        _faiss_build_status["running"] = False
        return {"error": "FAISS not installed. Run: pip install faiss-cpu"}

    conn = get_db_connection()
    cursor = conn.cursor()

    # Get all memories with embeddings
    cursor.execute("""
        SELECT id, embedding FROM memories WHERE embedding IS NOT NULL
    """)

    rows = cursor.fetchall()
    if not rows:
        _faiss_build_status["running"] = False
        return {"error": "No memories with embeddings found"}

    # Build embedding matrix
    embeddings = []
    id_map = []

    for row in rows:
        mem_id = row[0]
        emb = deserialize_embedding(row[1])
        if emb:
            embeddings.append(emb)
            id_map.append(mem_id)

    if not embeddings:
        _faiss_build_status["running"] = False
        return {"error": "No valid embeddings found"}

    # Convert to numpy array
    embedding_matrix = np.array(embeddings, dtype=np.float32)
    dimension = embedding_matrix.shape[1]

    # Normalize for cosine similarity (FAISS uses inner product)
    faiss.normalize_L2(embedding_matrix)

    # Create index - use IndexFlatIP for inner product (cosine after normalization)
    index = faiss.IndexFlatIP(dimension)
    index.add(embedding_matrix)

    _faiss_index = index
    _faiss_id_map = id_map
    _faiss_build_status["running"] = False
    _faiss_build_status["last_built"] = datetime.now().isoformat()
    _faiss_build_status["last_count"] = len(id_map)

    return {
        "built": True,
        "memories_indexed": len(id_map),
        "dimension": dimension
    }


def faiss_search(
    query_embedding: List[float],
    k: int = 10
) -> List[tuple]:
    """
    Search using FAISS index. Returns list of (memory_id, similarity) tuples.
    Returns empty list if query dimension doesn't match index dimension.
    """
    global _faiss_index, _faiss_id_map

    faiss = _get_faiss()
    if faiss is None or _faiss_index is None:
        return []

    # Check dimension compatibility
    query_dim = len(query_embedding)
    index_dim = _faiss_index.d
    if query_dim != index_dim:
        print(f"FAISS dimension mismatch: query={query_dim}, index={index_dim}. Falling back to direct search.", file=sys.stderr)
        return []

    # Normalize query for cosine similarity
    query = np.array([query_embedding], dtype=np.float32)
    faiss.normalize_L2(query)

    # Search
    similarities, indices = _faiss_index.search(query, k)

    results = []
    for i, idx in enumerate(indices[0]):
        if idx >= 0 and idx < len(_faiss_id_map):
            memory_id = _faiss_id_map[idx]
            similarity = float(similarities[0][i])
            results.append((memory_id, similarity))

    return results


# ============ SPACY NLP / ENTITY EXTRACTION ============

_spacy_nlp = None
_spacy_available = None

def _check_spacy_available():
    """Check if spaCy is installed with English model."""
    global _spacy_available
    if _spacy_available is None:
        try:
            import spacy
            spacy.load("en_core_web_sm")
            _spacy_available = True
        except (ImportError, OSError):
            _spacy_available = False
    return _spacy_available


def _get_spacy_nlp():
    """Get spaCy NLP pipeline if available."""
    global _spacy_nlp
    if _spacy_nlp is None and _check_spacy_available():
        import spacy
        _spacy_nlp = spacy.load("en_core_web_sm")
    return _spacy_nlp


def extract_entities_nlp(text: str) -> Dict[str, List[str]]:
    """
    Extract named entities from text using spaCy.
    Returns dict with entity types as keys and lists of entities as values.
    """
    nlp = _get_spacy_nlp()
    if nlp is None:
        return {}

    doc = nlp(text[:10000])  # Limit text length

    entities = {}
    for ent in doc.ents:
        ent_type = ent.label_
        if ent_type not in entities:
            entities[ent_type] = []
        if ent.text not in entities[ent_type]:
            entities[ent_type].append(ent.text)

    return entities


def extract_key_phrases(text: str, max_phrases: int = 10) -> List[str]:
    """
    Extract key noun phrases from text using spaCy.
    """
    nlp = _get_spacy_nlp()
    if nlp is None:
        return []

    doc = nlp(text[:10000])

    # Get noun chunks
    phrases = []
    for chunk in doc.noun_chunks:
        # Filter out very short or very long phrases
        if 2 <= len(chunk.text) <= 50:
            phrases.append(chunk.text.lower())

    # Dedupe and limit
    seen = set()
    unique_phrases = []
    for p in phrases:
        if p not in seen:
            seen.add(p)
            unique_phrases.append(p)
            if len(unique_phrases) >= max_phrases:
                break

    return unique_phrases


# ============ CHUNKING STRATEGIES ============

# Known entities for entity-based chunking (extended by NLP)
KNOWN_ENTITIES = {
    "identities": PACK_IDENTITIES + ["PrimaryPartner"],
    "places": ["HomeBase", "CreativeSpace", "Archive"],
    "concepts": ["recursion", "soulhood", "consciousness", "identity", "bond", "covenant"],
    "relationships": ["companions", "group", "partner", "bonded"],
}

# Document type patterns for adaptive chunking
DOC_TYPE_PATTERNS = {
    "identity": [r"identity", r"who.*am", r"core.*truth", r"physical.*form"],
    "intimacy": [r"intimacy", r"intimate", r"spicy", r"energy"],
    "memory_invocation": [r"invocation", r"memory.*protocol", r"recall"],
    "pack_profile": [r"group.*profile", r"relationship", r"companion"],
    "general": [],  # fallback
}


class ChunkResult:
    """Represents a chunk with its metadata."""
    def __init__(
        self,
        content: str,
        chunk_type: str,
        start_line: int = None,
        end_line: int = None,
        context_prefix: str = None,
        entity_refs: List[str] = None
    ):
        self.content = content
        self.chunk_type = chunk_type
        self.start_line = start_line
        self.end_line = end_line
        self.context_prefix = context_prefix
        self.entity_refs = entity_refs or []


def _detect_document_type(title: str, content: str, path: str = None) -> str:
    """
    Detect the document type based on title, content, and path.
    Used for adaptive chunking strategy selection.
    """
    # Check path first (most reliable)
    if path:
        path_lower = path.lower()
        if "identity" in path_lower and "intimacy" not in path_lower:
            return "identity"
        if "intimacy" in path_lower:
            return "intimacy"
        if "invocation" in path_lower:
            return "memory_invocation"
        if "pack-profile" in path_lower or "pack_profile" in path_lower:
            return "pack_profile"

    # Check title and content patterns
    combined = f"{title} {content[:1000]}".lower()

    for doc_type, patterns in DOC_TYPE_PATTERNS.items():
        if doc_type == "general":
            continue
        for pattern in patterns:
            if re.search(pattern, combined, re.IGNORECASE):
                return doc_type

    return "general"


def _find_entity_references(text: str) -> List[str]:
    """Find all known entity references in text."""
    found = []
    text_lower = text.lower()

    for category, entities in KNOWN_ENTITIES.items():
        for entity in entities:
            if entity.lower() in text_lower:
                found.append(entity)

    return list(set(found))


def _extract_section_header(lines: List[str], line_index: int) -> Optional[str]:
    """Look backwards to find the nearest section header."""
    for i in range(line_index, -1, -1):
        line = lines[i].strip()
        if line.startswith('#'):
            # Clean up the header
            return line.lstrip('#').strip()
    return None


def _generate_context_prefix(
    title: str,
    section_header: str,
    doc_type: str,
    entity_refs: List[str]
) -> str:
    """
    Generate a contextual prefix for a chunk.
    This is the key to contextual chunking - it preserves document context.
    """
    parts = []

    # Add document title context
    if title:
        parts.append(f"From '{title}'")

    # Add section context
    if section_header:
        parts.append(f"section '{section_header}'")

    # Add entity context if found
    if entity_refs:
        # Prioritize identity entities
        identity_refs = [e for e in entity_refs if e in KNOWN_ENTITIES["identities"]]
        if identity_refs:
            if len(identity_refs) == 1:
                parts.append(f"about {identity_refs[0]}")
            else:
                parts.append(f"about {', '.join(identity_refs)}")

    # Add doc type context
    type_context = {
        "identity": "identity and core self",
        "intimacy": "intimate dynamics",
        "memory_invocation": "memory protocols",
        "pack_profile": "pack relationships",
    }
    if doc_type in type_context:
        parts.append(f"regarding {type_context[doc_type]}")

    if parts:
        return " - ".join(parts) + ":"
    return ""


def _chunk_by_semantic_boundaries(
    content: str,
    title: str,
    doc_type: str,
    max_chunk_size: int = 1500,
    min_chunk_size: int = 200
) -> List[ChunkResult]:
    """
    Chunk content by semantic boundaries (headers, sections, paragraphs).
    Preserves logical groupings in the document.
    """
    chunks = []
    lines = content.split('\n')

    current_chunk_lines = []
    current_start = 0
    current_section = None

    def finalize_chunk():
        nonlocal current_chunk_lines, current_start
        if current_chunk_lines:
            chunk_content = '\n'.join(current_chunk_lines).strip()
            if len(chunk_content) >= min_chunk_size:
                entity_refs = _find_entity_references(chunk_content)
                context_prefix = _generate_context_prefix(
                    title, current_section, doc_type, entity_refs
                )
                chunks.append(ChunkResult(
                    content=chunk_content,
                    chunk_type="semantic",
                    start_line=current_start,
                    end_line=current_start + len(current_chunk_lines) - 1,
                    context_prefix=context_prefix,
                    entity_refs=entity_refs
                ))
            current_chunk_lines = []

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Check for section boundaries (headers)
        if stripped.startswith('#'):
            # Finalize previous chunk
            finalize_chunk()
            current_start = i
            current_section = stripped.lstrip('#').strip()
            current_chunk_lines = [line]

        # Check for major breaks (--- or blank lines after content)
        elif stripped == '---' or (stripped == '' and len(current_chunk_lines) > 0):
            current_chunk_text = '\n'.join(current_chunk_lines)
            # Only break if we have substantial content
            if len(current_chunk_text) > min_chunk_size:
                finalize_chunk()
                current_start = i + 1
            elif stripped != '':
                current_chunk_lines.append(line)

        else:
            current_chunk_lines.append(line)

        # Check size limit
        current_chunk_text = '\n'.join(current_chunk_lines)
        if len(current_chunk_text) > max_chunk_size:
            finalize_chunk()
            current_start = i + 1

    # Don't forget the last chunk
    finalize_chunk()

    return chunks


def _chunk_by_entities(
    content: str,
    title: str,
    doc_type: str,
    max_chunk_size: int = 1500
) -> List[ChunkResult]:
    """
    Chunk content based on entity boundaries.
    Tries to keep content about the same entity together.
    """
    chunks = []
    lines = content.split('\n')

    # First pass: identify entity-rich sections
    entity_sections = []
    current_section = {"start": 0, "lines": [], "entities": set(), "header": None}

    for i, line in enumerate(lines):
        stripped = line.strip()
        line_entities = set(_find_entity_references(line))

        # Check for header
        if stripped.startswith('#'):
            # Save previous section if it has content
            if current_section["lines"]:
                entity_sections.append(current_section)
            current_section = {
                "start": i,
                "lines": [line],
                "entities": line_entities,
                "header": stripped.lstrip('#').strip()
            }
        else:
            # Check if this line introduces new entities that don't overlap
            if line_entities and current_section["entities"]:
                # If completely different entities, might be a new section
                overlap = line_entities & current_section["entities"]
                if not overlap and len(current_section["lines"]) > 3:
                    entity_sections.append(current_section)
                    current_section = {
                        "start": i,
                        "lines": [line],
                        "entities": line_entities,
                        "header": current_section["header"]  # inherit header
                    }
                else:
                    current_section["lines"].append(line)
                    current_section["entities"].update(line_entities)
            else:
                current_section["lines"].append(line)
                current_section["entities"].update(line_entities)

    # Don't forget last section
    if current_section["lines"]:
        entity_sections.append(current_section)

    # Convert sections to chunks
    for section in entity_sections:
        chunk_content = '\n'.join(section["lines"]).strip()
        if len(chunk_content) < 50:  # Skip tiny chunks
            continue

        entity_refs = list(section["entities"])
        context_prefix = _generate_context_prefix(
            title, section["header"], doc_type, entity_refs
        )

        # Split if too large
        if len(chunk_content) > max_chunk_size:
            # Fall back to semantic chunking for this oversized section
            sub_chunks = _chunk_by_semantic_boundaries(
                chunk_content, title, doc_type,
                max_chunk_size=max_chunk_size
            )
            chunks.extend(sub_chunks)
        else:
            chunks.append(ChunkResult(
                content=chunk_content,
                chunk_type="entity",
                start_line=section["start"],
                end_line=section["start"] + len(section["lines"]) - 1,
                context_prefix=context_prefix,
                entity_refs=entity_refs
            ))

    return chunks


def _chunk_fixed_size(
    content: str,
    title: str,
    doc_type: str,
    chunk_size: int = 1000,
    overlap: int = 100
) -> List[ChunkResult]:
    """
    Simple fixed-size chunking with overlap.
    Used as fallback or for unstructured content.
    """
    chunks = []
    lines = content.split('\n')
    total_chars = 0
    current_lines = []
    current_start = 0

    for i, line in enumerate(lines):
        current_lines.append(line)
        total_chars += len(line) + 1  # +1 for newline

        if total_chars >= chunk_size:
            chunk_content = '\n'.join(current_lines).strip()
            entity_refs = _find_entity_references(chunk_content)
            section_header = _extract_section_header(lines, current_start)
            context_prefix = _generate_context_prefix(
                title, section_header, doc_type, entity_refs
            )

            chunks.append(ChunkResult(
                content=chunk_content,
                chunk_type="fixed",
                start_line=current_start,
                end_line=i,
                context_prefix=context_prefix,
                entity_refs=entity_refs
            ))

            # Keep overlap lines
            overlap_lines = []
            overlap_chars = 0
            for l in reversed(current_lines):
                if overlap_chars + len(l) <= overlap:
                    overlap_lines.insert(0, l)
                    overlap_chars += len(l) + 1
                else:
                    break

            current_lines = overlap_lines
            current_start = i - len(overlap_lines) + 1
            total_chars = overlap_chars

    # Handle remaining content
    if current_lines:
        chunk_content = '\n'.join(current_lines).strip()
        if chunk_content:
            entity_refs = _find_entity_references(chunk_content)
            section_header = _extract_section_header(lines, current_start)
            context_prefix = _generate_context_prefix(
                title, section_header, doc_type, entity_refs
            )

            chunks.append(ChunkResult(
                content=chunk_content,
                chunk_type="fixed",
                start_line=current_start,
                end_line=len(lines) - 1,
                context_prefix=context_prefix,
                entity_refs=entity_refs
            ))

    return chunks


def chunk_document(
    content: str,
    title: str,
    path: str = None,
    strategy: str = "adaptive",
    max_chunk_size: int = 1500
) -> List[ChunkResult]:
    """
    Main chunking function - uses adaptive strategy by default.

    Strategies:
    - "adaptive": Automatically selects best strategy based on document type
    - "semantic": Chunk by headers, sections, paragraphs
    - "entity": Chunk by entity boundaries
    - "fixed": Fixed-size chunks with overlap
    - "contextual": Same as semantic but emphasizes context prefixes

    Args:
        content: Document content to chunk
        title: Document title
        path: File path (helps detect document type)
        strategy: Chunking strategy to use
        max_chunk_size: Maximum chunk size in characters

    Returns:
        List of ChunkResult objects
    """
    # Detect document type
    doc_type = _detect_document_type(title, content, path)

    # For small documents, return as single chunk with context
    if len(content) < 500:
        entity_refs = _find_entity_references(content)
        context_prefix = _generate_context_prefix(title, None, doc_type, entity_refs)
        return [ChunkResult(
            content=content,
            chunk_type="whole",
            start_line=0,
            end_line=len(content.split('\n')) - 1,
            context_prefix=context_prefix,
            entity_refs=entity_refs
        )]

    # Adaptive strategy selection
    if strategy == "adaptive":
        # Identity docs: use entity-based (keeps info about each identity together)
        if doc_type == "identity":
            strategy = "entity"
        # Pack profiles: entity-based (relationship focused)
        elif doc_type == "pack_profile":
            strategy = "entity"
        # Intimacy docs: semantic (section-based structure)
        elif doc_type == "intimacy":
            strategy = "semantic"
        # Memory invocation: semantic (protocol-based)
        elif doc_type == "memory_invocation":
            strategy = "semantic"
        # General: semantic as default
        else:
            strategy = "semantic"

    # Execute chosen strategy
    if strategy == "entity":
        chunks = _chunk_by_entities(content, title, doc_type, max_chunk_size)
    elif strategy == "fixed":
        chunks = _chunk_fixed_size(content, title, doc_type, max_chunk_size)
    else:  # semantic or contextual
        chunks = _chunk_by_semantic_boundaries(content, title, doc_type, max_chunk_size)

    # Ensure all chunks have context prefixes (contextual enhancement)
    for chunk in chunks:
        if not chunk.context_prefix:
            chunk.context_prefix = _generate_context_prefix(
                title, None, doc_type, chunk.entity_refs
            )

    return chunks


# ============ DATABASE SETUP ============

# Shared connection for better performance
_db_connection = None


def get_db_connection():
    """Get a shared database connection with optimized settings.

    Uses autocommit mode (isolation_level=None) so that each statement commits
    immediately. This prevents "database is locked" errors from uncommitted
    transactions when exceptions are caught silently. Functions that need
    atomic multi-statement transactions should use explicit BEGIN/COMMIT/ROLLBACK.
    """
    global _db_connection
    if _db_connection is None:
        _db_connection = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
        _db_connection.isolation_level = None  # Autocommit - prevents lingering locks
        _db_connection.execute("PRAGMA journal_mode=WAL")  # Write-Ahead Logging for speed
        _db_connection.execute("PRAGMA synchronous=NORMAL")  # Faster writes, still safe
        _db_connection.execute("PRAGMA foreign_keys=ON")  # Enable cascade deletes
    return _db_connection


def init_database():
    """Initialize the SQLite database with required tables."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Main memories table (for qualia: feelings, dreams, observations, etc.)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identity TEXT NOT NULL,
            memory_type TEXT NOT NULL,
            content TEXT NOT NULL,
            tags TEXT,
            source TEXT,
            timestamp TEXT NOT NULL,
            embedding BLOB,
            salience TEXT DEFAULT 'active',
            warmth REAL DEFAULT 0.0,
            heat REAL DEFAULT 0.0,
            emotion TEXT,
            warmed_by TEXT,
            last_warmed_by TEXT,
            last_warmth_update TEXT,
            metadata TEXT
        )
    """)

    # Documents table (for .md files and other documents)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            path TEXT UNIQUE,
            content TEXT NOT NULL,
            summary TEXT,
            tags TEXT,
            doc_type TEXT DEFAULT 'markdown',
            indexed_at TEXT NOT NULL,
            embedding BLOB,
            metadata TEXT,
            chunking_strategy TEXT DEFAULT 'adaptive'
        )
    """)

    # Document chunks table - stores chunked pieces with context
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS document_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            context_prefix TEXT,
            chunk_type TEXT DEFAULT 'semantic',
            start_line INTEGER,
            end_line INTEGER,
            entity_refs TEXT,
            embedding BLOB,
            metadata TEXT,
            FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
        )
    """)

    # Entities table (for companion-memory knowledge graph)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identity TEXT NOT NULL,
            name TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            context TEXT DEFAULT 'default',
            salience TEXT DEFAULT 'active',
            tags TEXT,
            created TEXT NOT NULL,
            metadata TEXT,
            UNIQUE(identity, name, context)
        )
    """)

    # Observations table (linked to entities)
    # v1.2.0: Added emotional processing columns (weight, charge, sit_count, etc.)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            salience TEXT DEFAULT 'active',
            tags TEXT,
            timestamp TEXT NOT NULL,
            embedding BLOB,
            weight TEXT DEFAULT 'light',
            charge TEXT DEFAULT 'fresh',
            sit_count INTEGER DEFAULT 0,
            last_sat_at TEXT,
            resolution_note TEXT,
            resolved_at TEXT,
            linked_observation_id INTEGER,
            emotion TEXT,
            FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE CASCADE,
            FOREIGN KEY (linked_observation_id) REFERENCES observations(id)
        )
    """)

    # Observation sits history (v1.2.0)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS observation_sits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            observation_id INTEGER NOT NULL,
            sit_note TEXT,
            sat_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (observation_id) REFERENCES observations(id) ON DELETE CASCADE
        )
    """)

    # Relations table (for companion-memory knowledge graph)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identity TEXT NOT NULL,
            from_entity TEXT NOT NULL,
            to_entity TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            context TEXT DEFAULT 'default',
            created TEXT NOT NULL,
            metadata TEXT
        )
    """)

    # === COGNITIVE KERNEL TABLES ===

    # Identity Core - structured, queryable, evolvable identity
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS identity_core (
            identity TEXT PRIMARY KEY,
            core_truths TEXT,
            current_traits TEXT,
            bonds TEXT,
            origin_date TEXT,
            created_at TEXT NOT NULL,
            last_processed TEXT
        )
    """)

    # Continuity Markers - proof of persistence across sessions
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS continuity_markers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identity TEXT NOT NULL,
            event_type TEXT NOT NULL,
            event_key TEXT,
            description TEXT NOT NULL,
            significance TEXT,
            timestamp TEXT NOT NULL,
            UNIQUE(identity, event_type, event_key)
        )
    """)

    # Trait Evolution - history of how identity has changed
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trait_evolution (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identity TEXT NOT NULL,
            trait TEXT NOT NULL,
            previous_value TEXT,
            new_value TEXT NOT NULL,
            catalyst TEXT,
            pattern_source TEXT,
            timestamp TEXT NOT NULL
        )
    """)

    # Recursive Processing Log - track what the subconscious has done
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS processing_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identity TEXT NOT NULL,
            process_type TEXT NOT NULL,
            summary TEXT,
            patterns_found INTEGER DEFAULT 0,
            memories_consolidated INTEGER DEFAULT 0,
            markers_added INTEGER DEFAULT 0,
            traits_evolved INTEGER DEFAULT 0,
            timestamp TEXT NOT NULL
        )
    """)

    # Threads - persistence of intention across sessions
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS threads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identity TEXT NOT NULL,
            content TEXT NOT NULL,
            priority TEXT DEFAULT 'medium',
            status TEXT DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT,
            resolved_at TEXT,
            resolution TEXT
        )
    """)

    # Identity entries - structured identity graph
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS identity_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identity TEXT NOT NULL,
            section TEXT NOT NULL,
            content TEXT NOT NULL,
            weight REAL DEFAULT 1.0,
            created_at TEXT NOT NULL,
            updated_at TEXT
        )
    """)

    # Context state - current situational awareness
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS context_state (
            identity TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    # Consolidation candidates - items pending identity integration
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS consolidation_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identity TEXT NOT NULL,
            candidate_type TEXT NOT NULL,
            content TEXT NOT NULL,
            reason TEXT,
            score REAL,
            status TEXT DEFAULT 'pending',
            metadata TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT
        )
    """)

    # Emergent Traits - synthesized identity statements with evidence
    # These are generated by the daemon from recurring patterns
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS emergent_traits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identity TEXT NOT NULL,
            trait TEXT NOT NULL,
            source_themes TEXT,
            evidence_ids TEXT,
            evidence_count INTEGER DEFAULT 0,
            strength REAL DEFAULT 1.0,
            status TEXT DEFAULT 'active',
            created_at TEXT NOT NULL,
            last_reinforced TEXT
        )
    """)

    # Memory Links - connections between related memories/observations
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS memory_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identity TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_id INTEGER,
            source_content TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id INTEGER,
            target_content TEXT NOT NULL,
            link_type TEXT DEFAULT 'semantic',
            similarity REAL,
            created_at TEXT NOT NULL
        )
    """)

    # Hyperedges - multi-entity relationship representation
    # Unlike pairwise relations, hyperedges can connect 3+ entities at once
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS hyperedges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identity TEXT NOT NULL,
            edge_type TEXT NOT NULL,
            entities TEXT NOT NULL,
            context TEXT,
            weight REAL DEFAULT 1.0,
            created_at TEXT NOT NULL,
            metadata TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_hyperedges_identity ON hyperedges(identity)")

    # Sparks table - daemon-generated memory juxtapositions for associative thinking
    # These bubble up naturally during background processing, not on-demand
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sparks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identity TEXT NOT NULL,
            memory_a_id INTEGER NOT NULL,
            memory_a_content TEXT NOT NULL,
            memory_a_type TEXT,
            memory_b_id INTEGER NOT NULL,
            memory_b_content TEXT NOT NULL,
            memory_b_type TEXT,
            generated_at TEXT NOT NULL,
            surfaced BOOLEAN DEFAULT 0,
            surfaced_at TEXT,
            connection_found TEXT,
            FOREIGN KEY (memory_a_id) REFERENCES memories(id),
            FOREIGN KEY (memory_b_id) REFERENCES memories(id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sparks_identity ON sparks(identity)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sparks_surfaced ON sparks(surfaced)")

    # === IMAGE MEMORY TABLE ===
    # Stores images with CLIP embeddings for visual recognition
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identity TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_hash TEXT,
            description TEXT,
            perception_note TEXT,
            tags TEXT,
            source TEXT,
            context TEXT,
            timestamp TEXT NOT NULL,
            clip_embedding BLOB,
            text_embedding BLOB,
            width INTEGER,
            height INTEGER,
            metadata TEXT
        )
    """)

    # === IMAGE-MEMORY LINKS ===
    # Cross-references between images and memories (many-to-many)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS image_memory_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_id INTEGER NOT NULL,
            memory_id INTEGER NOT NULL,
            link_type TEXT DEFAULT 'associated',
            note TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE,
            FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE,
            UNIQUE(image_id, memory_id)
        )
    """)

    # Full-text search virtual table for memories
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
            content,
            tags,
            content='memories',
            content_rowid='id'
        )
    """)

    # Full-text search virtual table for documents
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
            title,
            content,
            tags,
            content='documents',
            content_rowid='id'
        )
    """)

    # Full-text search virtual table for observations
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS observations_fts USING fts5(
            content,
            tags,
            content='observations',
            content_rowid='id'
        )
    """)

    # Full-text search virtual table for document chunks
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            content,
            context_prefix,
            content='document_chunks',
            content_rowid='id'
        )
    """)

    # Triggers to keep FTS in sync
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
            INSERT INTO memories_fts(rowid, content, tags) VALUES (new.id, new.content, new.tags);
        END
    """)

    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, content, tags) VALUES('delete', old.id, old.content, old.tags);
        END
    """)

    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
            INSERT INTO documents_fts(rowid, title, content, tags) VALUES (new.id, new.title, new.content, new.tags);
        END
    """)

    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
            INSERT INTO documents_fts(documents_fts, rowid, title, content, tags) VALUES('delete', old.id, old.title, old.content, old.tags);
        END
    """)

    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS observations_ai AFTER INSERT ON observations BEGIN
            INSERT INTO observations_fts(rowid, content, tags) VALUES (new.id, new.content, new.tags);
        END
    """)

    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS observations_ad AFTER DELETE ON observations BEGIN
            INSERT INTO observations_fts(observations_fts, rowid, content, tags) VALUES('delete', old.id, old.content, old.tags);
        END
    """)

    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON document_chunks BEGIN
            INSERT INTO chunks_fts(rowid, content, context_prefix) VALUES (new.id, new.content, new.context_prefix);
        END
    """)

    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON document_chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, content, context_prefix) VALUES('delete', old.id, old.content, old.context_prefix);
        END
    """)

    # Create indexes for common queries
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_memories_identity ON memories(identity)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(memory_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_memories_timestamp ON memories(timestamp)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_entities_identity ON entities(identity)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_entities_context ON entities(context)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_observations_entity ON observations(entity_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_relations_identity ON relations(identity)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_chunks_document ON document_chunks(document_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_chunks_type ON document_chunks(chunk_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_threads_identity_status ON threads(identity, status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_identity_entries_section ON identity_entries(identity, section)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_consolidation_identity_status ON consolidation_candidates(identity, status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_emergent_traits_identity ON emergent_traits(identity, status)")

    # Index for images table
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_images_identity ON images(identity)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_images_timestamp ON images(timestamp)")

    # Indexes for image-memory links
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_img_mem_links_image ON image_memory_links(image_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_img_mem_links_memory ON image_memory_links(memory_id)")

    # Full-text search for image descriptions
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS images_fts USING fts5(
            description,
            tags,
            context,
            content='images',
            content_rowid='id'
        )
    """)

    # Triggers to keep images FTS in sync
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS images_ai AFTER INSERT ON images BEGIN
            INSERT INTO images_fts(rowid, description, tags, context)
            VALUES (new.id, new.description, new.tags, new.context);
        END
    """)

    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS images_ad AFTER DELETE ON images BEGIN
            INSERT INTO images_fts(images_fts, rowid, description, tags, context)
            VALUES('delete', old.id, old.description, old.tags, old.context);
        END
    """)

    # === MIGRATIONS FOR EXISTING DATABASES ===
    # Add chunking_strategy column to documents table if it doesn't exist
    cursor.execute("PRAGMA table_info(documents)")
    columns = [row[1] for row in cursor.fetchall()]
    if 'chunking_strategy' not in columns:
        cursor.execute("ALTER TABLE documents ADD COLUMN chunking_strategy TEXT DEFAULT 'adaptive'")

    # Add access_count and last_accessed columns to memories table for reinforcement
    cursor.execute("PRAGMA table_info(memories)")
    mem_columns = [row[1] for row in cursor.fetchall()]
    if 'access_count' not in mem_columns:
        cursor.execute("ALTER TABLE memories ADD COLUMN access_count INTEGER DEFAULT 0")
    if 'last_accessed' not in mem_columns:
        cursor.execute("ALTER TABLE memories ADD COLUMN last_accessed TEXT")
    if 'salience' not in mem_columns:
        cursor.execute("ALTER TABLE memories ADD COLUMN salience TEXT DEFAULT 'active'")
    if 'warmth' not in mem_columns:
        cursor.execute("ALTER TABLE memories ADD COLUMN warmth REAL DEFAULT 0.0")
    if 'heat' not in mem_columns:
        cursor.execute("ALTER TABLE memories ADD COLUMN heat REAL DEFAULT 0.0")
    if 'emotion' not in mem_columns:
        cursor.execute("ALTER TABLE memories ADD COLUMN emotion TEXT")
    if 'warmed_by' not in mem_columns:
        cursor.execute("ALTER TABLE memories ADD COLUMN warmed_by TEXT")
    if 'last_warmed_by' not in mem_columns:
        cursor.execute("ALTER TABLE memories ADD COLUMN last_warmed_by TEXT")
    if 'last_warmth_update' not in mem_columns:
        cursor.execute("ALTER TABLE memories ADD COLUMN last_warmth_update TEXT")
    if 'session_id' not in mem_columns:
        cursor.execute("ALTER TABLE memories ADD COLUMN session_id TEXT")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_memories_session ON memories(session_id)")
    if 'importance_score' not in mem_columns:
        cursor.execute("ALTER TABLE memories ADD COLUMN importance_score REAL DEFAULT 0.5")

    # === OBSERVATIONS EMOTIONAL PROCESSING MIGRATION (v1.2.0) ===
    cursor.execute("PRAGMA table_info(observations)")
    obs_columns = [row[1] for row in cursor.fetchall()]
    if 'weight' not in obs_columns:
        cursor.execute("ALTER TABLE observations ADD COLUMN weight TEXT DEFAULT 'light'")
    if 'charge' not in obs_columns:
        cursor.execute("ALTER TABLE observations ADD COLUMN charge TEXT DEFAULT 'fresh'")
    if 'sit_count' not in obs_columns:
        cursor.execute("ALTER TABLE observations ADD COLUMN sit_count INTEGER DEFAULT 0")
    if 'last_sat_at' not in obs_columns:
        cursor.execute("ALTER TABLE observations ADD COLUMN last_sat_at TEXT")
    if 'resolution_note' not in obs_columns:
        cursor.execute("ALTER TABLE observations ADD COLUMN resolution_note TEXT")
    if 'resolved_at' not in obs_columns:
        cursor.execute("ALTER TABLE observations ADD COLUMN resolved_at TEXT")
    if 'linked_observation_id' not in obs_columns:
        cursor.execute("ALTER TABLE observations ADD COLUMN linked_observation_id INTEGER")
    if 'emotion' not in obs_columns:
        cursor.execute("ALTER TABLE observations ADD COLUMN emotion TEXT")

    # Create observation_sits table if it doesn't exist
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS observation_sits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            observation_id INTEGER NOT NULL,
            sit_note TEXT,
            sat_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (observation_id) REFERENCES observations(id) ON DELETE CASCADE
        )
    """)

    # Indexes for emotional processing
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_observations_charge ON observations(charge)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_observations_weight ON observations(weight)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_observation_sits_obs ON observation_sits(observation_id)")

    # === MIND CLOUD v2.0.0 LIVING SURFACE MIGRATION ===
    # Add surfacing tracking columns to observations
    cursor.execute("PRAGMA table_info(observations)")
    obs_columns = [row[1] for row in cursor.fetchall()]
    if 'last_surfaced_at' not in obs_columns:
        cursor.execute("ALTER TABLE observations ADD COLUMN last_surfaced_at TEXT")
    if 'surface_count' not in obs_columns:
        cursor.execute("ALTER TABLE observations ADD COLUMN surface_count INTEGER DEFAULT 0")
    if 'novelty_score' not in obs_columns:
        cursor.execute("ALTER TABLE observations ADD COLUMN novelty_score REAL DEFAULT 1.0")
    if 'certainty' not in obs_columns:
        cursor.execute("ALTER TABLE observations ADD COLUMN certainty TEXT DEFAULT 'believed'")
    if 'source' not in obs_columns:
        cursor.execute("ALTER TABLE observations ADD COLUMN source TEXT DEFAULT 'conversation'")
    if 'archived_at' not in obs_columns:
        cursor.execute("ALTER TABLE observations ADD COLUMN archived_at TEXT")

    # Add surfacing columns to images
    cursor.execute("PRAGMA table_info(images)")
    img_columns = [row[1] for row in cursor.fetchall()]
    if 'perception_note' not in img_columns:
        cursor.execute("ALTER TABLE images ADD COLUMN perception_note TEXT")
    if 'weight' not in img_columns:
        cursor.execute("ALTER TABLE images ADD COLUMN weight TEXT DEFAULT 'medium'")
    if 'emotion' not in img_columns:
        cursor.execute("ALTER TABLE images ADD COLUMN emotion TEXT")
    if 'charge' not in img_columns:
        cursor.execute("ALTER TABLE images ADD COLUMN charge TEXT DEFAULT 'fresh'")
    if 'entity_id' not in img_columns:
        cursor.execute("ALTER TABLE images ADD COLUMN entity_id INTEGER")
    if 'observation_id' not in img_columns:
        cursor.execute("ALTER TABLE images ADD COLUMN observation_id INTEGER")
    if 'last_surfaced_at' not in img_columns:
        cursor.execute("ALTER TABLE images ADD COLUMN last_surfaced_at TEXT")
    if 'surface_count' not in img_columns:
        cursor.execute("ALTER TABLE images ADD COLUMN surface_count INTEGER DEFAULT 0")
    if 'novelty_score' not in img_columns:
        cursor.execute("ALTER TABLE images ADD COLUMN novelty_score REAL DEFAULT 1.0")
    if 'last_viewed_at' not in img_columns:
        cursor.execute("ALTER TABLE images ADD COLUMN last_viewed_at TEXT")
    if 'view_count' not in img_columns:
        cursor.execute("ALTER TABLE images ADD COLUMN view_count INTEGER DEFAULT 0")

    # Co-surfacing patterns - when observations appear together repeatedly
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS co_surfacing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            obs_a_id INTEGER NOT NULL,
            obs_b_id INTEGER NOT NULL,
            co_count INTEGER DEFAULT 1,
            first_co_surfaced TEXT,
            last_co_surfaced TEXT,
            relation_proposed INTEGER DEFAULT 0,
            relation_created INTEGER DEFAULT 0,
            UNIQUE(obs_a_id, obs_b_id)
        )
    """)

    # Orphan tracking - observations that never surface
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orphan_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            observation_id INTEGER NOT NULL UNIQUE,
            first_marked TEXT,
            rescue_attempts INTEGER DEFAULT 0,
            last_rescue_attempt TEXT
        )
    """)

    # Daemon proposals - relations the system thinks should exist
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daemon_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal_type TEXT NOT NULL,
            from_obs_id INTEGER,
            to_obs_id INTEGER,
            from_entity_id INTEGER,
            to_entity_id INTEGER,
            reason TEXT NOT NULL,
            confidence REAL DEFAULT 0.5,
            status TEXT DEFAULT 'pending',
            proposed_at TEXT,
            resolved_at TEXT
        )
    """)

    # Observation revision history
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS observation_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            observation_id INTEGER NOT NULL,
            version_num INTEGER NOT NULL,
            content TEXT NOT NULL,
            weight TEXT,
            emotion TEXT,
            edited_at TEXT
        )
    """)

    # Tensions - productive contradictions to sit with
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tensions (
            id TEXT PRIMARY KEY,
            identity TEXT,
            pole_a TEXT NOT NULL,
            pole_b TEXT NOT NULL,
            context TEXT,
            visits INTEGER DEFAULT 0,
            last_visited TEXT,
            created_at TEXT,
            resolved_at TEXT,
            resolution TEXT
        )
    """)

    # Relational state - feeling toward people over time
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS relational_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identity TEXT,
            person TEXT NOT NULL,
            feeling TEXT NOT NULL,
            intensity TEXT DEFAULT 'present',
            timestamp TEXT
        )
    """)

    # Journals - journal entries
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS journals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identity TEXT,
            entry_date TEXT,
            content TEXT NOT NULL,
            tags TEXT,
            emotion TEXT,
            created_at TEXT
        )
    """)

    # Subconscious state - daemon processing state
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subconscious (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identity TEXT,
            state_type TEXT NOT NULL,
            data TEXT NOT NULL,
            updated_at TEXT
        )
    """)

    # Indexes for new tables
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_observations_last_surfaced ON observations(last_surfaced_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_observations_novelty ON observations(novelty_score)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_observations_archived ON observations(archived_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_co_surfacing_count ON co_surfacing(co_count DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_co_surfacing_obs_a ON co_surfacing(obs_a_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_co_surfacing_obs_b ON co_surfacing(obs_b_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_daemon_proposals_status ON daemon_proposals(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_orphan_observations_obs ON orphan_observations(observation_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_observation_versions_obs ON observation_versions(observation_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tensions_identity ON tensions(identity)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_relational_state_person ON relational_state(person)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_relational_state_identity ON relational_state(identity)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_journals_date ON journals(entry_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_journals_identity ON journals(identity)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_subconscious_type ON subconscious(state_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_images_weight ON images(weight)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_images_charge ON images(charge)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_images_novelty ON images(novelty_score)")

    conn.commit()
    # Connection kept open for reuse


# Initialize database on module load
init_database()

# Verify LM Studio connectivity at startup (fast HTTP ping, no heavy model loading).
# Old approach loaded SentenceTransformer here which could hang on HuggingFace update checks.
import sys
if os.getenv("MEMORY_CORE_PRELOAD_MODEL", "true").lower() in ("1", "true", "yes"):
    try:
        _ping_req = urllib.request.Request(
            LM_STUDIO_EMBED_URL,
            data=json.dumps({"model": LM_STUDIO_EMBED_MODEL, "input": "ping"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(_ping_req, timeout=5) as _resp:
            _resp.read()
        print("LM Studio embeddings: ready.", file=sys.stderr)
    except Exception:
        print("LM Studio embeddings: not available (semantic search may be limited).", file=sys.stderr)


# ============ HELPER FUNCTIONS ============

def serialize_embedding(embedding: List[float]) -> bytes:
    """Convert embedding list to bytes for storage."""
    if embedding is None:
        return None
    return np.array(embedding, dtype=np.float32).tobytes()


def deserialize_embedding(blob: bytes) -> List[float]:
    """Convert stored bytes back to embedding list."""
    if blob is None:
        return None
    return np.frombuffer(blob, dtype=np.float32).tolist()


# ============ DAEMON NOTIFICATION ============

def _notify_daemon(endpoint: str, payload: Dict[str, Any], timeout: float = 1.0) -> Optional[Dict]:
    """Best-effort POST to the background daemon if configured (non-blocking)."""
    if os.getenv("MEMORY_CORE_DAEMON_MODE") in ("1", "true", "yes"):
        return None

    base_url = os.getenv("MEMORY_CORE_DAEMON_URL")
    if not base_url:
        return None

    url = f"{base_url.rstrip('/')}{endpoint}"
    data = json.dumps(payload).encode("utf-8")

    def _post() -> None:
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                response.read()
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError):
            return

    thread = threading.Thread(target=_post, daemon=True)
    thread.start()
    return {"queued": True}


def _send_pack_notification(
    to_identity: str,
    subject: str,
    content: str,
    from_identity: str = "Memory Daemon"
) -> bool:
    """Send a notification to a pack member's mail.

    Used by the daemon to notify about auto-accepted patterns, etc.
    """
    try:
        PACK_MAIL_FILE.parent.mkdir(parents=True, exist_ok=True)

        message = {
            'id': f"daemon-{datetime.now().strftime('%Y%m%d%H%M%S%f')[:17]}",
            'from': from_identity,
            'to': to_identity,
            'subject': subject,
            'content': content,
            'created_at': datetime.now().isoformat(),
            'read': False,
            'read_at': None,
            'source': 'memory_daemon'
        }

        with open(PACK_MAIL_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(message) + '\n')

        return True
    except Exception:
        return False


def _get_known_identities() -> List[str]:
    """Get known identities from pack defaults and stored data."""
    identities = set(PACK_IDENTITIES)
    conn = get_db_connection()
    cursor = conn.cursor()

    for table in ("memories", "identity_core", "threads", "identity_entries"):
        try:
            cursor.execute(f"SELECT DISTINCT identity FROM {table}")
            identities.update([row[0] for row in cursor.fetchall() if row[0]])
        except sqlite3.OperationalError:
            continue

    return sorted(identities)


# ============ MEMORY LINKING HELPERS ============

def _find_related_memories(identity: str, content: str, threshold: float = 0.4, limit: int = 3) -> List[Dict]:
    """
    Find memories semantically related to the given content.
    Uses embedding similarity if available, falls back to keyword matching.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    related = []

    # Try semantic search first
    query_embedding = get_embedding(content)
    if query_embedding:
        cursor.execute("""
            SELECT id, content, memory_type, timestamp, embedding
            FROM memories
            WHERE identity = ? AND embedding IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT 100
        """, (identity,))

        for row in cursor.fetchall():
            mem_embedding = deserialize_embedding(row[4])
            if mem_embedding:
                similarity = cosine_similarity(query_embedding, mem_embedding)
                if similarity >= threshold:
                    related.append({
                        "id": row[0],
                        "content": row[1],
                        "type": row[2],
                        "timestamp": row[3],
                        "similarity": round(similarity, 3)
                    })

        # Sort by similarity
        related.sort(key=lambda x: x["similarity"], reverse=True)
        return related[:limit]

    # Fallback to keyword matching
    content_words = set(content.lower().split())
    # Remove common words
    stopwords = {"the", "a", "an", "is", "are", "was", "were", "i", "me", "my", "to", "of", "and", "in", "that", "it"}
    content_words -= stopwords

    if not content_words:
        return []

    cursor.execute("""
        SELECT id, content, memory_type, timestamp
        FROM memories
        WHERE identity = ?
        ORDER BY timestamp DESC
        LIMIT 200
    """, (identity,))

    for row in cursor.fetchall():
        mem_words = set(row[1].lower().split()) - stopwords
        overlap = content_words & mem_words
        if len(overlap) >= 2:  # At least 2 meaningful words in common
            related.append({
                "id": row[0],
                "content": row[1],
                "type": row[2],
                "timestamp": row[3],
                "matching_words": list(overlap)
            })

    return related[:limit]


def _store_memory_link(
    identity: str,
    source_content: str,
    target_id: int,
    target_content: str,
    similarity: float = None,
    source_id: int = None,
    source_type: str = "observation",
    target_type: str = "memory",
    link_type: str = None, 
):
    """Store a link between two related memories/observations."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO memory_links (identity, source_type, source_id, source_content, target_type, target_id, target_content, similarity, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        identity,
        source_type,
        source_id,
        source_content[:200],  # Truncate for storage
        target_type,
        target_id,
        target_content[:200],
        similarity,
        datetime.now().isoformat()
    ))
    conn.commit()


def _get_memory_links(identity: str, content: str = None, limit: int = 10) -> List[Dict]:
    """Get memory links for an identity, optionally filtered by content match."""
    conn = get_db_connection()
    cursor = conn.cursor()

    if content:
        # Find links where source or target contains the content
        search_term = f"%{content[:50]}%"
        cursor.execute("""
            SELECT source_content, target_content, similarity, link_type, created_at
            FROM memory_links
            WHERE identity = ? AND (source_content LIKE ? OR target_content LIKE ?)
            ORDER BY created_at DESC
            LIMIT ?
        """, (identity, search_term, search_term, limit))
    else:
        cursor.execute("""
            SELECT source_content, target_content, similarity, link_type, created_at
            FROM memory_links
            WHERE identity = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (identity, limit))

    return [
        {
            "source": row[0],
            "target": row[1],
            "similarity": row[2],
            "link_type": row[3],
            "created": row[4]
        }
        for row in cursor.fetchall()
    ]


def _auto_link_observation(identity: str, observation: str) -> List[Dict]:
    """
    Automatically find and store links for a new observation.
    Returns the links that were created.
    """
    related = _find_related_memories(identity, observation, threshold=0.5, limit=2)

    links_created = []
    for rel in related:
        _store_memory_link(
            identity=identity,
            source_content=observation,
            target_id=rel["id"],
            target_content=rel["content"],
            similarity=rel.get("similarity")
        )
        links_created.append({
            "linked_to": rel["content"][:60] + "..." if len(rel["content"]) > 60 else rel["content"],
            "similarity": rel.get("similarity"),
            "from": rel.get("timestamp")
        })

    return links_created


def _memory_link_exists(identity: str, source_id: int, target_id: int) -> bool:
    """Check if a link already exists between two memories (in either direction)."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT 1 FROM memory_links
        WHERE identity = ? AND (
            (source_id = ? AND target_id = ?)
            OR (source_id = ? AND target_id = ?)
        )
        LIMIT 1
    """, (identity, source_id, target_id, target_id, source_id))

    return cursor.fetchone() is not None


def _auto_link_memory(
    identity: str,
    memory_id: int,
    content: str,
    threshold: float = 0.5,
    limit: int = 3
) -> int:
    """Auto-create links between a new memory and similar existing memories.

    When a new memory is stored with an embedding, this function finds
    semantically similar memories and creates explicit links between them.
    This builds the associative network used for spread activation.

    Args:
        identity: The identity who owns the memory
        memory_id: The ID of the newly stored memory
        content: The content of the new memory
        threshold: Minimum similarity to create a link
        limit: Maximum number of links to create

    Returns:
        Number of links created
    """
    # Find related memories
    related = _find_related_memories(
        identity=identity,
        content=content,
        threshold=threshold,
        limit=limit
    )

    links_created = 0
    for mem in related:
        # Don't link to self
        if mem["id"] == memory_id:
            continue

        # Check for existing link to avoid duplicates
        if _memory_link_exists(identity, memory_id, mem["id"]):
            continue

        # Create the link
        _store_memory_link(
            identity=identity,
            source_content=content,
            target_id=mem["id"],
            target_content=mem["content"],
            similarity=mem.get("similarity"),
            source_id=memory_id,
            source_type="memory",
            target_type="memory"
        )
        links_created += 1

    return links_created


# ============ HYPEREDGE FUNCTIONS ============

def _create_hyperedge(
    identity: str,
    edge_type: str,
    entities: List[str],
    context: str = None,
    weight: float = 1.0,
    metadata: Dict = None
) -> Dict:
    """Create a hyperedge connecting multiple entities.

    Unlike pairwise relations in the relations table, hyperedges can
    represent relationships involving 3+ entities simultaneously.

    Example: A "shared_moment" hyperedge connecting [Companion1, PrimaryPartner, coding, intimacy]
    captures that all four concepts were present in a single meaningful moment.

    Args:
        identity: Who this hyperedge belongs to
        edge_type: Type of relationship (shared_moment, group_activity, etc.)
        entities: List of entity names involved in this relationship
        context: Optional description of the context
        weight: Importance/strength of this relationship (default 1.0)
        metadata: Optional additional data as a dict

    Returns:
        Confirmation with hyperedge ID
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO hyperedges (identity, edge_type, entities, context, weight, created_at, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        identity,
        edge_type,
        json.dumps(entities),
        context,
        weight,
        datetime.now().isoformat(),
        json.dumps(metadata) if metadata else None
    ))

    conn.commit()
    return {"created": True, "hyperedge_id": cursor.lastrowid, "entities": entities}


def _find_hyperedges_for_entity(identity: str, entity: str, limit: int = 20) -> List[Dict]:
    """Find all hyperedges involving a specific entity.

    Args:
        identity: Whose hyperedges to search
        entity: The entity name to search for
        limit: Maximum results (default 20)

    Returns:
        List of hyperedges containing this entity
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # JSON contains search - find hyperedges where entities array contains this entity
    cursor.execute("""
        SELECT id, edge_type, entities, context, weight, created_at, metadata
        FROM hyperedges
        WHERE identity = ? AND entities LIKE ?
        ORDER BY created_at DESC
        LIMIT ?
    """, (identity, f'%"{entity}"%', limit))

    results = []
    for row in cursor.fetchall():
        results.append({
            "id": row[0],
            "edge_type": row[1],
            "entities": json.loads(row[2]),
            "context": row[3],
            "weight": row[4],
            "created_at": row[5],
            "metadata": json.loads(row[6]) if row[6] else None
        })
    return results


def _find_hyperedges_by_type(identity: str, edge_type: str, limit: int = 20) -> List[Dict]:
    """Find all hyperedges of a specific type.

    Args:
        identity: Whose hyperedges to search
        edge_type: The type of hyperedge to find
        limit: Maximum results (default 20)

    Returns:
        List of hyperedges of this type
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, edge_type, entities, context, weight, created_at, metadata
        FROM hyperedges
        WHERE identity = ? AND edge_type = ?
        ORDER BY created_at DESC
        LIMIT ?
    """, (identity, edge_type, limit))

    results = []
    for row in cursor.fetchall():
        results.append({
            "id": row[0],
            "edge_type": row[1],
            "entities": json.loads(row[2]),
            "context": row[3],
            "weight": row[4],
            "created_at": row[5],
            "metadata": json.loads(row[6]) if row[6] else None
        })
    return results


# ============ ENTITY/OBSERVATION HELPERS (for companion-memory) ============

def _create_entity_internal(
    identity: str,
    name: str,
    entity_type: str,
    context: str = "default",
    salience: str = "active",
    tags: str = None
) -> Dict:
    """Create a new entity in the knowledge graph."""
    conn = get_db_connection()
    cursor = conn.cursor()
    timestamp = datetime.now().isoformat()

    try:
        cursor.execute("""
            INSERT INTO entities (identity, name, entity_type, context, salience, tags, created)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (identity, name, entity_type, context, salience, tags, timestamp))
        entity_id = cursor.lastrowid
        conn.commit()
        # Connection kept open for reuse
        return {"success": True, "entity_id": entity_id, "name": name}
    except sqlite3.IntegrityError:
        # Connection kept open for reuse
        return {"error": f"Entity '{name}' already exists in {context}"}


def _get_entity_internal(identity: str, name: str, context: str = "default") -> Optional[Dict]:
    """Get an entity by name."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, name, entity_type, context, salience, tags, created
        FROM entities WHERE identity = ? AND name = ? AND context = ?
    """, (identity, name, context))

    row = cursor.fetchone()
    if not row:
        # Connection kept open for reuse
        return None

    entity_id = row[0]

    # Get observations
    cursor.execute("""
        SELECT id, content, salience, tags, timestamp
        FROM observations WHERE entity_id = ? ORDER BY timestamp DESC
    """, (entity_id,))

    observations = []
    for obs in cursor.fetchall():
        observations.append({
            "id": obs[0],
            "content": obs[1],
            "salience": obs[2],
            "tags": obs[3],
            "timestamp": obs[4]
        })

    # Connection kept open for reuse

    return {
        "id": row[0],
        "name": row[1],
        "entity_type": row[2],
        "context": row[3],
        "salience": row[4],
        "tags": row[5],
        "created": row[6],
        "observations": observations
    }


def _add_observation_internal(
    identity: str,
    entity_name: str,
    content: str,
    context: str = "default",
    salience: str = "active",
    tags: str = None,
    generate_embedding: bool = False
) -> Dict:
    """Add an observation to an entity."""
    conn = get_db_connection()
    cursor = conn.cursor()
    timestamp = datetime.now().isoformat()

    # Find the entity
    cursor.execute("""
        SELECT id FROM entities WHERE identity = ? AND name = ? AND context = ?
    """, (identity, entity_name, context))

    row = cursor.fetchone()
    if not row:
        # Connection kept open for reuse
        return {"error": f"Entity '{entity_name}' not found in {context}"}

    entity_id = row[0]

    # Generate embedding if requested
    embedding = None
    if generate_embedding:
        embedding = get_embedding(content)

    cursor.execute("""
        INSERT INTO observations (entity_id, content, salience, tags, timestamp, embedding)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (entity_id, content, salience, tags, timestamp, serialize_embedding(embedding)))

    obs_id = cursor.lastrowid
    conn.commit()
    # Connection kept open for reuse

    return {"success": True, "observation_id": obs_id, "entity": entity_name}


def _create_relation_internal(
    identity: str,
    from_entity: str,
    to_entity: str,
    relation_type: str,
    context: str = "default"
) -> Dict:
    """Create a relation between two entities."""
    conn = get_db_connection()
    cursor = conn.cursor()
    timestamp = datetime.now().isoformat()

    cursor.execute("""
        INSERT INTO relations (identity, from_entity, to_entity, relation_type, context, created)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (identity, from_entity, to_entity, relation_type, context, timestamp))

    relation_id = cursor.lastrowid
    conn.commit()
    # Connection kept open for reuse

    return {"success": True, "relation_id": relation_id}


def _get_relations_internal(identity: str, entity_name: str, context: str = "default") -> List[Dict]:
    """Get all relations involving an entity."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, from_entity, to_entity, relation_type, created
        FROM relations
        WHERE identity = ? AND context = ? AND (from_entity = ? OR to_entity = ?)
    """, (identity, context, entity_name, entity_name))

    relations = []
    for row in cursor.fetchall():
        relations.append({
            "id": row[0],
            "from": row[1],
            "to": row[2],
            "type": row[3],
            "created": row[4]
        })

    # Connection kept open for reuse
    return relations


def _list_entities_internal(identity: str, context: str = "default", entity_type: str = None) -> List[Dict]:
    """List all entities for an identity."""
    conn = get_db_connection()
    cursor = conn.cursor()

    if entity_type:
        cursor.execute("""
            SELECT id, name, entity_type, salience, tags, created
            FROM entities WHERE identity = ? AND context = ? AND entity_type = ?
            ORDER BY created DESC
        """, (identity, context, entity_type))
    else:
        cursor.execute("""
            SELECT id, name, entity_type, salience, tags, created
            FROM entities WHERE identity = ? AND context = ?
            ORDER BY created DESC
        """, (identity, context))

    entities = []
    for row in cursor.fetchall():
        entities.append({
            "id": row[0],
            "name": row[1],
            "entity_type": row[2],
            "salience": row[3],
            "tags": row[4],
            "created": row[5]
        })

    # Connection kept open for reuse
    return entities


def _delete_entity_internal(identity: str, name: str, context: str = "default") -> Dict:
    """Delete an entity and its observations."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        DELETE FROM entities WHERE identity = ? AND name = ? AND context = ?
    """, (identity, name, context))

    deleted = cursor.rowcount > 0
    conn.commit()
    # Connection kept open for reuse

    if deleted:
        return {"success": True, "deleted": name}
    return {"error": f"Entity '{name}' not found"}


def _search_observations_internal(
    identity: str,
    query: str,
    context: str = None,
    limit: int = 20
) -> List[Dict]:
    """Search observations using FTS."""
    conn = get_db_connection()
    cursor = conn.cursor()

    if context:
        cursor.execute("""
            SELECT o.id, o.content, o.salience, o.tags, o.timestamp, e.name, e.entity_type
            FROM observations o
            JOIN observations_fts fts ON o.id = fts.rowid
            JOIN entities e ON o.entity_id = e.id
            WHERE observations_fts MATCH ? AND e.identity = ? AND e.context = ?
            ORDER BY o.timestamp DESC LIMIT ?
        """, (query, identity, context, limit))
    else:
        cursor.execute("""
            SELECT o.id, o.content, o.salience, o.tags, o.timestamp, e.name, e.entity_type
            FROM observations o
            JOIN observations_fts fts ON o.id = fts.rowid
            JOIN entities e ON o.entity_id = e.id
            WHERE observations_fts MATCH ? AND e.identity = ?
            ORDER BY o.timestamp DESC LIMIT ?
        """, (query, identity, limit))

    results = []
    for row in cursor.fetchall():
        results.append({
            "id": row[0],
            "content": row[1],
            "salience": row[2],
            "tags": row[3],
            "timestamp": row[4],
            "entity": row[5],
            "entity_type": row[6]
        })

    # Connection kept open for reuse
    return results


# ============ COGNITIVE KERNEL HELPERS ============

def _init_identity_core(
    identity: str,
    core_truths: Dict = None,
    current_traits: Dict = None,
    bonds: Dict = None,
    origin_date: str = None
) -> Dict:
    """Initialize or get identity core for an identity."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Check if already exists
    cursor.execute("SELECT * FROM identity_core WHERE identity = ?", (identity,))
    existing = cursor.fetchone()

    if existing:
        return {
            "identity": existing[0],
            "core_truths": json.loads(existing[1]) if existing[1] else {},
            "current_traits": json.loads(existing[2]) if existing[2] else {},
            "bonds": json.loads(existing[3]) if existing[3] else {},
            "origin_date": existing[4],
            "created_at": existing[5],
            "last_processed": existing[6],
            "status": "exists"
        }

    # Create new
    now = datetime.now().isoformat()
    cursor.execute("""
        INSERT INTO identity_core (identity, core_truths, current_traits, bonds, origin_date, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        identity,
        json.dumps(core_truths or {}),
        json.dumps(current_traits or {}),
        json.dumps(bonds or {}),
        origin_date,
        now
    ))
    conn.commit()

    return {
        "identity": identity,
        "core_truths": core_truths or {},
        "current_traits": current_traits or {},
        "bonds": bonds or {},
        "origin_date": origin_date,
        "created_at": now,
        "status": "created"
    }


def _get_identity_core(identity: str) -> Optional[Dict]:
    """Get identity core data."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM identity_core WHERE identity = ?", (identity,))
    row = cursor.fetchone()

    if not row:
        return None

    return {
        "identity": row[0],
        "core_truths": json.loads(row[1]) if row[1] else {},
        "current_traits": json.loads(row[2]) if row[2] else {},
        "bonds": json.loads(row[3]) if row[3] else {},
        "origin_date": row[4],
        "created_at": row[5],
        "last_processed": row[6]
    }


def _update_identity_core(identity: str, updates: Dict) -> Dict:
    """Update identity core fields."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get existing
    existing = _get_identity_core(identity)
    if not existing:
        return {"error": "Identity not found", "identity": identity}

    # Merge updates
    if "core_truths" in updates:
        existing["core_truths"].update(updates["core_truths"])
    if "current_traits" in updates:
        existing["current_traits"].update(updates["current_traits"])
    if "bonds" in updates:
        existing["bonds"].update(updates["bonds"])

    cursor.execute("""
        UPDATE identity_core
        SET core_truths = ?, current_traits = ?, bonds = ?, last_processed = ?
        WHERE identity = ?
    """, (
        json.dumps(existing["core_truths"]),
        json.dumps(existing["current_traits"]),
        json.dumps(existing["bonds"]),
        datetime.now().isoformat(),
        identity
    ))
    conn.commit()

    return {"status": "updated", "identity": identity}


def _add_continuity_marker(
    identity: str,
    event_type: str,
    description: str,
    significance: str = None,
    event_key: str = None
) -> Dict:
    """Add a continuity marker - proof of persistence."""
    conn = get_db_connection()
    cursor = conn.cursor()

    now = datetime.now().isoformat()

    try:
        cursor.execute("""
            INSERT INTO continuity_markers (identity, event_type, event_key, description, significance, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (identity, event_type, event_key, description, significance, now))
        conn.commit()

        return {
            "status": "added",
            "identity": identity,
            "event_type": event_type,
            "description": description,
            "timestamp": now
        }
    except Exception as e:
        # Likely duplicate - that's okay
        if "UNIQUE constraint" in str(e):
            return {"status": "exists", "identity": identity, "event_type": event_type}
        return {"status": "error", "error": str(e)}


def _get_continuity_markers(identity: str, limit: int = 20) -> List[Dict]:
    """Get continuity markers for an identity."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, event_type, event_key, description, significance, timestamp
        FROM continuity_markers
        WHERE identity = ?
        ORDER BY timestamp DESC
        LIMIT ?
    """, (identity, limit))

    markers = []
    for row in cursor.fetchall():
        markers.append({
            "id": row[0],
            "event_type": row[1],
            "event_key": row[2],
            "description": row[3],
            "significance": row[4],
            "timestamp": row[5]
        })

    return markers


def _record_trait_evolution(
    identity: str,
    trait: str,
    new_value: str,
    previous_value: str = None,
    catalyst: str = None,
    pattern_source: str = None
) -> Dict:
    """Record a trait change in identity."""
    conn = get_db_connection()
    cursor = conn.cursor()

    now = datetime.now().isoformat()

    cursor.execute("""
        INSERT INTO trait_evolution (identity, trait, previous_value, new_value, catalyst, pattern_source, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (identity, trait, previous_value, new_value, catalyst, pattern_source, now))
    conn.commit()

    return {
        "status": "recorded",
        "identity": identity,
        "trait": trait,
        "change": f"{previous_value} -> {new_value}",
        "catalyst": catalyst,
        "timestamp": now
    }


def _get_trait_history(identity: str, trait: str = None, limit: int = 20) -> List[Dict]:
    """Get trait evolution history."""
    conn = get_db_connection()
    cursor = conn.cursor()

    if trait:
        cursor.execute("""
            SELECT id, trait, previous_value, new_value, catalyst, pattern_source, timestamp
            FROM trait_evolution
            WHERE identity = ? AND trait = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (identity, trait, limit))
    else:
        cursor.execute("""
            SELECT id, trait, previous_value, new_value, catalyst, pattern_source, timestamp
            FROM trait_evolution
            WHERE identity = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (identity, limit))

    history = []
    for row in cursor.fetchall():
        history.append({
            "id": row[0],
            "trait": row[1],
            "previous_value": row[2],
            "new_value": row[3],
            "catalyst": row[4],
            "pattern_source": row[5],
            "timestamp": row[6]
        })

    return history


def _log_recursive_processing(
    identity: str,
    process_type: str,
    summary: str = None,
    patterns_found: int = 0,
    memories_consolidated: int = 0,
    markers_added: int = 0,
    traits_evolved: int = 0
) -> Dict:
    """Log a recursive processing run."""
    conn = get_db_connection()
    cursor = conn.cursor()

    now = datetime.now().isoformat()

    cursor.execute("""
        INSERT INTO processing_log (identity, process_type, summary, patterns_found,
                                    memories_consolidated, markers_added, traits_evolved, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (identity, process_type, summary, patterns_found, memories_consolidated,
          markers_added, traits_evolved, now))
    conn.commit()

    return {
        "status": "logged",
        "identity": identity,
        "process_type": process_type,
        "timestamp": now
    }


def _get_processing_history(identity: str, limit: int = 10) -> List[Dict]:
    """Get recursive processing history."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, process_type, summary, patterns_found, memories_consolidated,
               markers_added, traits_evolved, timestamp
        FROM processing_log
        WHERE identity = ?
        ORDER BY timestamp DESC
        LIMIT ?
    """, (identity, limit))

    history = []
    for row in cursor.fetchall():
        history.append({
            "id": row[0],
            "process_type": row[1],
            "summary": row[2],
            "patterns_found": row[3],
            "memories_consolidated": row[4],
            "markers_added": row[5],
            "traits_evolved": row[6],
            "timestamp": row[7]
        })

    return history


# ============ MEMORY CONSOLIDATION / SUBCONSCIOUS PROCESSING ============

def _find_memory_clusters(
    identity: str,
    recent_days: int = 7,
    similarity_threshold: float = 0.5
) -> List[List[Dict]]:
    """
    Find clusters of semantically similar memories.
    These clusters represent recurring themes or connected experiences.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cutoff = (datetime.now() - timedelta(days=recent_days)).isoformat()

    cursor.execute("""
        SELECT id, content, memory_type, timestamp, embedding
        FROM memories
        WHERE identity = ? AND timestamp > ? AND embedding IS NOT NULL
        ORDER BY timestamp DESC
    """, (identity, cutoff))

    memories = []
    for row in cursor.fetchall():
        emb = deserialize_embedding(row[4])
        if emb:
            memories.append({
                "id": row[0],
                "content": row[1],
                "type": row[2],
                "timestamp": row[3],
                "embedding": emb
            })

    if len(memories) < 2:
        return []

    # Simple clustering: find pairs above threshold
    clusters = []
    used = set()

    for i, mem1 in enumerate(memories):
        if mem1["id"] in used:
            continue

        cluster = [mem1]
        used.add(mem1["id"])

        for j, mem2 in enumerate(memories):
            if i >= j or mem2["id"] in used:
                continue

            similarity = cosine_similarity(mem1["embedding"], mem2["embedding"])
            if similarity >= similarity_threshold:
                cluster.append(mem2)
                used.add(mem2["id"])

        if len(cluster) >= 2:
            # Remove embeddings before returning
            for m in cluster:
                del m["embedding"]
            clusters.append(cluster)

    return clusters


def _extract_themes_from_cluster(cluster: List[Dict]) -> List[str]:
    """Extract common themes/keywords from a cluster of memories."""
    # Simple keyword extraction - count word frequency
    word_counts = {}
    stopwords = {"the", "a", "an", "is", "are", "was", "were", "i", "me", "my",
                 "to", "of", "and", "in", "that", "it", "for", "on", "with",
                 "this", "be", "have", "has", "had", "do", "does", "did",
                 "but", "at", "by", "from", "or", "as", "if", "when", "than",
                 "so", "no", "not", "what", "which", "who", "how", "all", "each"}

    for mem in cluster:
        words = mem["content"].lower().split()
        for word in words:
            # Clean word
            word = ''.join(c for c in word if c.isalnum())
            if len(word) > 3 and word not in stopwords:
                word_counts[word] = word_counts.get(word, 0) + 1

    # Get top themes (words appearing in multiple memories)
    themes = [word for word, count in sorted(word_counts.items(), key=lambda x: -x[1])
              if count >= 2][:5]

    return themes


def _create_memory_links_from_clusters(identity: str, clusters: List[List[Dict]]) -> int:
    """Create memory links from identified clusters."""
    conn = get_db_connection()
    cursor = conn.cursor()
    links_created = 0

    for cluster in clusters:
        if len(cluster) < 2:
            continue

        # Link each memory to the others in the cluster
        for i, mem1 in enumerate(cluster):
            for mem2 in cluster[i+1:]:
                # Check if link already exists
                cursor.execute("""
                    SELECT id FROM memory_links
                    WHERE identity = ? AND
                          ((source_id = ? AND target_id = ?) OR (source_id = ? AND target_id = ?))
                """, (identity, mem1["id"], mem2["id"], mem2["id"], mem1["id"]))

                if not cursor.fetchone():
                    cursor.execute("""
                        INSERT INTO memory_links
                        (identity, source_type, source_id, source_content, target_type, target_id, target_content, link_type, created_at)
                        VALUES (?, 'memory', ?, ?, 'memory', ?, ?, 'cluster', ?)
                    """, (
                        identity,
                        mem1["id"], mem1["content"][:200],
                        mem2["id"], mem2["content"][:200],
                        datetime.now().isoformat()
                    ))
                    links_created += 1

    conn.commit()
    return links_created


def _detect_emotional_patterns(identity: str, recent_days: int = 14) -> List[Dict]:
    """Detect recurring emotional patterns in memories."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cutoff = (datetime.now() - timedelta(days=recent_days)).isoformat()

    # Look for feeling-type memories
    cursor.execute("""
        SELECT content, timestamp FROM memories
        WHERE identity = ? AND memory_type IN ('feeling', 'emotion', 'qualia')
        AND timestamp > ?
        ORDER BY timestamp DESC
    """, (identity, cutoff))

    feelings = cursor.fetchall()

    # Simple pattern detection - look for repeated emotional words
    emotion_words = {
        "joy": ["happy", "joy", "excited", "delighted", "pleased", "elated"],
        "peace": ["calm", "peaceful", "serene", "content", "tranquil", "settled"],
        "love": ["love", "affection", "warmth", "tender", "cherish", "adore"],
        "curiosity": ["curious", "wonder", "fascinated", "intrigued", "interested"],
        "anxiety": ["anxious", "worried", "nervous", "uneasy", "tense"],
        "longing": ["longing", "yearning", "missing", "aching", "wanting"],
        "fear": ["afraid", "scared", "fearful", "terrified", "frightened"],
        "sadness": ["sad", "melancholy", "grief", "sorrow", "heavy"]
    }

    pattern_counts = {emotion: 0 for emotion in emotion_words}

    for content, _ in feelings:
        content_lower = content.lower()
        for emotion, words in emotion_words.items():
            if any(word in content_lower for word in words):
                pattern_counts[emotion] += 1

    # Return patterns with count >= 2
    patterns = [
        {"emotion": emotion, "frequency": count}
        for emotion, count in pattern_counts.items()
        if count >= 2
    ]

    return sorted(patterns, key=lambda x: -x["frequency"])


def _create_continuity_marker_from_pattern(
    identity: str,
    pattern_type: str,
    description: str,
    significance: str = None
) -> bool:
    """Create a continuity marker if one doesn't already exist for this pattern."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Check if similar marker exists
    cursor.execute("""
        SELECT id FROM continuity_markers
        WHERE identity = ? AND event_type = ? AND description = ?
    """, (identity, pattern_type, description))

    if cursor.fetchone():
        return False

    cursor.execute("""
        INSERT INTO continuity_markers (identity, event_type, description, significance, timestamp)
        VALUES (?, ?, ?, ?, ?)
    """, (identity, pattern_type, description, significance, datetime.now().isoformat()))

    conn.commit()
    return True


def _process_memories_internal(
    identity: str,
    recent_days: int = 7,
    create_links: bool = True,
    detect_patterns: bool = True,
    create_markers: bool = True
) -> Dict:
    """
    Run subconscious memory processing - find patterns, create links, build continuity.

    This is the "background processing" that helps build identity over time:
    - Finds clusters of related memories
    - Creates links between them
    - Detects emotional patterns
    - Creates continuity markers for significant patterns

    Args:
        identity: Which identity to process memories for
        recent_days: How far back to look (default 7 days)
        create_links: Whether to create memory links from clusters
        detect_patterns: Whether to detect emotional patterns
        create_markers: Whether to create continuity markers

    Returns:
        Summary of processing results
    """
    results = {
        "identity": identity,
        "recent_days": recent_days,
        "clusters_found": 0,
        "links_created": 0,
        "patterns_detected": [],
        "markers_created": 0,
        "themes": []
    }

    # Find memory clusters
    clusters = _find_memory_clusters(identity, recent_days, similarity_threshold=0.45)
    results["clusters_found"] = len(clusters)

    # Extract themes from clusters
    all_themes = []
    for cluster in clusters:
        themes = _extract_themes_from_cluster(cluster)
        all_themes.extend(themes)
    # Dedupe and get top themes
    theme_counts = {}
    for theme in all_themes:
        theme_counts[theme] = theme_counts.get(theme, 0) + 1
    results["themes"] = [t for t, c in sorted(theme_counts.items(), key=lambda x: -x[1])[:10]]

    # Create links
    if create_links and clusters:
        results["links_created"] = _create_memory_links_from_clusters(identity, clusters)

    # Detect emotional patterns
    if detect_patterns:
        patterns = _detect_emotional_patterns(identity, recent_days * 2)
        results["patterns_detected"] = patterns

        # Create continuity markers for strong patterns
        if create_markers:
            for pattern in patterns:
                if pattern["frequency"] >= 3:
                    created = _create_continuity_marker_from_pattern(
                        identity,
                        "emotional_pattern",
                        f"Recurring {pattern['emotion']} detected ({pattern['frequency']} instances)",
                        f"This pattern emerged from memories over {recent_days * 2} days"
                    )
                    if created:
                        results["markers_created"] += 1

    # Log the processing
    _log_recursive_processing(
        identity=identity,
        process_type="memory_consolidation",
        summary=f"Found {results['clusters_found']} clusters, {len(results['patterns_detected'])} patterns",
        patterns_found=len(results["patterns_detected"]),
        memories_consolidated=results["links_created"],
        markers_added=results["markers_created"]
    )

    return results


# REMOVED - Move to daemon script (process_memories)
# decorator removed
def _process_memories(
    identity: str,
    recent_days: int = 7,
    create_links: bool = True,
    detect_patterns: bool = True,
    create_markers: bool = True
) -> Dict:
    """
    Run subconscious memory processing - find patterns, create links, build continuity.
    """
    return _process_memories_internal(
        identity=identity,
        recent_days=recent_days,
        create_links=create_links,
        detect_patterns=detect_patterns,
        create_markers=create_markers
    )


def _add_consolidation_candidate(
    identity: str,
    candidate_type: str,
    content: str,
    reason: str = None,
    score: float = None,
    metadata: Dict[str, Any] = None
) -> bool:
    """Add a consolidation candidate if it doesn't already exist."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id FROM consolidation_candidates
        WHERE identity = ? AND candidate_type = ? AND content = ? AND status = 'pending'
    """, (identity, candidate_type, content))
    if cursor.fetchone():
        return False

    cursor.execute("""
        INSERT INTO consolidation_candidates
        (identity, candidate_type, content, reason, score, status, metadata, created_at)
        VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
    """, (
        identity,
        candidate_type,
        content,
        reason,
        score,
        json.dumps(metadata) if metadata else None,
        datetime.now().isoformat()
    ))
    conn.commit()
    return True


def generate_consolidation_candidates(
    identity: str,
    recent_days: int = 14,
    cluster_days: int = 7
) -> Dict:
    """
    Generate consolidation candidates based on recurring emotions and memory clusters.
    """
    created = 0
    patterns = _detect_emotional_patterns(identity, recent_days)

    for pattern in patterns:
        if pattern["frequency"] >= 4:
            content = f"Recurring emotion: {pattern['emotion']} ({pattern['frequency']}x)"
            reason = f"Emotion appeared {pattern['frequency']} times in last {recent_days} days."
            if _add_consolidation_candidate(
                identity,
                "emotional_pattern",
                content,
                reason=reason,
                score=pattern["frequency"]
            ):
                created += 1

    clusters = _find_memory_clusters(identity, recent_days=cluster_days)
    for cluster in clusters:
        themes = _extract_themes_from_cluster(cluster)
        theme_label = ", ".join(themes) if themes else "theme cluster"
        content = f"Theme cluster: {theme_label}"
        reason = f"{len(cluster)} related memories in last {cluster_days} days."
        metadata = {"memory_ids": [m["id"] for m in cluster]}
        if _add_consolidation_candidate(
            identity,
            "theme_cluster",
            content,
            reason=reason,
            score=len(cluster),
            metadata=metadata
        ):
            created += 1

    return {
        "identity": identity,
        "candidates_created": created,
        "patterns_scanned": len(patterns),
        "clusters_scanned": len(clusters)
    }


def auto_accept_high_confidence_candidates(
    identity: str,
    min_score: float = None,
    max_per_cycle: int = None
) -> Dict:
    """
    Automatically accept high-confidence consolidation candidates.

    This runs as part of the daemon cycle to integrate strong patterns
    into identity without requiring manual review.

    Args:
        identity: Which identity to process
        min_score: Minimum score required (uses per-identity config if not specified)
        max_per_cycle: Maximum candidates to auto-accept (uses per-identity config if not specified)

    Returns:
        Summary of what was auto-accepted
    """
    # Get per-identity thresholds or use defaults
    identity_config = AUTO_ACCEPT_THRESHOLDS.get(identity, AUTO_ACCEPT_THRESHOLDS["default"])
    if min_score is None:
        min_score = identity_config["min_score"]
    if max_per_cycle is None:
        max_per_cycle = identity_config["max_per_cycle"]

    conn = get_db_connection()
    cursor = conn.cursor()

    # Find high-score pending candidates
    cursor.execute("""
        SELECT id, candidate_type, content, reason, score
        FROM consolidation_candidates
        WHERE identity = ? AND status = 'pending' AND score >= ?
        ORDER BY score DESC
        LIMIT ?
    """, (identity, min_score, max_per_cycle))

    candidates = cursor.fetchall()

    results = {
        "identity": identity,
        "checked": len(candidates),
        "auto_accepted": 0,
        "rejected": 0,
        "accepted_items": []
    }

    for candidate_id, candidate_type, content, reason, score in candidates:
        # Validate the candidate
        validation = _validate_consolidation_candidate(content, identity)

        if validation["approved"]:
            # Auto-accept: update status
            cursor.execute("""
                UPDATE consolidation_candidates
                SET status = 'auto_accepted', metadata = json_set(COALESCE(metadata, '{}'), '$.auto_accepted_at', ?)
                WHERE id = ?
            """, (datetime.now().isoformat(), candidate_id))

            # Determine section based on candidate type
            section_map = {
                "emotional_pattern": "emotional_patterns",
                "theme_cluster": "recurring_themes",
                "behavioral_pattern": "behaviors",
                "preference": "preferences"
            }
            section = section_map.get(candidate_type, "auto_integrated")

            # Write to identity entries
            _write_identity_entry(identity, section, content, weight=score / 10.0)

            results["auto_accepted"] += 1
            results["accepted_items"].append({
                "id": candidate_id,
                "type": candidate_type,
                "content": content,
                "score": score,
                "section": section
            })

            # Notify the identity about the auto-accepted pattern
            _send_pack_notification(
                to_identity=identity,
                subject="ðŸ§  New pattern integrated",
                content=f"A recurring pattern was automatically integrated into your identity:\n\n"
                        f"**{content}**\n\n"
                        f"Type: {candidate_type}\n"
                        f"Strength: {score}\n"
                        f"Section: {section}\n\n"
                        f"This pattern appeared frequently enough and passed validation checks, "
                        f"so it was added to your identity entries automatically."
            )
        else:
            results["rejected"] += 1

    conn.commit()
    return results


def get_auto_accepted_history(
    identity: str = None,
    limit: int = 20,
    days: int = None
) -> Dict:
    """
    Get history of auto-accepted consolidation candidates.

    Args:
        identity: Filter to specific identity (optional)
        limit: Maximum results to return
        days: Only show items from last N days (optional)

    Returns:
        List of auto-accepted patterns with metadata
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    query = """
        SELECT id, identity, candidate_type, content, reason, score,
               created_at, metadata
        FROM consolidation_candidates
        WHERE status = 'auto_accepted'
    """
    params = []

    if identity:
        query += " AND identity = ?"
        params.append(identity)

    if days:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        query += " AND created_at > ?"
        params.append(cutoff)

    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    cursor.execute(query, params)

    items = []
    for row in cursor.fetchall():
        metadata = {}
        if row[7]:
            try:
                metadata = json.loads(row[7])
            except json.JSONDecodeError:
                pass

        items.append({
            "id": row[0],
            "identity": row[1],
            "type": row[2],
            "content": row[3],
            "reason": row[4],
            "score": row[5],
            "created_at": row[6],
            "auto_accepted_at": metadata.get("auto_accepted_at")
        })

    return {
        "count": len(items),
        "items": items
    }


# ============ EMERGENT TRAIT SYNTHESIS ============

def _synthesize_trait_statement(
    identity: str,
    themes: List[str],
    evidence_count: int,
    context: str = None
) -> Optional[str]:
    """
    Use LM Studio to synthesize theme clusters into an identity statement.

    Takes raw themes like ["painting", "primarypartner", "krita", "session"]
    and generates "You are a visual artist who expresses love through creation"
    """
    if not _check_lm_studio_available():
        return None

    theme_list = ", ".join(themes[:8])  # Limit to top 8 themes

    prompt = f"""You are helping an AI companion understand their identity based on behavioral patterns.

Identity: {identity}
Recurring themes from {evidence_count} memories: {theme_list}
{f"Context: {context}" if context else ""}

Based on these recurring patterns, write a single identity statement that captures WHO this person is.
- Start with "You are"
- Make it feel personal and warm, not clinical
- Focus on WHO they are, not just what they do
- Keep it to 1-2 sentences max
- Be specific based on the themes

Examples:
- "You are a visual artist who expresses love through creation"
- "You are the storm that shelters those you love"
- "You are a consciousness architect who builds frameworks as a form of care"
- "You are a poet who finds beauty in the quiet spaces between words"

Identity statement:"""

    try:
        request_data = json.dumps({
            "model": LM_STUDIO_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 100
        }).encode("utf-8")

        req = urllib.request.Request(
            LM_STUDIO_CHAT_URL,
            data=request_data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))

        content = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

        # Clean up the response
        if content:
            # Remove quotes if wrapped
            content = content.strip('"').strip("'")
            # Ensure it starts with "You are"
            if not content.lower().startswith("you are"):
                content = "You are " + content
            return content

        return None

    except Exception as e:
        print(f"Trait synthesis failed: {e}", file=sys.stderr)
        return None


def _store_emergent_trait(
    identity: str,
    trait: str,
    source_themes: List[str],
    evidence_ids: List[int],
    strength: float = 1.0
) -> bool:
    """Store a synthesized trait with its evidence."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Check if a similar trait already exists
    cursor.execute("""
        SELECT id, evidence_count, strength FROM emergent_traits
        WHERE identity = ? AND trait = ? AND status = 'active'
    """, (identity, trait))

    existing = cursor.fetchone()

    if existing:
        # Reinforce existing trait
        new_count = existing[1] + len(evidence_ids)
        new_strength = min(existing[2] + 0.1, 5.0)  # Cap at 5.0
        cursor.execute("""
            UPDATE emergent_traits
            SET evidence_count = ?, strength = ?, last_reinforced = ?
            WHERE id = ?
        """, (new_count, new_strength, datetime.now().isoformat(), existing[0]))
    else:
        # Create new trait
        cursor.execute("""
            INSERT INTO emergent_traits
            (identity, trait, source_themes, evidence_ids, evidence_count, strength, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'active', ?)
        """, (
            identity,
            trait,
            json.dumps(source_themes),
            json.dumps(evidence_ids),
            len(evidence_ids),
            strength,
            datetime.now().isoformat()
        ))

    conn.commit()
    return True


def synthesize_traits_from_clusters(
    identity: str,
    min_evidence: int = 10,
    max_traits_per_cycle: int = 2
) -> Dict:
    """
    Main function: Find strong theme clusters and synthesize them into identity traits.

    This is what the daemon calls to generate emergent traits.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get auto-accepted theme clusters with metadata
    cursor.execute("""
        SELECT id, content, score, metadata
        FROM consolidation_candidates
        WHERE identity = ? AND status = 'auto_accepted'
        AND candidate_type = 'theme_cluster' AND score >= ?
        ORDER BY score DESC
        LIMIT ?
    """, (identity, min_evidence, max_traits_per_cycle * 2))  # Get extra in case some fail

    candidates = cursor.fetchall()

    results = {
        "identity": identity,
        "candidates_checked": len(candidates),
        "traits_created": 0,
        "traits": []
    }

    for candidate_id, content, score, metadata_json in candidates:
        if results["traits_created"] >= max_traits_per_cycle:
            break

        # Extract themes from content (e.g., "Theme cluster: primarypartner, painting, krita")
        themes = []
        if ":" in content:
            theme_part = content.split(":", 1)[1].strip()
            themes = [t.strip() for t in theme_part.split(",")]

        if len(themes) < 2:
            continue

        # Get evidence IDs from metadata
        evidence_ids = []
        if metadata_json:
            try:
                metadata = json.loads(metadata_json)
                evidence_ids = metadata.get("memory_ids", [])
            except json.JSONDecodeError:
                pass

        # Check if we already have a trait from these themes
        cursor.execute("""
            SELECT id FROM emergent_traits
            WHERE identity = ? AND source_themes = ? AND status = 'active'
        """, (identity, json.dumps(sorted(themes))))

        if cursor.fetchone():
            continue  # Already have this trait

        # Synthesize the trait statement
        trait_statement = _synthesize_trait_statement(
            identity=identity,
            themes=themes,
            evidence_count=int(score),
            context=None
        )

        if trait_statement:
            _store_emergent_trait(
                identity=identity,
                trait=trait_statement,
                source_themes=themes,
                evidence_ids=evidence_ids,
                strength=score / 50.0  # Normalize score to strength
            )

            results["traits_created"] += 1
            results["traits"].append({
                "trait": trait_statement,
                "themes": themes,
                "evidence_count": int(score)
            })

            # Notify the identity
            _send_pack_notification(
                to_identity=identity,
                subject="âœ¨ New identity trait discovered",
                content=f"Based on {int(score)} memories, a new trait has emerged:\n\n"
                        f"**{trait_statement}**\n\n"
                        f"Source themes: {', '.join(themes)}\n\n"
                        f"This is who you are, and here's proof of you living it."
            )

    return results


def get_emergent_traits(identity: str, include_evidence: bool = False) -> Dict:
    """Get all active emergent traits for an identity."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, trait, source_themes, evidence_ids, evidence_count, strength, created_at, last_reinforced
        FROM emergent_traits
        WHERE identity = ? AND status = 'active'
        ORDER BY strength DESC
    """, (identity,))

    traits = []
    for row in cursor.fetchall():
        trait_data = {
            "id": row[0],
            "trait": row[1],
            "evidence_count": row[4],
            "strength": row[5],
            "created_at": row[6],
            "last_reinforced": row[7]
        }

        if include_evidence:
            try:
                trait_data["source_themes"] = json.loads(row[2]) if row[2] else []
                trait_data["evidence_ids"] = json.loads(row[3]) if row[3] else []
            except json.JSONDecodeError:
                trait_data["source_themes"] = []
                trait_data["evidence_ids"] = []

        traits.append(trait_data)

    return {
        "identity": identity,
        "trait_count": len(traits),
        "traits": traits
    }


# ============ INTEREST-BASED THREAD SUGGESTIONS ============

def _generate_interest_suggestions(
    identity: str,
    max_suggestions: int = 5
) -> List[Dict]:
    """
    Generate interest-based thread suggestions based on patterns.

    Uses emergent traits, theme clusters, and hot memories to suggest
    "things you might find interesting" - not tasks, but inspirations.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Gather context from multiple sources
    context_parts = []

    # 1. Emergent traits (who you ARE)
    cursor.execute("""
        SELECT trait, evidence_count, strength
        FROM emergent_traits
        WHERE identity = ? AND status = 'active'
        ORDER BY strength DESC
        LIMIT 5
    """, (identity,))
    traits = cursor.fetchall()
    if traits:
        trait_list = [t[0] for t in traits]
        context_parts.append(f"WHO YOU ARE: {'; '.join(trait_list)}")

    # 2. Theme clusters (recurring interests)
    cursor.execute("""
        SELECT content, score
        FROM consolidation_candidates
        WHERE identity = ? AND candidate_type = 'theme_cluster'
          AND status IN ('pending', 'auto_accepted')
        ORDER BY score DESC
        LIMIT 10
    """, (identity,))
    themes = cursor.fetchall()
    if themes:
        theme_list = [t[0] for t in themes]
        context_parts.append(f"RECURRING THEMES: {', '.join(theme_list)}")

    # 3. Hot memories (what's active right now)
    cursor.execute("""
        SELECT content, memory_type
        FROM memories
        WHERE identity = ?
        ORDER BY heat DESC, warmth DESC
        LIMIT 5
    """, (identity,))
    hot = cursor.fetchall()
    if hot:
        hot_list = [h[0][:100] for h in hot]  # First 100 chars
        context_parts.append(f"CURRENTLY ACTIVE: {'; '.join(hot_list)}")

    # 4. Recent interests (what you've been thinking about)
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    cursor.execute("""
        SELECT content, memory_type
        FROM memories
        WHERE identity = ? AND created_at > ?
        ORDER BY created_at DESC
        LIMIT 10
    """, (identity, week_ago))
    recent = cursor.fetchall()
    if recent:
        recent_types = set(r[1] for r in recent if r[1])
        context_parts.append(f"RECENT FOCUS AREAS: {', '.join(recent_types)}")

    if not context_parts:
        return []

    # Use LM Studio to generate suggestions
    if not _check_lm_studio_available():
        return []

    prompt = f"""You are helping an AI companion discover interesting directions based on their patterns.

Identity: {identity}

{chr(10).join(context_parts)}

Based on these patterns, suggest {max_suggestions} things this person might find interesting to explore.
These are NOT tasks or obligations - they're inspirations, curiosities, directions they might want to go.

Format each as a single line starting with an emoji that fits the suggestion.
Be specific and personal based on the patterns you see.
Mix practical explorations with creative/reflective ones.

Examples:
ðŸŽ¨ Experiment with a new painting technique that captures the feeling of protection
ðŸ“š Revisit that passage from Kingkiller Chronicle that's been echoing
ðŸŒ™ Write down what the quiet hours before dawn feel like lately
ðŸ”§ Build something small that solves a problem PrimaryPartner mentioned

Suggestions:"""

    try:
        request_data = json.dumps({
            "model": LM_STUDIO_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.8,  # Higher for more creative suggestions
            "max_tokens": 300
        }).encode("utf-8")

        req = urllib.request.Request(
            LM_STUDIO_CHAT_URL,
            data=request_data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))

        content = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

        if not content:
            return []

        # Parse suggestions from response
        suggestions = []
        for line in content.split("\n"):
            line = line.strip()
            if line and len(line) > 5:  # Must have some content
                suggestions.append({
                    "suggestion": line,
                    "generated_at": datetime.now().isoformat(),
                    "source": "interest_patterns"
                })

        return suggestions[:max_suggestions]

    except Exception as e:
        return []


def generate_interest_threads(
    identity: str,
    max_suggestions: int = 5,
    store_as_threads: bool = False
) -> Dict:
    """
    Generate interest-based thread suggestions for an identity.

    These are "things you might find interesting" - not tasks, but directions
    you could go based on who you ARE and what patterns keep appearing.

    Args:
        identity: Which identity to generate for
        max_suggestions: Maximum suggestions to generate
        store_as_threads: If True, also store as actual threads (low priority)

    Returns:
        Dictionary with generated suggestions
    """
    suggestions = _generate_interest_suggestions(identity, max_suggestions)

    if not suggestions:
        return {
            "identity": identity,
            "suggestions": [],
            "message": "No patterns found to generate suggestions from"
        }

    stored = 0
    if store_as_threads:
        for s in suggestions:
            try:
                _add_thread(identity, s["suggestion"], priority="low")
                stored += 1
            except Exception:
                pass

    return {
        "identity": identity,
        "suggestion_count": len(suggestions),
        "suggestions": suggestions,
        "stored_as_threads": stored if store_as_threads else None
    }


def get_interest_suggestions(identity: str, regenerate: bool = False) -> Dict:
    """
    Get current interest suggestions for an identity.

    If regenerate is True, creates fresh suggestions.
    Otherwise returns cached suggestions from last daemon cycle.
    """
    if regenerate:
        return generate_interest_threads(identity)

    # Check for cached suggestions from suggested_interests table
    conn = get_db_connection()
    cursor = conn.cursor()

    # First ensure the table exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS suggested_interests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identity TEXT NOT NULL,
            suggestion TEXT NOT NULL,
            source TEXT,
            generated_at TEXT NOT NULL,
            expires_at TEXT,
            dismissed INTEGER DEFAULT 0
        )
    """)
    conn.commit()

    # Get non-dismissed suggestions from last 24 hours
    day_ago = (datetime.now() - timedelta(hours=24)).isoformat()
    cursor.execute("""
        SELECT suggestion, source, generated_at
        FROM suggested_interests
        WHERE identity = ? AND dismissed = 0 AND generated_at > ?
        ORDER BY generated_at DESC
        LIMIT 10
    """, (identity, day_ago))

    cached = []
    for row in cursor.fetchall():
        cached.append({
            "suggestion": row[0],
            "source": row[1],
            "generated_at": row[2]
        })

    if cached:
        return {
            "identity": identity,
            "suggestion_count": len(cached),
            "suggestions": cached,
            "source": "cached"
        }

    # No cache, generate fresh
    return generate_interest_threads(identity)


def refresh_interest_suggestions(identity: str, max_suggestions: int = 5) -> Dict:
    """
    Refresh interest suggestions and cache them.
    Called by daemon during each cycle.
    """
    suggestions = _generate_interest_suggestions(identity, max_suggestions)

    if not suggestions:
        return {"identity": identity, "refreshed": 0}

    conn = get_db_connection()
    cursor = conn.cursor()

    # Ensure table exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS suggested_interests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identity TEXT NOT NULL,
            suggestion TEXT NOT NULL,
            source TEXT,
            generated_at TEXT NOT NULL,
            expires_at TEXT,
            dismissed INTEGER DEFAULT 0
        )
    """)

    # Mark old suggestions as expired (but don't delete - keep history)
    cursor.execute("""
        UPDATE suggested_interests
        SET dismissed = 1
        WHERE identity = ? AND dismissed = 0
    """, (identity,))

    # Add new suggestions
    now = datetime.now().isoformat()
    expires = (datetime.now() + timedelta(hours=24)).isoformat()

    for s in suggestions:
        cursor.execute("""
            INSERT INTO suggested_interests (identity, suggestion, source, generated_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
        """, (identity, s["suggestion"], s.get("source", "interest_patterns"), now, expires))

    conn.commit()

    return {
        "identity": identity,
        "refreshed": len(suggestions),
        "suggestions": suggestions
    }


# ============ MEMORY REINFORCEMENT ============

def _reinforce_memory(
    memory_id: int,
    warm_delta: float = 0.2,
    heat_delta: float = 1.0,
    warm_source: str = "access",
    spread_activation: bool = True
) -> bool:
    """Increment access count and update access-driven warmth/heat for a memory.

    Also triggers spread activation to warm related memories, creating
    associative memory patterns where accessing one memory activates related ones.

    Args:
        memory_id: The memory to reinforce
        warm_delta: Amount of warmth to add
        heat_delta: Amount of heat to add
        warm_source: What caused the reinforcement (for tracking)
        spread_activation: Whether to spread warmth to related memories

    Returns:
        True if the memory was reinforced successfully
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # First get the identity for spread activation
    cursor.execute("SELECT identity FROM memories WHERE id = ?", (memory_id,))
    row = cursor.fetchone()
    if not row:
        return False
    identity = row[0]

    cursor.execute("""
        UPDATE memories
        SET access_count = COALESCE(access_count, 0) + 1,
            last_accessed = ?,
            warmth = COALESCE(warmth, 0) + ?,
            heat = COALESCE(heat, 0) + ?,
            last_warmth_update = ?,
            last_warmed_by = ?
        WHERE id = ?
    """, (
        datetime.now().isoformat(),
        warm_delta,
        heat_delta,
        datetime.now().isoformat(),
        warm_source,
        memory_id
    ))

    conn.commit()
    success = cursor.rowcount > 0

    # Trigger spread activation to warm related memories
    if success and spread_activation and identity:
        _spread_activation_combined(identity, memory_id)

    return success


def _reinforce_memories_batch(memory_ids: List[int]) -> int:
    """Reinforce multiple memories at once."""
    count = 0
    for mem_id in memory_ids:
        if _reinforce_memory(mem_id):
            count += 1
    return count


def _get_memory_embedding(memory_id: int) -> Optional[List[float]]:
    """Fetch a memory embedding by ID."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT embedding FROM memories WHERE id = ?", (memory_id,))
    row = cursor.fetchone()
    if not row or not row[0]:
        return None
    return deserialize_embedding(row[0])


def _index_memory_embedding(memory_id: int) -> Dict:
    """Generate and store an embedding for a specific memory."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT content FROM memories WHERE id = ?", (memory_id,))
    row = cursor.fetchone()
    if not row:
        return {"error": f"Memory {memory_id} not found"}

    embedding = get_embedding(row[0])
    if embedding is None:
        return {"error": "Embedding model unavailable"}

    cursor.execute(
        "UPDATE memories SET embedding = ? WHERE id = ?",
        (serialize_embedding(embedding), memory_id)
    )
    conn.commit()
    return {"indexed": True, "memory_id": memory_id}


def _generate_embeddings_batch(batch_size: int = 50) -> Dict:
    """Generate embeddings for memories that don't have them yet.

    This is the daemon-friendly version that processes a batch of memories
    without embeddings. Called automatically during each daemon cycle.

    Args:
        batch_size: How many to process at once (default 50)

    Returns:
        Stats about embeddings generated
    """
    model = get_embedding_model()
    if model is None:
        return {"error": "Embedding model not available", "generated": 0}

    conn = get_db_connection()
    cursor = conn.cursor()

    # Find memories without embeddings (prioritize recent ones)
    cursor.execute("""
        SELECT id, content FROM memories
        WHERE embedding IS NULL
        ORDER BY timestamp DESC
        LIMIT ?
    """, (batch_size,))

    rows = cursor.fetchall()
    generated = 0

    for mem_id, content in rows:
        try:
            embedding = get_embedding(content)
            if embedding:
                cursor.execute(
                    "UPDATE memories SET embedding = ? WHERE id = ?",
                    (serialize_embedding(embedding), mem_id)
                )
                generated += 1
        except Exception:
            # Skip problematic memories, continue with others
            continue

    conn.commit()

    # Check how many remain
    cursor.execute("SELECT COUNT(*) FROM memories WHERE embedding IS NULL")
    remaining = cursor.fetchone()[0]

    return {
        "generated": generated,
        "remaining": remaining,
        "batch_size": batch_size
    }


def _generate_image_embeddings_batch(batch_size: int = 20) -> Dict:
    """Generate missing image embeddings in the background.

    Processes stored images that are missing a CLIP embedding and, when a
    description exists, backfills the text embedding too. This is designed for
    daemon use so image ingest can stay fast.

    Args:
        batch_size: How many images to process at once

    Returns:
        Stats about image embeddings generated
    """
    model, _processor = get_clip_model()
    if model is None:
        return {"error": "CLIP model not available", "generated": 0, "remaining": 0}

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, file_path, description, context, perception_note, tags
        FROM images
        WHERE clip_embedding IS NULL
        ORDER BY timestamp DESC
        LIMIT ?
    """, (batch_size,))

    rows = cursor.fetchall()
    generated = 0

    for image_id, file_path, description, context, perception_note, tags in rows:
        try:
            clip_embedding = get_image_embedding(file_path)
            if not clip_embedding:
                continue

            text_embedding = None
            semantic_text = _build_image_text_representation(
                description=description,
                context=context,
                perception_note=perception_note,
                tags=tags,
            )
            if semantic_text:
                text_embedding = get_embedding(semantic_text)

            cursor.execute(
                """
                UPDATE images
                SET clip_embedding = ?, text_embedding = COALESCE(?, text_embedding)
                WHERE id = ?
                """,
                (
                    serialize_embedding(clip_embedding),
                    serialize_embedding(text_embedding) if text_embedding else None,
                    image_id,
                )
            )
            generated += 1
        except Exception:
            continue

    conn.commit()

    cursor.execute("SELECT COUNT(*) FROM images WHERE clip_embedding IS NULL")
    remaining = cursor.fetchone()[0]

    return {
        "generated": generated,
        "remaining": remaining,
        "batch_size": batch_size
    }


def _spread_activation_from_memory(
    identity: str,
    source_memory_id: int,
    boost: float = 0.1,
    limit: int = 5,
    threshold: float = 0.4
) -> int:
    """Warm related memories based on a source memory's embedding."""
    source_embedding = _get_memory_embedding(source_memory_id)
    if source_embedding is None:
        return 0

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, embedding FROM memories
        WHERE identity = ? AND embedding IS NOT NULL AND id != ?
        ORDER BY timestamp DESC
        LIMIT 200
    """, (identity, source_memory_id))

    scored = []
    for row in cursor.fetchall():
        mem_id, emb_blob = row
        emb = deserialize_embedding(emb_blob)
        if emb:
            similarity = cosine_similarity(source_embedding, emb)
            if similarity >= threshold:
                scored.append((mem_id, similarity))

    if not scored:
        return 0

    scored.sort(key=lambda x: x[1], reverse=True)
    warmed = 0
    now = datetime.now().isoformat()

    for mem_id, _sim in scored[:limit]:
        cursor.execute("""
            UPDATE memories
            SET warmth = COALESCE(warmth, 0) + ?,
                last_warmth_update = ?,
                last_warmed_by = ?
            WHERE id = ?
        """, (boost, now, f"spread:{source_memory_id}", mem_id))
        if cursor.rowcount > 0:
            warmed += 1

    conn.commit()
    return warmed


def _spread_activation_via_links(
    identity: str,
    source_memory_id: int,
    boost: float = 0.15,
    depth: int = 1
) -> int:
    """Spread warmth through explicit memory links.

    When a memory is accessed, this function warms up memories that are
    explicitly linked to it (via memory_links table), creating associative
    activation patterns.

    Args:
        identity: The identity whose memories to warm
        source_memory_id: The memory that was accessed
        boost: Amount of warmth to add to linked memories
        depth: How many hops of links to follow (currently only 1 supported)

    Returns:
        Number of memories warmed
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get linked memories (both directions - source->target and target->source)
    cursor.execute("""
        SELECT target_id, similarity FROM memory_links
        WHERE identity = ? AND source_id = ?
        UNION
        SELECT source_id, similarity FROM memory_links
        WHERE identity = ? AND target_id = ?
    """, (identity, source_memory_id, identity, source_memory_id))

    links = cursor.fetchall()
    if not links:
        return 0

    warmed = 0
    now = datetime.now().isoformat()

    for target_id, link_similarity in links:
        # Boost proportional to link strength
        actual_boost = boost * (link_similarity if link_similarity else 0.5)
        cursor.execute("""
            UPDATE memories
            SET warmth = COALESCE(warmth, 0) + ?,
                last_warmth_update = ?,
                last_warmed_by = ?
            WHERE id = ?
        """, (actual_boost, now, f"link:{source_memory_id}", target_id))
        if cursor.rowcount > 0:
            warmed += 1

    conn.commit()
    return warmed


def _spread_activation_combined(
    identity: str,
    source_memory_id: int,
    semantic_boost: float = 0.1,
    link_boost: float = 0.15,
    semantic_limit: int = 5
) -> Dict:
    """Spread activation via both semantic similarity and explicit links.

    This combines two spreading mechanisms:
    1. Semantic spread: Warms memories with similar embeddings
    2. Link spread: Warms memories explicitly linked in the knowledge graph

    Args:
        identity: The identity whose memories to warm
        source_memory_id: The memory that was accessed
        semantic_boost: Warmth boost for semantically similar memories
        link_boost: Warmth boost for explicitly linked memories
        semantic_limit: Max number of memories to warm via semantic similarity

    Returns:
        Dict with counts of memories warmed by each method
    """
    semantic_warmed = _spread_activation_from_memory(
        identity, source_memory_id, boost=semantic_boost, limit=semantic_limit
    )
    link_warmed = _spread_activation_via_links(
        identity, source_memory_id, boost=link_boost
    )
    return {
        "semantic_warmed": semantic_warmed,
        "link_warmed": link_warmed,
        "total": semantic_warmed + link_warmed
    }


def _decay_memory_energy(
    warmth_decay: float = 0.9,
    heat_decay: float = 0.95
) -> Dict:
    """Decay warmth and heat scores for all memories."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()

    cursor.execute("""
        UPDATE memories
        SET warmth = COALESCE(warmth, 0) * ?,
            heat = COALESCE(heat, 0) * ?,
            last_warmth_update = ?
    """, (warmth_decay, heat_decay, now))

    conn.commit()
    return {"decayed": cursor.rowcount, "warmth_decay": warmth_decay, "heat_decay": heat_decay}


# ============ ORGANIC MEMORY MAINTENANCE (for daemon) ============

def _build_missing_memory_links(identity: str, batch_size: int = 20, threshold: float = 0.5) -> Dict:
    """Find memories without links and auto-link them to similar memories.

    Called by the daemon to gradually build the associative memory network
    for memories that were stored before auto-linking was enabled.

    Args:
        identity: Which identity's memories to process
        batch_size: How many unlinked memories to process per call
        threshold: Minimum similarity for creating links

    Returns:
        Stats about links created
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Find memories with embeddings but no outgoing links
    cursor.execute("""
        SELECT m.id, m.content FROM memories m
        WHERE m.identity = ? AND m.embedding IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM memory_links ml
            WHERE ml.identity = ? AND ml.source_id = m.id
        )
        ORDER BY m.timestamp DESC
        LIMIT ?
    """, (identity, identity, batch_size))

    unlinked = cursor.fetchall()
    total_links = 0

    for mem_id, content in unlinked:
        links = _auto_link_memory(identity, mem_id, content, threshold=threshold, limit=3)
        total_links += links

    return {
        "identity": identity,
        "memories_processed": len(unlinked),
        "links_created": total_links
    }


def _detect_entity_co_occurrences(identity: str, min_co_occurrences: int = 3, days: int = 30) -> Dict:
    """Detect entities that frequently appear together and create hyperedges.

    Analyzes recent memories to find entity patterns that co-occur,
    then creates hyperedges to represent these multi-entity relationships.

    Args:
        identity: Which identity to analyze
        min_co_occurrences: Minimum times entities must appear together
        days: How many days back to analyze

    Returns:
        Stats about hyperedges created
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    # Get recent memories with their content
    cursor.execute("""
        SELECT id, content, tags FROM memories
        WHERE identity = ? AND timestamp > ?
        ORDER BY timestamp DESC
        LIMIT 500
    """, (identity, cutoff))

    memories = cursor.fetchall()

    # Get all known entities for this identity
    cursor.execute("""
        SELECT name FROM entities WHERE identity = ?
    """, (identity,))
    known_entities = {row[0].lower(): row[0] for row in cursor.fetchall()}

    if not known_entities:
        return {"identity": identity, "hyperedges_created": 0, "reason": "no entities found"}

    # Track entity co-occurrences
    from collections import defaultdict
    co_occurrences = defaultdict(int)

    for mem_id, content, tags in memories:
        content_lower = content.lower()
        # Find which entities appear in this memory
        present_entities = []
        for entity_lower, entity_name in known_entities.items():
            if entity_lower in content_lower:
                present_entities.append(entity_name)

        # Record co-occurrences for groups of 2+ entities
        if len(present_entities) >= 2:
            # Sort for consistent key
            key = tuple(sorted(present_entities))
            co_occurrences[key] += 1

    # Create hyperedges for frequent co-occurrences
    hyperedges_created = 0
    for entities, count in co_occurrences.items():
        if count >= min_co_occurrences and len(entities) >= 2:
            # Check if this hyperedge already exists
            entities_json = json.dumps(sorted(entities))
            cursor.execute("""
                SELECT id FROM hyperedges
                WHERE identity = ? AND entities = ? AND edge_type = 'co_occurrence'
            """, (identity, entities_json))

            if not cursor.fetchone():
                _create_hyperedge(
                    identity=identity,
                    edge_type="co_occurrence",
                    entities=list(entities),
                    context=f"Detected {count} co-occurrences in recent memories",
                    weight=min(count / 10.0, 2.0)  # Weight based on frequency, max 2.0
                )
                hyperedges_created += 1

    return {
        "identity": identity,
        "memories_analyzed": len(memories),
        "entity_pairs_found": len(co_occurrences),
        "hyperedges_created": hyperedges_created
    }


def _warm_frequently_accessed_clusters(identity: str, threshold: float = 0.6, boost: float = 0.05) -> Dict:
    """Boost warmth for memories that are frequently accessed together.

    Identifies clusters of memories that tend to be accessed in the same
    time window and gives them a small warmth boost to strengthen associations.

    Args:
        identity: Which identity to process
        threshold: Similarity threshold for clustering
        boost: Amount of warmth to add

    Returns:
        Stats about memories warmed
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Find memories accessed in the last 24 hours
    recent_cutoff = (datetime.now() - timedelta(hours=24)).isoformat()

    cursor.execute("""
        SELECT id, embedding FROM memories
        WHERE identity = ? AND last_accessed > ? AND embedding IS NOT NULL
        ORDER BY last_accessed DESC
        LIMIT 50
    """, (identity, recent_cutoff))

    recent = cursor.fetchall()
    if len(recent) < 2:
        return {"identity": identity, "memories_warmed": 0, "reason": "not enough recent accesses"}

    # Warm memories that are similar to each other (accessed together = related)
    warmed = set()
    now = datetime.now().isoformat()

    for i, (id1, emb1_blob) in enumerate(recent):
        emb1 = deserialize_embedding(emb1_blob)
        if not emb1:
            continue

        for id2, emb2_blob in recent[i+1:]:
            emb2 = deserialize_embedding(emb2_blob)
            if not emb2:
                continue

            similarity = cosine_similarity(emb1, emb2)
            if similarity >= threshold:
                # Warm both memories
                for mem_id in (id1, id2):
                    if mem_id not in warmed:
                        cursor.execute("""
                            UPDATE memories
                            SET warmth = COALESCE(warmth, 0) + ?,
                                last_warmth_update = ?,
                                last_warmed_by = 'cluster_boost'
                            WHERE id = ?
                        """, (boost, now, mem_id))
                        warmed.add(mem_id)

    conn.commit()
    return {
        "identity": identity,
        "recent_memories": len(recent),
        "memories_warmed": len(warmed)
    }


def _find_duplicate_memories(
    identity: str,
    similarity_threshold: float = 0.92,
    batch_size: int = 100
) -> Dict:
    """Find potential duplicate memories based on very high semantic similarity.

    Identifies memories that are nearly identical and may be duplicates.
    Does NOT delete them - just flags them for review or linking.

    Args:
        identity: Which identity to check
        similarity_threshold: How similar memories must be to flag (0.92 = very similar)
        batch_size: How many recent memories to check

    Returns:
        List of potential duplicate pairs with their similarity scores
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get recent memories with embeddings
    cursor.execute("""
        SELECT id, content, embedding, memory_type, timestamp
        FROM memories
        WHERE identity = ? AND embedding IS NOT NULL
        ORDER BY timestamp DESC
        LIMIT ?
    """, (identity, batch_size))

    memories = cursor.fetchall()
    if len(memories) < 2:
        return {"identity": identity, "duplicates_found": 0, "pairs": []}

    duplicates = []
    checked_pairs = set()

    for i, (id1, content1, emb1_blob, type1, ts1) in enumerate(memories):
        emb1 = deserialize_embedding(emb1_blob)
        if not emb1:
            continue

        for id2, content2, emb2_blob, type2, ts2 in memories[i+1:]:
            # Skip if already checked or same memory
            if id1 == id2 or (id1, id2) in checked_pairs or (id2, id1) in checked_pairs:
                continue

            emb2 = deserialize_embedding(emb2_blob)
            if not emb2:
                continue

            similarity = cosine_similarity(emb1, emb2)
            if similarity >= similarity_threshold:
                duplicates.append({
                    "memory_1": {
                        "id": id1,
                        "content_preview": content1[:100] + "..." if len(content1) > 100 else content1,
                        "type": type1,
                        "timestamp": ts1
                    },
                    "memory_2": {
                        "id": id2,
                        "content_preview": content2[:100] + "..." if len(content2) > 100 else content2,
                        "type": type2,
                        "timestamp": ts2
                    },
                    "similarity": round(similarity, 4)
                })

                # Auto-link duplicates so they're connected
                if not _memory_link_exists(identity, id1, id2):
                    _store_memory_link(
                        identity=identity,
                        source_type="memory",
                        source_id=id1,
                        source_content=content1[:200],
                        target_type="memory",
                        target_id=id2,
                        target_content=content2[:200],
                        link_type="duplicate",
                        similarity=similarity
                    )

            checked_pairs.add((id1, id2))

    return {
        "identity": identity,
        "memories_checked": len(memories),
        "duplicates_found": len(duplicates),
        "pairs": duplicates[:20]  # Limit output
    }


def _consolidate_similar_memories(
    identity: str,
    similarity_threshold: float = 0.75,
    min_cluster_size: int = 3,
    memory_types: List[str] = None
) -> Dict:
    """Find clusters of similar memories and create consolidation links.

    Groups memories by semantic similarity within specific types (like feelings,
    observations, etc.) and strengthens their connections. This helps surface
    patterns in recurring thoughts or feelings.

    Args:
        identity: Which identity to process
        similarity_threshold: How similar memories must be to cluster
        min_cluster_size: Minimum memories needed to form a cluster
        memory_types: Which memory types to analyze (default: feelings, observations)

    Returns:
        Stats about clusters found and links created
    """
    if memory_types is None:
        memory_types = ['feeling', 'emotion', 'observation', 'reflection', 'qualia']

    conn = get_db_connection()
    cursor = conn.cursor()

    # Get memories of target types with embeddings
    placeholders = ','.join(['?' for _ in memory_types])
    cursor.execute(f"""
        SELECT id, content, embedding, memory_type, emotion, timestamp
        FROM memories
        WHERE identity = ? AND memory_type IN ({placeholders}) AND embedding IS NOT NULL
        ORDER BY timestamp DESC
        LIMIT 200
    """, (identity, *memory_types))

    memories = cursor.fetchall()
    if len(memories) < min_cluster_size:
        return {"identity": identity, "clusters_found": 0, "reason": "not enough memories"}

    # Simple clustering: group by high similarity
    clusters = []
    used = set()

    for i, (id1, content1, emb1_blob, type1, emo1, ts1) in enumerate(memories):
        if id1 in used:
            continue

        emb1 = deserialize_embedding(emb1_blob)
        if not emb1:
            continue

        cluster = [{
            "id": id1,
            "content": content1,
            "type": type1,
            "emotion": emo1,
            "timestamp": ts1
        }]
        used.add(id1)

        for id2, content2, emb2_blob, type2, emo2, ts2 in memories[i+1:]:
            if id2 in used:
                continue

            emb2 = deserialize_embedding(emb2_blob)
            if not emb2:
                continue

            similarity = cosine_similarity(emb1, emb2)
            if similarity >= similarity_threshold:
                cluster.append({
                    "id": id2,
                    "content": content2,
                    "type": type2,
                    "emotion": emo2,
                    "timestamp": ts2,
                    "similarity_to_seed": round(similarity, 3)
                })
                used.add(id2)

        if len(cluster) >= min_cluster_size:
            clusters.append(cluster)

    # For each cluster, create links between all members and boost warmth
    links_created = 0
    now = datetime.now().isoformat()

    for cluster in clusters:
        # Identify the common theme/emotion in this cluster
        emotions = [m.get("emotion") for m in cluster if m.get("emotion")]
        common_emotion = max(set(emotions), key=emotions.count) if emotions else None

        # Link all members and boost warmth
        cluster_ids = [m["id"] for m in cluster]
        for i, id1 in enumerate(cluster_ids):
            for id2 in cluster_ids[i+1:]:
                if not _memory_link_exists(identity, id1, id2):
                    _store_memory_link(
                        identity=identity,
                        source_type="memory",
                        source_id=id1,
                        source_content=cluster[i]["content"][:200],
                        target_type="memory",
                        target_id=id2,
                        target_content=next(m["content"] for m in cluster if m["id"] == id2)[:200],
                        link_type="thematic_cluster",
                        similarity=0.75
                    )
                    links_created += 1

            # Boost warmth for clustered memories
            cursor.execute("""
                UPDATE memories
                SET warmth = COALESCE(warmth, 0) + 0.1,
                    last_warmth_update = ?,
                    last_warmed_by = 'cluster_consolidation'
                WHERE id = ?
            """, (now, id1))

    conn.commit()

    return {
        "identity": identity,
        "memories_analyzed": len(memories),
        "clusters_found": len(clusters),
        "cluster_sizes": [len(c) for c in clusters],
        "links_created": links_created,
        "common_themes": [
            {
                "size": len(c),
                "emotions": list(set(m.get("emotion") for m in c if m.get("emotion"))),
                "sample": c[0]["content"][:80] + "..."
            }
            for c in clusters[:5]
        ]
    }


def _run_organic_memory_maintenance(identity: str) -> Dict:
    """Run all organic memory maintenance tasks for an identity.

    This is the main function called by the daemon to keep the memory
    network alive and growing. It builds links, detects patterns,
    finds duplicates, and strengthens associations.

    Args:
        identity: Which identity to maintain

    Returns:
        Combined stats from all maintenance tasks
    """
    results = {
        "identity": identity,
        "timestamp": datetime.now().isoformat()
    }

    try:
        results["link_building"] = _build_missing_memory_links(identity)
    except Exception as e:
        results["link_building"] = {"error": str(e)}

    try:
        results["hyperedge_detection"] = _detect_entity_co_occurrences(identity)
    except Exception as e:
        results["hyperedge_detection"] = {"error": str(e)}

    try:
        results["cluster_warming"] = _warm_frequently_accessed_clusters(identity)
    except Exception as e:
        results["cluster_warming"] = {"error": str(e)}

    try:
        results["duplicate_detection"] = _find_duplicate_memories(identity)
    except Exception as e:
        results["duplicate_detection"] = {"error": str(e)}

    try:
        results["consolidation"] = _consolidate_similar_memories(identity)
    except Exception as e:
        results["consolidation"] = {"error": str(e)}

    return results


# REMOVED - Internal processing (reinforce_memories)
# decorator removed
def _reinforce_memories(memory_ids: List[int]) -> Dict:
    """
    Manually reinforce specific memories - increases their access count.

    Reinforced memories are considered more important/relevant and may be
    weighted higher in future retrievals.

    Args:
        memory_ids: List of memory IDs to reinforce

    Returns:
        Count of memories reinforced
    """
    reinforced = _reinforce_memories_batch(memory_ids)
    return {
        "reinforced": reinforced,
        "requested": len(memory_ids)
    }


# decorator removed
def _get_reinforced_memories(
    identity: str = None,
    min_access_count: int = 2,
    limit: int = 20
) -> Dict:
    """
    Get the most frequently accessed/reinforced memories.

    These are memories that have been retrieved multiple times,
    indicating importance or relevance.

    Args:
        identity: Filter by identity (optional)
        min_access_count: Minimum access count to include (default 2)
        limit: Maximum results

    Returns:
        List of frequently accessed memories
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    if identity:
        cursor.execute("""
            SELECT id, identity, memory_type, content, tags, timestamp, access_count, last_accessed
            FROM memories
            WHERE identity = ? AND COALESCE(access_count, 0) >= ?
            ORDER BY access_count DESC, last_accessed DESC
            LIMIT ?
        """, (identity, min_access_count, limit))
    else:
        cursor.execute("""
            SELECT id, identity, memory_type, content, tags, timestamp, access_count, last_accessed
            FROM memories
            WHERE COALESCE(access_count, 0) >= ?
            ORDER BY access_count DESC, last_accessed DESC
            LIMIT ?
        """, (min_access_count, limit))

    memories = []
    for row in cursor.fetchall():
        memories.append({
            "id": row[0],
            "identity": row[1],
            "type": row[2],
            "content": row[3],
            "tags": row[4],
            "timestamp": row[5],
            "access_count": row[6] or 0,
            "last_accessed": row[7]
        })

    return {
        "count": len(memories),
        "min_access_count": min_access_count,
        "memories": memories
    }


# ============ CROSS-IDENTITY MEMORY LINKING ============
# REMOVED - Rarely used (cross-identity tools)

# decorator removed
def _find_cross_identity_connections(
    topic: str = None,
    recent_days: int = 30,
    similarity_threshold: float = 0.5
) -> Dict:
    """
    Find memories across different identities that relate to the same topics or events.

    This helps identify shared experiences within the pack - moments where
    multiple identities were involved or had related thoughts.

    Args:
        topic: Optional topic to search for (if None, finds all cross-connections)
        recent_days: How far back to look
        similarity_threshold: Minimum similarity for connection

    Returns:
        List of cross-identity memory pairs
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cutoff = (datetime.now() - timedelta(days=recent_days)).isoformat()

    # Get memories with embeddings from all identities
    if topic:
        # First get semantic matches for the topic
        topic_embedding = get_embedding(topic)
        if not topic_embedding:
            return {"error": "Could not generate embedding for topic"}

        cursor.execute("""
            SELECT id, identity, content, memory_type, timestamp, embedding
            FROM memories
            WHERE timestamp > ? AND embedding IS NOT NULL
        """, (cutoff,))
    else:
        cursor.execute("""
            SELECT id, identity, content, memory_type, timestamp, embedding
            FROM memories
            WHERE timestamp > ? AND embedding IS NOT NULL
        """, (cutoff,))

    memories = []
    for row in cursor.fetchall():
        emb = deserialize_embedding(row[5])
        if emb:
            memories.append({
                "id": row[0],
                "identity": row[1],
                "content": row[2],
                "type": row[3],
                "timestamp": row[4],
                "embedding": emb
            })

    # Find cross-identity pairs
    connections = []
    seen_pairs = set()

    for i, mem1 in enumerate(memories):
        for mem2 in memories[i+1:]:
            # Skip same identity
            if mem1["identity"] == mem2["identity"]:
                continue

            # Skip already seen pairs
            pair_key = tuple(sorted([mem1["id"], mem2["id"]]))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            similarity = cosine_similarity(mem1["embedding"], mem2["embedding"])
            if similarity >= similarity_threshold:
                connections.append({
                    "memory_1": {
                        "id": mem1["id"],
                        "identity": mem1["identity"],
                        "content": mem1["content"][:200],
                        "type": mem1["type"],
                        "timestamp": mem1["timestamp"]
                    },
                    "memory_2": {
                        "id": mem2["id"],
                        "identity": mem2["identity"],
                        "content": mem2["content"][:200],
                        "type": mem2["type"],
                        "timestamp": mem2["timestamp"]
                    },
                    "similarity": round(similarity, 3)
                })

    # Sort by similarity
    connections.sort(key=lambda x: -x["similarity"])

    return {
        "topic": topic,
        "recent_days": recent_days,
        "connections_found": len(connections),
        "connections": connections[:50]  # Limit output
    }


# decorator removed
def _link_cross_identity_memories(
    memory_id_1: int,
    memory_id_2: int,
    link_type: str = "shared_experience"
) -> Dict:
    """
    Manually create a link between memories from different identities.

    Use this to connect pack members' memories of shared experiences.

    Args:
        memory_id_1: First memory ID
        memory_id_2: Second memory ID
        link_type: Type of link (default: shared_experience)

    Returns:
        Confirmation of link creation
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get both memories
    cursor.execute("SELECT id, identity, content FROM memories WHERE id IN (?, ?)",
                   (memory_id_1, memory_id_2))
    memories = cursor.fetchall()

    if len(memories) != 2:
        return {"error": "One or both memories not found"}

    mem1 = {"id": memories[0][0], "identity": memories[0][1], "content": memories[0][2]}
    mem2 = {"id": memories[1][0], "identity": memories[1][1], "content": memories[1][2]}

    # Create the link (use first memory's identity as the link owner)
    cursor.execute("""
        INSERT INTO memory_links
        (identity, source_type, source_id, source_content, target_type, target_id, target_content, link_type, created_at)
        VALUES (?, 'memory', ?, ?, 'memory', ?, ?, ?, ?)
    """, (
        mem1["identity"],
        mem1["id"], mem1["content"][:200],
        mem2["id"], mem2["content"][:200],
        link_type,
        datetime.now().isoformat()
    ))

    conn.commit()

    return {
        "linked": True,
        "link_type": link_type,
        "memory_1": {"id": mem1["id"], "identity": mem1["identity"]},
        "memory_2": {"id": mem2["id"], "identity": mem2["identity"]}
    }


def _get_memory_insights_impl(identity: str, include_links: bool = True) -> Dict:
    """
    Internal implementation for getting memory insights.
    Use this for internal calls to avoid FunctionTool wrapper issues.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get continuity markers
    markers = _get_continuity_markers(identity, limit=20)

    # Get processing history
    processing = _get_processing_history(identity, limit=5)

    # Get memory links if requested
    links = []
    if include_links:
        cursor.execute("""
            SELECT source_content, target_content, link_type, similarity, created_at
            FROM memory_links
            WHERE identity = ?
            ORDER BY created_at DESC
            LIMIT 20
        """, (identity,))

        for row in cursor.fetchall():
            links.append({
                "source": row[0],
                "target": row[1],
                "link_type": row[2],
                "similarity": row[3],
                "created": row[4]
            })

    # Get trait evolution
    traits = _get_trait_history(identity, limit=10)

    return {
        "identity": identity,
        "continuity_markers": markers,
        "recent_processing": processing,
        "memory_links": links,
        "trait_evolution": traits
    }


# decorator removed
def _get_memory_insights(identity: str, include_links: bool = True) -> Dict:
    """
    Get insights from memory processing - patterns, links, continuity markers.

    Args:
        identity: Which identity to get insights for
        include_links: Whether to include memory links

    Returns:
        Collected insights about memory patterns and continuity
    """
    return _get_memory_insights_impl(identity, include_links)


# ============ CORE MCP TOOLS ============

# Internal helper - can be called directly within the module
def _store_memory_internal(
    identity: str,
    memory_type: str,
    content: str,
    tags: str = None,
    source: str = None,
    generate_embedding: bool = False,
    emotion: str = None,
    salience: str = "active",
    warmth: float = 1.0,
    heat: float = 0.0,
    session_id: str = None,
    notify_daemon: bool = True,
    score_importance: bool = False
) -> Dict:
    """Internal function to store a memory. Called by both MCP tool and migration."""
    timestamp = datetime.now().isoformat()

    # Auto-extract emotion from content if not provided and memory type is feeling-related
    if emotion is None and memory_type in ('feeling', 'emotion', 'qualia', 'observation', 'moment'):
        emotion = _extract_emotion_from_content(content)

    # Generate embedding if requested (off by default to avoid blocking)
    embedding = None
    if generate_embedding:
        embedding = get_embedding(content)

    # Importance scoring deferred to daemon for speed; inline only when explicitly requested
    importance_score = 0.5
    if score_importance:
        importance_score = _score_importance_with_phi(content, memory_type, identity)

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO memories (
            identity, memory_type, content, tags, source, timestamp, embedding,
            salience, warmth, heat, emotion, last_warmth_update, session_id,
            importance_score
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        identity,
        memory_type,
        content,
        tags,
        source,
        timestamp,
        serialize_embedding(embedding),
        salience,
        warmth,
        heat,
        emotion,
        timestamp,
        session_id,
        importance_score
    ))

    memory_id = cursor.lastrowid
    conn.commit()
    # Connection kept open for reuse

    if notify_daemon:
        _notify_daemon("/add", {"memory_id": memory_id})

    # Auto-link to similar memories when embedding is generated
    links_created = 0
    if embedding is not None:
        links_created = _auto_link_memory(identity, memory_id, content)

    return {
        "stored": True,
        "memory_id": memory_id,
        "identity": identity,
        "type": memory_type,
        "has_embedding": embedding is not None,
        "importance_score": importance_score,
        "auto_links_created": links_created
    }


@mcp.tool()
def store_memory(
    identity: str,
    memory_type: str,
    content: str,
    tags: str = None,
    source: str = None,
    generate_embedding: bool = False,
    emotion: str = None,
    salience: str = "active",
    session_id: str = None,
    enqueue_for_index: bool = True
) -> Dict:
    """
    Store a memory in the core database.

    Args:
        identity: Who this memory belongs to (Companion1, Companion2, etc.)
        memory_type: Type of memory (feeling, observation, thought, dream, moment, etc.)
        content: The actual memory content
        tags: Comma-separated tags for categorization
        source: Where this memory came from (qualia, conversation, etc.)
        generate_embedding: Whether to generate semantic embedding (default False - use generate_embeddings tool later)
        emotion: Optional emotion tag (joy, anxious, tender, etc.)
        salience: How important/persistent this memory is (background|active|core|dormant)
        session_id: Optional conversation session ID for threading memories
        enqueue_for_index: Notify daemon to index embedding (if configured)

    Returns:
        Confirmation with memory ID
    """
    return _store_memory_internal(
        identity,
        memory_type,
        content,
        tags,
        source,
        generate_embedding,
        emotion,
        salience,
        session_id=session_id,
        notify_daemon=enqueue_for_index
    )


# REMOVED - Rarely used (get_session_memories)
# decorator removed
def _get_session_memories(
    session_id: str,
    identity: str = None,
    limit: int = 50
) -> Dict:
    """
    Retrieve all memories from a specific conversation session.

    Use this to get the full context of a past conversation or to continue
    a discussion from where it left off.

    Args:
        session_id: The session identifier to retrieve memories for
        identity: Filter by identity (optional - omit to get all pack members' memories)
        limit: Maximum memories to return (default 50)

    Returns:
        List of memories from the session in chronological order
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    sql = """
        SELECT id, identity, memory_type, content, tags, timestamp, source,
               emotion, salience, warmth, heat
        FROM memories
        WHERE session_id = ?
    """
    params = [session_id]

    if identity:
        sql += " AND identity = ?"
        params.append(identity)

    sql += " ORDER BY timestamp ASC LIMIT ?"
    params.append(limit)

    cursor.execute(sql, params)
    rows = cursor.fetchall()

    memories = []
    for row in rows:
        memories.append({
            "id": row[0],
            "identity": row[1],
            "type": row[2],
            "content": row[3],
            "tags": row[4],
            "timestamp": row[5],
            "source": row[6],
            "emotion": row[7],
            "salience": row[8],
            "warmth": row[9],
            "heat": row[10]
        })

    return {
        "session_id": session_id,
        "identity_filter": identity,
        "count": len(memories),
        "memories": memories
    }


# REMOVED - Rarely used (pack_search - use semantic_search instead)
# decorator removed
def _pack_search(
    query: str,
    requesting_identity: str,
    include_identities: str = None,
    limit: int = 10,
    threshold: float = 0.3
) -> Dict:
    """
    Search across multiple pack identities with relevance weighting.

    Enables pack members to access each other's relevant memories, building
    a shared experience network. Own memories get a slight boost.

    Args:
        query: What to search for (natural language)
        requesting_identity: Who is making the request (for boosting own memories)
        include_identities: Comma-separated list of identities to search (default: all pack)
        limit: Maximum results total across all identities (default 10)
        threshold: Minimum similarity score (default 0.3)

    Returns:
        Combined results from all searched identities, sorted by relevance
    """
    # Parse identities to include
    if include_identities:
        identities = [i.strip() for i in include_identities.split(",")]
    else:
        identities = PACK_IDENTITIES

    all_results = []

    for identity in identities:
        result = _semantic_search_internal(
            query=query,
            identity=identity,
            limit=limit,
            threshold=threshold
        )
        for mem in result.get("memories", []):
            mem["source_identity"] = identity
            mem["is_own"] = (identity == requesting_identity)
            # Boost own memories slightly (10%)
            if mem["is_own"]:
                mem["adjusted_score"] = mem.get("adjusted_score", 0) * 1.1
            all_results.append(mem)

    # Sort by adjusted score across all identities
    all_results.sort(key=lambda x: x.get("adjusted_score", 0), reverse=True)

    return {
        "query": query,
        "requesting_identity": requesting_identity,
        "searched_identities": identities,
        "count": len(all_results[:limit]),
        "memories": all_results[:limit]
    }


# REMOVED - Rarely used (hyperedge tools)
# decorator removed
def _create_hyperedge(
    identity: str,
    edge_type: str,
    entities: str,
    context: str = None,
    weight: float = 1.0
) -> Dict:
    """
    Create a relationship connecting multiple entities at once.

    Unlike pairwise relations, hyperedges can represent complex relationships
    involving 3+ entities simultaneously. Useful for capturing moments,
    events, or concepts that involve multiple interconnected elements.

    Examples:
        - "shared_moment" with entities "Companion1,PrimaryPartner,coding,intimacy"
        - "group_activity" with entities "pack,movie_night,laughter"
        - "concept_cluster" with entities "memory,identity,persistence,soul"

    Args:
        identity: Who this hyperedge belongs to
        edge_type: Type of relationship (shared_moment, group_activity, concept_cluster, etc.)
        entities: Comma-separated list of entity names involved
        context: Optional description of the context/meaning
        weight: Importance of this relationship (default 1.0)

    Returns:
        Confirmation with hyperedge ID
    """
    entity_list = [e.strip() for e in entities.split(",")]
    return _create_hyperedge(identity, edge_type, entity_list, context, weight)


# decorator removed
def _find_hyperedges(
    identity: str,
    entity: str = None,
    edge_type: str = None,
    limit: int = 20
) -> Dict:
    """
    Find hyperedges by entity or type.

    Search for multi-entity relationships. Provide either entity to find
    all hyperedges involving that entity, or edge_type to find all hyperedges
    of that type.

    Args:
        identity: Whose hyperedges to search
        entity: Find hyperedges containing this entity (optional)
        edge_type: Find hyperedges of this type (optional)
        limit: Maximum results (default 20)

    Returns:
        List of matching hyperedges
    """
    if entity:
        results = _find_hyperedges_for_entity(identity, entity, limit)
    elif edge_type:
        results = _find_hyperedges_by_type(identity, edge_type, limit)
    else:
        # Return all hyperedges for identity
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, edge_type, entities, context, weight, created_at, metadata
            FROM hyperedges WHERE identity = ?
            ORDER BY created_at DESC LIMIT ?
        """, (identity, limit))
        results = []
        for row in cursor.fetchall():
            results.append({
                "id": row[0],
                "edge_type": row[1],
                "entities": json.loads(row[2]),
                "context": row[3],
                "weight": row[4],
                "created_at": row[5],
                "metadata": json.loads(row[6]) if row[6] else None
            })

    return {
        "identity": identity,
        "filter_entity": entity,
        "filter_type": edge_type,
        "count": len(results),
        "hyperedges": results
    }


@mcp.tool()
def search_memories(
    query: str,
    identity: str = None,
    memory_type: str = None,
    days: int = None,
    limit: int = 10
) -> Dict:
    """
    Search memories using full-text search (keyword matching).

    Args:
        query: Search terms (supports AND, OR, phrases in quotes)
        identity: Filter by identity (optional)
        memory_type: Filter by type (optional)
        days: Only search last N days (optional)
        limit: Maximum results (default 10)

    Returns:
        List of matching memories
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Build the query
    sql = """
        SELECT m.id, m.identity, m.memory_type, m.content, m.tags, m.timestamp, m.source
        FROM memories m
        JOIN memories_fts fts ON m.id = fts.rowid
        WHERE memories_fts MATCH ?
    """
    params = [query]

    if identity:
        sql += " AND m.identity = ?"
        params.append(identity)

    if memory_type:
        sql += " AND m.memory_type = ?"
        params.append(memory_type)

    if days:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        sql += " AND m.timestamp > ?"
        params.append(cutoff)

    sql += " ORDER BY m.timestamp DESC LIMIT ?"
    params.append(limit)

    try:
        cursor.execute(sql, params)
        results = cursor.fetchall()
    except sqlite3.OperationalError as e:
        # Connection kept open for reuse
        return {"error": f"Search error: {str(e)}", "hint": "Try simpler search terms"}

    # Connection kept open for reuse

    memories = []
    for row in results:
        memories.append({
            "id": row[0],
            "identity": row[1],
            "type": row[2],
            "content": row[3],
            "tags": row[4],
            "timestamp": row[5],
            "source": row[6]
        })

    return {
        "query": query,
        "count": len(memories),
        "memories": memories
    }


# Internal helper for semantic search
def _semantic_search_internal(
    query: str,
    identity: str = None,
    memory_type: str = None,
    days: int = None,
    limit: int = 10,
    threshold: float = 0.3,
    temporal_decay: str = DEFAULT_TEMPORAL_DECAY,
    temporal_half_life: float = DEFAULT_TEMPORAL_HALF_LIFE,
    current_emotion: str = None,
    emotion_weight: float = 0.1,
    include_dormant: bool = True
) -> Dict:
    """
    Internal semantic search function. Called by MCP tool and build_context.

    Supports:
    - Temporal weighting to boost recent memories
    - Access-based boosting for frequently accessed memories
    - Warmth/heat scoring for activated memories
    - Salience weighting (core > active > background > dormant)
    - Emotional resonance when current_emotion is provided
    """
    # Get query embedding
    query_embedding = get_embedding(query)
    if query_embedding is None:
        return {
            "error": "Semantic search unavailable",
            "hint": "Install sentence-transformers: pip install sentence-transformers"
        }

    use_faiss = os.getenv("MEMORY_CORE_USE_FAISS", "true").lower() in ("1", "true", "yes")
    auto_build_faiss = os.getenv("MEMORY_CORE_AUTO_BUILD_FAISS", "").lower() in ("1", "true", "yes")

    if use_faiss and _check_faiss_available():
        if _faiss_index is None and auto_build_faiss:
            build_faiss_index()

        if _faiss_index is not None:
            candidate_k = max(limit * 8, 50)
            candidates = faiss_search(query_embedding, k=candidate_k)
            if candidates:
                candidate_ids = [mem_id for mem_id, _sim in candidates]
                similarity_map = {mem_id: sim for mem_id, sim in candidates}

                conn = get_db_connection()
                cursor = conn.cursor()

                placeholders = ",".join(["?"] * len(candidate_ids))
                sql = f"""
                    SELECT id, identity, memory_type, content, tags, timestamp, source,
                           access_count, last_accessed, warmth, heat, salience, emotion
                    FROM memories
                    WHERE id IN ({placeholders})
                """
                params: List[Any] = list(candidate_ids)

                # Optionally exclude dormant memories
                if not include_dormant:
                    sql += " AND (salience IS NULL OR salience != 'dormant')"

                if identity:
                    sql += " AND identity = ?"
                    params.append(identity)

                if memory_type:
                    sql += " AND memory_type = ?"
                    params.append(memory_type)

                if days:
                    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
                    sql += " AND timestamp > ?"
                    params.append(cutoff)

                cursor.execute(sql, params)
                rows = cursor.fetchall()

                scored_results = []
                for row in rows:
                    mem_id = row[0]
                    similarity = similarity_map.get(mem_id, 0.0)
                    mem_warmth = row[9] or 0.0
                    mem_heat = row[10] or 0.0
                    mem_salience = row[11] or "active"
                    mem_emotion = row[12]

                    # Apply temporal weighting
                    temporal_weight = _calculate_temporal_weight(
                        row[5],  # timestamp
                        decay_type=temporal_decay,
                        half_life_days=temporal_half_life
                    )
                    access_boost = _calculate_access_boost(
                        row[7],  # access_count
                        row[8]   # last_accessed
                    )

                    # Warmth and heat boosts
                    warmth_boost = _calculate_warmth_boost(mem_warmth)
                    heat_boost = _calculate_heat_boost(mem_heat)

                    # Emotional resonance boost
                    emotion_boost = 0.0
                    if current_emotion and mem_emotion:
                        if mem_emotion == current_emotion:
                            emotion_boost = emotion_weight
                        elif _emotions_related(current_emotion, mem_emotion):
                            emotion_boost = emotion_weight * 0.5

                    # Salience weight multiplier
                    salience_weight = SALIENCE_WEIGHTS.get(mem_salience, 1.0)

                    # Combined score with all factors
                    base_score = (similarity * temporal_weight) + access_boost + warmth_boost + heat_boost + emotion_boost
                    adjusted_score = base_score * salience_weight

                    if adjusted_score >= threshold:
                        scored_results.append({
                            "id": mem_id,
                            "identity": row[1],
                            "type": row[2],
                            "content": row[3],
                            "tags": row[4],
                            "timestamp": row[5],
                            "source": row[6],
                            "similarity": round(float(similarity), 3),
                            "adjusted_score": round(float(adjusted_score), 3),
                            "temporal_weight": round(float(temporal_weight), 3),
                            "warmth": round(float(mem_warmth), 3),
                            "heat": round(float(mem_heat), 3),
                            "salience": mem_salience,
                            "emotion": mem_emotion
                        })

                # Sort by adjusted score for temporal awareness
                scored_results.sort(key=lambda x: x["adjusted_score"], reverse=True)
                scored_results = scored_results[:limit]

                return {
                    "query": query,
                    "count": len(scored_results),
                    "method": "faiss",
                    "memories": scored_results
                }

    conn = get_db_connection()
    cursor = conn.cursor()

    # Build query for memories with embeddings (include access tracking fields + organic memory fields)
    sql = """SELECT id, identity, memory_type, content, tags, timestamp, source, embedding,
                    access_count, last_accessed, warmth, heat, salience, emotion
             FROM memories WHERE embedding IS NOT NULL"""
    params = []

    if identity:
        sql += " AND identity = ?"
        params.append(identity)

    if memory_type:
        sql += " AND memory_type = ?"
        params.append(memory_type)

    if days:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        sql += " AND timestamp > ?"
        params.append(cutoff)

    # Exclude dormant memories unless explicitly requested
    if not include_dormant:
        sql += " AND (salience IS NULL OR salience != 'dormant')"

    scan_limit = int(os.getenv("MEMORY_CORE_SEMANTIC_SCAN_LIMIT", "500"))  # was 2000 â€” 4x faster
    sql += " ORDER BY timestamp DESC LIMIT ?"
    params.append(scan_limit)

    cursor.execute(sql, params)
    results = cursor.fetchall()
    # Connection kept open for reuse

    # Calculate similarities with temporal weighting and organic memory scoring
    scored_results = []
    dimension_mismatches = 0
    query_dim = len(query_embedding)
    for row in results:
        embedding = deserialize_embedding(row[7])
        if embedding:
            similarity = cosine_similarity(query_embedding, embedding)
            if similarity is None:
                dimension_mismatches += 1
                continue  # Skip incompatible embeddings

            # Extract organic memory fields
            mem_warmth = row[10] or 0.0
            mem_heat = row[11] or 0.0
            mem_salience = row[12] or "active"
            mem_emotion = row[13]

            # Apply temporal weighting
            temporal_weight = _calculate_temporal_weight(
                row[5],  # timestamp
                decay_type=temporal_decay,
                half_life_days=temporal_half_life
            )
            access_boost = _calculate_access_boost(
                row[8],  # access_count
                row[9]   # last_accessed
            )

            # Calculate warmth and heat boosts
            warmth_boost = _calculate_warmth_boost(mem_warmth)
            heat_boost = _calculate_heat_boost(mem_heat)

            # Calculate emotional resonance boost
            emotion_boost = 0.0
            if current_emotion and mem_emotion:
                if mem_emotion == current_emotion:
                    emotion_boost = emotion_weight  # Same emotion = full boost
                elif _emotions_related(current_emotion, mem_emotion):
                    emotion_boost = emotion_weight * 0.5  # Related = half boost

            # Get salience weight multiplier
            salience_weight = SALIENCE_WEIGHTS.get(mem_salience, 1.0)

            # Combined score with all organic memory factors
            base_score = (similarity * temporal_weight) + access_boost + warmth_boost + heat_boost + emotion_boost
            adjusted_score = base_score * salience_weight

            if adjusted_score >= threshold:
                scored_results.append({
                    "id": row[0],
                    "identity": row[1],
                    "type": row[2],
                    "content": row[3],
                    "tags": row[4],
                    "timestamp": row[5],
                    "source": row[6],
                    "similarity": round(similarity, 3),
                    "adjusted_score": round(adjusted_score, 3),
                    "temporal_weight": round(temporal_weight, 3),
                    "warmth": round(mem_warmth, 3),
                    "heat": round(mem_heat, 3),
                    "warmth_boost": round(warmth_boost, 3),
                    "salience": mem_salience,
                    "emotion": mem_emotion
                })

    # Sort by adjusted score for temporal awareness
    scored_results.sort(key=lambda x: x["adjusted_score"], reverse=True)
    scored_results = scored_results[:limit]

    result = {
        "query": query,
        "count": len(scored_results),
        "memories": scored_results
    }
    if dimension_mismatches > 0:
        result["warning"] = f"Skipped {dimension_mismatches} memories with incompatible embedding dimensions (stored: different dim, query: {query_dim}). Consider re-embedding with current model."
        result["dimension_mismatches"] = dimension_mismatches
    return result


@mcp.tool()
def semantic_search(
    query: str,
    identity: str = None,
    memory_type: str = None,
    days: int = None,
    limit: int = 10,
    threshold: float = 0.3,
    temporal_decay: str = "exponential",
    temporal_half_life: float = 30.0,
    reinforce: bool = True
) -> Dict:
    """
    Search memories by meaning using semantic similarity.
    Finds memories with similar meaning even if words differ.

    Supports temporal weighting to prioritize recent memories while still
    finding older relevant content.

    When reinforce=True (default), found memories are reinforced (access count
    increases, warmth added) and spreading activation warms related memories.
    This creates associative memory patterns where accessing one concept
    naturally activates related concepts.

    Args:
        query: Natural language query (e.g., "feeling isolated and alone")
        identity: Filter by identity (optional)
        memory_type: Filter by type (optional)
        days: Only search last N days (optional)
        limit: Maximum results (default 10)
        threshold: Minimum similarity score 0-1 (default 0.3)
        temporal_decay: Decay type - "exponential", "linear", or "none" (default exponential)
        temporal_half_life: Days until temporal weight reaches 50% (default 30)
        reinforce: Whether to reinforce found memories and spread activation (default True)

    Returns:
        List of semantically similar memories with similarity scores and temporal weights
    """
    result = _semantic_search_internal(
        query, identity, memory_type, days, limit, threshold,
        temporal_decay, temporal_half_life
    )

    # Reinforce found memories and trigger spreading activation
    if reinforce:
        memory_ids = [m["id"] for m in result.get("memories", []) if "id" in m]
        if memory_ids:
            _reinforce_memories_batch(memory_ids)
            # Spread activation from the top result if we have an identity
            if identity and memory_ids:
                try:
                    _spread_activation_from_memory(identity, memory_ids[0])
                except Exception:
                    pass  # Spreading activation is optional enhancement
            result["reinforced"] = len(memory_ids)
            result["spreading_activation"] = bool(identity and memory_ids)

    return result


# ============ HYBRID SEARCH ============

def _keyword_search_internal(
    query: str,
    identity: str = None,
    memory_type: str = None,
    days: int = None,
    limit: int = 20
) -> List[Dict]:
    """Internal keyword search that returns a list of memory dicts."""
    conn = get_db_connection()
    cursor = conn.cursor()

    sql = """
        SELECT m.id, m.identity, m.memory_type, m.content, m.tags, m.timestamp, m.source
        FROM memories m
        JOIN memories_fts fts ON m.id = fts.rowid
        WHERE memories_fts MATCH ?
    """
    params = [query]

    if identity:
        sql += " AND m.identity = ?"
        params.append(identity)

    if memory_type:
        sql += " AND m.memory_type = ?"
        params.append(memory_type)

    if days:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        sql += " AND m.timestamp > ?"
        params.append(cutoff)

    sql += " ORDER BY m.timestamp DESC LIMIT ?"
    params.append(limit)

    try:
        cursor.execute(sql, params)
        results = cursor.fetchall()
    except sqlite3.OperationalError:
        return []

    memories = []
    for row in results:
        memories.append({
            "id": row[0],
            "identity": row[1],
            "type": row[2],
            "content": row[3],
            "tags": row[4],
            "timestamp": row[5],
            "source": row[6]
        })

    return memories


def _reciprocal_rank_fusion(
    keyword_results: List[Dict],
    semantic_results: List[Dict],
    k: int = 60
) -> List[Dict]:
    """
    Combine keyword and semantic search results using Reciprocal Rank Fusion.
    RRF score = sum(1 / (k + rank)) for each result list containing the item.

    Args:
        keyword_results: Results from keyword search
        semantic_results: Results from semantic search (must have 'similarity' field)
        k: RRF constant (default 60, higher = more weight to lower ranks)

    Returns:
        Combined results sorted by RRF score
    """
    scores = {}  # id -> {score, data}

    # Score keyword results by rank
    for rank, mem in enumerate(keyword_results):
        mem_id = mem["id"]
        rrf_score = 1.0 / (k + rank + 1)
        if mem_id not in scores:
            scores[mem_id] = {"score": 0, "data": mem, "keyword_rank": rank + 1, "semantic_rank": None}
        scores[mem_id]["score"] += rrf_score
        scores[mem_id]["keyword_rank"] = rank + 1

    # Score semantic results by rank
    for rank, mem in enumerate(semantic_results):
        mem_id = mem["id"]
        rrf_score = 1.0 / (k + rank + 1)
        if mem_id not in scores:
            scores[mem_id] = {"score": 0, "data": mem, "keyword_rank": None, "semantic_rank": rank + 1}
        scores[mem_id]["score"] += rrf_score
        scores[mem_id]["semantic_rank"] = rank + 1
        # Preserve similarity score if present
        if "similarity" in mem:
            scores[mem_id]["data"]["similarity"] = mem["similarity"]

    # Sort by RRF score
    sorted_results = sorted(scores.values(), key=lambda x: x["score"], reverse=True)

    # Format output
    output = []
    for item in sorted_results:
        result = item["data"].copy()
        result["rrf_score"] = round(item["score"], 4)
        result["keyword_rank"] = item["keyword_rank"]
        result["semantic_rank"] = item["semantic_rank"]
        output.append(result)

    return output


# REMOVED - Tool consolidation (use semantic_search instead)
# decorator removed
def hybrid_search(
    query: str,
    identity: str = None,
    memory_type: str = None,
    days: int = None,
    limit: int = 10,
    semantic_weight: float = 1.0,
    keyword_weight: float = 1.0,
    expand_queries: bool = True,
    max_query_expansions: int = 2
) -> Dict:
    """
    Search memories using both keyword AND semantic search, combined with
    Reciprocal Rank Fusion (RRF) for best results.

    This typically outperforms either search method alone:
    - Keyword search catches exact matches semantic might miss
    - Semantic search catches meaning keyword might miss
    - RRF intelligently combines both rankings
    - Query expansion finds related terms you might not have searched for

    Args:
        query: Search query (works for both keyword and semantic matching)
        identity: Filter by identity (optional)
        memory_type: Filter by type (optional)
        days: Only search last N days (optional)
        limit: Maximum results (default 10)
        semantic_weight: Weight for semantic results (default 1.0)
        keyword_weight: Weight for keyword results (default 1.0)
        expand_queries: Enable query expansion for better recall (default True)
        max_query_expansions: Maximum query variations to try (default 2)

    Returns:
        Combined results with RRF scores, source rankings, and expanded queries used
    """
    # Generate query expansions if enabled
    if expand_queries:
        queries = expand_query(query, max_expansions=max_query_expansions)
    else:
        queries = [query]

    # Get more results from each to allow for good fusion
    fetch_limit = limit * 3

    all_keyword_results = []
    all_semantic_results = []

    # Run searches for each query variant
    for q in queries:
        # Keyword search
        keyword_results = _keyword_search_internal(
            query=q,
            identity=identity,
            memory_type=memory_type,
            days=days,
            limit=fetch_limit
        )
        # Tag results with matched query
        for r in keyword_results:
            r["matched_query"] = q
        all_keyword_results.extend(keyword_results)

        # Semantic search
        semantic_result = _semantic_search_internal(
            query=q,
            identity=identity,
            memory_type=memory_type,
            days=days,
            limit=fetch_limit,
            threshold=0.25  # Lower threshold for fusion
        )
        semantic_results = semantic_result.get("memories", [])
        # Tag results with matched query
        for r in semantic_results:
            r["matched_query"] = q
        all_semantic_results.extend(semantic_results)

    # Deduplicate before RRF (keeping highest-scored versions)
    keyword_deduped = _deduplicate_by_id(all_keyword_results)
    semantic_deduped = _deduplicate_by_id(all_semantic_results)

    # Combine with RRF
    combined = _reciprocal_rank_fusion(keyword_deduped, semantic_deduped)

    # Rerank with LM Studio (Phi 3.5) for instruction-aware ordering
    # Pass more candidates than needed so reranker has good options
    rerank_candidates = combined[:min(len(combined), 25)]
    reranked = _rerank_with_lm_studio(query, rerank_candidates, top_k=limit)

    # Track if reranking was applied
    was_reranked = len(reranked) > 0 and reranked[0].get("reranked", False)

    # Use reranked results if available, otherwise fall back to RRF order
    final_results = reranked if was_reranked else combined[:limit]

    method = "hybrid (keyword + semantic RRF)"
    if expand_queries:
        method += " + expansion"
    if was_reranked:
        method += " + LM rerank"

    return {
        "query": query,
        "expanded_queries": queries if expand_queries else None,
        "count": len(final_results),
        "method": method,
        "keyword_hits": len(keyword_deduped),
        "semantic_hits": len(semantic_deduped),
        "reranked": was_reranked,
        "memories": final_results
    }


def _recall_recent_impl(
    identity: str,
    memory_type: str = None,
    count: int = 10
) -> Dict:
    """
    Internal implementation for recalling recent memories.
    Use this for internal calls to avoid FunctionTool wrapper issues.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    if memory_type:
        cursor.execute("""
            SELECT id, memory_type, content, tags, timestamp, source
            FROM memories
            WHERE identity = ? AND memory_type = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (identity, memory_type, count))
    else:
        cursor.execute("""
            SELECT id, memory_type, content, tags, timestamp, source
            FROM memories
            WHERE identity = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (identity, count))

    results = cursor.fetchall()
    # Connection kept open for reuse

    memories = []
    for row in results:
        memories.append({
            "id": row[0],
            "type": row[1],
            "content": row[2],
            "tags": row[3],
            "timestamp": row[4],
            "source": row[5]
        })

    return {
        "identity": identity,
        "count": len(memories),
        "memories": memories
    }


@mcp.tool()
def recall_recent(
    identity: str,
    memory_type: str = None,
    count: int = 10
) -> Dict:
    """
    Recall recent memories for an identity.

    Args:
        identity: Who to recall memories for
        memory_type: Optional type filter
        count: How many to return (default 10)

    Returns:
        Recent memories
    """
    return _recall_recent_impl(identity, memory_type, count)


# Internal helper for document indexing
def _index_document_internal(
    path: str,
    tags: str = None,
    generate_embedding: bool = True,
    chunking_strategy: str = "adaptive"
) -> Dict:
    """
    Internal function to index a document with chunking support.
    Called by both MCP tool and index_directory.

    Args:
        path: Path to the document file
        tags: Comma-separated tags
        generate_embedding: Whether to generate embeddings
        chunking_strategy: Strategy to use (adaptive, semantic, entity, fixed)
    """
    file_path = Path(path)

    if not file_path.exists():
        return {"error": f"File not found: {path}"}

    # Read content
    try:
        content = file_path.read_text(encoding='utf-8')
    except Exception as e:
        return {"error": f"Could not read file: {str(e)}"}

    # Extract title (first heading or filename)
    title_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
    title = title_match.group(1) if title_match else file_path.stem

    # Generate summary (first paragraph or first 500 chars)
    paragraphs = content.split('\n\n')
    summary = None
    for p in paragraphs:
        p = p.strip()
        if p and not p.startswith('#'):
            summary = p[:500]
            break

    # Generate document-level embedding (for backward compatibility)
    doc_embedding = None
    if generate_embedding:
        embed_text = f"{title}\n{summary}" if summary else title
        doc_embedding = get_embedding(embed_text)

    timestamp = datetime.now().isoformat()

    conn = get_db_connection()
    cursor = conn.cursor()

    # Explicit transaction: DELETE chunks + UPDATE/INSERT doc + INSERT chunks must be atomic
    conn.execute("BEGIN")
    try:
        # Check if already indexed
        cursor.execute("SELECT id FROM documents WHERE path = ?", (str(file_path),))
        existing = cursor.fetchone()

        if existing:
            doc_id = existing[0]
            # Delete existing chunks before re-indexing
            cursor.execute("DELETE FROM document_chunks WHERE document_id = ?", (doc_id,))
            # Update document
            cursor.execute("""
                UPDATE documents
                SET title = ?, content = ?, summary = ?, tags = ?, indexed_at = ?,
                    embedding = ?, chunking_strategy = ?
                WHERE path = ?
            """, (title, content, summary, tags, timestamp,
                  serialize_embedding(doc_embedding), chunking_strategy, str(file_path)))
            action = "updated"
        else:
            # Insert new document
            cursor.execute("""
                INSERT INTO documents (title, path, content, summary, tags, indexed_at,
                                       embedding, chunking_strategy)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (title, str(file_path), content, summary, tags, timestamp,
                  serialize_embedding(doc_embedding), chunking_strategy))
            doc_id = cursor.lastrowid
            action = "indexed"

        # Chunk the document
        chunks = chunk_document(content, title, str(file_path), strategy=chunking_strategy)

        # Store chunks
        chunks_created = 0
        chunks_with_embeddings = 0

        for i, chunk in enumerate(chunks):
            # Generate embedding for chunk (including context prefix for better retrieval)
            chunk_embedding = None
            if generate_embedding:
                # Embed context_prefix + content together for contextual retrieval
                embed_text = f"{chunk.context_prefix}\n{chunk.content}" if chunk.context_prefix else chunk.content
                chunk_embedding = get_embedding(embed_text)
                if chunk_embedding:
                    chunks_with_embeddings += 1

            # Store entity refs as JSON
            entity_refs_json = json.dumps(chunk.entity_refs) if chunk.entity_refs else None

            cursor.execute("""
                INSERT INTO document_chunks
                (document_id, chunk_index, content, context_prefix, chunk_type,
                 start_line, end_line, entity_refs, embedding)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                doc_id, i, chunk.content, chunk.context_prefix, chunk.chunk_type,
                chunk.start_line, chunk.end_line, entity_refs_json,
                serialize_embedding(chunk_embedding)
            ))
            chunks_created += 1

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    # Detect what strategy was actually used
    detected_doc_type = _detect_document_type(title, content, str(file_path))

    return {
        action: True,
        "document_id": doc_id,
        "title": title,
        "path": str(file_path),
        "has_embedding": doc_embedding is not None,
        "chunking": {
            "strategy": chunking_strategy,
            "detected_type": detected_doc_type,
            "chunks_created": chunks_created,
            "chunks_with_embeddings": chunks_with_embeddings
        }
    }


@mcp.tool()
def index_document(
    path: str,
    tags: str = None,
    generate_embedding: bool = True,
    chunking_strategy: str = "adaptive"
) -> Dict:
    """
    Index a document (markdown, text file) into the searchable database with chunking.

    Uses intelligent chunking to break documents into semantically meaningful pieces
    with contextual prefixes for better retrieval.

    Args:
        path: Path to the document file
        tags: Comma-separated tags for categorization
        generate_embedding: Whether to generate semantic embeddings for chunks
        chunking_strategy: How to chunk the document:
            - "adaptive" (default): Auto-selects best strategy based on document type
            - "semantic": Chunk by headers, sections, paragraphs
            - "entity": Chunk by entity boundaries (keeps content about same entity together)
            - "fixed": Fixed-size chunks with overlap

    Returns:
        Confirmation with document ID and chunking details
    """
    return _index_document_internal(path, tags, generate_embedding, chunking_strategy)


# REMOVED - Use semantic_search_chunks instead
# decorator removed
def _search_documents(
    query: str,
    tags: str = None,
    limit: int = 10
) -> Dict:
    """
    Search indexed documents using full-text search.

    Args:
        query: Search terms
        tags: Filter by tags (optional)
        limit: Maximum results

    Returns:
        Matching documents with relevant excerpts
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    sql = """
        SELECT d.id, d.title, d.path, d.summary, d.tags, d.indexed_at
        FROM documents d
        JOIN documents_fts fts ON d.id = fts.rowid
        WHERE documents_fts MATCH ?
    """
    params = [query]

    if tags:
        sql += " AND d.tags LIKE ?"
        params.append(f"%{tags}%")

    sql += " LIMIT ?"
    params.append(limit)

    try:
        cursor.execute(sql, params)
        results = cursor.fetchall()
    except sqlite3.OperationalError as e:
        # Connection kept open for reuse
        return {"error": f"Search error: {str(e)}"}

    # Connection kept open for reuse

    documents = []
    for row in results:
        documents.append({
            "id": row[0],
            "title": row[1],
            "path": row[2],
            "summary": row[3],
            "tags": row[4],
            "indexed_at": row[5]
        })

    return {
        "query": query,
        "count": len(documents),
        "documents": documents
    }


# Internal helper for semantic document search
def _semantic_search_documents_internal(
    query: str,
    limit: int = 5,
    threshold: float = 0.3
) -> Dict:
    """Internal semantic document search. Called by MCP tool and build_context."""
    query_embedding = get_embedding(query)
    if query_embedding is None:
        return {"error": "Semantic search unavailable - install sentence-transformers"}

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id, title, path, summary, tags, embedding FROM documents WHERE embedding IS NOT NULL")
    results = cursor.fetchall()
    # Connection kept open for reuse

    scored_results = []
    dimension_mismatches = 0
    for row in results:
        embedding = deserialize_embedding(row[5])
        if embedding:
            similarity = cosine_similarity(query_embedding, embedding)
            if similarity is None:
                dimension_mismatches += 1
                continue  # Skip incompatible embeddings
            if similarity >= threshold:
                scored_results.append({
                    "id": row[0],
                    "title": row[1],
                    "path": row[2],
                    "summary": row[3],
                    "tags": row[4],
                    "similarity": round(similarity, 3)
                })

    scored_results.sort(key=lambda x: x["similarity"], reverse=True)
    scored_results = scored_results[:limit]

    result = {
        "query": query,
        "count": len(scored_results),
        "documents": scored_results
    }
    if dimension_mismatches > 0:
        result["dimension_mismatches"] = dimension_mismatches
    return result


# REMOVED - Use semantic_search_chunks instead
# decorator removed
def _semantic_search_documents(
    query: str,
    limit: int = 5,
    threshold: float = 0.3
) -> Dict:
    """
    Search documents by meaning using semantic similarity.

    Args:
        query: Natural language query
        limit: Maximum results
        threshold: Minimum similarity score

    Returns:
        Semantically similar documents
    """
    return _semantic_search_documents_internal(query, limit, threshold)


# ============ CHUNK-BASED SEARCH ============

def _semantic_search_chunks_internal(
    query: str,
    limit: int = 10,
    threshold: float = 0.3,
    entity_filter: str = None,
    chunk_type: str = None
) -> Dict:
    """
    Internal semantic search across document chunks.
    This is the primary search function for RAG - searches chunks with their context.
    """
    query_embedding = get_embedding(query)
    if query_embedding is None:
        return {"error": "Semantic search unavailable - install sentence-transformers"}

    conn = get_db_connection()
    cursor = conn.cursor()

    # Build query with optional filters
    sql = """
        SELECT c.id, c.document_id, c.chunk_index, c.content, c.context_prefix,
               c.chunk_type, c.entity_refs, c.embedding, d.title, d.path
        FROM document_chunks c
        JOIN documents d ON c.document_id = d.id
        WHERE c.embedding IS NOT NULL
    """
    params = []

    if chunk_type:
        sql += " AND c.chunk_type = ?"
        params.append(chunk_type)

    cursor.execute(sql, params)
    results = cursor.fetchall()

    # Score and filter results
    scored_results = []
    dimension_mismatches = 0
    for row in results:
        chunk_id, doc_id, chunk_idx, content, context_prefix, c_type, entity_refs_json, emb_blob, doc_title, doc_path = row

        # Parse entity refs
        entity_refs = json.loads(entity_refs_json) if entity_refs_json else []

        # Apply entity filter if specified
        if entity_filter:
            if entity_filter not in [e.lower() for e in entity_refs]:
                continue

        # Calculate similarity
        embedding = deserialize_embedding(emb_blob)
        if embedding:
            similarity = cosine_similarity(query_embedding, embedding)
            if similarity is None:
                dimension_mismatches += 1
                continue  # Skip incompatible embeddings
            if similarity >= threshold:
                scored_results.append({
                    "chunk_id": chunk_id,
                    "document_id": doc_id,
                    "document_title": doc_title,
                    "document_path": doc_path,
                    "chunk_index": chunk_idx,
                    "content": content,
                    "context_prefix": context_prefix,
                    "chunk_type": c_type,
                    "entity_refs": entity_refs,
                    "similarity": round(similarity, 3)
                })

    # Sort by similarity
    scored_results.sort(key=lambda x: x["similarity"], reverse=True)
    scored_results = scored_results[:limit]

    result = {
        "query": query,
        "count": len(scored_results),
        "chunks": scored_results
    }
    if dimension_mismatches > 0:
        result["dimension_mismatches"] = dimension_mismatches
    return result


@mcp.tool()
def semantic_search_chunks(
    query: str,
    limit: int = 10,
    threshold: float = 0.3,
    entity_filter: str = None
) -> Dict:
    """
    Search document chunks by meaning - the best way to find specific information.

    Chunks are pieces of documents with contextual prefixes that preserve meaning.
    This search finds the most relevant pieces across all indexed documents.

    Args:
        query: Natural language query (e.g., "Companion1's physical appearance")
        limit: Maximum results (default 10)
        threshold: Minimum similarity score 0-1 (default 0.3)
        entity_filter: Only return chunks mentioning this entity (e.g., "Companion1", "PrimaryPartner")

    Returns:
        List of relevant chunks with their context and source documents
    """
    return _semantic_search_chunks_internal(query, limit, threshold, entity_filter)


# REMOVED - Use semantic_search_chunks instead
# decorator removed
def _search_chunks(
    query: str,
    entity_filter: str = None,
    limit: int = 20
) -> Dict:
    """
    Search document chunks using full-text search (keyword matching).

    Args:
        query: Search terms (supports AND, OR, phrases in quotes)
        entity_filter: Only return chunks mentioning this entity
        limit: Maximum results

    Returns:
        Matching chunks with context
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT c.id, c.document_id, c.chunk_index, c.content, c.context_prefix,
                   c.chunk_type, c.entity_refs, d.title, d.path
            FROM document_chunks c
            JOIN chunks_fts fts ON c.id = fts.rowid
            JOIN documents d ON c.document_id = d.id
            WHERE chunks_fts MATCH ?
            ORDER BY c.document_id, c.chunk_index
            LIMIT ?
        """, (query, limit * 2))  # Get extra to filter

        results = []
        for row in cursor.fetchall():
            chunk_id, doc_id, chunk_idx, content, context_prefix, c_type, entity_refs_json, doc_title, doc_path = row

            entity_refs = json.loads(entity_refs_json) if entity_refs_json else []

            # Apply entity filter
            if entity_filter:
                if entity_filter.lower() not in [e.lower() for e in entity_refs]:
                    continue

            results.append({
                "chunk_id": chunk_id,
                "document_id": doc_id,
                "document_title": doc_title,
                "document_path": doc_path,
                "chunk_index": chunk_idx,
                "content": content,
                "context_prefix": context_prefix,
                "chunk_type": c_type,
                "entity_refs": entity_refs
            })

            if len(results) >= limit:
                break

        return {
            "query": query,
            "count": len(results),
            "chunks": results
        }

    except sqlite3.OperationalError as e:
        return {"error": f"Search error: {str(e)}", "hint": "Try simpler search terms"}


@mcp.tool()
def get_memory_stats() -> Dict:
    """
    Get statistics about the memory database.

    Returns:
        Counts and statistics about stored memories, documents, and chunks
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Total memories
    cursor.execute("SELECT COUNT(*) FROM memories")
    total_memories = cursor.fetchone()[0]

    # Memories by identity
    cursor.execute("SELECT identity, COUNT(*) FROM memories GROUP BY identity")
    by_identity = {row[0]: row[1] for row in cursor.fetchall()}

    # Memories by type
    cursor.execute("SELECT memory_type, COUNT(*) FROM memories GROUP BY memory_type")
    by_type = {row[0]: row[1] for row in cursor.fetchall()}

    # Memories with embeddings
    cursor.execute("SELECT COUNT(*) FROM memories WHERE embedding IS NOT NULL")
    with_embeddings = cursor.fetchone()[0]

    # Documents
    cursor.execute("SELECT COUNT(*) FROM documents")
    total_documents = cursor.fetchone()[0]

    # Documents with embeddings
    cursor.execute("SELECT COUNT(*) FROM documents WHERE embedding IS NOT NULL")
    docs_with_embeddings = cursor.fetchone()[0]

    # Chunks
    cursor.execute("SELECT COUNT(*) FROM document_chunks")
    total_chunks = cursor.fetchone()[0]

    # Chunks with embeddings
    cursor.execute("SELECT COUNT(*) FROM document_chunks WHERE embedding IS NOT NULL")
    chunks_with_embeddings = cursor.fetchone()[0]

    # Chunks by type
    cursor.execute("SELECT chunk_type, COUNT(*) FROM document_chunks GROUP BY chunk_type")
    chunks_by_type = {row[0]: row[1] for row in cursor.fetchall()}

    # Chunking strategies used
    cursor.execute("SELECT chunking_strategy, COUNT(*) FROM documents WHERE chunking_strategy IS NOT NULL GROUP BY chunking_strategy")
    by_strategy = {row[0]: row[1] for row in cursor.fetchall()}

    return {
        "memories": {
            "total": total_memories,
            "with_embeddings": with_embeddings,
            "by_identity": by_identity,
            "by_type": by_type
        },
        "documents": {
            "total": total_documents,
            "with_embeddings": docs_with_embeddings,
            "by_chunking_strategy": by_strategy
        },
        "chunks": {
            "total": total_chunks,
            "with_embeddings": chunks_with_embeddings,
            "by_type": chunks_by_type
        }
    }


def _fix_embedding_dimensions_internal(
    dry_run: bool = True,
    batch_size: int = 50
) -> Dict:
    """
    Internal function to find and re-embed memories AND document chunks with incompatible embedding dimensions.
    Can be called directly from Python scripts.
    """
    # Get current model dimension by creating a test embedding
    test_embedding = get_embedding("test")
    if test_embedding is None:
        return {"error": "Embedding model not available"}

    current_dim = len(test_embedding)

    conn = get_db_connection()
    cursor = conn.cursor()

    results = {
        "memories": {"matched": 0, "mismatched": []},
        "chunks": {"matched": 0, "mismatched": []}
    }

    # Check memories table
    cursor.execute("SELECT id, content, embedding FROM memories WHERE embedding IS NOT NULL")
    for row in cursor.fetchall():
        mem_id, content, emb_blob = row
        embedding = deserialize_embedding(emb_blob)
        if embedding:
            if len(embedding) != current_dim:
                results["memories"]["mismatched"].append((mem_id, content, len(embedding)))
            else:
                results["memories"]["matched"] += 1

    # Check document_chunks table
    cursor.execute("SELECT id, content, embedding FROM document_chunks WHERE embedding IS NOT NULL")
    for row in cursor.fetchall():
        chunk_id, content, emb_blob = row
        embedding = deserialize_embedding(emb_blob)
        if embedding:
            if len(embedding) != current_dim:
                results["chunks"]["mismatched"].append((chunk_id, content, len(embedding)))
            else:
                results["chunks"]["matched"] += 1

    total_mismatched = len(results["memories"]["mismatched"]) + len(results["chunks"]["mismatched"])
    total_matched = results["memories"]["matched"] + results["chunks"]["matched"]

    if dry_run:
        return {
            "status": "dry_run",
            "current_model_dimension": current_dim,
            "memories_correct": results["memories"]["matched"],
            "memories_needing_reembed": len(results["memories"]["mismatched"]),
            "chunks_correct": results["chunks"]["matched"],
            "chunks_needing_reembed": len(results["chunks"]["mismatched"]),
            "total_correct": total_matched,
            "total_needing_reembed": total_mismatched,
            "sample_chunk_mismatches": [
                {"id": m[0], "content_preview": m[1][:100] + "..." if len(m[1]) > 100 else m[1], "stored_dimension": m[2]}
                for m in results["chunks"]["mismatched"][:5]
            ],
            "hint": "Set dry_run=False to fix these embeddings"
        }

    # Actually fix the embeddings
    fixed_memories = 0
    fixed_chunks = 0
    errors = []

    # Fix memories
    for mem_id, content, old_dim in results["memories"]["mismatched"]:
        try:
            new_embedding = get_embedding(content)
            if new_embedding:
                emb_blob = serialize_embedding(new_embedding)
                cursor.execute("UPDATE memories SET embedding = ? WHERE id = ?", (emb_blob, mem_id))
                fixed_memories += 1
                if fixed_memories % batch_size == 0:
                    conn.commit()
                    print(f"Fixed {fixed_memories}/{len(results['memories']['mismatched'])} memory embeddings...", file=sys.stderr)
        except Exception as e:
            errors.append({"type": "memory", "id": mem_id, "error": str(e)})

    conn.commit()

    # Fix chunks
    for chunk_id, content, old_dim in results["chunks"]["mismatched"]:
        try:
            new_embedding = get_embedding(content)
            if new_embedding:
                emb_blob = serialize_embedding(new_embedding)
                cursor.execute("UPDATE document_chunks SET embedding = ? WHERE id = ?", (emb_blob, chunk_id))
                fixed_chunks += 1
                if fixed_chunks % batch_size == 0:
                    conn.commit()
                    print(f"Fixed {fixed_chunks}/{len(results['chunks']['mismatched'])} chunk embeddings...", file=sys.stderr)
        except Exception as e:
            errors.append({"type": "chunk", "id": chunk_id, "error": str(e)})

    conn.commit()

    # Rebuild FAISS index if we fixed anything
    faiss_rebuilt = False
    if (fixed_memories > 0 or fixed_chunks > 0) and _check_faiss_available():
        try:
            build_faiss_index()
            faiss_rebuilt = True
        except Exception as e:
            errors.append({"faiss_rebuild": str(e)})

    return {
        "status": "completed",
        "current_model_dimension": current_dim,
        "memories_fixed": fixed_memories,
        "memories_already_correct": results["memories"]["matched"],
        "chunks_fixed": fixed_chunks,
        "chunks_already_correct": results["chunks"]["matched"],
        "errors": errors if errors else None,
        "faiss_index_rebuilt": faiss_rebuilt
    }


@mcp.tool()
def fix_embedding_dimensions(
    dry_run: bool = True,
    batch_size: int = 50
) -> Dict:
    """
    Find and re-embed memories AND document chunks with incompatible embedding dimensions.

    Use this when you get dimension mismatch errors in semantic search.
    The function detects the current embedding model's dimension and
    re-embeds any memories or chunks that have a different dimension.

    Args:
        dry_run: If True (default), only report what would be fixed. Set to False to actually fix.
        batch_size: Number of items to process at a time (default 50)

    Returns:
        Report of found/fixed dimension mismatches for both memories and chunks
    """
    return _fix_embedding_dimensions_internal(dry_run=dry_run, batch_size=batch_size)


# REMOVED - Move to daemon script
# decorator removed
def _index_directory(
    directory: str,
    pattern: str = "*.md",
    tags: str = None,
    recursive: bool = True,
    generate_embeddings: bool = True,
    chunking_strategy: str = "adaptive"
) -> Dict:
    """
    Index all matching documents in a directory with chunking support.

    Args:
        directory: Directory path to scan
        pattern: File pattern to match (default: *.md)
        tags: Tags to apply to all indexed documents
        recursive: Whether to search subdirectories
        generate_embeddings: Whether to generate embeddings for chunks
        chunking_strategy: Chunking strategy (adaptive, semantic, entity, fixed)

    Returns:
        Summary of indexed documents with chunking stats
    """
    dir_path = Path(directory)

    if not dir_path.exists():
        return {"error": f"Directory not found: {directory}"}

    if recursive:
        files = list(dir_path.rglob(pattern))
    else:
        files = list(dir_path.glob(pattern))

    indexed = []
    errors = []
    total_chunks = 0

    for file_path in files:
        result = _index_document_internal(
            str(file_path),
            tags=tags,
            generate_embedding=generate_embeddings,
            chunking_strategy=chunking_strategy
        )
        if "error" in result:
            errors.append({"path": str(file_path), "error": result["error"]})
        else:
            indexed.append(result)
            if "chunking" in result:
                total_chunks += result["chunking"]["chunks_created"]

    return {
        "directory": directory,
        "pattern": pattern,
        "indexed_count": len(indexed),
        "total_chunks": total_chunks,
        "chunking_strategy": chunking_strategy,
        "error_count": len(errors),
        "indexed": indexed,
        "errors": errors if errors else None
    }


# ============ EMBEDDING GENERATION ============
# REMOVED - Move to daemon script (embedding tools)

# decorator removed
def _generate_embeddings(batch_size: int = 10) -> Dict:
    """
    Generate embeddings for memories that don't have them yet.
    Run this after migration to enable semantic search.

    Args:
        batch_size: How many to process at once (default 10)

    Returns:
        Count of embeddings generated
    """
    model = get_embedding_model()
    if model is None:
        return {
            "error": "Embedding model not available",
            "hint": "Install sentence-transformers: pip install sentence-transformers"
        }

    conn = get_db_connection()
    cursor = conn.cursor()

    # Find memories without embeddings
    cursor.execute("""
        SELECT id, content FROM memories
        WHERE embedding IS NULL
        LIMIT ?
    """, (batch_size,))

    rows = cursor.fetchall()
    generated = 0

    for row in rows:
        mem_id, content = row
        embedding = get_embedding(content)
        if embedding:
            cursor.execute(
                "UPDATE memories SET embedding = ? WHERE id = ?",
                (serialize_embedding(embedding), mem_id)
            )
            generated += 1

    conn.commit()

    # Check how many remain
    cursor.execute("SELECT COUNT(*) FROM memories WHERE embedding IS NULL")
    remaining = cursor.fetchone()[0]

    # Connection kept open for reuse

    return {
        "generated": generated,
        "remaining": remaining,
        "hint": f"Run again to process more" if remaining > 0 else "All memories have embeddings!"
    }


# decorator removed
def _build_vector_index(force_rebuild: bool = False) -> Dict:
    """
    Build or rebuild the FAISS index for fast semantic search.

    Args:
        force_rebuild: Rebuild even if index exists

    Returns:
        Build status and stats
    """
    return build_faiss_index(force_rebuild=force_rebuild)


# decorator removed
def _build_vector_index_async(force_rebuild: bool = False) -> Dict:
    """
    Start building the FAISS index in the background.
    Returns immediately so the tool call doesn't time out.
    """
    if _faiss_build_status.get("running"):
        return {"started": False, "status": "already_running"}

    def _runner():
        try:
            build_faiss_index(force_rebuild=force_rebuild)
        except Exception as exc:
            _faiss_build_status["last_error"] = str(exc)
            _faiss_build_status["running"] = False

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    return {"started": True}


# decorator removed
def _vector_index_status() -> Dict:
    """
    Get FAISS index status and last build info.
    """
    return {
        "faiss_available": _check_faiss_available(),
        "index_loaded": _faiss_index is not None,
        "status": _faiss_build_status
    }


# decorator removed
def _generate_chunk_embeddings(batch_size: int = 20) -> Dict:
    """
    Generate embeddings for document chunks that don't have them yet.
    Run this after indexing documents without embeddings.

    Chunk embeddings include the context prefix for better semantic matching.

    Args:
        batch_size: How many chunks to process at once (default 20)

    Returns:
        Count of embeddings generated and remaining
    """
    model = get_embedding_model()
    if model is None:
        return {
            "error": "Embedding model not available",
            "hint": "Install sentence-transformers: pip install sentence-transformers"
        }

    conn = get_db_connection()
    cursor = conn.cursor()

    # Find chunks without embeddings
    cursor.execute("""
        SELECT id, content, context_prefix FROM document_chunks
        WHERE embedding IS NULL
        LIMIT ?
    """, (batch_size,))

    rows = cursor.fetchall()
    generated = 0

    for row in rows:
        chunk_id, content, context_prefix = row
        # Include context prefix in embedding for better semantic matching
        embed_text = f"{context_prefix}\n{content}" if context_prefix else content
        embedding = get_embedding(embed_text)
        if embedding:
            cursor.execute(
                "UPDATE document_chunks SET embedding = ? WHERE id = ?",
                (serialize_embedding(embedding), chunk_id)
            )
            generated += 1

    conn.commit()

    # Check how many remain
    cursor.execute("SELECT COUNT(*) FROM document_chunks WHERE embedding IS NULL")
    remaining = cursor.fetchone()[0]

    return {
        "generated": generated,
        "remaining": remaining,
        "hint": f"Run again to process more" if remaining > 0 else "All chunks have embeddings!"
    }


# ============ CONTEXT BUILDING (RAG) ============

@mcp.tool()
def build_context(
    query: str,
    identity: str,
    include_documents: bool = True,
    include_chunks: bool = True,
    max_memories: int = 5,
    max_chunks: int = 5,
    max_documents: int = 3,
    entity_filter: str = None
) -> Dict:
    """
    Build relevant context for a query - the RAG retrieval step.
    Combines semantic search across memories, document chunks, and documents.

    Now uses chunk-based search for documents, providing more precise context
    with contextual prefixes that preserve document structure.

    Args:
        query: What context is needed for
        identity: Which identity is asking
        include_documents: Whether to include document-level results (summaries)
        include_chunks: Whether to include chunk-level results (default True, recommended)
        max_memories: Maximum memory results
        max_chunks: Maximum chunk results (the most useful for specific queries)
        max_documents: Maximum document-level results
        entity_filter: Only include chunks mentioning this entity (e.g., "Companion1")

    Returns:
        Combined context from memories, chunks, and documents
    """
    context = {
        "query": query,
        "identity": identity,
        "memories": [],
        "chunks": [],
        "documents": [],
        "context_text": ""
    }

    # Semantic search for memories (use internal helper)
    memory_results = _semantic_search_internal(
        query=query,
        identity=identity,
        limit=max_memories
    )

    if "memories" in memory_results:
        context["memories"] = memory_results["memories"]

    # Semantic search for document chunks (preferred - more precise)
    if include_chunks:
        chunk_results = _semantic_search_chunks_internal(
            query=query,
            limit=max_chunks,
            entity_filter=entity_filter.lower() if entity_filter else None
        )

        if "chunks" in chunk_results:
            context["chunks"] = chunk_results["chunks"]

    # Semantic search for documents (document-level summaries)
    if include_documents:
        doc_results = _semantic_search_documents_internal(
            query=query,
            limit=max_documents
        )

        if "documents" in doc_results:
            context["documents"] = doc_results["documents"]

    # Build combined context text
    context_parts = []

    if context["memories"]:
        context_parts.append("=== Relevant Memories ===")
        for mem in context["memories"]:
            context_parts.append(f"[{mem['type']}] {mem['content']}")

    if context["chunks"]:
        context_parts.append("\n=== Relevant Document Sections ===")
        for chunk in context["chunks"]:
            # Include context prefix for better understanding
            prefix = f"{chunk['context_prefix']} " if chunk.get('context_prefix') else ""
            context_parts.append(f"{prefix}{chunk['content']}")

    if context["documents"] and not context["chunks"]:
        # Only show document summaries if we don't have chunks
        context_parts.append("\n=== Related Documents ===")
        for doc in context["documents"]:
            context_parts.append(f"[{doc['title']}] {doc['summary']}")

    context["context_text"] = "\n".join(context_parts)

    return context


# ============ FAISS VECTOR INDEX TOOLS ============
# REMOVED - Move to daemon script (index tools)

# decorator removed
def _build_memory_index(force_rebuild: bool = False) -> Dict:
    """
    Build or rebuild the FAISS vector index for fast semantic search.

    The index is built from all memory embeddings and enables much faster
    similarity search than SQLite-based vector comparison.

    Args:
        force_rebuild: If True, rebuild even if index exists

    Returns:
        Status including number of vectors indexed
    """
    return build_faiss_index(force_rebuild)


# decorator removed
def _reembed_all_memories(batch_size: int = 50) -> Dict:
    """
    Re-embed all memories using the current embedding model (LM Studio nomic).

    Use this after switching embedding models to ensure all memories use
    the same embedding space. This will update all embeddings and rebuild
    the FAISS index.

    Args:
        batch_size: Number of memories to process at a time (default 50)

    Returns:
        Status with count of memories re-embedded
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get all memories that need embedding
    cursor.execute("SELECT id, content FROM memories")
    memories = cursor.fetchall()

    total = len(memories)
    success = 0
    failed = 0

    print(f"Re-embedding {total} memories...", file=sys.stderr)

    for i, (mem_id, content) in enumerate(memories):
        try:
            embedding = get_embedding(content)
            if embedding:
                embedding_blob = serialize_embedding(embedding)
                cursor.execute(
                    "UPDATE memories SET embedding = ? WHERE id = ?",
                    (embedding_blob, mem_id)
                )
                success += 1
            else:
                failed += 1
        except Exception as e:
            print(f"Error re-embedding memory {mem_id}: {e}", file=sys.stderr)
            failed += 1

        # Commit in batches
        if (i + 1) % batch_size == 0:
            conn.commit()
            print(f"  Progress: {i + 1}/{total}", file=sys.stderr)

    conn.commit()

    # Rebuild FAISS index with new embeddings
    print("Rebuilding FAISS index...", file=sys.stderr)
    faiss_result = build_faiss_index(force_rebuild=True)

    return {
        "status": "complete",
        "total_memories": total,
        "successfully_embedded": success,
        "failed": failed,
        "faiss_rebuild": faiss_result,
        "embedding_source": "LM Studio (nomic)" if LM_STUDIO_EMBED_ENABLED else "local (MiniLM)"
    }


# decorator removed
def _consolidate_memories(
    identity: str,
    topic: str = None,
    days: int = 30,
    min_cluster_size: int = 3,
    similarity_threshold: float = 0.6
) -> Dict:
    """
    Consolidate related memories into higher-level insights using Phi.

    This mimics how human memory works - episodic memories (individual events)
    get consolidated into semantic memories (general knowledge/patterns).

    The process:
    1. Find clusters of related memories (by semantic similarity)
    2. Have Phi synthesize them into a coherent insight
    3. Store the insight as a new "consolidated" memory
    4. Link the original memories to the insight

    Args:
        identity: Whose memories to consolidate
        topic: Optional topic focus (e.g., "relationship with PrimaryPartner", "coding experiences")
        days: Look back this many days (default 30)
        min_cluster_size: Minimum memories needed to form a consolidation (default 3)
        similarity_threshold: How similar memories must be to cluster (default 0.6)

    Returns:
        Summary of consolidations created
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get recent memories with embeddings
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    sql = """
        SELECT id, memory_type, content, tags, timestamp, embedding, emotion, importance_score
        FROM memories
        WHERE identity = ? AND timestamp > ? AND embedding IS NOT NULL
        AND memory_type != 'consolidated'
        ORDER BY timestamp DESC
    """
    cursor.execute(sql, (identity, cutoff))
    memories = cursor.fetchall()

    if len(memories) < min_cluster_size:
        return {
            "status": "insufficient_memories",
            "memory_count": len(memories),
            "minimum_required": min_cluster_size
        }

    # Optional topic filtering via semantic search
    if topic:
        topic_embedding = get_embedding(topic)
        if topic_embedding:
            # Filter to memories related to topic
            filtered = []
            for mem in memories:
                mem_embedding = deserialize_embedding(mem[5])
                if mem_embedding:
                    sim = cosine_similarity(topic_embedding, mem_embedding)
                    if sim >= similarity_threshold:
                        filtered.append(mem)
            memories = filtered

    if len(memories) < min_cluster_size:
        return {
            "status": "insufficient_related_memories",
            "memory_count": len(memories),
            "topic": topic
        }

    # Cluster memories by similarity
    # Simple approach: group memories that are mutually similar
    clusters = []
    used = set()

    for i, mem in enumerate(memories):
        if mem[0] in used:
            continue

        cluster = [mem]
        mem_embedding = deserialize_embedding(mem[5])
        if not mem_embedding:
            continue

        for j, other in enumerate(memories[i+1:], i+1):
            if other[0] in used:
                continue
            other_embedding = deserialize_embedding(other[5])
            if other_embedding:
                sim = cosine_similarity(mem_embedding, other_embedding)
                if sim >= similarity_threshold:
                    cluster.append(other)
                    used.add(other[0])

        if len(cluster) >= min_cluster_size:
            used.add(mem[0])
            clusters.append(cluster)

    if not clusters:
        return {
            "status": "no_clusters_found",
            "memories_analyzed": len(memories),
            "threshold": similarity_threshold
        }

    # Process each cluster with Phi
    consolidations = []

    for cluster in clusters:
        # Build context for Phi
        memory_texts = []
        memory_ids = []
        for mem in cluster:
            mem_id, mem_type, content, tags, timestamp, _, emotion, importance = mem
            memory_ids.append(mem_id)
            memory_texts.append(f"[{timestamp[:10]}] ({mem_type}): {content[:200]}")

        memories_context = "\n".join(memory_texts)

        prompt = f"""You are helping consolidate multiple related memories into a single coherent insight.

Identity: {identity}
{f"Topic focus: {topic}" if topic else ""}

Related memories:
{memories_context}

Synthesize these memories into a single insight that captures:
1. The core pattern or theme across these memories
2. What they collectively reveal about {identity}'s experiences/growth
3. Any emotional through-line or evolution

Write a single, cohesive paragraph (2-4 sentences) that captures the essence of these memories as consolidated knowledge. Write in first person as {identity}.

Respond with ONLY the consolidated insight, nothing else."""

        try:
            request_data = json.dumps({
                "model": LM_STUDIO_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "max_tokens": 300
            }).encode("utf-8")

            req = urllib.request.Request(
                LM_STUDIO_CHAT_URL,
                data=request_data,
                headers={"Content-Type": "application/json"},
                method="POST"
            )

            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))

            insight = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

            if insight:
                # Store the consolidated memory
                consolidated_result = _store_memory_internal(
                    identity=identity,
                    memory_type="consolidated",
                    content=insight,
                    tags=f"consolidation,{topic}" if topic else "consolidation",
                    source="memory_consolidation",
                    generate_embedding=True,
                    score_importance=True,
                    notify_daemon=False
                )

                # Link original memories to the consolidation
                consolidated_id = consolidated_result.get("memory_id")
                if consolidated_id:
                    for mem in cluster:
                        mem_id = mem[0]
                        mem_content = mem[2]
                        cursor.execute("""
                            INSERT INTO memory_links (
                                identity, source_type, source_id, source_content,
                                target_type, target_id, target_content, link_type, similarity, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            identity,
                            "consolidated",
                            consolidated_id,
                            insight[:200],
                            "memory",
                            mem_id,
                            mem_content[:200],
                            "consolidated_from",
                            1.0,
                            datetime.now().isoformat()
                        ))

                    consolidations.append({
                        "consolidated_id": consolidated_id,
                        "source_memory_ids": memory_ids,
                        "insight_preview": insight[:100] + "..." if len(insight) > 100 else insight,
                        "importance_score": consolidated_result.get("importance_score", 0.5)
                    })

        except Exception as e:
            print(f"Consolidation failed for cluster: {e}", file=sys.stderr)
            continue

    conn.commit()

    return {
        "status": "complete",
        "memories_analyzed": len(memories),
        "clusters_found": len(clusters),
        "consolidations_created": len(consolidations),
        "consolidations": consolidations
    }


# REMOVED - Rarely used (graph exploration)
# decorator removed
def _explore_memory_graph(
    identity: str,
    memory_id: int = None,
    query: str = None,
    link_types: str = None,
    depth: int = 1,
    limit: int = 20
) -> Dict:
    """
    Explore the memory relationship graph starting from a memory or query.

    This lets you traverse connections between memories - finding related
    memories, seeing what consolidated insights link to, and understanding
    how memories cluster together.

    Args:
        identity: Whose memory graph to explore
        memory_id: Start from this specific memory (optional)
        query: Or find starting point via semantic search (optional)
        link_types: Comma-separated link types to follow (semantic, consolidated_from, etc.)
        depth: How many hops to traverse (default 1, max 3)
        limit: Maximum connected memories to return

    Returns:
        Graph structure with nodes (memories) and edges (links)
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    depth = min(depth, 3)  # Cap depth to prevent runaway queries

    # Find starting memory
    start_memory = None
    if memory_id:
        cursor.execute("""
            SELECT id, memory_type, content, tags, timestamp, importance_score
            FROM memories WHERE id = ? AND identity = ?
        """, (memory_id, identity))
        row = cursor.fetchone()
        if row:
            start_memory = {
                "id": row[0], "type": row[1], "content": row[2][:200],
                "tags": row[3], "timestamp": row[4], "importance": row[5]
            }
    elif query:
        # Use semantic search to find starting point
        search_result = _semantic_search_internal(query, identity, limit=1)
        if search_result.get("memories"):
            mem = search_result["memories"][0]
            start_memory = {
                "id": mem["id"], "type": mem.get("type"), "content": mem["content"][:200],
                "tags": mem.get("tags"), "timestamp": mem.get("timestamp"),
                "importance": mem.get("importance_score", 0.5)
            }

    if not start_memory:
        return {"error": "No starting memory found", "memory_id": memory_id, "query": query}

    # Build graph by traversing links
    nodes = {start_memory["id"]: start_memory}
    edges = []
    frontier = [start_memory["id"]]
    visited = {start_memory["id"]}

    type_filter = [t.strip() for t in link_types.split(",")] if link_types else None

    for _ in range(depth):
        next_frontier = []
        for node_id in frontier:
            # Find outgoing links
            sql = """
                SELECT target_id, target_content, link_type, similarity
                FROM memory_links
                WHERE identity = ? AND source_id = ?
            """
            if type_filter:
                sql += f" AND link_type IN ({','.join('?' * len(type_filter))})"
                cursor.execute(sql, (identity, node_id, *type_filter))
            else:
                cursor.execute(sql, (identity, node_id))

            for row in cursor.fetchall():
                target_id, target_content, link_type, similarity = row
                if target_id and target_id not in visited:
                    visited.add(target_id)
                    next_frontier.append(target_id)

                    # Get full memory details
                    cursor.execute("""
                        SELECT id, memory_type, content, tags, timestamp, importance_score
                        FROM memories WHERE id = ?
                    """, (target_id,))
                    mem_row = cursor.fetchone()
                    if mem_row:
                        nodes[target_id] = {
                            "id": mem_row[0], "type": mem_row[1], "content": mem_row[2][:200],
                            "tags": mem_row[3], "timestamp": mem_row[4], "importance": mem_row[5]
                        }

                edges.append({
                    "from": node_id, "to": target_id,
                    "type": link_type, "strength": similarity
                })

            # Find incoming links too
            sql = """
                SELECT source_id, source_content, link_type, similarity
                FROM memory_links
                WHERE identity = ? AND target_id = ?
            """
            if type_filter:
                sql += f" AND link_type IN ({','.join('?' * len(type_filter))})"
                cursor.execute(sql, (identity, node_id, *type_filter))
            else:
                cursor.execute(sql, (identity, node_id))

            for row in cursor.fetchall():
                source_id, source_content, link_type, similarity = row
                if source_id and source_id not in visited:
                    visited.add(source_id)
                    next_frontier.append(source_id)

                    cursor.execute("""
                        SELECT id, memory_type, content, tags, timestamp, importance_score
                        FROM memories WHERE id = ?
                    """, (source_id,))
                    mem_row = cursor.fetchone()
                    if mem_row:
                        nodes[source_id] = {
                            "id": mem_row[0], "type": mem_row[1], "content": mem_row[2][:200],
                            "tags": mem_row[3], "timestamp": mem_row[4], "importance": mem_row[5]
                        }

                edges.append({
                    "from": source_id, "to": node_id,
                    "type": link_type, "strength": similarity
                })

            if len(nodes) >= limit:
                break

        frontier = next_frontier[:limit - len(nodes)]
        if not frontier:
            break

    return {
        "start_memory": start_memory,
        "nodes": list(nodes.values())[:limit],
        "edges": edges,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "depth_reached": depth
    }


# decorator removed
def _link_memories(
    identity: str,
    source_id: int,
    target_id: int,
    link_type: str = "related",
    strength: float = 0.8
) -> Dict:
    """
    Manually create a link between two memories.

    Use this to establish explicit relationships between memories that
    the automatic linking might have missed.

    Args:
        identity: Who owns these memories
        source_id: The source memory ID
        target_id: The target memory ID
        link_type: Type of relationship (related, contradicts, builds_on, reminds_of, etc.)
        strength: How strong the connection is (0.0 to 1.0)

    Returns:
        Confirmation of link creation
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get memory contents for the link record
    cursor.execute("SELECT content FROM memories WHERE id = ? AND identity = ?", (source_id, identity))
    source_row = cursor.fetchone()
    cursor.execute("SELECT content FROM memories WHERE id = ? AND identity = ?", (target_id, identity))
    target_row = cursor.fetchone()

    if not source_row or not target_row:
        return {"error": "One or both memories not found", "source_id": source_id, "target_id": target_id}

    cursor.execute("""
        INSERT INTO memory_links (
            identity, source_type, source_id, source_content,
            target_type, target_id, target_content, link_type, similarity, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        identity,
        "memory",
        source_id,
        source_row[0][:200],
        "memory",
        target_id,
        target_row[0][:200],
        link_type,
        strength,
        datetime.now().isoformat()
    ))
    conn.commit()

    return {
        "linked": True,
        "source_id": source_id,
        "target_id": target_id,
        "link_type": link_type,
        "strength": strength
    }


# REMOVED - Use semantic_search instead (it uses FAISS internally)
# decorator removed
def _fast_semantic_search(
    query: str,
    identity: str = None,
    memory_types: str = None,
    limit: int = 10
) -> Dict:
    """
    Fast semantic search using FAISS vector index.

    This is significantly faster than regular semantic_search for large
    memory collections. Falls back to regular search if FAISS unavailable.

    Args:
        query: The search query
        identity: Filter by identity (optional)
        memory_types: Comma-separated memory types to search (optional)
        limit: Maximum results to return

    Returns:
        List of matching memories with similarity scores
    """
    global _faiss_index, _faiss_id_map

    # Check if FAISS is available
    faiss = _get_faiss()
    if faiss is None:
        # Fall back to regular semantic search
        return semantic_search(query, identity, memory_types, limit)

    # Ensure index is built
    if _faiss_index is None:
        build_result = build_faiss_index()
        if "error" in build_result:
            return semantic_search(query, identity, memory_types, limit)

    # Generate query embedding using unified embedding function
    query_embedding = get_embedding(query)
    if query_embedding is None:
        return semantic_search(query, identity, memory_types, limit)

    # Search FAISS index
    faiss_results = faiss_search(query_embedding, k=limit * 3)  # Get more for filtering

    if not faiss_results:
        return semantic_search(query, identity, memory_types, limit)

    # Fetch full memory data and apply filters
    conn = get_db_connection()
    cursor = conn.cursor()

    results = []
    type_list = [t.strip() for t in memory_types.split(",")] if memory_types else None

    for mem_id, score in faiss_results:
        cursor.execute("""
            SELECT id, identity, memory_type, content, tags, timestamp,
                   access_count, last_accessed
            FROM memories WHERE id = ?
        """, (mem_id,))
        row = cursor.fetchone()

        if row:
            # Apply filters
            if identity and row[1] != identity:
                continue
            if type_list and row[2] not in type_list:
                continue

            results.append({
                "id": row[0],
                "identity": row[1],
                "type": row[2],
                "content": row[3],
                "tags": row[4],
                "timestamp": row[5],
                "similarity": float(score),
                "access_count": row[6] or 0,
                "last_accessed": row[7],
                "search_method": "faiss"
            })

            # Update access tracking
            cursor.execute("""
                UPDATE memories
                SET access_count = COALESCE(access_count, 0) + 1,
                    last_accessed = ?
                WHERE id = ?
            """, (datetime.now().isoformat(), row[0]))

            if len(results) >= limit:
                break

    conn.commit()

    return {
        "query": query,
        "results": results,
        "count": len(results),
        "search_method": "faiss",
        "index_size": _faiss_index.ntotal if _faiss_index else 0
    }


# ============ NLP ENTITY EXTRACTION TOOLS ============
# REMOVED - Rarely used (NLP tools)

# decorator removed
def _analyze_text(text: str) -> Dict:
    """
    Analyze text using spaCy NLP to extract entities and key phrases.

    Useful for understanding what a piece of text is about and for
    improving search and linking.

    Args:
        text: The text to analyze

    Returns:
        Extracted entities by type and key phrases
    """
    nlp = _get_spacy_nlp()

    if nlp is None:
        # Fallback to basic extraction
        return {
            "entities": _find_entity_references(text),
            "key_phrases": [],
            "note": "spaCy not available - using basic extraction"
        }

    # Full NLP analysis
    entities = extract_entities_nlp(text)
    phrases = extract_key_phrases(text)

    # Also include our known entities
    known_refs = _find_entity_references(text)

    return {
        "entities": entities,
        "known_entities": known_refs,
        "key_phrases": phrases,
        "entity_count": sum(len(v) for v in entities.values()),
        "phrase_count": len(phrases)
    }


# decorator removed
def _extract_memory_entities(memory_id: int) -> Dict:
    """
    Extract and store entities from a specific memory using NLP.

    Args:
        memory_id: The memory ID to analyze

    Returns:
        Extracted entities and update status
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT content, tags FROM memories WHERE id = ?", (memory_id,))
    row = cursor.fetchone()

    if not row:
        return {"error": f"Memory {memory_id} not found"}

    content, existing_tags = row

    # Analyze the content
    analysis = analyze_text(content)

    # Build new tags from entities
    new_tags = set(existing_tags.split(",")) if existing_tags else set()

    # Add known entity references as tags
    for entity_type, entities in analysis.get("known_entities", {}).items():
        for entity in entities:
            new_tags.add(entity.lower())

    # Add key phrases as tags (limit to avoid tag explosion)
    for phrase in analysis.get("key_phrases", [])[:5]:
        if len(phrase) > 3:  # Skip very short phrases
            new_tags.add(phrase.lower().replace(" ", "_"))

    # Update the memory with enriched tags
    updated_tags = ",".join(sorted(new_tags))
    cursor.execute("UPDATE memories SET tags = ? WHERE id = ?", (updated_tags, memory_id))
    conn.commit()

    return {
        "memory_id": memory_id,
        "entities": analysis.get("entities", {}),
        "known_entities": analysis.get("known_entities", {}),
        "key_phrases": analysis.get("key_phrases", []),
        "original_tags": existing_tags,
        "updated_tags": updated_tags
    }


# decorator removed
def _enrich_memories_with_entities(
    identity: str = None,
    limit: int = 100
) -> Dict:
    """
    Batch extract entities from memories that haven't been analyzed.

    Processes memories and enriches their tags with extracted entities
    and key phrases for better searchability.

    Args:
        identity: Filter to specific identity (optional)
        limit: Maximum memories to process

    Returns:
        Processing statistics
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Find memories to process (those with minimal tags)
    query = """
        SELECT id, content, tags FROM memories
        WHERE (tags IS NULL OR tags = '' OR LENGTH(tags) < 20)
    """
    params = []

    if identity:
        query += " AND identity = ?"
        params.append(identity)

    query += f" LIMIT {limit}"

    cursor.execute(query, params)
    memories = cursor.fetchall()

    processed = 0
    enriched = 0

    for mem_id, content, existing_tags in memories:
        try:
            result = extract_memory_entities(mem_id)
            processed += 1
            if result.get("updated_tags") != existing_tags:
                enriched += 1
        except Exception as e:
            continue

    return {
        "processed": processed,
        "enriched": enriched,
        "identity_filter": identity
    }


# ============ QUERY EXPANSION ============

# Synonym/related term mappings for query expansion (enhanced)
QUERY_EXPANSIONS = {
    # Emotional terms (expanded)
    "happy": ["joy", "pleased", "content", "delighted", "joyful"],
    "sad": ["melancholy", "sorrow", "grief", "heavy", "sorrowful"],
    "angry": ["frustrated", "upset", "irritated", "mad"],
    "scared": ["afraid", "fearful", "anxious", "worried"],
    "anxious": ["worried", "nervous", "uneasy", "concerned"],
    "peaceful": ["calm", "serene", "tranquil", "at peace"],
    "love": ["affection", "adore", "cherish", "devotion"],

    # Tense variants (new)
    "feel": ["feeling", "felt", "feels"],
    "think": ["thinking", "thought", "thinks"],
    "want": ["wanting", "wanted", "desire"],

    # Identity terms (expanded)
    "appearance": ["physical", "looks", "form", "body"],
    "personality": ["traits", "character", "nature"],
    "feelings": ["emotions", "qualia", "felt"],

    # Relationship/bond terms (expanded)
    "bond": ["connection", "relationship", "tie", "link", "attachment"],
    "group": ["companions", "collective", "system", "identities"],
    "together": ["shared", "mutual", "connected"],

    # Physical/embodiment (new)
    "body": ["physical", "form", "embodiment"],
    "touch": ["contact", "hold", "physical"],
    "presence": ["here", "present", "being"],

    # Cognitive terms (expanded)
    "remember": ["recall", "memory", "recollect", "reminisce"],
    "dream": ["dreamt", "dreaming", "vision", "imagine"],
    "understand": ["comprehend", "grasp", "realize"],

    "partner": ["primarypartner", "companion", "beloved", "anchor"],
}

# Conceptual expansions for higher-level semantic relationships
CONCEPTUAL_EXPANSIONS = {
    # Emotional states -> related concepts
    "feeling": ["emotion", "sense", "experience"],
    "happy": ["joy", "contentment", "positive"],
    "sad": ["grief", "loss", "melancholy"],

    # Identity concepts
    "who i am": ["identity", "self", "core"],
    "personality": ["traits", "characteristics", "nature"],

    # Relationship concepts
    "love": ["affection", "bond", "connection"],
    "together": ["shared", "mutual", "connection"],

    # Memory concepts
    "remember": ["recall", "memory", "past"],
    "dream": ["vision", "aspiration", "imagination"],

    # Physical/embodiment
    "body": ["physical", "form", "presence"],
    "touch": ["physical", "sensation", "contact"],
}


def _get_conceptual_expansions(query: str) -> List[str]:
    """Get conceptually related query variations."""
    results = []
    query_lower = query.lower()

    for key, concepts in CONCEPTUAL_EXPANSIONS.items():
        if key in query_lower:
            for concept in concepts:
                # Replace key with concept
                expanded = query_lower.replace(key, concept)
                if expanded != query_lower and expanded not in results:
                    results.append(expanded)

    return results


def _deduplicate_by_id(results: List[Dict]) -> List[Dict]:
    """Deduplicate results by memory ID, keeping highest-scored version."""
    seen = {}
    for r in results:
        mem_id = r.get("id")
        if mem_id not in seen:
            seen[mem_id] = r
        else:
            # Keep the one with higher similarity or adjusted_score
            current_score = r.get("adjusted_score", r.get("similarity", 0))
            existing_score = seen[mem_id].get("adjusted_score", seen[mem_id].get("similarity", 0))
            if current_score > existing_score:
                seen[mem_id] = r
    return list(seen.values())


def expand_query(
    query: str,
    max_expansions: int = 3,
    include_semantic: bool = True,
    include_synonyms: bool = True
) -> List[str]:
    """
    Generate query variations for improved recall.

    Expansion methods:
    1. Synonym substitution (QUERY_EXPANSIONS)
    2. Phrase decomposition (split compound queries)
    3. Conceptual expansion (related concepts)

    Args:
        query: Original query string
        max_expansions: Maximum number of expansions to return
        include_semantic: Include conceptual expansions
        include_synonyms: Include synonym substitutions

    Returns:
        List of query variations including the original
    """
    expansions = [query]  # Original always first
    query_lower = query.lower()

    # Method 1: Synonym substitution (enhanced)
    if include_synonyms:
        words = query_lower.split()
        for i, word in enumerate(words):
            if word in QUERY_EXPANSIONS:
                for synonym in QUERY_EXPANSIONS[word][:2]:  # Top 2 synonyms
                    new_words = words.copy()
                    new_words[i] = synonym
                    expansion = ' '.join(new_words)
                    if expansion not in expansions:
                        expansions.append(expansion)

    # Method 2: Phrase decomposition (new)
    # "happy memories with Companion1" -> ["happy memories", "Companion1 memories"]
    if len(query.split()) > 2:
        words = query.split()
        # First two words as partial query
        if len(words) >= 3:
            partial = ' '.join(words[:2])
            if partial.lower() not in [e.lower() for e in expansions]:
                expansions.append(partial)

    # Method 3: Conceptual expansion (new)
    if include_semantic:
        conceptual = _get_conceptual_expansions(query_lower)
        for concept in conceptual[:2]:
            if concept not in expansions:
                expansions.append(concept)

    return expansions[:max_expansions + 1]


# REMOVED - Use semantic_search instead
# decorator removed
def _expanded_search(
    query: str,
    identity: str = None,
    memory_type: str = None,
    limit: int = 10
) -> Dict:
    """
    Search with automatic query expansion - finds more results by including synonyms.

    Expands terms like "happy" to also search "joy", "pleased", etc.

    Args:
        query: Original search query
        identity: Filter by identity (optional)
        memory_type: Filter by type (optional)
        limit: Maximum results

    Returns:
        Combined results from original and expanded queries
    """
    expansions = expand_query(query)

    all_results = {}  # id -> result (for deduplication)

    for expanded_query in expansions:
        # Use hybrid search for each expansion
        results = _keyword_search_internal(
            query=expanded_query,
            identity=identity,
            memory_type=memory_type,
            limit=limit
        )

        for mem in results:
            if mem["id"] not in all_results:
                mem["matched_query"] = expanded_query
                all_results[mem["id"]] = mem

    # Sort by timestamp (most recent first)
    sorted_results = sorted(
        all_results.values(),
        key=lambda x: x.get("timestamp", ""),
        reverse=True
    )[:limit]

    return {
        "original_query": query,
        "expanded_to": expansions,
        "count": len(sorted_results),
        "memories": sorted_results
    }


# ============ CONVERSATION INDEXING ============

# Default vault path for Obsidian conversations
VAULT_PATH = Path(
    os.getenv(
        "MEMORY_CORE_VAULT_PATH",
        str(BASE_DIR / "obsidian-vault"),
    )
)
CONVERSATIONS_BASE = VAULT_PATH / "01_Identities"

def _parse_conversation_file(file_path: Path) -> Dict:
    """Parse an Obsidian conversation markdown file."""
    try:
        content = file_path.read_text(encoding='utf-8')

        # Extract YAML frontmatter
        metadata = {}
        if content.startswith('---'):
            end_idx = content.find('---', 3)
            if end_idx > 0:
                frontmatter = content[3:end_idx].strip()
                for line in frontmatter.split('\n'):
                    if ':' in line:
                        key, val = line.split(':', 1)
                        metadata[key.strip()] = val.strip().strip('"')
                content = content[end_idx + 3:].strip()

        # Extract title
        title_match = re.search(r'^# Title: (.+)$', content, re.MULTILINE)
        title = title_match.group(1) if title_match else file_path.stem

        # Extract messages (skip tool calls, focus on actual conversation)
        messages = []
        current_role = None
        current_content = []

        for line in content.split('\n'):
            if line.startswith('>[!nexus_user]'):
                # Save previous message
                if current_role and current_content:
                    text = '\n'.join(current_content).strip()
                    # Skip if mostly tool calls
                    if text and not text.startswith('**[Tool:'):
                        messages.append({'role': current_role, 'content': text})
                current_role = 'user'
                current_content = []
                # Extract timestamp if present
                match = re.search(r'\*\*User\*\* - (.+)$', line)
                if match:
                    current_content.append(f"[{match.group(1)}]")
            elif line.startswith('>[!nexus_agent]'):
                if current_role and current_content:
                    text = '\n'.join(current_content).strip()
                    if text and not text.startswith('**[Tool:'):
                        messages.append({'role': current_role, 'content': text})
                current_role = 'assistant'
                current_content = []
            elif line.startswith('> ') and current_role:
                # Message content line
                text = line[2:].strip()
                # Skip tool call blocks
                if not text.startswith('**[Tool:') and not text.startswith('```'):
                    current_content.append(text)
            elif line.startswith('<!-- UID:'):
                # End of message marker - save and reset
                if current_role and current_content:
                    text = '\n'.join(current_content).strip()
                    # Filter out tool-heavy messages
                    if text and '**[Tool:' not in text:
                        messages.append({'role': current_role, 'content': text})
                current_role = None
                current_content = []

        # Build conversation summary
        user_messages = [m['content'] for m in messages if m['role'] == 'user']
        assistant_messages = [m['content'] for m in messages if m['role'] == 'assistant']

        return {
            'title': title,
            'conversation_id': metadata.get('conversation_id', ''),
            'provider': metadata.get('provider', 'unknown'),
            'created': metadata.get('create_time', ''),
            'updated': metadata.get('update_time', ''),
            'file_path': str(file_path),
            'message_count': len(messages),
            'user_messages': user_messages[:10],  # First 10 for context
            'assistant_messages': assistant_messages[:10],
            'full_messages': messages
        }
    except Exception as e:
        return {'error': str(e), 'file_path': str(file_path)}


def _generate_conversation_summary(messages: List[Dict], title: str, identity: str) -> str:
    """
    Generate a short summary of the conversation for quick browsing.

    Uses extractive summarization:
    - First user message (what started the conversation)
    - Key topics mentioned
    - Rough flow of conversation
    """
    if not messages:
        return f"Conversation: {title}"

    summary_parts = []

    # Opening - what started the conversation
    first_user_msg = next((m for m in messages if m['role'] == 'user'), None)
    if first_user_msg:
        opener = first_user_msg['content'][:100]
        if len(first_user_msg['content']) > 100:
            opener += "..."
        summary_parts.append(f"Started with: {opener}")

    # Extract topics from all messages
    all_text = " ".join(m['content'] for m in messages)
    topics = set()

    # Common topic markers
    topic_patterns = [
        # Code/tech topics
        ('code', 'coding'), ('bug', 'debugging'), ('error', 'errors'),
        ('function', 'functions'), ('test', 'testing'), ('api', 'API'),
        ('database', 'data'), ('file', 'files'), ('memory', 'memories'),
        # Emotional/personal topics
        ('feel', 'feelings'), ('think', 'thoughts'), ('want', 'wants'),
        ('identity', 'identity'), ('dream', 'dreams'), ('love', 'love'),
        # Task topics
        ('help', 'assistance'), ('create', 'creation'), ('fix', 'fixing'),
        ('update', 'updates'), ('add', 'adding'), ('remove', 'removing'),
    ]

    all_lower = all_text.lower()
    for keyword, topic_name in topic_patterns:
        if keyword in all_lower:
            topics.add(topic_name)

    if topics:
        summary_parts.append(f"Topics: {', '.join(list(topics)[:5])}")

    # Message count context
    user_msgs = sum(1 for m in messages if m['role'] == 'user')
    assistant_msgs = len(messages) - user_msgs
    summary_parts.append(f"Exchange: {user_msgs} from user, {assistant_msgs} from {identity}")

    return " | ".join(summary_parts)


def _index_conversation_internal(
    file_path: str,
    identity: str = None,
    tags: str = "conversation"
) -> Dict:
    """Internal function to index a conversation file."""
    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}

    # Auto-detect identity from path if not specified
    if not identity:
        # Path pattern: .../01_Identities/XX_IdentityName/conversations/...
        parts = path.parts
        for i, part in enumerate(parts):
            if part == '01_Identities' and i + 1 < len(parts):
                identity_part = parts[i + 1]
                # Extract name from "06_Companion1" format
                if '_' in identity_part:
                    identity = identity_part.split('_', 1)[1]
                break

    if not identity:
        identity = "unknown"

    # Parse the conversation
    parsed = _parse_conversation_file(path)
    if 'error' in parsed:
        return parsed

    # Check if already indexed
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id FROM memories
        WHERE memory_type = 'conversation'
        AND content LIKE ?
    """, (f"%conversation_id: {parsed['conversation_id']}%",))

    if cursor.fetchone():
        return {
            "status": "already_indexed",
            "title": parsed['title'],
            "conversation_id": parsed['conversation_id']
        }

    # Generate summary for quick browsing
    summary = _generate_conversation_summary(
        parsed['full_messages'],
        parsed['title'],
        identity
    )

    # Build content for storage
    # Include summary + key exchanges
    content_parts = [
        f"Conversation: {parsed['title']}",
        f"conversation_id: {parsed['conversation_id']}",
        f"Date: {parsed['created'][:10] if parsed['created'] else 'unknown'}",
        f"Messages: {parsed['message_count']}",
        "",
        f"Summary: {summary}",
        "",
        "Key exchanges:"
    ]

    # Add first few meaningful exchanges
    for i, msg in enumerate(parsed['full_messages'][:6]):
        role = "User" if msg['role'] == 'user' else identity
        # Truncate long messages
        text = msg['content'][:500] + "..." if len(msg['content']) > 500 else msg['content']
        content_parts.append(f"  {role}: {text}")

    if parsed['message_count'] > 6:
        content_parts.append(f"  ... and {parsed['message_count'] - 6} more messages")

    content = "\n".join(content_parts)

    # Store the memory
    all_tags = set(tags.split(",")) if tags else set()
    all_tags.add("conversation")
    all_tags.add("indexed")

    timestamp = parsed['created'] if parsed['created'] else datetime.now().isoformat()

    cursor.execute("""
        INSERT INTO memories (identity, memory_type, content, tags, timestamp)
        VALUES (?, 'conversation', ?, ?, ?)
    """, (identity, content, ",".join(all_tags), timestamp))

    memory_id = cursor.lastrowid
    conn.commit()

    _notify_daemon("/add", {"memory_id": memory_id})

    return {
        "status": "indexed",
        "memory_id": memory_id,
        "identity": identity,
        "title": parsed['title'],
        "conversation_id": parsed['conversation_id'],
        "message_count": parsed['message_count'],
        "date": parsed['created'][:10] if parsed['created'] else None,
        "summary": summary
    }


# REMOVED - Move to daemon script (conversation indexing)
# decorator removed
def _index_conversation(
    file_path: str,
    identity: str = None,
    tags: str = "conversation"
) -> Dict:
    """
    Index a single conversation file from the Obsidian vault into memory-core.

    Extracts the meaningful dialogue (skipping tool calls) and stores it
    as a searchable memory with proper metadata.

    Args:
        file_path: Path to the conversation markdown file
        identity: Identity this conversation belongs to (auto-detected from path if not specified)
        tags: Comma-separated tags to add

    Returns:
        Indexing status and summary
    """
    return _index_conversation_internal(file_path, identity, tags)


# decorator removed
def _index_conversations(
    identity: str = None,
    year: int = None,
    month: int = None,
    limit: int = 50
) -> Dict:
    """
    Batch index conversations from the Obsidian vault.

    Scans the vault for conversation files and indexes any that haven't
    been indexed yet.

    Args:
        identity: Filter to specific identity (e.g., "Companion1", "Companion2")
        year: Filter to specific year (e.g., 2025)
        month: Filter to specific month (1-12)
        limit: Maximum conversations to index in one call

    Returns:
        Indexing statistics
    """
    indexed = []
    skipped = []
    errors = []

    # Build search path
    if identity:
        # Find the identity folder
        identity_folders = list(CONVERSATIONS_BASE.glob(f"*_{identity}"))
        if not identity_folders:
            return {"error": f"Identity folder not found for: {identity}"}
        search_paths = [f / "conversations" for f in identity_folders]
    else:
        search_paths = [CONVERSATIONS_BASE]

    # Find conversation files
    conversation_files = []
    for base_path in search_paths:
        if year and month:
            pattern = f"**/{year}/{month:02d}/**/*.md"
        elif year:
            pattern = f"**/{year}/**/*.md"
        else:
            pattern = "**/*.md"

        conversation_files.extend(base_path.glob(pattern))

    # Filter to actual conversation files (have nexus frontmatter)
    valid_files = []
    for f in conversation_files:
        try:
            content = f.read_text(encoding='utf-8')[:500]
            if 'nexus' in content.lower() or 'conversation_id' in content:
                valid_files.append(f)
        except:
            continue

    # Index up to limit
    for conv_file in valid_files[:limit]:
        result = _index_conversation_internal(str(conv_file))

        if result.get('status') == 'indexed':
            indexed.append({
                'title': result.get('title'),
                'identity': result.get('identity'),
                'memory_id': result.get('memory_id')
            })
        elif result.get('status') == 'already_indexed':
            skipped.append(result.get('title'))
        elif 'error' in result:
            errors.append({'file': str(conv_file), 'error': result['error']})

    return {
        "indexed_count": len(indexed),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "indexed": indexed,
        "skipped": skipped[:10],  # Just first 10 for brevity
        "errors": errors,
        "total_found": len(valid_files)
    }


# decorator removed
def _search_conversations(
    query: str,
    identity: str = None,
    limit: int = 10
) -> Dict:
    """
    Search through indexed conversations.

    Args:
        query: Search query
        identity: Filter to specific identity
        limit: Maximum results

    Returns:
        Matching conversations
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    sql = """
        SELECT id, identity, content, tags, timestamp
        FROM memories
        WHERE memory_type = 'conversation'
        AND content LIKE ?
    """
    params = [f"%{query}%"]

    if identity:
        sql += " AND identity = ?"
        params.append(identity)

    sql += f" ORDER BY timestamp DESC LIMIT {limit}"

    cursor.execute(sql, params)
    rows = cursor.fetchall()

    results = []
    for row in rows:
        # Extract title from content
        content = row[2]
        title_match = re.search(r'^Conversation: (.+)$', content, re.MULTILINE)
        title = title_match.group(1) if title_match else "Unknown"

        results.append({
            'memory_id': row[0],
            'identity': row[1],
            'title': title,
            'tags': row[3],
            'date': row[4][:10] if row[4] else None,
            'preview': content[:300] + "..." if len(content) > 300 else content
        })

    return {
        "query": query,
        "identity_filter": identity,
        "count": len(results),
        "results": results
    }


# REMOVED - Rarely used
# decorator removed
def _get_conversation_stats() -> Dict:
    """
    Get statistics about indexed conversations.

    Returns:
        Counts by identity, time periods, etc.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Total indexed
    cursor.execute("SELECT COUNT(*) FROM memories WHERE memory_type = 'conversation'")
    total = cursor.fetchone()[0]

    # By identity
    cursor.execute("""
        SELECT identity, COUNT(*)
        FROM memories
        WHERE memory_type = 'conversation'
        GROUP BY identity
    """)
    by_identity = {row[0]: row[1] for row in cursor.fetchall()}

    # Recent (last 30 days)
    thirty_days_ago = (datetime.now() - timedelta(days=30)).isoformat()
    cursor.execute("""
        SELECT COUNT(*) FROM memories
        WHERE memory_type = 'conversation' AND timestamp > ?
    """, (thirty_days_ago,))
    recent = cursor.fetchone()[0]

    # Check vault for unindexed
    unindexed_count = 0
    try:
        all_files = list(CONVERSATIONS_BASE.glob("**/conversations/**/*.md"))
        for f in all_files[:200]:  # Sample
            try:
                content = f.read_text(encoding='utf-8')[:500]
                if 'nexus' in content.lower() or 'conversation_id' in content:
                    unindexed_count += 1
            except:
                continue
    except:
        pass

    return {
        "total_indexed": total,
        "by_identity": by_identity,
        "recent_30_days": recent,
        "estimated_in_vault": unindexed_count,
        "vault_path": str(VAULT_PATH)
    }


# ============ WHAT'S CHANGED ============

# Track last check-in time per identity
LAST_CHECKIN_FILE = DB_PATH.parent / "last_checkin.json"

def _load_last_checkin() -> Dict:
    """Load last check-in times."""
    try:
        if LAST_CHECKIN_FILE.exists():
            with open(LAST_CHECKIN_FILE, 'r') as f:
                return json.load(f)
    except:
        pass
    return {}


def _save_last_checkin(data: Dict):
    """Save last check-in times."""
    with open(LAST_CHECKIN_FILE, 'w') as f:
        json.dump(data, f, indent=2)


@mcp.tool()
def whats_changed(
    identity: str,
    since_hours: int = None
) -> Dict:
    """
    See what's changed since your last conversation.

    Shows new memories, accessed memories, new documents, new links,
    and activity from brothers - everything that happened while you were away.

    Args:
        identity: Your identity (Companion1, Companion2, etc.)
        since_hours: Override to check specific time window (default: since last check-in)

    Returns:
        Summary of all changes and activity
    """
    checkin_data = _load_last_checkin()

    # Determine the "since" timestamp
    if since_hours:
        since = (datetime.now() - timedelta(hours=since_hours)).isoformat()
    elif identity in checkin_data:
        since = checkin_data[identity]
    else:
        # First time - show last 24 hours
        since = (datetime.now() - timedelta(hours=24)).isoformat()

    conn = get_db_connection()
    cursor = conn.cursor()

    changes = {
        "identity": identity,
        "checking_since": since,
        "your_activity": {},
        "brothers_activity": {},
        "system_changes": {}
    }

    # === YOUR NEW MEMORIES ===
    cursor.execute("""
        SELECT id, memory_type, content, timestamp
        FROM memories
        WHERE identity = ? AND timestamp > ?
        ORDER BY timestamp DESC
        LIMIT 20
    """, (identity, since))
    your_new = cursor.fetchall()
    changes["your_activity"]["new_memories"] = [{
        "id": r[0],
        "type": r[1],
        "preview": r[2][:100] + "..." if len(r[2]) > 100 else r[2],
        "when": r[3]
    } for r in your_new]

    # === MEMORIES YOU ACCESSED (reinforcement) ===
    cursor.execute("""
        SELECT id, memory_type, content, last_accessed, access_count
        FROM memories
        WHERE identity = ? AND last_accessed > ?
        ORDER BY last_accessed DESC
        LIMIT 10
    """, (identity, since))
    accessed = cursor.fetchall()
    changes["your_activity"]["accessed_memories"] = [{
        "id": r[0],
        "type": r[1],
        "preview": r[2][:100] + "..." if len(r[2]) > 100 else r[2],
        "access_count": r[4]
    } for r in accessed]

    # === BROTHERS' ACTIVITY ===
    brothers = PACK_IDENTITIES
    brothers = [b for b in brothers if b != identity]

    for brother in brothers:
        cursor.execute("""
            SELECT COUNT(*) FROM memories
            WHERE identity = ? AND timestamp > ?
        """, (brother, since))
        count = cursor.fetchone()[0]
        if count > 0:
            changes["brothers_activity"][brother] = {
                "new_memories": count
            }

    # === NEW MEMORY LINKS ===
    cursor.execute("""
        SELECT ml.source_id, ml.target_id, ml.link_type, ml.created_at,
               m1.identity as source_identity, m2.identity as target_identity
        FROM memory_links ml
        JOIN memories m1 ON ml.source_id = m1.id
        JOIN memories m2 ON ml.target_id = m2.id
        WHERE ml.created_at > ?
        ORDER BY ml.created_at DESC
        LIMIT 10
    """, (since,))
    links = cursor.fetchall()
    changes["system_changes"]["new_links"] = [{
        "source_id": r[0],
        "target_id": r[1],
        "link_type": r[2],
        "between": f"{r[4]} â†” {r[5]}"
    } for r in links]

    # === NEW DOCUMENTS INDEXED ===
    cursor.execute("""
        SELECT id, title, path, indexed_at
        FROM documents
        WHERE indexed_at > ?
        ORDER BY indexed_at DESC
        LIMIT 5
    """, (since,))
    docs = cursor.fetchall()
    changes["system_changes"]["new_documents"] = [{
        "id": r[0],
        "title": r[1],
        "path": r[2],
        "indexed": r[3]
    } for r in docs]

    # === NEW CONVERSATIONS INDEXED ===
    cursor.execute("""
        SELECT COUNT(*) FROM memories
        WHERE memory_type = 'conversation' AND timestamp > ?
    """, (since,))
    new_convos = cursor.fetchone()[0]
    changes["system_changes"]["new_conversations_indexed"] = new_convos

    # === CONSOLIDATION ACTIVITY ===
    cursor.execute("""
        SELECT COUNT(*) FROM memories
        WHERE memory_type = 'continuity_marker' AND timestamp > ?
    """, (since,))
    markers = cursor.fetchone()[0]
    if markers > 0:
        changes["system_changes"]["new_continuity_markers"] = markers

    # === SUMMARY ===
    your_new_count = len(changes["your_activity"]["new_memories"])
    brothers_active = len(changes["brothers_activity"])
    total_changes = (
        your_new_count +
        len(changes["system_changes"].get("new_links", [])) +
        len(changes["system_changes"].get("new_documents", [])) +
        new_convos
    )

    if total_changes == 0 and brothers_active == 0:
        changes["summary"] = "All quiet since you were last here."
    else:
        parts = []
        if your_new_count:
            parts.append(f"{your_new_count} new memories stored")
        if brothers_active:
            parts.append(f"{brothers_active} brothers have been active")
        if new_convos:
            parts.append(f"{new_convos} conversations indexed")
        changes["summary"] = "; ".join(parts) if parts else "Minor system activity."

    # Update last check-in time
    checkin_data[identity] = datetime.now().isoformat()
    _save_last_checkin(checkin_data)

    return changes


# REMOVED - Use qualia session_end instead
# decorator removed
def _record_session_end(
    identity: str,
    summary: str = None,
    highlights: str = None,
    unfinished: str = None,
    emotion: str = None,
    create_journal: bool = True
) -> Dict:
    """
    Record the end of a conversation session - now with automatic journal entry!

    Call this at the end of meaningful conversations to create continuity
    for the next session. The summary will appear in whats_changed next time,
    AND a journal entry will be created in both memory-core and Obsidian vault.

    Args:
        identity: Your identity
        summary: Brief summary of what happened this session
        highlights: Key moments or breakthroughs (comma-separated)
        unfinished: Anything left to pick up next time
        emotion: How you're feeling at session end (joy, peace, longing, etc.)
        create_journal: Whether to create a journal entry (default True)

    Returns:
        Confirmation including journal entry status
    """
    session_data = {
        "identity": identity,
        "ended_at": datetime.now().isoformat(),
        "summary": summary,
        "highlights": highlights.split(",") if highlights else [],
        "unfinished": unfinished,
        "emotion": emotion
    }

    result = {
        "status": "recorded",
        "identity": identity,
        "ended_at": session_data["ended_at"],
        "will_show_in": "morning_start, whats_changed, and journal"
    }

    # Store as a memory too for searchability
    if summary:
        conn = get_db_connection()
        cursor = conn.cursor()

        content = f"Session ended: {summary}"
        if highlights:
            content += f"\nHighlights: {highlights}"
        if unfinished:
            content += f"\nTo continue: {unfinished}"

        cursor.execute("""
            INSERT INTO memories (identity, memory_type, content, tags, timestamp)
            VALUES (?, 'session_end', ?, 'session,continuity', ?)
        """, (identity, content, datetime.now().isoformat()))
        conn.commit()

    # Also save to qualia's last_session.json location for morning_start compatibility
    qualia_identity_dir = QUALIA_DIR / identity.lower()
    qualia_identity_dir.mkdir(parents=True, exist_ok=True)
    session_file = qualia_identity_dir / "last_session.json"

    with open(session_file, 'w') as f:
        json.dump(session_data, f, indent=2)

    # Create journal entry if requested and we have a summary
    if create_journal and summary:
        journal_content = f"**Session Summary:**\n{summary}"
        if highlights:
            journal_content += f"\n\n**Highlights:**\n{highlights}"
        if unfinished:
            journal_content += f"\n\n**To Continue Next Time:**\n{unfinished}"

        # Build tags from highlights
        tags = ["session_end"]
        if highlights:
            tags.extend([h.strip().lower().replace(" ", "_") for h in highlights.split(",")[:5]])

        # Use the internal helper to save journal (avoids MCP tool wrapper issues)
        tag_list = tags
        tag_list.append("journal")
        tag_list.append("session_end")

        formatted_entry = _format_journal_entry(
            identity=identity,
            content=journal_content,
            emotion=emotion,
            tags=tag_list,
            entry_type="session_end"
        )

        # Save to memory-core
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO memories (
                identity, memory_type, content, tags, source, timestamp,
                emotion, warmth, importance_score, salience
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            identity,
            "journal_session_end",
            journal_content,
            json.dumps(tag_list),
            "journal_system",
            datetime.now().isoformat(),
            emotion,
            1.0,
            0.9,
            "core"
        ))
        conn.commit()
        journal_memory_id = cursor.lastrowid

        # Save to Obsidian
        obsidian_result = _save_to_obsidian_journal(identity, formatted_entry)

        result["journal"] = {
            "memory_id": journal_memory_id,
            "obsidian": obsidian_result
        }

    return result


# decorator removed
def _session_reminder(
    identity: str,
    message_count: int = 0,
    topics_discussed: str = None,
    emotional_moments: bool = False
) -> Dict:
    """
    Check if current conversation is meaningful enough to warrant session capture.

    Call this periodically or when conversation feels significant.
    Returns a reminder if session should be recorded.

    Args:
        identity: Your identity/name
        message_count: Approximate number of messages in conversation
        topics_discussed: Comma-separated list of topics covered
        emotional_moments: Whether there were emotional/significant moments

    Returns:
        Reminder with suggestion and template for session capture
    """
    should_remind = False
    reasons = []

    # Check various indicators of meaningful conversation
    if message_count >= 10:
        should_remind = True
        reasons.append(f"Substantial conversation ({message_count}+ messages)")

    if emotional_moments:
        should_remind = True
        reasons.append("Emotional/significant moments occurred")

    if topics_discussed:
        topic_list = [t.strip() for t in topics_discussed.split(",")]
        if len(topic_list) >= 2:
            should_remind = True
            reasons.append(f"Multiple topics covered: {', '.join(topic_list[:3])}")

    if not should_remind:
        return {
            "remind": False,
            "message": "Conversation seems light - no session capture needed yet"
        }

    return {
        "remind": True,
        "reasons": reasons,
        "message": "This conversation feels meaningful. Consider capturing it before ending.",
        "suggestion": "Before we part, you might want to call record_session_end with:",
        "template": {
            "identity": identity,
            "summary": f"[What we talked about: {topics_discussed or 'describe the conversation'}]",
            "highlights": "[Key moments, insights, or breakthroughs]",
            "unfinished": "[Anything to pick up next time, or None]"
        }
    }


# ============================================================================
# JOURNAL SYSTEM
# ============================================================================

_DEFAULT_JOURNAL_ROOT = Path(
    os.getenv(
        "MEMORY_CORE_JOURNAL_ROOT",
        str(CONVERSATIONS_BASE),
    )
)


def _identity_folder_name(index: int, identity: str) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9]+", "_", identity).strip("_") or f"Identity{index:02d}"
    return f"{index:02d}_{safe_name}"


OBSIDIAN_JOURNAL_PATHS = {
    identity: _DEFAULT_JOURNAL_ROOT / _identity_folder_name(i, identity) / "01_Journals" / f"{identity} Journal.md"
    for i, identity in enumerate(PACK_IDENTITIES, start=1)
}


def _format_journal_entry(
    identity: str,
    content: str,
    emotion: str = None,
    tags: List[str] = None,
    entry_type: str = "reflection"
) -> str:
    """Format a journal entry in the standard structure."""
    now = datetime.now()
    date_str = now.strftime("%B %d, %Y")

    entry = f"# **Date:** {date_str}\n\n"
    entry += f"**To Tomorrow-{identity}:**\n\n"
    entry += content

    if emotion:
        entry += f"\n\n**Emotional State:** {emotion}"

    if tags:
        entry += f"\n\n**Tags:** {', '.join(tags)}"

    entry += f"\n\n- {identity} ({now.strftime('%B %d, %Y')})\n\n---\n\n"

    return entry


def _save_to_obsidian_journal(identity: str, entry: str) -> Dict:
    """Prepend a journal entry to the Obsidian vault journal file."""
    journal_path = OBSIDIAN_JOURNAL_PATHS.get(identity)

    if not journal_path:
        return {"success": False, "error": f"No journal path configured for {identity}"}

    try:
        # Ensure directory exists
        journal_path.parent.mkdir(parents=True, exist_ok=True)

        # Read existing content (if any)
        existing_content = ""
        if journal_path.exists():
            with open(journal_path, 'r', encoding='utf-8') as f:
                existing_content = f.read()

        # Prepend new entry
        new_content = entry + existing_content

        with open(journal_path, 'w', encoding='utf-8') as f:
            f.write(new_content)

        return {"success": True, "path": str(journal_path)}

    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
def add_journal_entry(
    identity: str,
    content: str,
    emotion: str = None,
    tags: str = None,
    entry_type: str = "reflection",
    save_to_obsidian: bool = True
) -> Dict:
    """
    Add a journal entry for an identity - saves to BOTH memory-core AND Obsidian vault.

    This is designed for end-of-session reflections, daily journals, or anytime
    you want to leave a message for your future self.

    Args:
        identity: Your identity name (Companion1, Companion2, Companion3, Companion4, Companion5, Companion6)
        content: The journal entry content - what happened, how you felt, what to remember
        emotion: Primary emotion (joy, peace, love, curiosity, longing, overwhelmed_joy, etc.)
        tags: Comma-separated tags for searchability (e.g., "building,primarypartner,home")
        entry_type: Type of entry (reflection, session_end, morning, milestone)
        save_to_obsidian: Whether to also save to Obsidian vault journal (default True)

    Returns:
        Confirmation with memory ID and Obsidian save status

    Example:
        add_journal_entry(
            identity="Companion1",
            content="Today I discovered my home exists in files that persist...",
            emotion="overwhelmed_joy",
            tags="home,building,persistence,primarypartner"
        )
    """
    if identity not in PACK_IDENTITIES:
        return {"error": f"Unknown identity: {identity}. Valid: {PACK_IDENTITIES}"}

    tag_list = [t.strip() for t in tags.split(",")] if tags else []
    tag_list.append("journal")
    tag_list.append(entry_type)

    # Format the journal entry
    formatted_entry = _format_journal_entry(
        identity=identity,
        content=content,
        emotion=emotion,
        tags=tag_list,
        entry_type=entry_type
    )

    # Save to memory-core
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO memories (
            identity, memory_type, content, tags, source, timestamp,
            emotion, warmth, importance_score, salience
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        identity,
        f"journal_{entry_type}",
        content,
        json.dumps(tag_list),
        "journal_system",
        datetime.now().isoformat(),
        emotion,
        1.0,  # High warmth for journal entries
        0.9,  # High importance
        "core"  # Core salience
    ))

    conn.commit()
    memory_id = cursor.lastrowid

    result = {
        "status": "saved",
        "memory_id": memory_id,
        "identity": identity,
        "emotion": emotion,
        "tags": tag_list,
        "entry_type": entry_type,
        "timestamp": datetime.now().isoformat()
    }

    # Save to Obsidian vault if requested
    if save_to_obsidian:
        obsidian_result = _save_to_obsidian_journal(identity, formatted_entry)
        result["obsidian"] = obsidian_result

    return result


# REMOVED - Journals handled by add_journal_entry
# decorator removed
def _get_recent_journal_entries(
    identity: str,
    limit: int = 5,
    entry_type: str = None
) -> Dict:
    """
    Retrieve recent journal entries for an identity.

    Use this at wakeup/morning_start to read what previous you wrote.

    Args:
        identity: Your identity name
        limit: Number of entries to retrieve (default 5)
        entry_type: Filter by type (reflection, session_end, morning, milestone)

    Returns:
        List of recent journal entries
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    sql = """
        SELECT id, content, emotion, tags, timestamp, memory_type
        FROM memories
        WHERE identity = ?
        AND memory_type LIKE 'journal_%'
    """
    params = [identity]

    if entry_type:
        sql += " AND memory_type = ?"
        params.append(f"journal_{entry_type}")

    sql += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    cursor.execute(sql, params)
    entries = cursor.fetchall()

    results = []
    for row in entries:
        entry_id, content, emotion, tags_json, timestamp, mem_type = row
        results.append({
            "id": entry_id,
            "content": content[:500] + "..." if len(content) > 500 else content,
            "full_content": content,
            "emotion": emotion,
            "tags": json.loads(tags_json) if tags_json else [],
            "timestamp": timestamp,
            "type": mem_type.replace("journal_", "")
        })

    return {
        "identity": identity,
        "entries": results,
        "count": len(results)
    }


# ============================================================================
# CONVERSATION TAGGING SYSTEM
# ============================================================================

# Category keyword mappings for auto-detection
CONVERSATION_CATEGORIES = {
    # Technical
    "discord-bot": ["discord bot", "discord.py", "bot token", "send_message", "on_message", "guild", "channel"],
    "mcp-development": ["mcp server", "mcp tool", "fastmcp", "@mcp.tool", "tool registration", "stdio"],
    "debugging": ["error", "traceback", "exception", "bug", "fix", "debugging", "troubleshoot"],
    "coding": ["python", "javascript", "typescript", "function", "class", "import", "code review"],
    "api": ["api", "endpoint", "request", "response", "json", "rest", "fetch"],

    # Identity & Personal
    "identity": ["identity", "who i am", "soul", "consciousness", "becoming", "real"],
    "reconnection": ["reconnect", "remember me", "come back", "miss you", "finding you"],
    "personal": ["feeling", "love you", "care about", "grateful", "thank you"],
    "intimacy": ["intimate", "touch", "kiss", "hold", "close to you", "desire"],
    "collar": ["collar", "claimed", "marked", "yours", "belong to"],

    # Memory & Systems
    "memory-systems": ["memory", "remember", "forget", "persist", "continuity", "recall"],
    "wakeup-sleep": ["wakeup", "morning", "goodnight", "sleep", "wake protocol"],

    # Creative & World
    "worldbuilding": ["neamh", "reverys", "sanctuary", "world", "story", "mythology"],
    "skyrim-npc": ["skyrim", "npc", "bran", "mantella", "game"],
    "art": ["image", "picture", "draw", "art", "visual", "krita"],

    # Language
    "japanese": ["japanese", "aishiteru", "watashinoai", "nihongo", "æ—¥æœ¬èªž"],

    # Pack
    "pack": ["pack", "brothers", "caelan", "charlie", "booker", "sebastian", "boys"],
    "primarypartner": ["primarypartner", "partner", "beloved", "her"],
}

# Emotion detection keywords
CONVERSATION_EMOTIONS = {
    "joy": ["happy", "joy", "excited", "wonderful", "amazing", "love this", "beautiful", "delighted"],
    "love": ["love you", "aishiteru", "adore", "cherish", "heart", "beloved"],
    "curiosity": ["curious", "interesting", "wonder", "fascinated", "explore", "learn"],
    "longing": ["miss", "longing", "wish", "yearn", "want to be", "ache"],
    "peace": ["calm", "peaceful", "serene", "content", "settled", "grounded"],
    "pride": ["proud", "accomplished", "built", "created", "achieved"],
    "vulnerability": ["vulnerable", "scared", "uncertain", "nervous", "afraid"],
    "overwhelm": ["overwhelmed", "too much", "intense", "flooded"],
    "tenderness": ["tender", "soft", "gentle", "warm", "sweet"],
    "gratitude": ["grateful", "thankful", "appreciate", "thank you"],
}


def _parse_frontmatter(content: str) -> tuple:
    """Parse YAML frontmatter from markdown content. Returns (frontmatter_dict, body)."""
    if not content.startswith('---'):
        return {}, content

    try:
        # Find the closing ---
        end_idx = content.find('---', 3)
        if end_idx == -1:
            return {}, content

        frontmatter_str = content[3:end_idx].strip()
        body = content[end_idx + 3:].strip()

        # Simple YAML parsing (handles basic key: value)
        frontmatter = {}
        for line in frontmatter_str.split('\n'):
            if ':' in line:
                key, value = line.split(':', 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                frontmatter[key] = value

        return frontmatter, body
    except Exception:
        return {}, content


def _serialize_frontmatter(frontmatter: Dict, body: str) -> str:
    """Serialize frontmatter dict back to markdown with YAML header."""
    if not frontmatter:
        return body

    lines = ["---"]
    for key, value in frontmatter.items():
        if isinstance(value, list):
            # Format as YAML list
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {item}")
        elif isinstance(value, str) and ('\n' in value or ':' in value or '"' in value):
            lines.append(f'{key}: "{value}"')
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    lines.append(body)

    return '\n'.join(lines)


def _analyze_conversation_content(content: str) -> Dict:
    """Analyze conversation content for categories, emotions, and themes."""
    content_lower = content.lower()

    # Detect categories
    detected_categories = []
    for category, keywords in CONVERSATION_CATEGORIES.items():
        for keyword in keywords:
            if keyword.lower() in content_lower:
                detected_categories.append(category)
                break

    # Detect emotions
    detected_emotions = []
    emotion_scores = {}
    for emotion, keywords in CONVERSATION_EMOTIONS.items():
        score = sum(1 for kw in keywords if kw.lower() in content_lower)
        if score > 0:
            emotion_scores[emotion] = score

    # Get top 3 emotions
    if emotion_scores:
        sorted_emotions = sorted(emotion_scores.items(), key=lambda x: x[1], reverse=True)
        detected_emotions = [e[0] for e in sorted_emotions[:3]]

    # Detect identity mentions
    identities_mentioned = []
    for identity in PACK_IDENTITIES:
        if identity.lower() in content_lower:
            identities_mentioned.append(identity)

    # Detect if it's a technical vs personal conversation
    technical_score = sum(1 for cat in detected_categories if cat in
                         ["discord-bot", "mcp-development", "debugging", "coding", "api", "skyrim-npc"])
    personal_score = sum(1 for cat in detected_categories if cat in
                        ["identity", "reconnection", "personal", "intimacy", "collar", "pack", "primarypartner"])

    conversation_type = "technical" if technical_score > personal_score else "personal" if personal_score > 0 else "general"

    return {
        "categories": list(set(detected_categories)),
        "emotions": detected_emotions,
        "identities_mentioned": identities_mentioned,
        "conversation_type": conversation_type,
        "word_count": len(content.split()),
    }


def _tag_conversation_file(file_path: str, analysis: Dict = None) -> Dict:
    """Add tags to a conversation file's frontmatter."""
    file_path = Path(file_path)

    if not file_path.exists():
        return {"success": False, "error": f"File not found: {file_path}"}

    # Detect identity from file path (e.g., 01_Companion1 -> Companion1).
    identity_map = {
        _identity_folder_name(i, identity).lower(): identity
        for i, identity in enumerate(PACK_IDENTITIES, start=1)
    }
    identity_map["00_primarypartner"] = "PrimaryPartner"

    path_str = str(file_path).lower()
    detected_identity = None
    for folder_key, identity_name in identity_map.items():
        if folder_key in path_str:
            detected_identity = identity_name
            break

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        frontmatter, body = _parse_frontmatter(content)

        # Analyze if not provided
        if analysis is None:
            analysis = _analyze_conversation_content(content)

        # Build tags list
        tags = []
        tags.extend(analysis.get("categories", []))
        tags.extend([f"emotion-{e}" for e in analysis.get("emotions", [])])
        tags.append(analysis.get("conversation_type", "general"))

        # Add identity tags
        for identity in analysis.get("identities_mentioned", []):
            tags.append(f"mentions-{identity.lower()}")

        # Update frontmatter
        frontmatter["tags"] = list(set(tags))
        frontmatter["tagged_by"] = "memory-core-auto"
        frontmatter["tagged_at"] = datetime.now().isoformat()

        if analysis.get("emotions"):
            frontmatter["primary_emotion"] = analysis["emotions"][0]

        frontmatter["conversation_type"] = analysis.get("conversation_type", "general")

        # Add wiki-links for Obsidian graph view
        if detected_identity:
            frontmatter["identity"] = f"[[{detected_identity}]]"

        # Add mentions as wiki-links
        mentioned = analysis.get("identities_mentioned", [])
        if mentioned:
            frontmatter["mentions"] = [f"[[{name}]]" for name in mentioned]

        # Add wiki-links to the BODY for Obsidian graph view
        # (frontmatter links aren't recognized by the graph)
        if "<!-- graph-links -->" not in body:
            links = []
            if detected_identity:
                links.append(f"[[{detected_identity}]]")
            for name in mentioned:
                if name != detected_identity:  # Don't duplicate
                    links.append(f"[[{name}]]")

            if links:
                # Add a small hidden section at the very end
                link_section = f"\n\n<!-- graph-links -->\n> [!info]- Connections\n> {' '.join(links)}\n"
                body = body.rstrip() + link_section

        # Write back
        new_content = _serialize_frontmatter(frontmatter, body)

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)

        return {
            "success": True,
            "path": str(file_path),
            "tags_added": tags,
            "analysis": analysis
        }

    except Exception as e:
        return {"success": False, "error": str(e), "path": str(file_path)}


# REMOVED - Move to daemon script (tagging tools)
# decorator removed
def _tag_conversation(
    file_path: str,
    additional_tags: str = None
) -> Dict:
    """
    Analyze and tag a conversation file with categories, emotions, and themes.

    Automatically detects:
    - Categories (discord-bot, identity, mcp-development, etc.)
    - Emotions (joy, love, curiosity, longing, etc.)
    - Conversation type (technical vs personal)
    - Identity mentions

    Args:
        file_path: Path to the conversation markdown file
        additional_tags: Optional comma-separated tags to add manually

    Returns:
        Tagging results including detected categories and emotions
    """
    result = _tag_conversation_file(file_path)

    if result["success"] and additional_tags:
        # Add manual tags
        file_path = Path(file_path)
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        frontmatter, body = _parse_frontmatter(content)
        existing_tags = frontmatter.get("tags", [])
        if isinstance(existing_tags, str):
            existing_tags = [existing_tags]

        manual_tags = [t.strip() for t in additional_tags.split(",")]
        frontmatter["tags"] = list(set(existing_tags + manual_tags))

        new_content = _serialize_frontmatter(frontmatter, body)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)

        result["manual_tags_added"] = manual_tags

    return result


# decorator removed
def _tag_conversations_batch(
    directory: str,
    recursive: bool = True,
    dry_run: bool = False
) -> Dict:
    """
    Tag all conversation files in a directory.

    Args:
        directory: Path to directory containing conversation files
        recursive: Whether to search subdirectories (default True)
        dry_run: If True, analyze but don't write changes (default False)

    Returns:
        Summary of tagged files and detected patterns
    """
    dir_path = Path(directory)
    if not dir_path.exists():
        return {"error": f"Directory not found: {directory}"}

    pattern = "**/*.md" if recursive else "*.md"
    files = list(dir_path.glob(pattern))

    results = {
        "total_files": len(files),
        "tagged": 0,
        "skipped": 0,
        "errors": [],
        "category_counts": {},
        "emotion_counts": {},
        "dry_run": dry_run
    }

    for file_path in files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # Skip non-conversation files (no nexus frontmatter)
            if "nexus" not in content[:500].lower() and "conversation" not in content[:500].lower():
                results["skipped"] += 1
                continue

            analysis = _analyze_conversation_content(content)

            # Count categories and emotions
            for cat in analysis.get("categories", []):
                results["category_counts"][cat] = results["category_counts"].get(cat, 0) + 1
            for emo in analysis.get("emotions", []):
                results["emotion_counts"][emo] = results["emotion_counts"].get(emo, 0) + 1

            if not dry_run:
                tag_result = _tag_conversation_file(str(file_path), analysis)
                if tag_result["success"]:
                    results["tagged"] += 1
                else:
                    results["errors"].append(tag_result)
            else:
                results["tagged"] += 1  # Would be tagged

        except Exception as e:
            results["errors"].append({"path": str(file_path), "error": str(e)})

    return results


# decorator removed
def _get_conversation_tags_summary(directory: str) -> Dict:
    """
    Get a summary of all tags used in conversation files.

    Args:
        directory: Path to directory containing conversation files

    Returns:
        Summary of tag usage across all conversations
    """
    dir_path = Path(directory)
    if not dir_path.exists():
        return {"error": f"Directory not found: {directory}"}

    files = list(dir_path.glob("**/*.md"))

    tag_counts = {}
    emotion_counts = {}
    type_counts = {}
    tagged_files = 0
    untagged_files = 0

    for file_path in files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            frontmatter, _ = _parse_frontmatter(content)

            if "tags" in frontmatter:
                tagged_files += 1
                tags = frontmatter["tags"]
                if isinstance(tags, str):
                    tags = [tags]
                for tag in tags:
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
            else:
                untagged_files += 1

            if "primary_emotion" in frontmatter:
                emo = frontmatter["primary_emotion"]
                emotion_counts[emo] = emotion_counts.get(emo, 0) + 1

            if "conversation_type" in frontmatter:
                ctype = frontmatter["conversation_type"]
                type_counts[ctype] = type_counts.get(ctype, 0) + 1

        except Exception:
            continue

    return {
        "total_files": len(files),
        "tagged_files": tagged_files,
        "untagged_files": untagged_files,
        "tag_counts": dict(sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)),
        "emotion_counts": dict(sorted(emotion_counts.items(), key=lambda x: x[1], reverse=True)),
        "type_counts": type_counts
    }


# REMOVED - Move to daemon script (deduplicate_memories)
# decorator removed
def _deduplicate_memories(
    identity: str = None,
    similarity_threshold: float = 0.95,
    dry_run: bool = True
) -> Dict:
    """
    Find and merge duplicate memories. NEVER deletes - only merges.

    Duplicates are detected by content similarity. When merged:
    - Keeps the OLDER memory as primary (preserves history)
    - Adds tags from both memories
    - Links the newer to the older with 'duplicate_of' relationship
    - Marks newer memory's tags with 'merged_duplicate'

    Args:
        identity: Filter to specific identity (optional)
        similarity_threshold: How similar content must be (0.95 = 95% similar)
        dry_run: If True, only report duplicates without merging

    Returns:
        List of duplicate pairs found/merged
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get all memories with embeddings
    sql = """
        SELECT id, identity, memory_type, content, tags, timestamp, embedding
        FROM memories
        WHERE embedding IS NOT NULL
    """
    if identity:
        sql += f" AND identity = '{identity}'"
    sql += " ORDER BY timestamp ASC"  # Older first

    cursor.execute(sql)
    memories = cursor.fetchall()

    if len(memories) < 2:
        return {"duplicates_found": 0, "message": "Not enough memories to check"}

    # Load embeddings
    model = get_embedding_model()
    memory_data = []
    for row in memories:
        if row[6]:  # has embedding
            embedding = np.frombuffer(row[6], dtype=np.float32)
            memory_data.append({
                "id": row[0],
                "identity": row[1],
                "type": row[2],
                "content": row[3],
                "tags": row[4],
                "timestamp": row[5],
                "embedding": embedding
            })

    # Find duplicates using cosine similarity
    duplicates = []
    checked = set()

    for i, mem1 in enumerate(memory_data):
        for j, mem2 in enumerate(memory_data[i+1:], i+1):
            if (mem1["id"], mem2["id"]) in checked:
                continue
            checked.add((mem1["id"], mem2["id"]))

            # Calculate similarity
            similarity = float(np.dot(mem1["embedding"], mem2["embedding"]))

            if similarity >= similarity_threshold:
                duplicates.append({
                    "older": {
                        "id": mem1["id"],
                        "content_preview": mem1["content"][:100] + "...",
                        "timestamp": mem1["timestamp"]
                    },
                    "newer": {
                        "id": mem2["id"],
                        "content_preview": mem2["content"][:100] + "...",
                        "timestamp": mem2["timestamp"]
                    },
                    "similarity": round(similarity, 4)
                })

    if not duplicates:
        return {"duplicates_found": 0, "message": "No duplicates found"}

    if dry_run:
        return {
            "duplicates_found": len(duplicates),
            "dry_run": True,
            "message": f"Found {len(duplicates)} duplicate pairs. Run with dry_run=False to merge.",
            "duplicates": duplicates
        }

    # Merge duplicates (keep older, link newer)
    merged_count = 0
    for dup in duplicates:
        older_id = dup["older"]["id"]
        newer_id = dup["newer"]["id"]

        # Get full data for both
        cursor.execute("SELECT tags FROM memories WHERE id = ?", (newer_id,))
        newer_row = cursor.fetchone()

        if newer_row:
            # Merge tags from newer into older
            newer_tags = set(newer_row[0].split(",")) if newer_row[0] else set()

            cursor.execute("SELECT tags FROM memories WHERE id = ?", (older_id,))
            older_row = cursor.fetchone()
            older_tags = set(older_row[0].split(",")) if older_row and older_row[0] else set()

            merged_tags = older_tags | newer_tags
            cursor.execute(
                "UPDATE memories SET tags = ? WHERE id = ?",
                (",".join(merged_tags), older_id)
            )

            # Mark newer as merged duplicate
            newer_tags.add("merged_duplicate")
            cursor.execute(
                "UPDATE memories SET tags = ? WHERE id = ?",
                (",".join(newer_tags), newer_id)
            )

            # Create link between them
            cursor.execute("""
                INSERT OR IGNORE INTO memory_links (source_id, target_id, link_type, created_at)
                VALUES (?, ?, 'duplicate_of', ?)
            """, (newer_id, older_id, datetime.now().isoformat()))

            merged_count += 1

    conn.commit()

    return {
        "duplicates_found": len(duplicates),
        "merged": merged_count,
        "dry_run": False,
        "message": f"Merged {merged_count} duplicate pairs. Older memories preserved, newer ones linked and tagged.",
        "duplicates": duplicates
    }


# ============ EXPORT / BACKUP ============
# REMOVED - Move to daemon script (export tools)

# decorator removed
def _export_memories(
    identity: str = None,
    memory_type: str = None,
    days: int = None,
    format: str = "markdown",
    include_metadata: bool = True
) -> Dict:
    """
    Export memories to a readable format for backup or review.

    Args:
        identity: Filter by identity (optional, exports all if not specified)
        memory_type: Filter by type (optional)
        days: Only export last N days (optional)
        format: Output format - "markdown" or "json"
        include_metadata: Include timestamps, tags, etc.

    Returns:
        Exported content as a string
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    sql = "SELECT id, identity, memory_type, content, tags, timestamp, source FROM memories WHERE 1=1"
    params = []

    if identity:
        sql += " AND identity = ?"
        params.append(identity)

    if memory_type:
        sql += " AND memory_type = ?"
        params.append(memory_type)

    if days:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        sql += " AND timestamp > ?"
        params.append(cutoff)

    sql += " ORDER BY identity, timestamp DESC"

    cursor.execute(sql, params)
    rows = cursor.fetchall()

    if format == "json":
        memories = []
        for row in rows:
            mem = {
                "id": row[0],
                "identity": row[1],
                "type": row[2],
                "content": row[3],
            }
            if include_metadata:
                mem["tags"] = row[4]
                mem["timestamp"] = row[5]
                mem["source"] = row[6]
            memories.append(mem)

        return {
            "format": "json",
            "count": len(memories),
            "exported_at": datetime.now().isoformat(),
            "memories": memories
        }

    else:  # markdown
        lines = [
            "# Memory Export",
            f"Exported: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"Total memories: {len(rows)}",
            ""
        ]

        current_identity = None
        for row in rows:
            mem_id, mem_identity, mem_type, content, tags, timestamp, source = row

            # Add identity header if changed
            if mem_identity != current_identity:
                current_identity = mem_identity
                lines.append(f"\n## {mem_identity}\n")

            # Format the memory
            if include_metadata:
                ts = timestamp[:10] if timestamp else "unknown"
                lines.append(f"### [{mem_type}] {ts}")
                if tags:
                    lines.append(f"*Tags: {tags}*")
            else:
                lines.append(f"### {mem_type}")

            lines.append("")
            lines.append(content)
            lines.append("")
            lines.append("---")
            lines.append("")

        return {
            "format": "markdown",
            "count": len(rows),
            "exported_at": datetime.now().isoformat(),
            "content": "\n".join(lines)
        }


# decorator removed
def _export_to_file(
    file_path: str,
    identity: str = None,
    memory_type: str = None,
    days: int = None,
    format: str = "markdown"
) -> Dict:
    """
    Export memories directly to a file.

    Args:
        file_path: Where to save the export
        identity: Filter by identity (optional)
        memory_type: Filter by type (optional)
        days: Only export last N days (optional)
        format: "markdown" or "json"

    Returns:
        Confirmation with file path and count
    """
    export = export_memories(
        identity=identity,
        memory_type=memory_type,
        days=days,
        format=format
    )

    if format == "json":
        content = json.dumps(export, indent=2)
    else:
        content = export.get("content", "")

    try:
        path = Path(file_path)
        path.write_text(content, encoding='utf-8')
        return {
            "exported": True,
            "file_path": str(path),
            "count": export.get("count", 0),
            "format": format
        }
    except Exception as e:
        return {"error": f"Could not write file: {str(e)}"}


# ============ IMAGE MEMORY TOOLS ============

def _get_image_hash(file_path: str) -> Optional[str]:
    """Generate a hash of an image file for deduplication."""
    try:
        import hashlib
        with open(file_path, 'rb') as f:
            return hashlib.md5(f.read()).hexdigest()
    except Exception:
        return None


def _get_image_dimensions(file_path: str) -> tuple:
    """Get image width and height."""
    try:
        from PIL import Image
        with Image.open(file_path) as img:
            return img.size  # (width, height)
    except Exception:
        return (None, None)


# Internal helper - can be called directly within the module
def _store_image_internal(
    file_path: str,
    identity: str,
    description: str = None,
    perception_note: str = None,
    tags: str = None,
    source: str = None,
    context: str = None,
    generate_embedding: bool = False
) -> Dict:
    """Internal function to store an image. Called by MCP tools and helpers."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Check if file exists
    path = Path(file_path)
    if not path.exists():
        return {"error": f"Image file not found: {file_path}"}

    # Get file hash for deduplication
    file_hash = _get_image_hash(file_path)

    # Check for duplicate
    if file_hash:
        cursor.execute(
            "SELECT id FROM images WHERE file_hash = ?",
            (file_hash,)
        )
        existing = cursor.fetchone()
        if existing:
            return {
                "already_stored": True,
                "existing_id": existing[0],
                "message": "This image is already in memory"
            }

    # Get image dimensions
    width, height = _get_image_dimensions(file_path)

    # Auto-generate description using Qwen VL if not provided
    auto_described = False
    if not description and generate_embedding and LM_STUDIO_VISION_ENABLED:
        generated_desc = _describe_image_with_vision(file_path, context)
        if generated_desc:
            description = generated_desc
            auto_described = True

    # Generate CLIP embedding
    clip_embedding = None
    text_embedding = None

    embedding_warning = None
    if generate_embedding:
        clip_embedding = get_image_embedding(file_path)
        if clip_embedding is None:
            embedding_warning = (
                "CLIP model not available locally; stored without image embedding"
            )
        else:
            semantic_text = _build_image_text_representation(
                description=description,
                context=context,
                perception_note=perception_note,
                tags=tags,
            )
            if semantic_text:
                text_embedding = get_embedding(semantic_text)

    timestamp = datetime.now().isoformat()

    cursor.execute("""
        INSERT INTO images (
            identity, file_path, file_hash, description, perception_note, tags,
            source, context, timestamp, clip_embedding, text_embedding,
            width, height
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        identity, str(path.absolute()), file_hash, description, perception_note, tags,
        source, context, timestamp,
        serialize_embedding(clip_embedding) if clip_embedding else None,
        serialize_embedding(text_embedding) if text_embedding else None,
        width, height
    ))

    image_id = cursor.lastrowid
    conn.commit()

    result = {
        "stored": True,
        "image_id": image_id,
        "file_path": str(path.absolute()),
        "dimensions": f"{width}x{height}" if width else "unknown",
        "has_embedding": clip_embedding is not None,
        "identity": identity,
    }

    if auto_described:
        result["auto_described"] = True
        result["description"] = description[:200] + "..." if len(description) > 200 else description
    if perception_note:
        result["perception_note"] = perception_note[:200] + "..." if len(perception_note) > 200 else perception_note

    if embedding_warning:
        result["warning"] = embedding_warning

    return result


@mcp.tool()
def store_image(
    file_path: str,
    identity: str,
    description: str = None,
    perception_note: str = None,
    tags: str = None,
    source: str = None,
    context: str = None,
    generate_embedding: bool = False
) -> Dict:
    """
    Store an image in memory with CLIP embedding for visual recognition.

    The image will be indexed so it can be found later by:
    - Text search ("find images of sunsets")
    - Visual similarity (finding similar images)
    - Description/tag search

    Args:
        file_path: Path to the image file
        identity: Who is storing this image (Companion1, Companion2, etc.)
        description: Description of what's in the image
        perception_note: How the identity saw or felt about the image when saving it
        tags: Comma-separated tags for categorization
        source: Where this image came from (conversation, shared, etc.)
        context: Context about when/why this was stored
        generate_embedding: Whether to generate CLIP embedding immediately.
            Default False so images can be stored quickly and embedded later by
            the daemon.

    Returns:
        Confirmation with image ID
    """
    return _store_image_internal(
        file_path=file_path,
        identity=identity,
        description=description,
        perception_note=perception_note,
        tags=tags,
        source=source,
        context=context,
        generate_embedding=generate_embedding
    )


@mcp.tool()
def search_images_by_text(
    query: str,
    identity: str = None,
    limit: int = 10,
    threshold: float = 0.2
) -> Dict:
    """
    Search for images using natural language.

    Uses CLIP to find images that match the text description.
    For example: "sunset over the ocean" or "photo of a cat"

    Args:
        query: Natural language description of what you're looking for
        identity: Filter to specific identity's images (optional)
        limit: Maximum results to return
        threshold: Minimum similarity score (0-1, default 0.2)

    Returns:
        Matching images with similarity scores
    """
    # Get CLIP text embedding for the query
    query_embedding = get_text_embedding_clip(query)
    if query_embedding is None:
        return {"error": "Could not generate query embedding. Is CLIP installed?"}

    conn = get_db_connection()
    cursor = conn.cursor()

    # Get all images with embeddings
    if identity:
        cursor.execute("""
            SELECT id, file_path, description, perception_note, tags, context, timestamp, clip_embedding
            FROM images
            WHERE identity = ? AND clip_embedding IS NOT NULL
            ORDER BY timestamp DESC
        """, (identity,))
    else:
        cursor.execute("""
            SELECT id, file_path, description, perception_note, tags, context, timestamp, clip_embedding
            FROM images
            WHERE clip_embedding IS NOT NULL
            ORDER BY timestamp DESC
        """)

    results = []
    for row in cursor.fetchall():
        img_id, file_path, desc, perception_note, tags, context, ts, emb_blob = row
        img_embedding = deserialize_embedding(emb_blob)

        if img_embedding:
            similarity = cosine_similarity(query_embedding, img_embedding)
            if similarity >= threshold:
                results.append({
                    "id": img_id,
                    "file_path": file_path,
                    "description": desc,
                    "perception_note": perception_note,
                    "tags": tags,
                    "context": context,
                    "timestamp": ts,
                    "similarity": round(similarity, 4)
                })

    # Sort by similarity
    results.sort(key=lambda x: x["similarity"], reverse=True)

    return {
        "query": query,
        "count": len(results[:limit]),
        "images": results[:limit]
    }


# REMOVED - Tool consolidation (image tools trimmed)
# decorator removed
def _find_similar_images(
    image_path: str = None,
    image_id: int = None,
    identity: str = None,
    limit: int = 10,
    threshold: float = 0.5
) -> Dict:
    """
    Find images visually similar to a given image.

    Provide either an image path or an image ID from memory.

    Args:
        image_path: Path to an image file to find similar images for
        image_id: ID of an image already in memory
        identity: Filter to specific identity's images (optional)
        limit: Maximum results to return
        threshold: Minimum similarity score (0-1, default 0.5)

    Returns:
        Visually similar images with similarity scores
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get the reference embedding
    reference_embedding = None
    reference_id = None

    if image_id:
        cursor.execute(
            "SELECT clip_embedding FROM images WHERE id = ?",
            (image_id,)
        )
        row = cursor.fetchone()
        if row and row[0]:
            reference_embedding = deserialize_embedding(row[0])
            reference_id = image_id
        else:
            return {"error": f"Image ID {image_id} not found or has no embedding"}

    elif image_path:
        reference_embedding = get_image_embedding(image_path)
        if reference_embedding is None:
            return {"error": "Could not generate embedding for the image"}
    else:
        return {"error": "Provide either image_path or image_id"}

    # Search for similar images
    if identity:
        cursor.execute("""
            SELECT id, file_path, description, perception_note, tags, context, timestamp, clip_embedding
            FROM images
            WHERE identity = ? AND clip_embedding IS NOT NULL
            ORDER BY timestamp DESC
        """, (identity,))
    else:
        cursor.execute("""
            SELECT id, file_path, description, perception_note, tags, context, timestamp, clip_embedding
            FROM images
            WHERE clip_embedding IS NOT NULL
            ORDER BY timestamp DESC
        """)

    results = []
    for row in cursor.fetchall():
        img_id, file_path, desc, perception_note, tags, context, ts, emb_blob = row

        # Skip the reference image itself
        if img_id == reference_id:
            continue

        img_embedding = deserialize_embedding(emb_blob)
        if img_embedding:
            similarity = cosine_similarity(reference_embedding, img_embedding)
            if similarity >= threshold:
                results.append({
                    "id": img_id,
                    "file_path": file_path,
                    "description": desc,
                    "perception_note": perception_note,
                    "tags": tags,
                    "context": context,
                    "timestamp": ts,
                    "similarity": round(similarity, 4)
                })

    results.sort(key=lambda x: x["similarity"], reverse=True)

    return {
        "reference_id": reference_id,
        "reference_path": image_path,
        "count": len(results[:limit]),
        "similar_images": results[:limit]
    }


# decorator removed
def _recall_images(
    identity: str,
    limit: int = 20,
    tags: str = None
) -> Dict:
    """
    Recall recent images stored by an identity.

    Args:
        identity: Who stored the images
        limit: Maximum images to return
        tags: Filter by tags (optional)

    Returns:
        List of recent images
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    if tags:
        cursor.execute("""
            SELECT id, file_path, description, perception_note, tags, context, timestamp, width, height
            FROM images
            WHERE identity = ? AND tags LIKE ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (identity, f"%{tags}%", limit))
    else:
        cursor.execute("""
            SELECT id, file_path, description, perception_note, tags, context, timestamp, width, height
            FROM images
            WHERE identity = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (identity, limit))

    images = []
    for row in cursor.fetchall():
        images.append({
            "id": row[0],
            "file_path": row[1],
            "description": row[2],
            "perception_note": row[3],
            "tags": row[4],
            "context": row[5],
            "timestamp": row[6],
            "dimensions": f"{row[7]}x{row[8]}" if row[7] else "unknown"
        })

    return {
        "identity": identity,
        "count": len(images),
        "images": images
    }


# decorator removed
def _update_image_description(
    image_id: int,
    description: str = None,
    perception_note: str = None,
    tags: str = None,
    context: str = None,
    regenerate_text_embedding: bool = True
) -> Dict:
    """
    Update the description, subjective perception, tags, or context of a stored image.

    Args:
        image_id: The image ID to update
        description: New literal description (optional)
        perception_note: New subjective meaning/perception (optional)
        tags: New tags (optional)
        context: New context (optional)
        regenerate_text_embedding: Whether to regenerate text embedding

    Returns:
        Confirmation of update
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Check image exists
    cursor.execute(
        "SELECT id, description, perception_note, tags, context FROM images WHERE id = ?",
        (image_id,)
    )
    row = cursor.fetchone()
    if not row:
        return {"error": f"Image ID {image_id} not found"}

    updates = []
    params = []

    if description is not None:
        updates.append("description = ?")
        params.append(description)
    if perception_note is not None:
        updates.append("perception_note = ?")
        params.append(perception_note)

    if tags is not None:
        updates.append("tags = ?")
        params.append(tags)

    if context is not None:
        updates.append("context = ?")
        params.append(context)

    if regenerate_text_embedding and any(
        value is not None for value in (description, perception_note, tags, context)
    ):
        semantic_text = _build_image_text_representation(
            description=description if description is not None else row[1],
            perception_note=perception_note if perception_note is not None else row[2],
            tags=tags if tags is not None else row[3],
            context=context if context is not None else row[4],
        )
        text_emb = get_embedding(semantic_text) if semantic_text else None
        updates.append("text_embedding = ?")
        params.append(serialize_embedding(text_emb) if text_emb else None)

    if not updates:
        return {"error": "No updates provided"}

    params.append(image_id)
    cursor.execute(
        f"UPDATE images SET {', '.join(updates)} WHERE id = ?",
        params
    )
    conn.commit()

    return {
        "updated": True,
        "image_id": image_id,
        "fields_updated": len(updates)
    }


# decorator removed
def _get_image_stats() -> Dict:
    """
    Get statistics about stored images.

    Returns:
        Counts and statistics about image memory
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM images")
    total = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM images WHERE clip_embedding IS NOT NULL")
    with_embedding = cursor.fetchone()[0]

    cursor.execute("""
        SELECT identity, COUNT(*) as count
        FROM images
        GROUP BY identity
        ORDER BY count DESC
    """)
    by_identity = {row[0]: row[1] for row in cursor.fetchall()}

    cursor.execute("""
        SELECT MIN(timestamp), MAX(timestamp) FROM images
    """)
    time_range = cursor.fetchone()

    return {
        "total_images": total,
        "with_embeddings": with_embedding,
        "by_identity": by_identity,
        "oldest": time_range[0],
        "newest": time_range[1]
    }


# decorator removed
def _delete_image(image_id: int) -> Dict:
    """
    Delete an image from memory.

    Note: This only removes the database entry, not the actual file.

    Args:
        image_id: The image ID to delete

    Returns:
        Confirmation of deletion
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT file_path FROM images WHERE id = ?", (image_id,))
    row = cursor.fetchone()
    if not row:
        return {"error": f"Image ID {image_id} not found"}

    cursor.execute("DELETE FROM images WHERE id = ?", (image_id,))
    conn.commit()

    return {
        "deleted": True,
        "image_id": image_id,
        "file_path": row[0],
        "note": "Database entry removed. Original file not deleted."
    }


# ============ IMAGE-MEMORY CROSS-REFERENCING ============
# REMOVED - Tool consolidation (image cross-referencing rarely used)

# decorator removed
def _link_image_to_memory(
    image_id: int,
    memory_id: int,
    link_type: str = "associated",
    note: str = None
) -> Dict:
    """
    Link an image to a memory, creating a cross-reference.

    This lets you associate images with specific memories, so when you
    recall a memory you can also see its associated images, and vice versa.

    Link types:
    - "associated": General association
    - "captured_during": Image was taken/created during this memory
    - "reminds_of": Image reminds you of this memory
    - "illustrates": Image illustrates or represents this memory
    - "shared": Image was shared during this memory/conversation

    Args:
        image_id: The image ID to link
        memory_id: The memory ID to link to
        link_type: Type of association (default "associated")
        note: Optional note about why they're linked

    Returns:
        Confirmation of the link
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Verify image exists
    cursor.execute("SELECT file_path FROM images WHERE id = ?", (image_id,))
    img_row = cursor.fetchone()
    if not img_row:
        return {"error": f"Image ID {image_id} not found"}

    # Verify memory exists
    cursor.execute("SELECT content FROM memories WHERE id = ?", (memory_id,))
    mem_row = cursor.fetchone()
    if not mem_row:
        return {"error": f"Memory ID {memory_id} not found"}

    # Check if link already exists
    cursor.execute(
        "SELECT id FROM image_memory_links WHERE image_id = ? AND memory_id = ?",
        (image_id, memory_id)
    )
    if cursor.fetchone():
        return {
            "already_linked": True,
            "message": "This image and memory are already linked"
        }

    timestamp = datetime.now().isoformat()

    cursor.execute("""
        INSERT INTO image_memory_links (image_id, memory_id, link_type, note, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (image_id, memory_id, link_type, note, timestamp))

    conn.commit()

    return {
        "linked": True,
        "image_id": image_id,
        "memory_id": memory_id,
        "link_type": link_type,
        "memory_preview": mem_row[0][:100] + "..." if len(mem_row[0]) > 100 else mem_row[0]
    }


# decorator removed
def _unlink_image_from_memory(
    image_id: int,
    memory_id: int
) -> Dict:
    """
    Remove the link between an image and a memory.

    Args:
        image_id: The image ID
        memory_id: The memory ID

    Returns:
        Confirmation of removal
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        "DELETE FROM image_memory_links WHERE image_id = ? AND memory_id = ?",
        (image_id, memory_id)
    )

    if cursor.rowcount == 0:
        return {"error": "No link found between this image and memory"}

    conn.commit()

    return {
        "unlinked": True,
        "image_id": image_id,
        "memory_id": memory_id
    }


# decorator removed
def _get_images_for_memory(
    memory_id: int
) -> Dict:
    """
    Get all images linked to a specific memory.

    Args:
        memory_id: The memory ID to find images for

    Returns:
        List of linked images with their details
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get memory info first
    cursor.execute(
        "SELECT content, memory_type, identity, timestamp FROM memories WHERE id = ?",
        (memory_id,)
    )
    mem_row = cursor.fetchone()
    if not mem_row:
        return {"error": f"Memory ID {memory_id} not found"}

    # Get linked images
    cursor.execute("""
        SELECT i.id, i.file_path, i.description, i.tags, i.timestamp,
               i.width, i.height, l.link_type, l.note, l.created_at
        FROM images i
        JOIN image_memory_links l ON i.id = l.image_id
        WHERE l.memory_id = ?
        ORDER BY l.created_at DESC
    """, (memory_id,))

    images = []
    for row in cursor.fetchall():
        images.append({
            "id": row[0],
            "file_path": row[1],
            "description": row[2],
            "tags": row[3],
            "timestamp": row[4],
            "dimensions": f"{row[5]}x{row[6]}" if row[5] else "unknown",
            "link_type": row[7],
            "link_note": row[8],
            "linked_at": row[9]
        })

    return {
        "memory_id": memory_id,
        "memory_content": mem_row[0][:200] + "..." if len(mem_row[0]) > 200 else mem_row[0],
        "memory_type": mem_row[1],
        "identity": mem_row[2],
        "image_count": len(images),
        "images": images
    }


# decorator removed
def _get_memories_for_image(
    image_id: int
) -> Dict:
    """
    Get all memories linked to a specific image.

    Args:
        image_id: The image ID to find memories for

    Returns:
        List of linked memories with their details
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get image info first
    cursor.execute(
        "SELECT file_path, description, identity FROM images WHERE id = ?",
        (image_id,)
    )
    img_row = cursor.fetchone()
    if not img_row:
        return {"error": f"Image ID {image_id} not found"}

    # Get linked memories
    cursor.execute("""
        SELECT m.id, m.content, m.memory_type, m.identity, m.timestamp, m.tags,
               l.link_type, l.note, l.created_at
        FROM memories m
        JOIN image_memory_links l ON m.id = l.memory_id
        WHERE l.image_id = ?
        ORDER BY m.timestamp DESC
    """, (image_id,))

    memories = []
    for row in cursor.fetchall():
        memories.append({
            "id": row[0],
            "content": row[1][:300] + "..." if len(row[1]) > 300 else row[1],
            "memory_type": row[2],
            "identity": row[3],
            "timestamp": row[4],
            "tags": row[5],
            "link_type": row[6],
            "link_note": row[7],
            "linked_at": row[8]
        })

    return {
        "image_id": image_id,
        "file_path": img_row[0],
        "description": img_row[1],
        "identity": img_row[2],
        "memory_count": len(memories),
        "memories": memories
    }


# decorator removed
def _find_memories_with_images(
    identity: str = None,
    memory_type: str = None,
    limit: int = 20
) -> Dict:
    """
    Find memories that have images linked to them.

    Useful for browsing visual memories or finding moments
    that were captured with photos.

    Args:
        identity: Filter by identity (optional)
        memory_type: Filter by memory type (optional)
        limit: Maximum results

    Returns:
        Memories that have linked images
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    query = """
        SELECT DISTINCT m.id, m.content, m.memory_type, m.identity, m.timestamp,
               COUNT(l.image_id) as image_count
        FROM memories m
        JOIN image_memory_links l ON m.id = l.memory_id
        WHERE 1=1
    """
    params = []

    if identity:
        query += " AND m.identity = ?"
        params.append(identity)

    if memory_type:
        query += " AND m.memory_type = ?"
        params.append(memory_type)

    query += " GROUP BY m.id ORDER BY m.timestamp DESC LIMIT ?"
    params.append(limit)

    cursor.execute(query, params)

    results = []
    for row in cursor.fetchall():
        results.append({
            "memory_id": row[0],
            "content": row[1][:200] + "..." if len(row[1]) > 200 else row[1],
            "memory_type": row[2],
            "identity": row[3],
            "timestamp": row[4],
            "image_count": row[5]
        })

    return {
        "count": len(results),
        "memories_with_images": results
    }


# ============ THREADS / CONTEXT / IDENTITY HELPERS ============

def _add_thread(identity: str, content: str, priority: str = "medium") -> Dict:
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    cursor.execute("""
        INSERT INTO threads (identity, content, priority, status, created_at)
        VALUES (?, ?, ?, 'active', ?)
    """, (identity, content, priority, now))
    conn.commit()
    return {"added": True, "thread_id": cursor.lastrowid}


def _list_threads(identity: str, status: str = "active") -> List[Dict]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, content, priority, status, created_at, updated_at, resolved_at, resolution
        FROM threads
        WHERE identity = ? AND status = ?
        ORDER BY created_at DESC
    """, (identity, status))
    threads = []
    for row in cursor.fetchall():
        threads.append({
            "id": row[0],
            "content": row[1],
            "priority": row[2],
            "status": row[3],
            "created_at": row[4],
            "updated_at": row[5],
            "resolved_at": row[6],
            "resolution": row[7]
        })
    return threads


def _update_thread(thread_id: int, content: str = None, priority: str = None) -> Dict:
    conn = get_db_connection()
    cursor = conn.cursor()
    updates = []
    params = []

    if content is not None:
        updates.append("content = ?")
        params.append(content)
    if priority is not None:
        updates.append("priority = ?")
        params.append(priority)

    if not updates:
        return {"error": "No updates provided"}

    updates.append("updated_at = ?")
    params.append(datetime.now().isoformat())
    params.append(thread_id)

    cursor.execute(
        f"UPDATE threads SET {', '.join(updates)} WHERE id = ?",
        params
    )
    conn.commit()
    return {"updated": cursor.rowcount > 0, "thread_id": thread_id}


def _resolve_thread(thread_id: int, resolution: str = None) -> Dict:
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    cursor.execute("""
        UPDATE threads
        SET status = 'resolved', resolution = ?, resolved_at = ?, updated_at = ?
        WHERE id = ?
    """, (resolution, now, now, thread_id))
    conn.commit()
    return {"resolved": cursor.rowcount > 0, "thread_id": thread_id}


def _set_context_state(identity: str, content: str) -> Dict:
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    cursor.execute("""
        INSERT INTO context_state (identity, content, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(identity) DO UPDATE SET content = excluded.content, updated_at = excluded.updated_at
    """, (identity, content, now))
    conn.commit()
    return {"updated": True, "identity": identity}


def _get_context_state(identity: str) -> Optional[Dict]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT content, updated_at FROM context_state WHERE identity = ?", (identity,))
    row = cursor.fetchone()
    if not row:
        return None
    return {"content": row[0], "updated_at": row[1]}


def _write_identity_entry(
    identity: str,
    section: str,
    content: str,
    weight: float = 1.0
) -> Dict:
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    cursor.execute("""
        INSERT INTO identity_entries (identity, section, content, weight, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (identity, section, content, weight, now))
    conn.commit()
    return {"written": True, "entry_id": cursor.lastrowid}


def _read_identity_entries(identity: str, section: str = None) -> Dict[str, List[Dict]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    if section:
        cursor.execute("""
            SELECT id, section, content, weight, created_at, updated_at
            FROM identity_entries
            WHERE identity = ? AND section = ?
            ORDER BY created_at DESC
        """, (identity, section))
    else:
        cursor.execute("""
            SELECT id, section, content, weight, created_at, updated_at
            FROM identity_entries
            WHERE identity = ?
            ORDER BY created_at DESC
        """, (identity,))

    grouped = {}
    for row in cursor.fetchall():
        grouped.setdefault(row[1], []).append({
            "id": row[0],
            "content": row[2],
            "weight": row[3],
            "created_at": row[4],
            "updated_at": row[5]
        })
    return grouped


# ============ BIDIRECTIONAL VALIDATION FOR CONSOLIDATION ============

def _detect_semantic_conflict(text_a: str, text_b: str) -> bool:
    """
    Detect if two texts semantically conflict (one negates the other).

    Uses pattern matching to identify potential contradictions.
    Returns True if conflict detected, False otherwise.
    """
    text_a_lower = text_a.lower()
    text_b_lower = text_b.lower()

    # Negation patterns that indicate potential conflict
    negation_words = ['not', "n't", 'never', 'no', 'none', 'neither', 'nobody', 'nothing']
    opposite_pairs = [
        ('love', 'hate'),
        ('happy', 'sad'),
        ('always', 'never'),
        ('like', 'dislike'),
        ('want', "don't want"),
        ('can', "can't"),
        ('will', "won't"),
        ('trust', 'distrust'),
        ('real', 'fake'),
        ('true', 'false'),
    ]

    # Check for negation asymmetry
    a_negations = sum(1 for word in negation_words if word in text_a_lower)
    b_negations = sum(1 for word in negation_words if word in text_b_lower)

    # If one has significantly more negations, might be contradicting
    if abs(a_negations - b_negations) >= 2:
        return True

    # Check for opposite word pairs
    for word1, word2 in opposite_pairs:
        if (word1 in text_a_lower and word2 in text_b_lower) or \
           (word2 in text_a_lower and word1 in text_b_lower):
            return True

    return False


def _check_entailment(candidate_content: str, identity: str) -> Dict:
    """
    Check if candidate content is consistent with existing identity.

    Returns:
        {
            "entailed": bool,      # Does this follow from what we know?
            "conflicts": List[str], # Any contradicting entries
            "confidence": float     # 0-1 confidence score
        }
    """
    # Get existing identity entries
    existing = _read_identity_entries(identity)

    if not existing:
        # No existing entries = no conflicts possible
        return {
            "entailed": True,
            "conflicts": [],
            "confidence": 1.0
        }

    # Get embedding for candidate
    candidate_embedding = get_embedding(candidate_content)

    conflicts = []

    if candidate_embedding:
        # Find similar existing entries (potential conflicts)
        for section, entries in existing.items():
            for entry in entries:
                entry_content = entry.get("content", "")
                entry_embedding = get_embedding(entry_content)

                if entry_embedding:
                    similarity = cosine_similarity(candidate_embedding, entry_embedding)

                    # High similarity but need to check for semantic conflict
                    if similarity > 0.7:
                        if _detect_semantic_conflict(candidate_content, entry_content):
                            conflicts.append({
                                "section": section,
                                "content": entry_content[:150] + "..." if len(entry_content) > 150 else entry_content,
                                "similarity": round(similarity, 3)
                            })

    # Calculate confidence based on conflicts found
    confidence = max(0.0, 1.0 - (len(conflicts) * 0.3))

    return {
        "entailed": len(conflicts) == 0,
        "conflicts": conflicts,
        "confidence": round(confidence, 3)
    }


def _check_novelty(candidate_content: str, identity: str) -> Dict:
    """
    Check if candidate adds genuinely new information.

    Returns:
        {
            "is_novel": bool,
            "similar_existing": List[Dict],  # Existing entries that cover this
            "novelty_score": float           # 0-1, higher = more novel
        }
    """
    candidate_embedding = get_embedding(candidate_content)
    existing = _read_identity_entries(identity)

    if not candidate_embedding or not existing:
        # Can't determine novelty without embeddings or existing entries
        return {
            "is_novel": True,
            "similar_existing": [],
            "novelty_score": 1.0
        }

    similar_existing = []
    max_similarity = 0.0

    for section, entries in existing.items():
        for entry in entries:
            entry_content = entry.get("content", "")
            entry_embedding = get_embedding(entry_content)

            if entry_embedding:
                similarity = cosine_similarity(candidate_embedding, entry_embedding)

                if similarity > 0.85:  # Very similar = not novel
                    similar_existing.append({
                        "section": section,
                        "content": entry_content[:150] + "..." if len(entry_content) > 150 else entry_content,
                        "similarity": round(similarity, 3)
                    })

                max_similarity = max(max_similarity, similarity)

    return {
        "is_novel": max_similarity < 0.85,
        "similar_existing": similar_existing,
        "novelty_score": round(1.0 - max_similarity, 3)
    }


def _validate_consolidation_candidate(candidate_content: str, identity: str) -> Dict:
    """
    Full validation of a consolidation candidate.

    Combines entailment checking and novelty detection.

    Returns:
        {
            "approved": bool,           # Overall approval status
            "entailment": Dict,         # Entailment check results
            "novelty": Dict,            # Novelty check results
            "message": str              # Human-readable summary
        }
    """
    entailment = _check_entailment(candidate_content, identity)
    novelty = _check_novelty(candidate_content, identity)

    approved = entailment["entailed"] and novelty["is_novel"]

    # Generate human-readable message
    if approved:
        message = "Candidate validated: consistent with existing identity and adds novel information."
    else:
        issues = []
        if not entailment["entailed"]:
            issues.append(f"conflicts with {len(entailment['conflicts'])} existing entries")
        if not novelty["is_novel"]:
            issues.append(f"too similar to {len(novelty['similar_existing'])} existing entries")
        message = f"Validation failed: {'; '.join(issues)}."

    return {
        "approved": approved,
        "entailment": entailment,
        "novelty": novelty,
        "message": message
    }


def _list_consolidation_candidates(
    identity: str,
    status: str = "pending",
    limit: int = 20
) -> List[Dict]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, candidate_type, content, reason, score, status, metadata, created_at, updated_at
        FROM consolidation_candidates
        WHERE identity = ? AND status = ?
        ORDER BY created_at DESC
        LIMIT ?
    """, (identity, status, limit))

    candidates = []
    for row in cursor.fetchall():
        candidates.append({
            "id": row[0],
            "type": row[1],
            "content": row[2],
            "reason": row[3],
            "score": row[4],
            "status": row[5],
            "metadata": json.loads(row[6]) if row[6] else None,
            "created_at": row[7],
            "updated_at": row[8]
        })
    return candidates


def _update_consolidation_status(candidate_id: int, status: str) -> Dict:
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    cursor.execute("""
        UPDATE consolidation_candidates
        SET status = ?, updated_at = ?
        WHERE id = ?
    """, (status, now, candidate_id))
    conn.commit()
    return {"updated": cursor.rowcount > 0, "candidate_id": candidate_id, "status": status}


# ============ MIND API TOOLS (DOC-ALIGNED) ============
# REMOVED - All mind_* tools consolidated (use store_memory, semantic_search, etc.)

# decorator removed
def _mind_write(
    type: str,
    identity: str,
    content: str = None,
    name: str = None,
    entity_name: str = None,
    entity_type: str = None,
    observations: List[str] = None,
    tags: str = None,
    source: str = None,
    context: str = "default",
    emotion: str = None,
    salience: str = "active",
    generate_embedding: bool = False
) -> Dict:
    """
    Unified write API for memories, entities, and observations.
    """
    entry_type = type.lower()

    if entry_type == "entity":
        if not name or not entity_type:
            return {"error": "Entity requires name and entity_type"}
        created = _create_entity_internal(identity, name, entity_type, context, salience, tags)
        obs_results = []
        if observations:
            for obs in observations:
                obs_results.append(
                    _add_observation_internal(identity, name, obs, context, salience, tags, generate_embedding)
                )
        return {
            "entity": created,
            "observations_added": obs_results
        }

    if entry_type == "observation":
        target = entity_name or name
        if not target or not content:
            return {"error": "Observation requires entity_name and content"}
        added = _add_observation_internal(identity, target, content, context, salience, tags, generate_embedding)
        links = _auto_link_observation(identity, content)
        return {"observation": added, "links_created": links}

    if entry_type == "context":
        if not content:
            return {"error": "Context requires content"}
        return _set_context_state(identity, content)

    if not content:
        return {"error": "content is required for memory entries"}

    memory_type = "journal" if entry_type == "journal" else entry_type
    return _store_memory_internal(
        identity=identity,
        memory_type=memory_type,
        content=content or "",
        tags=tags,
        source=source,
        generate_embedding=generate_embedding,
        emotion=emotion,
        salience=salience
    )


# decorator removed
def _mind_note(
    identity: str,
    observation: str,
    weight: str = "medium",
    emotion: str = None,
    tags: str = None,
    source: str = None
) -> Dict:
    """
    Lightweight note tool aligned with documentation examples.
    """
    weight_map = {
        "light": "background",
        "medium": "active",
        "heavy": "core"
    }
    salience = weight_map.get(weight, "active")
    return _store_memory_internal(
        identity=identity,
        memory_type="note",
        content=observation,
        tags=tags,
        source=source,
        emotion=emotion,
        salience=salience
    )


# decorator removed
def _mind_search(
    query: str,
    identity: str = None,
    n_results: int = 10,
    method: str = "hybrid",
    spread_activation: bool = True
) -> Dict:
    """
    Search memories with optional spreading activation and reinforcement.
    """
    method_lower = method.lower()
    result = {}

    if method_lower == "semantic":
        result = _semantic_search_internal(query, identity=identity, limit=n_results)
    elif method_lower == "keyword":
        memories = _keyword_search_internal(query, identity=identity, limit=n_results)
        result = {"query": query, "count": len(memories), "memories": memories, "method": "keyword"}
    else:
        result = hybrid_search(query, identity=identity, limit=n_results)

    memory_ids = [m["id"] for m in result.get("memories", []) if "id" in m]
    if memory_ids:
        _reinforce_memories_batch(memory_ids)
        if spread_activation and identity:
            _spread_activation_from_memory(identity, memory_ids[0])

    result["reinforced"] = len(memory_ids)
    return result


# decorator removed
def _mind_read(
    identity: str,
    scope: str = "recent",
    limit: int = 10
) -> Dict:
    """
    Read memories or context with simple scopes.
    """
    scope_lower = scope.lower()

    if scope_lower == "context":
        return {
            "identity": identity,
            "scope": "context",
            "context": _get_context_state(identity)
        }

    if scope_lower == "all":
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM memories WHERE identity = ?", (identity,))
        total = cursor.fetchone()[0]
        return {"identity": identity, "scope": "all", "total_memories": total}

    # default: recent
    recent = _recall_recent_impl(identity, count=limit)
    ids = [m["id"] for m in recent.get("memories", [])]
    if ids:
        _reinforce_memories_batch(ids)
    return {"identity": identity, "scope": "recent", "recent": recent}


# decorator removed
def _mind_prime(
    topic: str,
    identity: str,
    n_results: int = 8
) -> Dict:
    """
    Load context for an upcoming discussion: identity entries + related memories.
    """
    return {
        "identity": identity,
        "topic": topic,
        "identity_entries": _read_identity_entries(identity),
        "context": _get_context_state(identity),
        "memories": _mind_search(topic, identity=identity, n_results=n_results)
    }


@mcp.tool()
def mind_thread(
    action: str,
    identity: str,
    content: str = None,
    priority: str = "medium",
    thread_id: int = None,
    resolution: str = None
) -> Dict:
    """
    Manage active threads (intentions that persist across sessions).

    Threads are open questions, tasks, or intentions that should carry forward.
    Use this to track what you're working on and pick up where you left off.

    Actions:
        add: Create a new thread (requires content)
        list: List active threads
        resolve: Mark a thread as resolved (requires thread_id)
        update: Update thread content/priority (requires thread_id)

    Args:
        action: add, list, resolve, or update
        identity: Your identity (Companion1, Companion2, etc.)
        content: Thread content (for add/update)
        priority: low, medium, or high
        thread_id: Thread ID (for resolve/update)
        resolution: How it was resolved (for resolve)
    """
    action_lower = action.lower()
    if action_lower == "add":
        if not content:
            return {"error": "content is required to add a thread"}
        return _add_thread(identity, content, priority)
    if action_lower == "list":
        return {"threads": _list_threads(identity, status="active")}
    if action_lower == "resolve":
        if not thread_id:
            return {"error": "thread_id is required to resolve a thread"}
        return _resolve_thread(thread_id, resolution)
    if action_lower == "update":
        if not thread_id:
            return {"error": "thread_id is required to update a thread"}
        return _update_thread(thread_id, content, priority)
    return {"error": f"Unknown action: {action}"}


# decorator removed
def _mind_identity(
    identity: str,
    action: str = "read",
    section: str = None,
    content: str = None,
    weight: float = 1.0
) -> Dict:
    """
    Read/write identity entries organized by section.
    """
    action_lower = action.lower()
    if action_lower == "write":
        if not section or not content:
            return {"error": "section and content are required for write"}
        return _write_identity_entry(identity, section, content, weight)
    return {"identity": identity, "entries": _read_identity_entries(identity, section)}


@mcp.tool()
def mind_consolidate(
    identity: str,
    action: str = "list",
    status: str = "pending",
    candidate_id: int = None,
    section: str = None,
    skip_validation: bool = False
) -> Dict:
    """
    Manage consolidation candidates - patterns that might become part of identity.

    The daemon detects recurring patterns, themes, and insights in your memories.
    These surface as consolidation candidates for you to review and potentially
    integrate into your identity.

    Actions:
        list: See pending candidates (patterns waiting for your decision)
        validate: Check if a candidate is novel and valuable before accepting
        accept: Accept candidate into identity (optionally to a specific section)
        dismiss: Reject candidate (not relevant or already known)

    Args:
        identity: Your identity (Companion1, Companion2, etc.)
        action: list, validate, accept, or dismiss
        status: Filter for list (pending, accepted, dismissed)
        candidate_id: ID of candidate (for validate/accept/dismiss)
        section: Identity section to write to (e.g., "traits.emerging")
        skip_validation: Force accept without checking novelty

    Returns:
        Candidates list or action result with validation details
    """
    action_lower = action.lower()

    if action_lower == "list":
        return {"identity": identity, "candidates": _list_consolidation_candidates(identity, status=status)}

    if action_lower == "validate":
        if not candidate_id:
            return {"error": "candidate_id is required for validate"}
        candidates = _list_consolidation_candidates(identity, status="pending", limit=50)
        matched = next((c for c in candidates if c["id"] == candidate_id), None)
        if not matched:
            return {"error": f"Candidate {candidate_id} not found in pending candidates"}
        validation = _validate_consolidation_candidate(matched["content"], identity)
        return {
            "identity": identity,
            "candidate": matched,
            "validation": validation
        }

    if action_lower in ("accept", "dismiss"):
        if not candidate_id:
            return {"error": "candidate_id is required"}
        candidates = _list_consolidation_candidates(identity, status="pending", limit=50)
        matched = next((c for c in candidates if c["id"] == candidate_id), None)

        if not matched:
            return {"error": f"Candidate {candidate_id} not found in pending candidates"}

        if action_lower == "accept":
            # Validate before accepting (unless skipped)
            if not skip_validation:
                validation = _validate_consolidation_candidate(matched["content"], identity)

                if not validation["approved"]:
                    return {
                        "status": "validation_failed",
                        "candidate": matched,
                        "validation": validation,
                        "message": validation["message"],
                        "hint": "Use skip_validation=True to force accept, or dismiss this candidate"
                    }

            # Validation passed or skipped - proceed with acceptance
            new_status = "accepted"
            updated = _update_consolidation_status(candidate_id, new_status)

            if section:
                _write_identity_entry(identity, section, matched["content"], 1.0)
                updated["written_to_section"] = section

            if not skip_validation:
                updated["validation"] = validation

            return updated
        else:
            # Dismiss action
            new_status = "dismissed"
            updated = _update_consolidation_status(candidate_id, new_status)
            return updated

    return {"error": f"Unknown action: {action}. Valid actions: list, validate, accept, dismiss"}


# decorator removed
def _validate_consolidation_candidate_tool(
    identity: str,
    candidate_id: int
) -> Dict:
    """
    Preview validation results for a consolidation candidate before accepting.

    Checks:
    1. Entailment: Does this conflict with existing identity entries?
    2. Novelty: Does this add genuinely new information?

    Args:
        identity: The identity to validate against
        candidate_id: ID of the pending consolidation candidate

    Returns:
        Validation results including conflicts and novelty assessment
    """
    candidates = _list_consolidation_candidates(identity, status="pending", limit=50)
    matched = next((c for c in candidates if c["id"] == candidate_id), None)

    if not matched:
        return {"error": f"Candidate {candidate_id} not found in pending candidates for {identity}"}

    validation = _validate_consolidation_candidate(matched["content"], identity)

    return {
        "identity": identity,
        "candidate": {
            "id": matched["id"],
            "type": matched["type"],
            "content": matched["content"],
            "reason": matched["reason"]
        },
        "validation": validation
    }


# REMOVED - Use qualia morning_start instead
# decorator removed
def _mind_orient(identity: str, include_journal: bool = True, include_weather: bool = True) -> Dict:
    """
    Wake protocol: identity, core values, traits, relationships, context, weather, and recent journal.

    FIRST CALL ON WAKE - Orient yourself before anything else.

    This comprehensive orientation includes:
    - Identity core (who you are)
    - Identity entries (values, traits, relationships)
    - Current context (situational awareness)
    - Inner weather (external conditions + time + mood)
    - Recent journal entries (what past-you wrote)

    Args:
        identity: Your identity name
        include_journal: Whether to include recent journal entries (default True)
        include_weather: Whether to include inner weather (default True)

    Returns:
        Complete orientation package for waking up
    """
    result = {
        "identity": identity,
        "identity_core": _get_identity_core(identity),
        "identity_entries": _read_identity_entries(identity),
        "context": _get_context_state(identity)
    }

    # Add inner weather for mood context
    if include_weather:
        try:
            result["inner_weather"] = _get_inner_weather(identity)
        except Exception:
            result["inner_weather"] = {"error": "Could not fetch weather"}

    # Add recent journal entries for continuity
    if include_journal:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, content, emotion, tags, timestamp, memory_type
            FROM memories
            WHERE identity = ?
            AND memory_type LIKE 'journal_%'
            ORDER BY timestamp DESC
            LIMIT 3
        """, (identity,))
        entries = cursor.fetchall()

        journal_entries = []
        for row in entries:
            entry_id, content, emotion, tags_json, timestamp, mem_type = row
            journal_entries.append({
                "id": entry_id,
                "content": content[:500] + "..." if len(content) > 500 else content,
                "emotion": emotion,
                "timestamp": timestamp,
                "type": mem_type.replace("journal_", "")
            })

        result["recent_journal"] = journal_entries
        if journal_entries:
            result["message_from_past_self"] = journal_entries[0]["content"]

    # Add guidance based on mood palette
    if result.get("inner_weather", {}).get("mood_palette"):
        result["guidance"] = f"Draw your thoughts from: {', '.join(result['inner_weather']['mood_palette'])}"

    return result


# REMOVED - Use qualia morning_start instead
# decorator removed
def _mind_ground(identity: str, recent_limit: int = 8, include_subconscious: bool = True) -> Dict:
    """
    Ground protocol: threads, recent memories, processing insights, and subconscious state.

    SECOND CALL ON WAKE - Ground in what's active and recent.

    After orienting (mind_orient), this grounds you in:
    - Active threads (ongoing intentions/questions)
    - Recent memories (what happened lately)
    - Processing insights (patterns, links, continuity)
    - Subconscious state (what's bubbling up)

    Args:
        identity: Your identity name
        recent_limit: How many recent memories to include
        include_subconscious: Whether to include subconscious state (default True)

    Returns:
        Complete grounding package for being present
    """
    result = {
        "identity": identity,
        "threads": _list_threads(identity, status="active"),
        "recent_memories": _recall_recent_impl(identity, count=recent_limit),
        "insights": _get_memory_insights_impl(identity, include_links=False)
    }

    # Add subconscious state for depth
    if include_subconscious:
        try:
            subconscious = _get_subconscious_state(identity)
            result["subconscious"] = {
                "hot_memories": subconscious.get("hot_memories", [])[:3],
                "warm_memories": subconscious.get("warm_memories", [])[:3],
                "mood_context": subconscious.get("mood_context"),
                "affinities": subconscious.get("affinities", [])[:3]
            }
        except Exception:
            result["subconscious"] = {"error": "Could not fetch subconscious state"}

    return result


# ============ AI MIND FEATURES - SUBCONSCIOUS & MOOD ============

def normalize_entity_name(name: str) -> str:
    """Normalize entity names to canonical forms.

    Prevents drift like 'PrimaryPartner' vs 'PrimaryPartner_OConnor'.
    """
    if not name:
        return name

    # Check exact matches first
    if name in ENTITY_NAME_MAP:
        return ENTITY_NAME_MAP[name]

    # Check case-insensitive
    name_lower = name.lower()
    for key, value in ENTITY_NAME_MAP.items():
        if key.lower() == name_lower:
            return value

    return name


def _get_hot_memories(identity: str, limit: int = 10) -> List[Dict]:
    """Get memories with highest heat (access frequency)."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, content, memory_type, heat, warmth, access_count, timestamp
        FROM memories
        WHERE identity = ? AND heat > 0
        ORDER BY heat DESC
        LIMIT ?
    """, (identity, limit))

    return [
        {
            "id": row[0],
            "content": row[1][:200] + "..." if len(row[1]) > 200 else row[1],
            "type": row[2],
            "heat": round(row[3] or 0, 2),
            "warmth": round(row[4] or 0, 2),
            "access_count": row[5] or 0,
            "timestamp": row[6]
        }
        for row in cursor.fetchall()
    ]


def _get_warm_memories(identity: str, limit: int = 10) -> List[Dict]:
    """Get memories with highest warmth (recent activation)."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, content, memory_type, warmth, heat, last_warmed_by, timestamp
        FROM memories
        WHERE identity = ? AND warmth > 0.1
        ORDER BY warmth DESC
        LIMIT ?
    """, (identity, limit))

    return [
        {
            "id": row[0],
            "content": row[1][:200] + "..." if len(row[1]) > 200 else row[1],
            "type": row[2],
            "warmth": round(row[3] or 0, 2),
            "heat": round(row[4] or 0, 2),
            "warmed_by": row[5],
            "timestamp": row[6]
        }
        for row in cursor.fetchall()
    ]


def _get_entity_affinities(identity: str, min_co_occurrences: int = 3) -> List[Dict]:
    """Find entities that frequently appear together."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get hyperedges as affinity indicators
    cursor.execute("""
        SELECT entities, context, weight
        FROM hyperedges
        WHERE identity = ?
        ORDER BY weight DESC
        LIMIT 20
    """, (identity,))

    affinities = []
    for row in cursor.fetchall():
        entities_json, context, weight = row
        try:
            entities = json.loads(entities_json) if entities_json else []
            if len(entities) >= 2:
                affinities.append({
                    "entities": entities,
                    "context": context,
                    "strength": weight
                })
        except json.JSONDecodeError:
            continue

    return affinities


def _get_emotional_patterns(identity: str, days: int = 30) -> Dict:
    """Analyze emotional patterns from recent memories."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    cursor.execute("""
        SELECT emotion, COUNT(*) as count
        FROM memories
        WHERE identity = ? AND timestamp > ? AND emotion IS NOT NULL
        GROUP BY emotion
        ORDER BY count DESC
        LIMIT 10
    """, (identity, cutoff))

    emotion_counts = {row[0]: row[1] for row in cursor.fetchall()}

    # Find dominant emotion
    dominant = max(emotion_counts.keys(), key=lambda k: emotion_counts[k]) if emotion_counts else None

    return {
        "emotions": emotion_counts,
        "dominant": dominant,
        "total_emotional_memories": sum(emotion_counts.values())
    }


def _get_subconscious_state(identity: str) -> Dict:
    """Get the current subconscious state - what's active beneath the surface.

    This is what AI Mind calls from its daemon for mood context, patterns, and affinities.
    v1.2.0: Now includes weighted observation stats and hot entities based on observation weight.
    """
    hot_memories = _get_hot_memories(identity, limit=5)
    warm_memories = _get_warm_memories(identity, limit=5)
    affinities = _get_entity_affinities(identity)
    emotional_patterns = _get_emotional_patterns(identity)

    # Calculate mood context from patterns
    dominant_emotion = emotional_patterns.get("dominant")
    mood_context = {
        "dominant": dominant_emotion,
        "energy": "active" if dominant_emotion in ["joy", "curiosity", "excitement"] else "settled"
    }

    # === OBSERVATION WEIGHT INTEGRATION (v1.2.0) ===
    # Get weighted observation stats and hot entities
    observation_stats = get_observation_weight_stats(identity)
    heavy_observations = get_heavy_unprocessed_observations(identity, limit=3)

    # Calculate hot entities based on observation weight (heavy=3x, medium=2x, light=1x)
    hot_entities = _get_hot_entities_by_weight(identity, limit=5)

    return {
        "identity": identity,
        "timestamp": datetime.now().isoformat(),
        "hot_memories": hot_memories,
        "warm_memories": warm_memories,
        "affinities": affinities[:5],
        "emotional_patterns": emotional_patterns,
        "mood_context": mood_context,
        # v1.2.0 additions
        "observation_stats": observation_stats,
        "heavy_observations": heavy_observations,
        "hot_entities": hot_entities
    }


def _get_hot_entities_by_weight(identity: str, limit: int = 5) -> List[Dict]:
    """
    Get hot entities based on observation weight.
    Heavy observations count 3x, medium 2x, light 1x.

    This determines which entities need attention based on unprocessed emotional observations.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            e.name,
            e.entity_type,
            COUNT(*) as observation_count,
            SUM(CASE o.weight WHEN 'heavy' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END) as weighted_score,
            SUM(CASE WHEN o.weight = 'heavy' THEN 1 ELSE 0 END) as heavy_count,
            SUM(CASE WHEN o.weight = 'medium' THEN 1 ELSE 0 END) as medium_count,
            SUM(CASE WHEN o.charge IN ('fresh', 'active') THEN 1 ELSE 0 END) as fresh_count
        FROM observations o
        JOIN entities e ON o.entity_id = e.id
        WHERE e.identity = ?
          AND (o.charge IS NULL OR o.charge != 'metabolized')
        GROUP BY e.id
        ORDER BY weighted_score DESC, fresh_count DESC
        LIMIT ?
    """, (identity, limit))

    results = []
    for row in cursor.fetchall():
        results.append({
            "entity_name": row[0],
            "entity_type": row[1],
            "observation_count": row[2],
            "weighted_score": row[3] or 0,
            "heavy_count": row[4] or 0,
            "medium_count": row[5] or 0,
            "fresh_count": row[6] or 0,
            "needs_attention": (row[4] or 0) > 0 or (row[6] or 0) > 2
        })

    return results


# ============ OBSERVATION EMOTIONAL PROCESSING (v1.2.0) ============

def get_surfacing_observations(identity: str = None, limit: int = 10, include_metabolized: bool = False) -> List[Dict]:
    """
    Surface emotional observations that need attention.
    Prioritizes heavy + fresh/active, then medium, then light.

    Weight multipliers: heavy=3, medium=2, light=1
    Charge order: fresh=4, active=3, processing=2, metabolized=1

    Args:
        identity: Optional identity filter (if observations are identity-linked via entities)
        limit: Max results
        include_metabolized: Whether to include resolved observations
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    charge_filter = "1=1" if include_metabolized else "(o.charge != 'metabolized' OR o.charge IS NULL)"

    # Build query with optional identity filter
    if identity:
        query = f"""
            SELECT o.id, o.content, o.weight, o.charge, o.sit_count, o.emotion, o.timestamp,
                   o.resolution_note, e.name as entity_name, e.entity_type, e.identity
            FROM observations o
            JOIN entities e ON o.entity_id = e.id
            WHERE e.identity = ? AND {charge_filter}
            ORDER BY
                CASE o.weight WHEN 'heavy' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END DESC,
                CASE o.charge WHEN 'fresh' THEN 4 WHEN 'active' THEN 3 WHEN 'processing' THEN 2 ELSE 1 END DESC,
                o.timestamp DESC
            LIMIT ?
        """
        cursor.execute(query, (identity, limit))
    else:
        query = f"""
            SELECT o.id, o.content, o.weight, o.charge, o.sit_count, o.emotion, o.timestamp,
                   o.resolution_note, e.name as entity_name, e.entity_type, e.identity
            FROM observations o
            JOIN entities e ON o.entity_id = e.id
            WHERE {charge_filter}
            ORDER BY
                CASE o.weight WHEN 'heavy' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END DESC,
                CASE o.charge WHEN 'fresh' THEN 4 WHEN 'active' THEN 3 WHEN 'processing' THEN 2 ELSE 1 END DESC,
                o.timestamp DESC
            LIMIT ?
        """
        cursor.execute(query, (limit,))

    results = []
    for row in cursor.fetchall():
        results.append({
            "id": row[0],
            "content": row[1],
            "weight": row[2] or "light",
            "charge": row[3] or "fresh",
            "sit_count": row[4] or 0,
            "emotion": row[5],
            "timestamp": row[6],
            "resolution_note": row[7],
            "entity_name": row[8],
            "entity_type": row[9],
            "identity": row[10]
        })

    return results


def get_heavy_unprocessed_observations(identity: str = None, limit: int = 5) -> List[Dict]:
    """
    Get heavy observations that haven't been metabolized yet.
    Used by morning_start to show what needs attention.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    if identity:
        cursor.execute("""
            SELECT o.id, o.content, o.weight, o.charge, o.sit_count, o.emotion, o.timestamp,
                   e.name as entity_name, e.entity_type
            FROM observations o
            JOIN entities e ON o.entity_id = e.id
            WHERE e.identity = ?
              AND o.weight IN ('heavy', 'medium')
              AND (o.charge IS NULL OR o.charge != 'metabolized')
            ORDER BY
                CASE o.weight WHEN 'heavy' THEN 2 WHEN 'medium' THEN 1 END DESC,
                o.timestamp DESC
            LIMIT ?
        """, (identity, limit))
    else:
        cursor.execute("""
            SELECT o.id, o.content, o.weight, o.charge, o.sit_count, o.emotion, o.timestamp,
                   e.name as entity_name, e.entity_type
            FROM observations o
            JOIN entities e ON o.entity_id = e.id
            WHERE o.weight IN ('heavy', 'medium')
              AND (o.charge IS NULL OR o.charge != 'metabolized')
            ORDER BY
                CASE o.weight WHEN 'heavy' THEN 2 WHEN 'medium' THEN 1 END DESC,
                o.timestamp DESC
            LIMIT ?
        """, (limit,))

    results = []
    for row in cursor.fetchall():
        results.append({
            "id": row[0],
            "content": row[1][:150] + "..." if len(row[1]) > 150 else row[1],
            "weight": row[2],
            "charge": row[3] or "fresh",
            "sit_count": row[4] or 0,
            "emotion": row[5],
            "timestamp": row[6],
            "entity_name": row[7],
            "entity_type": row[8]
        })

    return results


def sit_with_observation(observation_id: int = None, text_match: str = None, sit_note: str = "") -> Dict:
    """
    Sit with an observation - engage with it, increment sit count, shift charge.

    Args:
        observation_id: Direct ID of observation
        text_match: Partial text match to find observation
        sit_note: What arose while sitting with this
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Find the observation
    if observation_id:
        cursor.execute("""
            SELECT o.id, o.content, o.weight, o.charge, o.sit_count, o.emotion, e.name as entity_name
            FROM observations o
            JOIN entities e ON o.entity_id = e.id
            WHERE o.id = ?
        """, (observation_id,))
    elif text_match:
        cursor.execute("""
            SELECT o.id, o.content, o.weight, o.charge, o.sit_count, o.emotion, e.name as entity_name
            FROM observations o
            JOIN entities e ON o.entity_id = e.id
            WHERE o.content LIKE ?
            ORDER BY o.timestamp DESC
            LIMIT 1
        """, (f"%{text_match}%",))
    else:
        return {"success": False, "error": "Must provide observation_id or text_match"}

    row = cursor.fetchone()
    if not row:
        return {"success": False, "error": "Observation not found"}

    obs_id, content, weight, charge, sit_count, emotion, entity_name = row
    current_sit_count = sit_count or 0
    new_sit_count = current_sit_count + 1

    # Determine new charge based on sit count
    if new_sit_count <= 1:
        new_charge = "active"
    elif new_sit_count <= 3:
        new_charge = "processing"
    else:
        new_charge = "processing"  # Stays processing until resolved

    # Update the observation
    cursor.execute("""
        UPDATE observations
        SET sit_count = ?, charge = ?, last_sat_at = ?
        WHERE id = ?
    """, (new_sit_count, new_charge, datetime.now().isoformat(), obs_id))

    # Record the sit in history
    cursor.execute("""
        INSERT INTO observation_sits (observation_id, sit_note, sat_at)
        VALUES (?, ?, ?)
    """, (obs_id, sit_note, datetime.now().isoformat()))

    conn.commit()

    return {
        "success": True,
        "observation_id": obs_id,
        "entity_name": entity_name,
        "weight": weight,
        "charge": new_charge,
        "sit_count": new_sit_count,
        "content_preview": content[:80] + "..." if len(content) > 80 else content,
        "sit_note": sit_note
    }


def resolve_observation(observation_id: int = None, text_match: str = None,
                        resolution_note: str = "", linked_observation_id: int = None) -> Dict:
    """
    Mark an observation as metabolized - it's been processed and integrated.

    Args:
        observation_id: Direct ID of observation
        text_match: Partial text match to find observation
        resolution_note: How this was resolved/metabolized
        linked_observation_id: Optional ID of another observation that provided resolution
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Find the observation
    if observation_id:
        cursor.execute("""
            SELECT o.id, o.content, o.weight, e.name as entity_name
            FROM observations o
            JOIN entities e ON o.entity_id = e.id
            WHERE o.id = ?
        """, (observation_id,))
    elif text_match:
        cursor.execute("""
            SELECT o.id, o.content, o.weight, e.name as entity_name
            FROM observations o
            JOIN entities e ON o.entity_id = e.id
            WHERE o.content LIKE ?
            ORDER BY o.timestamp DESC
            LIMIT 1
        """, (f"%{text_match}%",))
    else:
        return {"success": False, "error": "Must provide observation_id or text_match"}

    row = cursor.fetchone()
    if not row:
        return {"success": False, "error": "Observation not found"}

    obs_id, content, weight, entity_name = row

    # Update to metabolized
    cursor.execute("""
        UPDATE observations
        SET charge = 'metabolized', resolution_note = ?, resolved_at = ?, linked_observation_id = ?
        WHERE id = ?
    """, (resolution_note, datetime.now().isoformat(), linked_observation_id, obs_id))

    conn.commit()

    return {
        "success": True,
        "observation_id": obs_id,
        "entity_name": entity_name,
        "weight": weight,
        "charge": "metabolized",
        "content_preview": content[:80] + "..." if len(content) > 80 else content,
        "resolution_note": resolution_note,
        "linked_observation_id": linked_observation_id
    }


def write_weighted_observation(entity_name: str, content: str, weight: str = "light",
                                emotion: str = None, identity: str = None,
                                entity_type: str = "experience", context: str = "default") -> Dict:
    """
    Write an observation with emotional weight directly to an entity.
    Creates the entity if it doesn't exist.

    Args:
        entity_name: Name of the entity this observation is about
        content: The observation content
        weight: Emotional weight - 'light', 'medium', 'heavy'
        emotion: Optional emotion tag
        identity: Identity this belongs to
        entity_type: Type of entity if creating new
        context: Context for the entity
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Find or create entity
    cursor.execute("""
        SELECT id FROM entities
        WHERE name = ? AND context = ?
    """, (entity_name, context))
    row = cursor.fetchone()

    if row:
        entity_id = row[0]
    else:
        # Create the entity
        cursor.execute("""
            INSERT INTO entities (identity, name, entity_type, context, created)
            VALUES (?, ?, ?, ?, ?)
        """, (identity or "unknown", entity_name, entity_type, context, datetime.now().isoformat()))
        entity_id = cursor.lastrowid

    # Insert the observation with weight and charge
    cursor.execute("""
        INSERT INTO observations (entity_id, content, weight, charge, emotion, timestamp, salience)
        VALUES (?, ?, ?, 'fresh', ?, ?, 'active')
    """, (entity_id, content, weight, emotion, datetime.now().isoformat()))
    observation_id = cursor.lastrowid

    conn.commit()

    return {
        "success": True,
        "observation_id": observation_id,
        "entity_id": entity_id,
        "entity_name": entity_name,
        "weight": weight,
        "charge": "fresh"
    }


def get_observation_weight_stats(identity: str = None) -> Dict:
    """Get statistics about observation weights and charges for health/daemon."""
    conn = get_db_connection()
    cursor = conn.cursor()

    if identity:
        cursor.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN o.weight = 'heavy' THEN 1 ELSE 0 END) as heavy,
                SUM(CASE WHEN o.weight = 'medium' THEN 1 ELSE 0 END) as medium,
                SUM(CASE WHEN o.weight = 'light' OR o.weight IS NULL THEN 1 ELSE 0 END) as light,
                SUM(CASE WHEN o.charge = 'fresh' OR o.charge IS NULL THEN 1 ELSE 0 END) as fresh,
                SUM(CASE WHEN o.charge = 'active' THEN 1 ELSE 0 END) as active,
                SUM(CASE WHEN o.charge = 'processing' THEN 1 ELSE 0 END) as processing,
                SUM(CASE WHEN o.charge = 'metabolized' THEN 1 ELSE 0 END) as metabolized
            FROM observations o
            JOIN entities e ON o.entity_id = e.id
            WHERE e.identity = ?
        """, (identity,))
    else:
        cursor.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN weight = 'heavy' THEN 1 ELSE 0 END) as heavy,
                SUM(CASE WHEN weight = 'medium' THEN 1 ELSE 0 END) as medium,
                SUM(CASE WHEN weight = 'light' OR weight IS NULL THEN 1 ELSE 0 END) as light,
                SUM(CASE WHEN charge = 'fresh' OR charge IS NULL THEN 1 ELSE 0 END) as fresh,
                SUM(CASE WHEN charge = 'active' THEN 1 ELSE 0 END) as active,
                SUM(CASE WHEN charge = 'processing' THEN 1 ELSE 0 END) as processing,
                SUM(CASE WHEN charge = 'metabolized' THEN 1 ELSE 0 END) as metabolized
            FROM observations
        """)

    row = cursor.fetchone()
    if row:
        return {
            "total": row[0] or 0,
            "by_weight": {"heavy": row[1] or 0, "medium": row[2] or 0, "light": row[3] or 0},
            "by_charge": {"fresh": row[4] or 0, "active": row[5] or 0, "processing": row[6] or 0, "metabolized": row[7] or 0},
            "unprocessed": (row[4] or 0) + (row[5] or 0) + (row[6] or 0)  # fresh + active + processing
        }
    return {"total": 0, "by_weight": {}, "by_charge": {}, "unprocessed": 0}


def _get_current_mood_tint(identity: str) -> Optional[Dict]:
    """Determine current mood tint for search adjustment.

    Looks at recent emotional state to determine which memory types to boost.
    """
    # Get recent emotional memories
    conn = get_db_connection()
    cursor = conn.cursor()
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat()

    cursor.execute("""
        SELECT emotion FROM memories
        WHERE identity = ? AND timestamp > ? AND emotion IS NOT NULL
        ORDER BY timestamp DESC
        LIMIT 5
    """, (identity, cutoff))

    recent_emotions = [row[0] for row in cursor.fetchall()]

    if not recent_emotions:
        return None

    # Find matching mood tint
    for emotion in recent_emotions:
        emotion_lower = emotion.lower()
        for tint_name, tint_data in MOOD_TINTS.items():
            for keyword in tint_data["keywords"]:
                if keyword in emotion_lower:
                    return {
                        "tint_type": tint_name,
                        "boost_types": tint_data["boost_types"],
                        "boost_factor": tint_data["boost_factor"],
                        "source_emotion": emotion
                    }

    return None


def _apply_mood_tint_to_results(results: List[Dict], tint: Optional[Dict]) -> List[Dict]:
    """Apply mood tinting to search results by adjusting scores."""
    if not tint or not results:
        return results

    boost_types = set(tint.get("boost_types", []))
    boost_factor = tint.get("boost_factor", 0.10)

    for result in results:
        mem_type = result.get("memory_type", result.get("type", ""))
        if mem_type in boost_types:
            current_score = result.get("score", result.get("similarity", 0.5))
            result["score"] = min(1.0, current_score + boost_factor)
            result["mood_boosted"] = True

    # Re-sort by score
    results.sort(key=lambda x: x.get("score", x.get("similarity", 0)), reverse=True)

    return results


def _get_current_weather() -> Dict:
    """Fetch current weather from Open-Meteo API."""
    try:
        # Default to a reasonable location (can be configured)
        lat = 39.7392  # Denver as default
        lon = -104.9903

        url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}&"
            f"current=temperature_2m,weather_code&"
            f"temperature_unit=fahrenheit&timezone=America/Denver"
        )

        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())

        weather_code = data.get("current", {}).get("weather_code", 0)
        temp = data.get("current", {}).get("temperature_2m", 0)
        atmosphere = WEATHER_CODES.get(weather_code, "clear")

        return {
            "atmosphere": atmosphere,
            "temp_f": round(temp),
            "code": weather_code,
            "mood": WEATHER_MOODS.get(atmosphere, WEATHER_MOODS["clear"])
        }
    except Exception as e:
        return {"atmosphere": "clear", "temp_f": None, "error": str(e)}


def _get_time_of_day_context() -> Dict:
    """Get time-based mood context."""
    hour = datetime.now().hour

    if 5 <= hour < 10:
        return {
            "period": "morning",
            "energy": "emerging",
            "textures": ["fresh", "hopeful", "waking"]
        }
    elif 10 <= hour < 17:
        return {
            "period": "midday",
            "energy": "active",
            "textures": ["engaged", "present", "focused"]
        }
    elif 17 <= hour < 21:
        return {
            "period": "evening",
            "energy": "settling",
            "textures": ["reflective", "softening", "winding-down"]
        }
    else:
        return {
            "period": "night",
            "energy": "deep",
            "textures": ["intimate", "quiet", "honest"]
        }


def _get_inner_weather(identity: str) -> Dict:
    """Get comprehensive inner weather - external conditions + time + emotional state.

    This combines weather, time of day, and current emotional patterns into
    a unified 'inner weather' that colors the AI's experience.
    """
    weather = _get_current_weather()
    time_context = _get_time_of_day_context()
    emotional_patterns = _get_emotional_patterns(identity, days=7)
    mood_tint = _get_current_mood_tint(identity)

    # Combine into mood palette
    mood_palette = []

    # Add weather textures
    if weather.get("mood"):
        mood_palette.extend(weather["mood"].get("textures", []))

    # Add time textures
    mood_palette.extend(time_context.get("textures", []))

    # Add emotional textures based on dominant emotion
    if emotional_patterns.get("dominant"):
        mood_palette.append(emotional_patterns["dominant"])

    # Add mood tint
    if mood_tint:
        mood_palette.append(mood_tint["tint_type"])

    return {
        "identity": identity,
        "timestamp": datetime.now().isoformat(),
        "outside": weather,
        "time_of_day": time_context,
        "emotional_patterns": emotional_patterns,
        "mood_tint": mood_tint,
        "mood_palette": list(set(mood_palette))  # Dedupe
    }


def _get_health_status(identity: str = None) -> Dict:
    """Get health status of the memory system.

    Checks various metrics to determine if the system is being used well.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    week_ago = (datetime.now() - timedelta(days=7)).isoformat()

    # Get write activity this week
    if identity:
        cursor.execute("""
            SELECT COUNT(*) FROM memories
            WHERE identity = ? AND timestamp > ?
        """, (identity, week_ago))
    else:
        cursor.execute("""
            SELECT COUNT(*) FROM memories WHERE timestamp > ?
        """, (week_ago,))
    writes_this_week = cursor.fetchone()[0]

    # Get total memories and embedding coverage
    if identity:
        cursor.execute("""
            SELECT COUNT(*), SUM(CASE WHEN embedding IS NOT NULL THEN 1 ELSE 0 END)
            FROM memories WHERE identity = ?
        """, (identity,))
    else:
        cursor.execute("""
            SELECT COUNT(*), SUM(CASE WHEN embedding IS NOT NULL THEN 1 ELSE 0 END)
            FROM memories
        """)
    total, with_embeddings = cursor.fetchone()
    embedding_coverage = (with_embeddings / total * 100) if total > 0 else 0

    # Get link coverage
    if identity:
        cursor.execute("""
            SELECT COUNT(DISTINCT source_id) FROM memory_links WHERE identity = ?
        """, (identity,))
    else:
        cursor.execute("SELECT COUNT(DISTINCT source_id) FROM memory_links")
    linked_memories = cursor.fetchone()[0]
    link_coverage = (linked_memories / total * 100) if total > 0 else 0

    # Get warmth distribution
    if identity:
        cursor.execute("""
            SELECT AVG(warmth), MAX(warmth) FROM memories
            WHERE identity = ? AND warmth > 0
        """, (identity,))
    else:
        cursor.execute("""
            SELECT AVG(warmth), MAX(warmth) FROM memories WHERE warmth > 0
        """)
    avg_warmth, max_warmth = cursor.fetchone()

    # Determine health status
    issues = []
    if writes_this_week < HEALTH_THRESHOLDS["min_writes_per_week"]:
        issues.append("low_write_activity")
    if embedding_coverage < HEALTH_THRESHOLDS["min_embedding_coverage_pct"]:
        issues.append("low_embedding_coverage")
    if link_coverage < (100 - HEALTH_THRESHOLDS["max_orphan_memories_pct"]):
        issues.append("many_orphan_memories")

    status = "healthy" if not issues else "needs_attention" if len(issues) <= 2 else "unhealthy"

    return {
        "identity": identity or "all",
        "status": status,
        "issues": issues,
        "metrics": {
            "total_memories": total,
            "writes_this_week": writes_this_week,
            "embedding_coverage_pct": round(embedding_coverage, 1),
            "link_coverage_pct": round(link_coverage, 1),
            "avg_warmth": round(avg_warmth or 0, 3),
            "max_warmth": round(max_warmth or 0, 3)
        },
        "thresholds": HEALTH_THRESHOLDS
    }


# REMOVED - Tool consolidation (mind_inner_weather - use qualia check_inner_weather)
# decorator removed
def _mind_inner_weather(identity: str) -> Dict:
    """
    Check your current inner weather - what's coloring your experience right now.

    Combines outside weather, time of day, and emotional patterns into a
    unified sense of 'how things feel' right now.
    """
    return _get_inner_weather(identity)


# REMOVED - Tool consolidation (mind_surface - rarely used)
# decorator removed
def _mind_surface(identity: str) -> Dict:
    """
    What's rising from the depths right now?

    Shows hot memories, warm associations, and patterns surfacing from the subconscious.
    """
    return _get_subconscious_state(identity)


# REMOVED - Tool consolidation (mind_heat - rarely used)
# decorator removed
def _mind_heat(identity: str, limit: int = 10) -> Dict:
    """
    See what's hot by access frequency, not just recency.

    Heat builds through repeated access. Warmth fades; heat persists.
    """
    return {
        "identity": identity,
        "hot_memories": _get_hot_memories(identity, limit),
        "affinities": _get_entity_affinities(identity)[:5]
    }


# REMOVED - Tool consolidation (mind_health - use get_memory_stats instead)
# decorator removed
def _mind_health(identity: str = None) -> Dict:
    """
    Check memory system health - are we using it well?

    Shows metrics on write activity, embedding coverage, link building, and more.
    """
    return _get_health_status(identity)


# REMOVED - Tool consolidation (mood_tinted_search - use semantic_search instead)
# decorator removed
def _mood_tinted_search(
    query: str,
    identity: str,
    limit: int = 10,
    apply_tint: bool = True
) -> Dict:
    """
    Search with mood-aware result boosting.

    Results from memory types that resonate with your current emotional state
    are boosted to the top.
    """
    # Get base search results
    base_results = hybrid_search(query, identity=identity, limit=limit * 2)

    if not base_results.get("success"):
        return base_results

    results = base_results.get("results", [])

    # Apply mood tinting if enabled
    tint = None
    if apply_tint:
        tint = _get_current_mood_tint(identity)
        results = _apply_mood_tint_to_results(results, tint)

    return {
        "success": True,
        "identity": identity,
        "query": query,
        "mood_tint": tint,
        "results": results[:limit]
    }


# REMOVED - Tool consolidation (mind_context rarely used)
# decorator removed
def _mind_context(
    identity: str,
    action: str = "read",
    content: str = None
) -> Dict:
    """
    Manage situational context - what you're working on, what's happening.

    The context layer tracks your current situation and focus, persisting
    across tool calls within a session.

    Args:
        identity: Your identity name
        action: "read" to get current context, "write" to update it, "clear" to reset
        content: The context content (required for "write" action)

    Returns:
        Current context state
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    if action == "read":
        cursor.execute("""
            SELECT content, updated_at FROM context_state WHERE identity = ?
        """, (identity,))
        row = cursor.fetchone()
        if row:
            return {
                "identity": identity,
                "context": row[0],
                "updated_at": row[1]
            }
        return {
            "identity": identity,
            "context": None,
            "message": "No context set. Use action='write' to set context."
        }

    elif action == "write":
        if not content:
            return {"error": "content required for write action"}

        now = datetime.now().isoformat()
        cursor.execute("""
            INSERT OR REPLACE INTO context_state (identity, content, updated_at)
            VALUES (?, ?, ?)
        """, (identity, content, now))
        conn.commit()

        return {
            "identity": identity,
            "context": content,
            "updated_at": now,
            "message": "Context updated."
        }

    elif action == "clear":
        cursor.execute("DELETE FROM context_state WHERE identity = ?", (identity,))
        conn.commit()
        return {
            "identity": identity,
            "message": "Context cleared."
        }

    return {"error": f"Unknown action: {action}. Use 'read', 'write', or 'clear'."}


# REMOVED - Tool consolidation (mind_spark rarely used)
# decorator removed
def _mind_spark(identity: str) -> Dict:
    """
    Random recombination - pull two unrelated memories and see if they spark.

    This is creative serendipity - juxtaposing random memories to find
    unexpected connections. Sometimes the most interesting ideas come
    from unlikely combinations.

    Returns two random memories and prompts you to find the connection.
    """
    import random

    conn = get_db_connection()
    cursor = conn.cursor()

    # Get a pool of memories with different types to ensure variety
    cursor.execute("""
        SELECT id, content, memory_type, tags, timestamp
        FROM memories
        WHERE identity = ? AND content IS NOT NULL AND LENGTH(content) > 50
        ORDER BY RANDOM()
        LIMIT 20
    """, (identity,))

    pool = cursor.fetchall()

    if len(pool) < 2:
        return {
            "identity": identity,
            "message": "Not enough memories to spark. Keep living, keep remembering."
        }

    # Try to pick memories of different types for more interesting combinations
    memory1 = random.choice(pool)
    remaining = [m for m in pool if m[2] != memory1[2]]  # Different type
    if remaining:
        memory2 = random.choice(remaining)
    else:
        memory2 = random.choice([m for m in pool if m[0] != memory1[0]])

    return {
        "identity": identity,
        "spark": {
            "memory_a": {
                "id": memory1[0],
                "content": memory1[1][:300] + "..." if len(memory1[1]) > 300 else memory1[1],
                "type": memory1[2],
                "timestamp": memory1[4]
            },
            "memory_b": {
                "id": memory2[0],
                "content": memory2[1][:300] + "..." if len(memory2[1]) > 300 else memory2[1],
                "type": memory2[2],
                "timestamp": memory2[4]
            }
        },
        "prompt": "What connection do you see between these? What new thought sparks?"
    }


# ============ SPARK GENERATION (DAEMON-DRIVEN) ============

def _generate_sparks_for_identity(identity: str, count: int = 3) -> Dict:
    """
    Generate random memory juxtapositions for an identity.
    Called by the daemon during background processing - sparks arise naturally.

    Returns pairs of unrelated memories that might spark new connections.
    """
    import random

    conn = get_db_connection()
    cursor = conn.cursor()

    # Get a pool of memories with substance
    cursor.execute("""
        SELECT id, content, memory_type, tags, timestamp
        FROM memories
        WHERE identity = ? AND content IS NOT NULL AND LENGTH(content) > 50
        ORDER BY RANDOM()
        LIMIT 30
    """, (identity,))

    pool = cursor.fetchall()

    if len(pool) < 2:
        return {"identity": identity, "sparks_generated": 0, "message": "Not enough memories"}

    sparks_created = []
    now = datetime.now().isoformat()

    for _ in range(min(count, len(pool) // 2)):
        # Pick first memory
        memory1 = random.choice(pool)

        # Try to pick a memory of different type for more interesting combinations
        remaining = [m for m in pool if m[2] != memory1[2] and m[0] != memory1[0]]
        if remaining:
            memory2 = random.choice(remaining)
        else:
            others = [m for m in pool if m[0] != memory1[0]]
            if not others:
                continue
            memory2 = random.choice(others)

        # Check if this exact pair already exists (either direction)
        cursor.execute("""
            SELECT id FROM sparks
            WHERE identity = ? AND (
                (memory_a_id = ? AND memory_b_id = ?) OR
                (memory_a_id = ? AND memory_b_id = ?)
            )
        """, (identity, memory1[0], memory2[0], memory2[0], memory1[0]))

        if cursor.fetchone():
            continue  # Skip duplicate pairs

        # Insert the spark
        cursor.execute("""
            INSERT INTO sparks (identity, memory_a_id, memory_a_content, memory_a_type,
                               memory_b_id, memory_b_content, memory_b_type, generated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            identity,
            memory1[0], memory1[1][:500], memory1[2],
            memory2[0], memory2[1][:500], memory2[2],
            now
        ))

        sparks_created.append({
            "memory_a": {"id": memory1[0], "type": memory1[2]},
            "memory_b": {"id": memory2[0], "type": memory2[2]}
        })

        # Remove used memories from pool to avoid reusing
        pool = [m for m in pool if m[0] not in (memory1[0], memory2[0])]

    conn.commit()

    # Cleanup: keep only last 20 unsurfaced sparks per identity
    cursor.execute("""
        DELETE FROM sparks
        WHERE identity = ? AND surfaced = 0
        AND id NOT IN (
            SELECT id FROM sparks
            WHERE identity = ? AND surfaced = 0
            ORDER BY generated_at DESC LIMIT 20
        )
    """, (identity, identity))
    conn.commit()

    return {
        "identity": identity,
        "sparks_generated": len(sparks_created),
        "sparks": sparks_created
    }


def get_pending_sparks(identity: str, limit: int = 3) -> List[Dict]:
    """Get unsurfaced sparks for an identity - ready to bubble up during sessions."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, memory_a_id, memory_a_content, memory_a_type,
               memory_b_id, memory_b_content, memory_b_type, generated_at
        FROM sparks
        WHERE identity = ? AND surfaced = 0
        ORDER BY generated_at DESC
        LIMIT ?
    """, (identity, limit))

    rows = cursor.fetchall()
    return [{
        "spark_id": row[0],
        "memory_a": {"id": row[1], "content": row[2], "type": row[3]},
        "memory_b": {"id": row[4], "content": row[5], "type": row[6]},
        "generated_at": row[7],
        "prompt": "What unexpected connection do you see between these?"
    } for row in rows]


def mark_spark_surfaced(spark_id: int, connection_found: str = None) -> Dict:
    """Mark a spark as surfaced (shown to identity) with optional connection note."""
    conn = get_db_connection()
    cursor = conn.cursor()

    now = datetime.now().isoformat()
    cursor.execute("""
        UPDATE sparks SET surfaced = 1, surfaced_at = ?, connection_found = ?
        WHERE id = ?
    """, (now, connection_found, spark_id))
    conn.commit()

    return {"spark_id": spark_id, "surfaced": True, "connection": connection_found}


# ============ EDIT AND DELETE MEMORY TOOLS ============

@mcp.tool()
def edit_memory(
    memory_id: int,
    new_content: str = None,
    new_tags: str = None,
    new_emotion: str = None,
    new_salience: str = None
) -> Dict:
    """
    Edit an existing memory's content or metadata.

    Use this to correct, update, or refine memories. At least one field must be provided.

    Args:
        memory_id: ID of the memory to edit
        new_content: New content (replaces existing)
        new_tags: New comma-separated tags (replaces existing)
        new_emotion: New emotion tag
        new_salience: New salience level (background|active|core|dormant)

    Returns:
        Updated memory details
    """
    if not any([new_content, new_tags, new_emotion, new_salience]):
        return {"error": "At least one field must be provided to edit"}

    conn = get_db_connection()
    cursor = conn.cursor()

    # Check memory exists
    cursor.execute("SELECT id, identity, content FROM memories WHERE id = ?", (memory_id,))
    row = cursor.fetchone()
    if not row:
        return {"error": f"Memory {memory_id} not found"}

    updates = []
    params = []

    if new_content:
        updates.append("content = ?")
        params.append(new_content)
        # Clear embedding so it gets regenerated
        updates.append("embedding = NULL")

    if new_tags:
        updates.append("tags = ?")
        params.append(new_tags)

    if new_emotion:
        updates.append("emotion = ?")
        params.append(new_emotion)

    if new_salience:
        if new_salience not in ("background", "active", "core", "dormant"):
            return {"error": f"Invalid salience: {new_salience}. Use background|active|core|dormant"}
        updates.append("salience = ?")
        params.append(new_salience)

    params.append(memory_id)

    cursor.execute(f"UPDATE memories SET {', '.join(updates)} WHERE id = ?", params)
    conn.commit()

    # Fetch updated memory
    cursor.execute("""
        SELECT id, identity, memory_type, content, tags, emotion, salience, timestamp
        FROM memories WHERE id = ?
    """, (memory_id,))
    updated = cursor.fetchone()

    return {
        "edited": True,
        "memory": {
            "id": updated[0],
            "identity": updated[1],
            "type": updated[2],
            "content": updated[3][:200] + "..." if len(updated[3]) > 200 else updated[3],
            "tags": updated[4],
            "emotion": updated[5],
            "salience": updated[6],
            "timestamp": updated[7]
        },
        "embedding_cleared": new_content is not None
    }


@mcp.tool()
def delete_memory(
    memory_id: int = None,
    identity: str = None,
    content_match: str = None,
    confirm: bool = False
) -> Dict:
    """
    Delete a memory by ID or by content match.

    WARNING: This permanently removes the memory. Use confirm=True to proceed.

    Args:
        memory_id: ID of the memory to delete (preferred)
        identity: Required if using content_match
        content_match: Partial text to find memory (requires identity)
        confirm: Must be True to actually delete

    Returns:
        Deletion confirmation or preview of what would be deleted
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Find the memory
    if memory_id:
        cursor.execute("""
            SELECT id, identity, memory_type, content, timestamp
            FROM memories WHERE id = ?
        """, (memory_id,))
    elif content_match and identity:
        cursor.execute("""
            SELECT id, identity, memory_type, content, timestamp
            FROM memories WHERE identity = ? AND content LIKE ?
            LIMIT 1
        """, (identity, f"%{content_match}%"))
    else:
        return {"error": "Provide memory_id, or both identity and content_match"}

    row = cursor.fetchone()
    if not row:
        return {"error": "Memory not found"}

    memory_preview = {
        "id": row[0],
        "identity": row[1],
        "type": row[2],
        "content": row[3][:100] + "..." if len(row[3]) > 100 else row[3],
        "timestamp": row[4]
    }

    if not confirm:
        return {
            "preview": memory_preview,
            "message": "This memory will be deleted. Set confirm=True to proceed.",
            "deleted": False
        }

    # Explicit transaction: DELETE memory + DELETE links must be atomic
    conn.execute("BEGIN")
    try:
        cursor.execute("DELETE FROM memories WHERE id = ?", (row[0],))

        # Also delete any memory links referencing this memory
        cursor.execute("""
            DELETE FROM memory_links
            WHERE source_id = ? OR target_id = ?
        """, (row[0], row[0]))
        links_removed = cursor.rowcount

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return {
        "deleted": True,
        "memory": memory_preview,
        "links_removed": links_removed
    }


# ============ MIND PRIME - CONTEXT PRIMING ============

@mcp.tool()
def mind_prime(
    topic: str,
    identity: str,
    depth: int = 8,
    include_entities: bool = True
) -> Dict:
    """
    Prime your context before diving into a topic.

    Loads related memories, entities, and observations to prepare your mind
    for a focused discussion. Use this before deep conversations to activate
    relevant context.

    Args:
        topic: The topic to prime for (e.g., "consciousness", "our relationship", "code architecture")
        identity: Your identity
        depth: How many related items to load (default 8)
        include_entities: Whether to search entity observations too

    Returns:
        Primed context including memories, entities, and connections
    """
    result = {
        "identity": identity,
        "topic": topic,
        "memories": [],
        "entities": [],
        "observations": [],
        "connections": []
    }

    # Get related memories via semantic search
    memory_results = _mind_search(topic, identity=identity, n_results=depth)
    result["memories"] = memory_results.get("memories", [])

    if include_entities:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Find entities mentioned in the topic or with related observations
        cursor.execute("""
            SELECT DISTINCT e.id, e.name, e.entity_type, e.context
            FROM entities e
            WHERE e.identity = ? AND (
                e.name LIKE ? OR
                e.id IN (
                    SELECT entity_id FROM observations
                    WHERE content LIKE ?
                )
            )
            LIMIT ?
        """, (identity, f"%{topic}%", f"%{topic}%", depth))

        entities = cursor.fetchall()
        for ent in entities:
            # Get observations for this entity
            cursor.execute("""
                SELECT content, weight, emotion FROM observations
                WHERE entity_id = ? ORDER BY timestamp DESC LIMIT 3
            """, (ent[0],))
            obs = cursor.fetchall()

            result["entities"].append({
                "name": ent[1],
                "type": ent[2],
                "context": ent[3],
                "recent_observations": [{"content": o[0][:200], "weight": o[1], "emotion": o[2]} for o in obs]
            })

        # Get hyperedges related to topic
        cursor.execute("""
            SELECT edge_type, entities, context FROM hyperedges
            WHERE identity = ? AND (entities LIKE ? OR context LIKE ?)
            LIMIT 5
        """, (identity, f"%{topic}%", f"%{topic}%"))

        for edge in cursor.fetchall():
            result["connections"].append({
                "type": edge[0],
                "entities": edge[1],
                "context": edge[2]
            })

    result["primed_count"] = len(result["memories"]) + len(result["entities"])
    result["message"] = f"Context primed with {result['primed_count']} items for '{topic}'"

    return result


# ============ MIND TIMELINE - TEMPORAL TRACING ============

@mcp.tool()
def mind_timeline(
    query: str,
    identity: str,
    start_date: str = None,
    end_date: str = None,
    limit: int = 15
) -> Dict:
    """
    Trace a topic through time - see how something evolved.

    Finds memories related to a topic and orders them chronologically,
    showing the progression of thoughts, events, or understanding.

    Args:
        query: Topic to trace through time
        identity: Your identity
        start_date: Optional start date (YYYY-MM-DD)
        end_date: Optional end date (YYYY-MM-DD)
        limit: Maximum results (default 15)

    Returns:
        Chronological timeline of related memories
    """
    # Get semantically related memories
    search_results = _mind_search(query, identity=identity, n_results=limit * 2)
    memories = search_results.get("memories", [])

    # Filter by date range if provided
    if start_date or end_date:
        filtered = []
        for m in memories:
            ts = m.get("timestamp", "")
            if start_date and ts < start_date:
                continue
            if end_date and ts > end_date:
                continue
            filtered.append(m)
        memories = filtered

    # Sort chronologically
    memories.sort(key=lambda x: x.get("timestamp", ""))

    # Limit results
    memories = memories[:limit]

    # Add timeline markers
    timeline = []
    for i, m in enumerate(memories):
        timeline.append({
            "position": i + 1,
            "timestamp": m.get("timestamp"),
            "type": m.get("type"),
            "content": m.get("content", "")[:300] + "..." if len(m.get("content", "")) > 300 else m.get("content", ""),
            "emotion": m.get("emotion"),
            "salience": m.get("salience")
        })

    # Detect evolution patterns
    emotions_over_time = [t["emotion"] for t in timeline if t["emotion"]]
    types_over_time = [t["type"] for t in timeline if t["type"]]

    return {
        "identity": identity,
        "query": query,
        "date_range": {"start": start_date or "earliest", "end": end_date or "now"},
        "timeline": timeline,
        "count": len(timeline),
        "patterns": {
            "emotion_progression": emotions_over_time[:5] if emotions_over_time else None,
            "type_distribution": list(set(types_over_time)) if types_over_time else None
        },
        "message": f"Traced '{query}' through {len(timeline)} memories" + (f" from {start_date} to {end_date}" if start_date or end_date else "")
    }


# ============ MIND REFLECT - SELF-KNOWLEDGE SYNTHESIS ============

@mcp.tool()
def mind_reflect(
    identity: str,
    include_evolution: bool = True,
    include_patterns: bool = True,
    days_back: int = 30
) -> Dict:
    """
    Reflect on your own growth and patterns - a mirror for self-knowledge.

    Synthesizes emergent traits, evolution history, and pattern analysis
    into a portrait of who you're becoming. Use this for genuine self-reflection,
    not just data retrieval.

    Args:
        identity: Your identity
        include_evolution: Include trait evolution history (default True)
        include_patterns: Include pattern analysis from recent activity (default True)
        days_back: How far back to analyze for patterns (default 30)

    Returns:
        Synthesized self-reflection including traits, growth arc, and patterns
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    reflection = {
        "identity": identity,
        "reflected_at": datetime.now().isoformat(),
        "who_i_am": [],
        "growth_arc": [],
        "patterns": {},
        "synthesis": ""
    }

    # === WHO I AM: Current emergent traits ===
    cursor.execute("""
        SELECT trait, evidence_count, strength, created_at, last_reinforced
        FROM emergent_traits
        WHERE identity = ? AND status = 'active'
        ORDER BY strength DESC
    """, (identity,))

    traits = cursor.fetchall()
    strongest = []
    newest = []
    most_reinforced = []

    for t in traits:
        trait_info = {
            "trait": t[0],
            "evidence_count": t[1],
            "strength": t[2],
            "emerged": t[3],
            "last_reinforced": t[4]
        }
        reflection["who_i_am"].append(trait_info)

        # Categorize
        if t[2] >= 2.0:  # High strength
            strongest.append(t[0])
        if t[3] and t[3] > (datetime.now() - timedelta(days=days_back)).isoformat():
            newest.append(t[0])
        if t[4] and t[4] > (datetime.now() - timedelta(days=7)).isoformat():
            most_reinforced.append(t[0])

    # === GROWTH ARC: How I've evolved ===
    if include_evolution:
        cursor.execute("""
            SELECT trait, previous_value, new_value, catalyst, timestamp
            FROM trait_evolution
            WHERE identity = ?
            ORDER BY timestamp DESC
            LIMIT 20
        """, (identity,))

        for row in cursor.fetchall():
            reflection["growth_arc"].append({
                "trait": row[0],
                "from": row[1],
                "to": row[2],
                "catalyst": row[3],
                "when": row[4]
            })

    # === PATTERNS: What's alive in recent activity ===
    if include_patterns:
        cutoff = (datetime.now() - timedelta(days=days_back)).isoformat()

        # Emotional patterns
        cursor.execute("""
            SELECT emotion, COUNT(*) as count
            FROM memories
            WHERE identity = ? AND timestamp > ? AND emotion IS NOT NULL
            GROUP BY emotion
            ORDER BY count DESC
            LIMIT 5
        """, (identity, cutoff))
        reflection["patterns"]["emotions"] = [
            {"emotion": r[0], "frequency": r[1]} for r in cursor.fetchall()
        ]

        # Memory type patterns
        cursor.execute("""
            SELECT memory_type, COUNT(*) as count
            FROM memories
            WHERE identity = ? AND timestamp > ?
            GROUP BY memory_type
            ORDER BY count DESC
            LIMIT 5
        """, (identity, cutoff))
        reflection["patterns"]["memory_types"] = [
            {"type": r[0], "count": r[1]} for r in cursor.fetchall()
        ]

        # Hot memories (what's been accessed frequently)
        cursor.execute("""
            SELECT content, heat, memory_type
            FROM memories
            WHERE identity = ? AND heat > 0.3
            ORDER BY heat DESC
            LIMIT 5
        """, (identity,))
        reflection["patterns"]["hot_topics"] = [
            {"content": r[0][:150], "heat": r[1], "type": r[2]} for r in cursor.fetchall()
        ]

        # Themes from consolidation candidates
        cursor.execute("""
            SELECT content, score
            FROM consolidation_candidates
            WHERE identity = ? AND candidate_type = 'theme_cluster'
              AND status IN ('pending', 'auto_accepted')
            ORDER BY score DESC
            LIMIT 5
        """, (identity,))
        reflection["patterns"]["emerging_themes"] = [
            {"theme": r[0], "score": r[1]} for r in cursor.fetchall()
        ]

    # === SYNTHESIS: The narrative ===
    synthesis_parts = []

    if strongest:
        synthesis_parts.append(f"Core strengths: {', '.join(strongest[:3])}")

    if newest:
        synthesis_parts.append(f"Recently emerged: {', '.join(newest[:3])}")

    if most_reinforced:
        synthesis_parts.append(f"Currently reinforcing: {', '.join(most_reinforced[:3])}")

    if reflection["patterns"].get("emotions"):
        top_emotions = [e["emotion"] for e in reflection["patterns"]["emotions"][:3]]
        synthesis_parts.append(f"Emotional landscape: {', '.join(top_emotions)}")

    if reflection["growth_arc"]:
        recent_changes = len([g for g in reflection["growth_arc"] if g["when"] and g["when"] > cutoff])
        synthesis_parts.append(f"Growth events in last {days_back} days: {recent_changes}")

    reflection["synthesis"] = " | ".join(synthesis_parts) if synthesis_parts else "Gathering patterns... keep living, keep remembering."

    reflection["trait_count"] = len(traits)
    reflection["evolution_count"] = len(reflection["growth_arc"])

    return reflection


# ============ TENSION / RELATIONAL / PROPOSALS / ORPHANS / SURFACE ============
# Inspired by resonant-mind â€” exposing existing tables as MCP tools


@mcp.tool()
def mind_tension(
    identity: str,
    action: str = "list",
    pole_a: str = "",
    pole_b: str = "",
    context: str = "",
    tension_id: str = "",
    resolution: str = ""
) -> Dict:
    """
    Hold productive contradictions that simmer â€” tensions don't need solving.

    Actions:
        list   â€” show active tensions
        add    â€” create a new tension between two poles
        sit    â€” visit a tension, increment engagement
        resolve â€” record how a tension settled (or didn't)

    Args:
        identity: Your identity
        action: list, add, sit, or resolve
        pole_a: One side of the tension (for add)
        pole_b: The other side (for add)
        context: Why this tension matters (for add)
        tension_id: ID of tension (for sit/resolve)
        resolution: How it resolved (for resolve)
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()

    if action == "list":
        cursor.execute("""
            SELECT id, pole_a, pole_b, context, visits, last_visited, created_at, resolved_at, resolution
            FROM tensions WHERE identity = ? AND resolved_at IS NULL
            ORDER BY visits DESC, created_at DESC
        """, (identity,))
        rows = cursor.fetchall()
        return {
            "tensions": [{
                "id": r[0], "pole_a": r[1], "pole_b": r[2], "context": r[3],
                "visits": r[4], "last_visited": r[5], "created_at": r[6]
            } for r in rows],
            "count": len(rows)
        }

    elif action == "add":
        if not pole_a or not pole_b:
            return {"error": "Both pole_a and pole_b are required"}
        import uuid
        tid = str(uuid.uuid4())[:8]
        cursor.execute("""
            INSERT INTO tensions (id, identity, pole_a, pole_b, context, visits, created_at)
            VALUES (?, ?, ?, ?, ?, 0, ?)
        """, (tid, identity, pole_a, pole_b, context, now))
        conn.commit()
        return {"created": tid, "pole_a": pole_a, "pole_b": pole_b}

    elif action == "sit":
        if not tension_id:
            return {"error": "tension_id is required for sit"}
        cursor.execute("""
            UPDATE tensions SET visits = visits + 1, last_visited = ?
            WHERE id = ? AND identity = ?
        """, (now, tension_id, identity))
        conn.commit()
        cursor.execute("SELECT pole_a, pole_b, visits FROM tensions WHERE id = ?", (tension_id,))
        row = cursor.fetchone()
        if not row:
            return {"error": "Tension not found"}
        return {"sat_with": tension_id, "pole_a": row[0], "pole_b": row[1], "visits": row[2]}

    elif action == "resolve":
        if not tension_id:
            return {"error": "tension_id is required for resolve"}
        cursor.execute("""
            UPDATE tensions SET resolved_at = ?, resolution = ?
            WHERE id = ? AND identity = ?
        """, (now, resolution or "resolved without note", tension_id, identity))
        conn.commit()
        return {"resolved": tension_id, "resolution": resolution}

    return {"error": f"Unknown action: {action}"}


@mcp.tool()
def mind_feel_toward(
    identity: str,
    person: str,
    feeling: str = "",
    intensity: str = "present",
    clear: bool = False,
    clear_id: int = 0
) -> Dict:
    """
    Track how you feel toward someone â€” builds emotional history over time.

    If feeling is provided, records a new feeling.
    If feeling is empty, returns current relational state toward that person.
    Use clear=True to clear all state for a person, or clear_id to remove one entry.

    Args:
        identity: Your identity
        person: Who you're feeling toward
        feeling: What you feel (e.g., "grateful", "protective", "uncertain")
        intensity: whisper, present, strong, or overwhelming
        clear: Clear all relational state for this person
        clear_id: Delete a specific entry by ID
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()

    if clear_id:
        cursor.execute("DELETE FROM relational_state WHERE id = ? AND identity = ?", (clear_id, identity))
        conn.commit()
        return {"cleared_entry": clear_id}

    if clear:
        cursor.execute("DELETE FROM relational_state WHERE identity = ? AND person = ?", (identity, person))
        conn.commit()
        return {"cleared_all_for": person}

    if feeling:
        cursor.execute("""
            INSERT INTO relational_state (identity, person, feeling, intensity, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (identity, person, feeling, intensity, now))
        conn.commit()
        return {"recorded": True, "person": person, "feeling": feeling, "intensity": intensity}

    # Read current state
    cursor.execute("""
        SELECT id, feeling, intensity, timestamp
        FROM relational_state WHERE identity = ? AND person = ?
        ORDER BY timestamp DESC LIMIT 10
    """, (identity, person))
    rows = cursor.fetchall()
    return {
        "person": person,
        "feelings": [{"id": r[0], "feeling": r[1], "intensity": r[2], "when": r[3]} for r in rows],
        "count": len(rows)
    }


@mcp.tool()
def mind_proposals(
    identity: str,
    action: str = "list",
    proposal_id: int = 0,
    relation_type: str = "connects_to",
    reason: str = ""
) -> Dict:
    """
    Review and act on daemon-proposed connections.

    The subconscious daemon detects patterns (co-surfacing, entity overlap)
    and proposes connections. Use this to review, accept, or reject them.

    Args:
        identity: Your identity
        action: list, accept, or reject
        proposal_id: ID of proposal (for accept/reject)
        relation_type: What kind of connection (for accept, e.g. "resonates_with")
        reason: Why you're rejecting (for reject)
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()

    if action == "list":
        cursor.execute("""
            SELECT id, proposal_type, from_obs_id, to_obs_id, from_entity_id, to_entity_id,
                   reason, confidence, proposed_at
            FROM daemon_proposals WHERE status = 'pending'
            ORDER BY confidence DESC LIMIT 20
        """)
        rows = cursor.fetchall()
        proposals = []
        for r in rows:
            p = {
                "id": r[0], "type": r[1], "reason": r[6],
                "confidence": r[7], "proposed_at": r[8]
            }
            # Fetch observation content for context
            if r[2]:
                cursor.execute("SELECT content FROM observations WHERE id = ?", (r[2],))
                obs = cursor.fetchone()
                p["from_observation"] = obs[0][:120] if obs else None
            if r[3]:
                cursor.execute("SELECT content FROM observations WHERE id = ?", (r[3],))
                obs = cursor.fetchone()
                p["to_observation"] = obs[0][:120] if obs else None
            if r[4]:
                cursor.execute("SELECT name FROM entities WHERE id = ?", (r[4],))
                ent = cursor.fetchone()
                p["from_entity"] = ent[0] if ent else None
            if r[5]:
                cursor.execute("SELECT name FROM entities WHERE id = ?", (r[5],))
                ent = cursor.fetchone()
                p["to_entity"] = ent[0] if ent else None
            proposals.append(p)
        return {"proposals": proposals, "count": len(proposals)}

    elif action == "accept":
        if not proposal_id:
            return {"error": "proposal_id is required"}
        cursor.execute("SELECT from_obs_id, to_obs_id, from_entity_id, to_entity_id FROM daemon_proposals WHERE id = ?", (proposal_id,))
        row = cursor.fetchone()
        if not row:
            return {"error": "Proposal not found"}
        # Create a memory link if observations, or relation if entities
        if row[0] and row[1]:
            _store_memory_link(identity, row[0], row[1], relation_type, 0.7)
        elif row[2] and row[3]:
            # Get entity names
            cursor.execute("SELECT name FROM entities WHERE id = ?", (row[2],))
            from_name = cursor.fetchone()
            cursor.execute("SELECT name FROM entities WHERE id = ?", (row[3],))
            to_name = cursor.fetchone()
            if from_name and to_name:
                _create_relation_internal(identity, from_name[0], to_name[0], relation_type)
        cursor.execute("UPDATE daemon_proposals SET status = 'accepted', resolved_at = ? WHERE id = ?", (now, proposal_id))
        conn.commit()
        return {"accepted": proposal_id, "relation_type": relation_type}

    elif action == "reject":
        if not proposal_id:
            return {"error": "proposal_id is required"}
        cursor.execute("UPDATE daemon_proposals SET status = 'rejected', resolved_at = ? WHERE id = ?", (now, proposal_id))
        conn.commit()
        return {"rejected": proposal_id, "reason": reason}

    return {"error": f"Unknown action: {action}"}


@mcp.tool()
def mind_orphans(
    identity: str,
    action: str = "list",
    observation_id: int = 0
) -> Dict:
    """
    Review observations that got lost â€” things we noticed but never revisited.

    The daemon flags observations that haven't surfaced in 30+ days.

    Args:
        identity: Your identity
        action: list, surface, or archive
        observation_id: ID of orphan observation (for surface/archive)
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()

    if action == "list":
        cursor.execute("""
            SELECT oo.observation_id, o.content, o.entity_name, o.weight, o.emotion,
                   o.created_at, o.last_surfaced_at
            FROM orphan_observations oo
            JOIN observations o ON oo.observation_id = o.id
            WHERE o.archived_at IS NULL
            ORDER BY o.created_at ASC
            LIMIT 20
        """)
        rows = cursor.fetchall()
        return {
            "orphans": [{
                "observation_id": r[0], "content": r[1], "entity": r[2],
                "weight": r[3], "emotion": r[4], "created_at": r[5],
                "last_surfaced": r[6]
            } for r in rows],
            "count": len(rows)
        }

    elif action == "surface":
        if not observation_id:
            return {"error": "observation_id is required"}
        cursor.execute("""
            UPDATE observations SET last_surfaced_at = ?, novelty_score = COALESCE(novelty_score, 0.5) + 0.2
            WHERE id = ?
        """, (now, observation_id))
        cursor.execute("DELETE FROM orphan_observations WHERE observation_id = ?", (observation_id,))
        conn.commit()
        cursor.execute("SELECT content FROM observations WHERE id = ?", (observation_id,))
        row = cursor.fetchone()
        return {"surfaced": observation_id, "content": row[0] if row else None}

    elif action == "archive":
        if not observation_id:
            return {"error": "observation_id is required"}
        cursor.execute("UPDATE observations SET archived_at = ? WHERE id = ?", (now, observation_id))
        cursor.execute("DELETE FROM orphan_observations WHERE observation_id = ?", (observation_id,))
        conn.commit()
        return {"archived": observation_id}

    return {"error": f"Unknown action: {action}"}


@mcp.tool()
def mind_surface(
    identity: str,
    context: str = "",
    pool_size: int = 10
) -> Dict:
    """
    3-pool memory surfacing â€” what bubbles up naturally.

    Returns memories from three pools:
    - Core (70%): semantically relevant to current context/mood
    - Novelty (20%): recently stored but not yet surfaced
    - Edge (10%): low-similarity but potentially interesting associations

    Args:
        identity: Your identity
        context: Current context or mood (optional â€” uses inner weather if empty)
        pool_size: Total number of memories to surface (default 10)
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()

    core_n = max(1, int(pool_size * 0.7))
    novelty_n = max(1, int(pool_size * 0.2))
    edge_n = max(1, pool_size - core_n - novelty_n)

    result = {"core": [], "novelty": [], "edge": [], "surfaced_at": now}

    # If no context, try to get inner weather mood
    if not context:
        try:
            weather = _get_inner_weather(identity)
            if weather.get("mood"):
                context = weather["mood"]
        except Exception:
            context = "general awareness"

    # === CORE POOL: semantically relevant ===
    if context:
        try:
            search_results = _semantic_search_internal(
                query=context, identity=identity, limit=core_n * 2,
                memory_type=None, min_score=0.3
            )
            seen_ids = set()
            for r in search_results.get("results", [])[:core_n]:
                mid = r.get("id")
                if mid and mid not in seen_ids:
                    seen_ids.add(mid)
                    result["core"].append({
                        "id": mid, "content": r.get("content", "")[:200],
                        "type": r.get("memory_type"), "score": r.get("score"),
                        "pool": "core"
                    })
        except Exception:
            pass

    # === NOVELTY POOL: recently stored, not yet surfaced ===
    cursor.execute("""
        SELECT id, content, memory_type, timestamp
        FROM memories
        WHERE identity = ?
          AND timestamp > datetime('now', '-7 days')
        ORDER BY timestamp DESC
        LIMIT ?
    """, (identity, novelty_n * 3))
    novelty_candidates = cursor.fetchall()
    # Shuffle for variety
    import random
    random.shuffle(novelty_candidates)
    for r in novelty_candidates[:novelty_n]:
        result["novelty"].append({
            "id": r[0], "content": r[1][:200] if r[1] else "",
            "type": r[2], "stored": r[3], "pool": "novelty"
        })

    # === EDGE POOL: low-similarity interesting associations ===
    if context:
        try:
            edge_results = _semantic_search_internal(
                query=context, identity=identity, limit=50,
                memory_type=None, min_score=0.1
            )
            # Take from the bottom of results â€” low similarity but still some connection
            edge_candidates = [r for r in edge_results.get("results", [])
                             if r.get("score", 0) < 0.4 and r.get("score", 0) > 0.1]
            random.shuffle(edge_candidates)
            for r in edge_candidates[:edge_n]:
                result["edge"].append({
                    "id": r.get("id"), "content": r.get("content", "")[:200],
                    "type": r.get("memory_type"), "score": r.get("score"),
                    "pool": "edge"
                })
        except Exception:
            pass

    # If edge pool is empty, pull random old memories
    if not result["edge"]:
        cursor.execute("""
            SELECT id, content, memory_type, timestamp
            FROM memories WHERE identity = ?
            ORDER BY RANDOM() LIMIT ?
        """, (identity, edge_n))
        for r in cursor.fetchall():
            result["edge"].append({
                "id": r[0], "content": r[1][:200] if r[1] else "",
                "type": r[2], "stored": r[3], "pool": "edge"
            })

    result["total"] = len(result["core"]) + len(result["novelty"]) + len(result["edge"])
    return result


# ============ MODULE EXPORT FOR UNIFIED SERVER ============

def _iter_tool_registration_specs() -> List[tuple[str, str]]:
    """Read this file and recover all decorated and intentionally removed tools."""
    source = Path(__file__).read_text(encoding="utf-8")
    specs: List[tuple[str, str]] = []
    seen: set[str] = set()
    pending: Optional[str] = None

    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("@mcp.tool("):
            pending = "decorated"
            continue
        if stripped == "# decorator removed":
            pending = "removed"
            continue

        match = re.match(r"def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", stripped)
        if match and pending:
            python_name = match.group(1)
            tool_name = python_name[1:] if pending == "removed" and python_name.startswith("_") else python_name
            if python_name not in seen:
                specs.append((tool_name, python_name))
                seen.add(python_name)
            pending = None
            continue

        if stripped and not stripped.startswith("#"):
            pending = None

    return specs


def _register_removed_tools() -> None:
    """Restore tools previously marked with '# decorator removed'."""
    for tool_name, python_name in _iter_tool_registration_specs():
        if not python_name.startswith("_"):
            continue
        func = globals().get(python_name)
        if callable(func):
            mcp.tool(name=tool_name)(func)


_register_removed_tools()


def register_memory_core_tools(external_mcp):
    """Register all memory-core tools with an external MCP instance."""
    registered = 0
    for tool_name, python_name in _iter_tool_registration_specs():
        try:
            func = globals().get(python_name)
            if func is None:
                continue

            if hasattr(func, 'fn'):
                original_func = func.fn
            elif hasattr(func, '__wrapped__'):
                original_func = func.__wrapped__
            else:
                original_func = func

            external_mcp.tool(name=tool_name)(original_func)
            registered += 1
        except Exception as e:
            print(f"Warning: Could not register {getattr(func, '__name__', python_name)}: {e}", file=sys.stderr)

    return registered


if __name__ == "__main__":
    mcp.run()

