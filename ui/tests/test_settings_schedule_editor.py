"""Regression checks for editable seeded autowake schedules."""

from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_nightly_consolidation_is_a_selectable_schedule_type():
    settings_html = (ROOT / "static" / "settings.html").read_text(encoding="utf-8")

    assert '<option value="nightly_consolidation">Nightly Consolidation</option>' in settings_html


def test_schedule_editor_preserves_unknown_existing_types_and_reports_save_errors():
    settings_js = (ROOT / "static" / "js" / "settings.js").read_text(encoding="utf-8")

    assert "option.value === typeValue" in settings_js
    assert "option.value = typeValue" in settings_js
    assert "error.textContent = err?.message" in settings_js


def test_nightly_consolidations_render_in_the_night_group():
    settings_js = (ROOT / "static" / "js" / "settings.js").read_text(encoding="utf-8")

    assert "'Night': ['bedtime_reminder', 'nightly_consolidation']" in settings_js


def test_schedule_editor_offers_and_saves_chatgpt_provider_override():
    settings_html = (ROOT / "static" / "settings.html").read_text(encoding="utf-8")
    settings_js = (ROOT / "static" / "js" / "settings.js").read_text(encoding="utf-8")

    assert 'id="modal-provider"' in settings_html
    assert '<option value="chatgpt">ChatGPT bridge</option>' in settings_html
    assert "provider: document.getElementById('modal-provider').value || ''" in settings_js
    assert "existing.provider || ''" in settings_js
