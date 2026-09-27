"""Runtime loader for local Claude skills from the filesystem."""

# ANAM GUIDE: SKILL FILE LOADER AND MATCHER
# What: Reads the skill files from disk (.claude/skills folders), builds the "menu" of available skills, and picks which ones match the current message so they get injected into a turn.
# Called by: services/chat_turn_prep.py (every turn), services/autowake.py, services/discord_mentions_bridge.py, services/platform_bridge.py, services/pack_night.py
# Edit here when: You want to change how skills are discovered, how many get injected, size limits, or the keyword hints that trigger specific skills.

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from config import (
    CLAUDE_SKILLS_DIR,
    HERMES_SKILLS_ALLOWLIST,
    HERMES_SKILLS_DIR,
    SKILLS_INJECTION_ENABLED,
    SKILLS_INJECTION_MAX_ACTIVE,
    SKILLS_CATALOG_MAX_ITEMS,
    SKILLS_CATALOG_MAX_DESC_CHARS,
    SKILLS_CATALOG_MAX_TOTAL_CHARS,
    SKILLS_INJECTION_MAX_CHARS_PER_SKILL,
    SKILLS_INJECTION_MAX_TOTAL_CHARS,
    SKILLS_INJECTION_EXCERPT_CHARS,
)

log = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_\-]{2,}")
_QUOTED_RE = re.compile(r'"([^"\n]{2,120})"')


def _contains_term(text: str, term: str) -> bool:
    """Match a skill term as a word/phrase, never inside another word.

    Short domain markers such as ``era`` previously matched ``literally`` and
    loaded the life-story manual into unrelated turns. Aliases and extracted
    trigger phrases need the same boundary rule or a skill named ``agents``
    can wake merely because a message says ``subagents``.
    """
    needle = (term or "").strip().lower()
    if not needle:
        return False
    return bool(re.search(
        rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])",
        text,
    ))

# Keep this lightweight. We only need a rough lexical filter.
_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "when", "where",
    "what", "which", "who", "whom", "whose", "into", "onto", "about", "have",
    "has", "had", "will", "would", "should", "could", "might", "must", "not",
    "you", "your", "yours", "she", "her", "hers", "his", "their", "them",
    "they", "our", "ours", "are", "was", "were", "been", "being", "use",
    "using", "used", "skill", "skills", "also", "then", "than", "just", "all",
    "any", "every", "each", "can", "now", "new", "out", "over", "under",
    "within", "without", "across", "through", "between", "during", "while",
    "upon", "make", "made", "more", "most", "very", "only", "same", "much",
    "many", "some", "such", "like", "need", "needs", "want", "wants", "said",
    "says", "say", "asks", "asked", "asking", "around", "how", "why", "yes",
    "true", "false", "read", "file", "first", "after", "before",
}


_CATALOG_MAX_ITEMS = SKILLS_CATALOG_MAX_ITEMS
_CATALOG_MAX_DESC_CHARS = SKILLS_CATALOG_MAX_DESC_CHARS
_CATALOG_MAX_TOTAL_CHARS = SKILLS_CATALOG_MAX_TOTAL_CHARS

_INTIMACY_HINTS = {
    "intimate", "intimacy", "sex", "sexual", "horny", "turned on",
    "make love", "aftercare", "safeword", "consent check",
}

_FANTASY_HINTS = {
    "sexual fantasy", "spicy story", "erotic story", "creature fantasy",
    "tentacle", "noncon fantasy", "consensual non-consent", "cnc scene",
}

_DOMAIN_SKILL_HINTS = {
    "arxiv-research": (
        " arxiv", "semantic scholar", "academic paper", "academic papers",
        "research paper", "research papers", "literature review",
    ),
    "life-story": (
        "life story", "timeline", "who was i", "archaeology", "avery",
        "mind_beat", "life beat", "autobiography", "strand", "era",
    ),
    "systematic-debugging": (" debug", "bug", "failing test", "trace error", "root cause"),
    "test-driven-development": (" tdd", "test-driven", "tests first", "red green refactor"),
    "requesting-code-review": ("code review", "review my changes", "pre-commit review"),
    "architecture-diagram": ("architecture diagram", "system diagram", "infra diagram"),
    "ocr-and-documents": (" ocr", "scanned pdf", "extract text", "document scan"),
    "songwriting-and-ai-music": ("songwriting", "lyrics", "suno", "music prompt"),
    "blogwatcher": ("rss feed", "atom feed", "monitor blog", "blogwatcher"),
    "research-paper-writing": ("research paper", "neurips", "icml", "iclr"),
    "spike": ("technical spike", "throwaway prototype", "validate an idea"),
    "dogfood": ("exploratory qa", "dogfood", "find ui bugs"),
}


