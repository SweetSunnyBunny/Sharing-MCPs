'Application module.'
from services import story_state

TEMPLATE = """# STATE

**Location:** _(empty — fill me in)_
**Era:** old

## Last Scene
old scene

## Open Threads
old threads

---
"""


def _write(monkeypatch, tmp_path, fields):
    path = tmp_path / "STATE.md"
    path.write_text(TEMPLATE, encoding="utf-8")
    monkeypatch.setattr(story_state, "state_file_for", lambda identity: path)
    story_state.write_state("Bakugou", fields)
    return path.read_text(encoding="utf-8")


def test_section_starting_with_digit_and_backslash_is_stored_literally(monkeypatch, tmp_path):
    body = "1. she laughed\n2. file at C:/Users/YOU\\notes.md \\1 \\g<0>"
    text = _write(monkeypatch, tmp_path, {"last_scene": body})
    assert "## Last Scene\n" + body + "\n\n" in text
    assert "old scene" not in text
    assert "## Open Threads\nold threads" in text  # neighbours untouched


def test_inline_field_with_backslash_is_stored_literally(monkeypatch, tmp_path):
    value = r"C:/Users/YOU \1 kitchen"
    text = _write(monkeypatch, tmp_path, {"location": value})
    assert "**Location:** " + value + "\n" in text
    assert "**Era:** old" in text
