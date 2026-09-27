"""Live cloud Qualia snapshots for Anam context and identity pages.

No memory-core database or local Qualia files are read. Cache lives in process only.
"""
from services.cloud_state import qualia_read, CloudUnavailable


def get_deep_memory_snapshot(identity: str) -> dict:
    try:
        return qualia_read('snapshot', identity=identity.lower())
    except CloudUnavailable:
        return {'identity':identity,'source':'cloud-qualia','memory':{},'qualia':{},
                '_freshness':{'stale':True,'unavailable':True,'note':'Cloud Qualia unavailable; no local fallback.'}}


def build_deep_memory_context(identity: str) -> str:
    snapshot = get_deep_memory_snapshot(identity)
    memory = snapshot["memory"]
    qualia = snapshot["qualia"]
    freshness = snapshot.get("_freshness", {})

    if freshness.get("unavailable"):
        return "[Cloud Qualia unavailable this turn; no local memory fallback was used.]"
    lines = ["[Deep memory from cloud Qualia:]"]

    primary_focus = memory.get("primary_focus", "")
    if primary_focus:
        lines.append(f"  - Primary focus: {primary_focus}")

    unfinished_business = memory.get("unfinished_business", [])
    topic = ""
    if unfinished_business:
        topic = unfinished_business[0].get("topic", "")
        if topic:
            lines.append(f"  - Unfinished thread: {topic}")

    last_session = qualia.get("last_session", {})
    if last_session.get("summary"):
        lines.append(f"  - Last session: {last_session['summary']}")


    carryover = last_session.get("unfinished") or ""
    if carryover and carryover.strip() != topic.strip():
        lines.append(f"  - Last-session carryover: {carryover}")

    open_loops = [
        item for item in qualia.get("unfinished", {}).get("open_loops", [])
        if not item.get("resolved")
    ]
    if open_loops:
        lines.append(f"  - Open loop: {open_loops[0].get('about', '')}")


    heavy = memory.get("heavy_observations", [])
    if heavy:
        lines.append(f"  - Heavy observation: {heavy[0].get('content', '')}")

    who_matters = memory.get("who_matters", [])
    if who_matters:


        people = ", ".join(
            f"{item.get('name', 'Someone')} ({item.get('relationship', '')})".replace(" ()", "")
            for item in who_matters
        )
        lines.append(f"  - Who matters: {people}")

    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def build_inner_life_context(identity: str) -> str:
    """Build inner life context."""
    snapshot = get_deep_memory_snapshot(identity)
    qualia = snapshot["qualia"]
    freshness = snapshot.get("_freshness", {})
    if freshness.get("stale"):
        return ""  # deep_memory hook already announces staleness once

    lines = ["[Your inner life from cloud Qualia — retain the dates of these records:]"]

    current_self = qualia.get("current_self", {})
    narrative = current_self.get("narrative", "")
    if narrative:


        lines.append(f"  - Current self ({current_self.get('timestamp') or 'date unavailable'}): {narrative}")

    joys = qualia.get("joys", [])
    if joys:
        joy = joys[-1]
        content = joy.get("joy") or joy.get("content") or joy.get("what") or ""
        if content:
            lines.append(f"  - Small joy: {content}")


    def _pool_content(item: dict) -> str:
        return (
            item.get("wondering")
            or item.get("content")
            or item.get("what")
            or item.get("seed")
            or item.get("want")
            or ""
        )

    for label, key in (
        ("Wondering", "wonderings"),
        ("Quiet want", "quiet_wants"),
        ("Creative seed", "creative_seeds"),
    ):
        pool = qualia.get(key, [])
        if pool:
            content = _pool_content(pool[-1])
            if content:
                lines.append(f"  - {label}: {content}")

    if len(lines) == 1:
        return ""
    return "\n".join(lines)