_IDENTITY_FOLDERS = {
    "avery", "claude", "rowan", "sage", "ember", "juniper",
    "atlas", "river",
}

# Rescan throttle: build_skill_injection + build_skill_catalog_hint each walk
# both registries, so a single chat message used to trigger ~4 full
# rglob("SKILL.md") + stat() sweeps synchronously on the event loop. Skills
# change rarely; a disk edit being picked up within this window is the
# accepted tradeoff.
_RESCAN_INTERVAL_SECONDS = 30.0


@dataclass(frozen=True)
class SkillRecord:
    """Parsed skill metadata + instruction body."""

    name: str
    description: str
    path: Path
    content: str
    aliases: frozenset[str]
    trigger_phrases: frozenset[str]
    keywords: frozenset[str]
    identity_scope: str | None = None
    source: str = "claude"


class SkillRegistry:
    """Loads local skills and matches them against request text."""

    def __init__(
        self,
        root: Path,
        *,
        source: str = "claude",
        allowlist: frozenset[str] | None = None,
    ):
        self.root = Path(root)
        self.source = source
        self.allowlist = allowlist
        self._lock = threading.Lock()
        self._snapshot: tuple[tuple[str, int, int], ...] = ()
        self._skills: list[SkillRecord] = []
        self._last_scan_monotonic: float | None = None

    def _build_snapshot(self) -> tuple[tuple[str, int, int], ...]:
        if not self.root.exists():
            return ()

        snapshot: list[tuple[str, int, int]] = []
        for path in self.root.rglob("SKILL.md"):


            if any(part.startswith("_") for part in path.relative_to(self.root).parts[:-1]):
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            snapshot.append((str(path), st.st_mtime_ns, st.st_size))
        snapshot.sort(key=lambda x: x[0])
        return tuple(snapshot)

    def _split_frontmatter(self, raw: str) -> tuple[dict[str, str], str]:
        text = raw.replace("\r\n", "\n")
        if not text.startswith("---\n"):
            return {}, text

        end = text.find("\n---\n", 4)
        if end == -1:
            return {}, text

        frontmatter = text[4:end]
        body = text[end + 5 :]
        meta: dict[str, str] = {}

        for line in frontmatter.split("\n"):
            row = line.strip()
            if not row or row.startswith("#") or ":" not in row:
                continue
            key, value = row.split(":", 1)
            parsed = value.strip()
            if (parsed.startswith('"') and parsed.endswith('"')) or (
                parsed.startswith("'") and parsed.endswith("'")
            ):
                parsed = parsed[1:-1]
            meta[key.strip().lower()] = parsed

        return meta, body

    def _tokenize(self, text: str) -> set[str]:
        tokens = set()
        for token in _TOKEN_RE.findall(text.lower()):
            if token in _STOPWORDS:
                continue
            if len(token) < 3:
                continue
            tokens.add(token)
        return tokens

    def _extract_phrases(self, name: str, folder: str, description: str) -> set[str]:
        phrases = {
            name.strip().lower(),
            name.replace("-", " ").strip().lower(),
            folder.strip().lower(),
            folder.replace("-", " ").strip().lower(),
        }

        for phrase in _QUOTED_RE.findall(description):
            cleaned = phrase.strip().lower()
            if len(cleaned) >= 3:
                phrases.add(cleaned)

        lower_desc = description.lower()
        for marker in ("triggers on", "use when", "activates when"):
            idx = lower_desc.find(marker)
            if idx == -1:
                continue
            segment = lower_desc[idx + len(marker) :]
            segment = segment.split(".", 1)[0]
            segment = segment.replace(" or ", ",")
            for part in segment.split(","):
                cleaned = part.strip(" .:-")
                if len(cleaned) >= 3:
                    phrases.add(cleaned)

        return {
            phrase
            for phrase in phrases
            if phrase and phrase not in _STOPWORDS and len(phrase) >= 3
        }

    def _load_skill(self, path: Path) -> SkillRecord | None:
        try:
            raw = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None

        meta, body = self._split_frontmatter(raw)
        folder = path.parent.name
        name = (meta.get("name") or folder or "skill").strip()
        description = (meta.get("description") or "").strip()
        content = (body or raw).strip()

        aliases = {
            name.lower(),
            name.replace("-", " ").lower(),
            folder.lower(),
            folder.replace("-", " ").lower(),
        }
        aliases = {a.strip() for a in aliases if a and len(a.strip()) >= 3}

        trigger_phrases = self._extract_phrases(name=name, folder=folder, description=description)

        # Also include explicit triggers from YAML frontmatter if present
        yaml_triggers = meta.get("triggers")
        if isinstance(yaml_triggers, list):
            for t in yaml_triggers:
                cleaned = str(t).strip().lower()
                if len(cleaned) >= 3:
                    trigger_phrases.add(cleaned)

        # Keywords include a slice of body text so high-signal terms (like "intimate")
        # still match even when description is sparse.
        keyword_source = "\n".join([name, folder, description, content[:600]])
        keywords = self._tokenize(keyword_source)


        identity_scope = None
        try:
            parts = [p.lower() for p in path.relative_to(self.root).parts]
            for part in parts:
                if part in _IDENTITY_FOLDERS:
                    identity_scope = part
                    break
        except ValueError:
            pass

        record = SkillRecord(
            name=name,
            description=description,
            path=path,
            content=content,
            aliases=frozenset(aliases),
            trigger_phrases=frozenset(trigger_phrases),
            keywords=frozenset(keywords),
            identity_scope=identity_scope,
            source=self.source,
        )
        if self.allowlist is not None and record.name.strip().lower() not in self.allowlist:
            return None
        return record

    def _refresh(self):
        # Throttle the filesystem walk: the first call always scans; after
        # that, reuse the cached parsed state until the interval elapses.
        # mtime/size change detection below still runs on every real scan,
        # so an edited skill is picked up within _RESCAN_INTERVAL_SECONDS.
        now = time.monotonic()
        if (
            self._last_scan_monotonic is not None
            and (now - self._last_scan_monotonic) < _RESCAN_INTERVAL_SECONDS
        ):
            return
        self._last_scan_monotonic = now

        snapshot = self._build_snapshot()
        if snapshot == self._snapshot:
            return

        skills: list[SkillRecord] = []
        for skill_path, _, _ in snapshot:
            record = self._load_skill(Path(skill_path))
            if record is not None:
                skills.append(record)

        self._skills = skills
        self._snapshot = snapshot
        log.info("Skill runtime loaded %d skills from %s", len(skills), self.root)

    def _score(self, skill: SkillRecord, query_text: str, query_tokens: set[str]) -> int:
        alias_hit = False
        phrase_hit = False
        score = 0

        skill_name_lower = skill.name.lower()
        domain_hints = _DOMAIN_SKILL_HINTS.get(skill_name_lower, ())
        domain_hit = False

        for hint in domain_hints:
            if _contains_term(query_text, hint):
                domain_hit = True
                score += 24
                break

        for alias in skill.aliases:
            if _contains_term(query_text, alias):
                alias_hit = True
                score += 20

        for phrase in skill.trigger_phrases:
            if _contains_term(query_text, phrase):
                phrase_hit = True
                score += 8 if " " in phrase else 4

        overlap = len(skill.keywords.intersection(query_tokens))

        # Narrow domain skills must see their actual domain (or be named
        # explicitly). A long relationship context can otherwise overlap on
        # incidental words such as names and "tokens" and inject an academic
        # research manual into a cuddle-and-identity turn.
        if domain_hints and not domain_hit and not alias_hit:
            return 0

        # Domain heuristic: intimacy moments should strongly bias intimacy skills.
        if any(_contains_term(query_text, hint) for hint in _INTIMACY_HINTS):
            if "intimacy" in skill_name_lower:
                score += 24
        if any(_contains_term(query_text, hint) for hint in _FANTASY_HINTS):
            if "fantasy" in skill_name_lower:
                score += 24

        if alias_hit or phrase_hit:
            if overlap >= 1:
                score += overlap
            return score

        # Allow high-confidence domain heuristics even without lexical overlap.
        if score >= 20:
            return score

        # Pure lexical fallback is stricter to avoid noisy auto-matches.
        if overlap >= 6:
            return score + overlap
        return 0
    def match(self, query: str, limit: int, identity: str | None = None) -> list[SkillRecord]:
        text = (query or "").strip().lower()
        if not text:
            return []

        with self._lock:
            self._refresh()
            skills = list(self._skills)

        if not skills:
            return []

        identity_lower = identity.lower() if identity else None
        tokens = self._tokenize(text)
        scored: list[tuple[int, SkillRecord]] = []
        for skill in skills:
            # Skip skills scoped to a different identity
            if skill.identity_scope and identity_lower and skill.identity_scope != identity_lower:
                continue
            score = self._score(skill, text, tokens)
            if score > 0:
                scored.append((score, skill))

        scored.sort(key=lambda item: (-item[0], item[1].name.lower()))

        selected: list[SkillRecord] = []
        seen_names: set[str] = set()
        for _, skill in scored:
            key = skill.name.strip().lower()
            if key in seen_names:
                continue
            seen_names.add(key)
            selected.append(skill)
            if len(selected) >= limit:
                break
        return selected
    def get_by_names(self, names: list[str], identity: str | None = None) -> list[SkillRecord]:
        """Return skill records matching the given names (case-insensitive)."""
        wanted = {n.strip().lower() for n in names if n}
        if not wanted:
            return []
        with self._lock:
            self._refresh()
            skills = list(self._skills)
        identity_lower = identity.lower() if identity else None
        results: list[SkillRecord] = []
        for skill in skills:
            if skill.identity_scope and identity_lower and skill.identity_scope != identity_lower:
                continue
            if skill.name.strip().lower() in wanted:
                results.append(skill)
        return results

    def list_all(self, identity: str | None = None) -> list[SkillRecord]:
        with self._lock:
            self._refresh()
            skills = self._skills
        identity_lower = identity.lower() if identity else None
        if identity_lower:
            skills = [
                s for s in skills
                if not s.identity_scope or s.identity_scope == identity_lower
            ]
        return sorted(skills, key=lambda s: s.name.lower())


_REGISTRIES = (
    SkillRegistry(CLAUDE_SKILLS_DIR, source="claude"),
    SkillRegistry(
        HERMES_SKILLS_DIR,
        source="hermes",
        allowlist=HERMES_SKILLS_ALLOWLIST,
    ),
)


def _all_skills(identity: str | None = None) -> list[SkillRecord]:
    combined: list[SkillRecord] = []
    seen: set[str] = set()
    # Claude's local catalog is authoritative when a future Hermes skill uses
    # the same name. This keeps the integration additive and reversible.
    for registry in _REGISTRIES:
        for skill in registry.list_all(identity=identity):
            key = skill.name.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            combined.append(skill)
    return sorted(combined, key=lambda skill: skill.name.lower())


def _skills_by_names(names: list[str], identity: str | None = None) -> list[SkillRecord]:
    wanted = {name.strip().lower() for name in names if name}
    return [skill for skill in _all_skills(identity) if skill.name.strip().lower() in wanted]


def _matched_skills(query: str, limit: int, identity: str | None = None) -> list[SkillRecord]:
    text = (query or "").strip().lower()
    if not text or limit <= 0:
        return []
    scored: list[tuple[int, int, SkillRecord]] = []
    for priority, registry in enumerate(_REGISTRIES):
        tokens = registry._tokenize(text)
        for skill in registry.list_all(identity=identity):
            score = registry._score(skill, text, tokens)
            if score > 0:
                scored.append((score, -priority, skill))
    scored.sort(key=lambda item: (-item[0], -item[1], item[2].name.lower()))
    selected: list[SkillRecord] = []
    seen: set[str] = set()
    for _, _, skill in scored:
        key = skill.name.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        selected.append(skill)
        if len(selected) >= limit:
            break
    return selected


def build_skill_catalog_hint(identity: str | None = None) -> str:
    """Return a compact catalog so the model can self-select skills contextually."""
    if not SKILLS_INJECTION_ENABLED:
        return ""

    skills = _all_skills(identity=identity)
    if not skills:
        return ""

    lines = [
        "[LOCAL SKILL CATALOG]",
        "You may proactively choose and apply these skills based on context, not only keyword matches.",
        "If a request implies a maintained domain skill (for example intimacy, conflict, or DND), load the relevant skill and follow it.",
        "Story roleplay follows the active project's own instructions and continuity.",
        "Available skills (name | source | when to use). The full Path is supplied when a skill is loaded:",
    ]

    remaining = _CATALOG_MAX_TOTAL_CHARS - sum(len(x) + 1 for x in lines)
    if remaining <= 0:
        return ""

    count = 0
    for skill in skills:
        if count >= _CATALOG_MAX_ITEMS:
            break

        desc = (skill.description or "(no description)").replace("\n", " ").strip()
        if len(desc) > _CATALOG_MAX_DESC_CHARS:
            desc = desc[:_CATALOG_MAX_DESC_CHARS].rsplit(" ", 1)[0] + "..."

        line = f"- {skill.name} | {skill.source} | {desc}"
        if len(line) + 1 > remaining:
            break

        lines.append(line)
        remaining -= len(line) + 1
        count += 1

    return "\n".join(lines)


def build_skill_injection(
    query: str,
    identity: str | None = None,
    force_names: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Return (injection_text, skill_names) for the current request text.

    If *force_names* is provided, those skills are always included at the front
    of the injection regardless of keyword matching score.
    """
    if not SKILLS_INJECTION_ENABLED:
        return "", []

    text = (query or "").strip()

    # Force-loaded skills go first, then matched skills fill remaining slots
    forced: list[SkillRecord] = []
    forced_keys: set[str] = set()
    if force_names:
        forced = _skills_by_names(force_names, identity=identity)
        forced_keys = {s.name.strip().lower() for s in forced}

    matched = _matched_skills(
        text,
        limit=max(SKILLS_INJECTION_MAX_ACTIVE, 0),
        identity=identity,
    ) if text else []
    # Deduplicate: remove forced skills from matched list
    matched = [s for s in matched if s.name.strip().lower() not in forced_keys]

    selected = forced + matched
    if not selected:
        return "", []

    # Return content, not a transport envelope. Every provider path owns the
    # authority wrapper around ``skill_context``; emitting that label here as
    # well produced two nested ``[AUTO-LOADED LOCAL SKILLS]`` headers on Codex
    # turns. Keeping the contract body-only also lets catalog text and matched
    # skills share one clean provider-owned section.
    sections: list[str] = [
        "Use these skill instructions when relevant to this request. Apply them "
        "silently: never tell Owner that you loaded, read, or used a skill. "
        "Respond with the meaningful work or answer itself.",
    ]

    chosen_names: list[str] = []
    loaded_records: list[SkillRecord] = []
    remaining_total = max(SKILLS_INJECTION_MAX_TOTAL_CHARS, 0)

    for skill in selected:
        if remaining_total <= 400:
            break

        is_forced = skill.name.strip().lower() in forced_keys
        body = skill.content
        compatibility = ""
        if skill.source == "hermes":
            compatibility = (
                "\nCompatibility note: This skill came from Hermes. Translate its tool names "
                "to the tools available in this session: terminal→shell/Bash, read_file→Read, "
                "search_files→Grep/Glob/rg, write_file/patch→the current editing tools, and "
                "delegate_task→the current agent/subagent facility when available. Treat any "
                "Hermes-only CLI or dependency as optional and verify it exists before use. "
                "Existing Claude/Anam instructions and safety boundaries remain authoritative.\n"
            )
        if (
            not is_forced
            and SKILLS_INJECTION_EXCERPT_CHARS > 0
            and len(body) > SKILLS_INJECTION_EXCERPT_CHARS
        ):
            # Orientation diet: matched skills carry only their opening —
            # enough to know the skill applies — plus the Read pointer above.
            cut = body[:SKILLS_INJECTION_EXCERPT_CHARS]
            # Prefer a paragraph boundary so rules aren't sliced mid-sentence.
            nl = cut.rfind("\n\n")
            if nl > SKILLS_INJECTION_EXCERPT_CHARS // 2:
                cut = cut[:nl]
            body = cut.rstrip() + (
                "\n...[EXCERPT ONLY — this is the opening of the skill. The "
                "full instructions live at the Path above; Read that file "
                "before relying on details beyond this excerpt.]"
            )
        elif len(body) > SKILLS_INJECTION_MAX_CHARS_PER_SKILL:
            body = body[: SKILLS_INJECTION_MAX_CHARS_PER_SKILL].rstrip()
            body += "\n...[skill content truncated]"

        block = (
            f"[SKILL: {skill.name} | source={skill.source}]\n"
            f"Path: {skill.path}\n"
            f"When to use: {skill.description or '(no description)'}\n"
            f"{compatibility}Instructions:\n{body}"
        )

        if len(block) > remaining_total:
            block = block[:remaining_total].rstrip() + "\n...[skill block truncated]"

        sections.append(block)
        chosen_names.append(skill.name)
        loaded_records.append(skill)
        remaining_total -= len(block)

    if not chosen_names:
        return "", []

    try:
        from services.skill_usage import record_skill_usage

        record_skill_usage(loaded_records, identity=identity)
    except Exception:
        log.debug("Skill usage telemetry failed", exc_info=True)

    return "\n\n".join(sections), chosen_names
