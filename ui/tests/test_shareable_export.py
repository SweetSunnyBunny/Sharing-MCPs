"""Regression tests for export boundaries and generic starter operation."""
from pathlib import Path
import importlib.util
import json
import pytest

SPEC = importlib.util.spec_from_file_location('share_export', Path(__file__).resolve().parents[1] / 'scripts/export_shareable_ui.py')
EXPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORT)


def profile():
    return {'reviewed': True, 'replacements': [{'pattern': 'PRIVATE_OWNER', 'replacement': 'Owner'}], 'forbidden': ['PRIVATE_OWNER'], 'exclude': [], 'binary_allowlist': []}


def test_export_never_copies_runtime_prompts_or_unreviewed_binary(tmp_path):
    src, stage = tmp_path / 'source', tmp_path / 'stage'
    for rel, data in [('api/main.py', b'name="PRIVATE_OWNER"'), ('api/main.py.bak', b'private'), ('data/history.db', b'private'), ('prompts/secret.md', b'private'), ('static/photo.png', b'private'), ('static/view.mjs', b'export const enabled = true;')]:
        path = src / rel; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    manifest = EXPORT.build(src, stage, profile())
    assert (stage / 'api/main.py').read_text() == 'name="Owner"'
    assert (stage / 'static/view.mjs').exists()
    assert {row['path'] for row in manifest['files']} == {'api/main.py', 'static/view.mjs'}


def test_bad_overlay_prevents_publication(tmp_path):
    src, stage, overlay = tmp_path / 'source', tmp_path / 'stage', tmp_path / 'overlay'
    src.mkdir(); overlay.mkdir(); (overlay / 'README.md').write_text('PRIVATE_OWNER')
    cfg = profile(); cfg['overlay_dir'] = str(overlay)
    with pytest.raises(ValueError, match='Public-data checks failed'):
        EXPORT.build(src, stage, cfg)


def test_publish_removes_legacy_managed_files_but_preserves_cloud_packages(tmp_path):
    stage, dst = tmp_path / 'stage', tmp_path / 'destination'
    stage.mkdir()
    for rel in ('scheduler/old.py', '.codex/old.json', 'cloud-setups/example/keep.txt', '.git/keep.txt'):
        p = dst / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text('x')
    assert EXPORT.publish(stage, dst, True) == 2
    assert (dst / 'scheduler/old.py').exists()
    assert EXPORT.publish(stage, dst, False) == 2
    assert not (dst / 'scheduler/old.py').exists()
    assert (dst / 'cloud-setups/example/keep.txt').exists()
    assert (dst / '.git/keep.txt').exists()


def test_forbidden_patterns_preserve_case_sensitive_identifier_boundaries(tmp_path):
    (tmp_path / 'test.py').write_text('wordname = 1')
    assert EXPORT.verify(tmp_path, {'forbidden': [{'pattern': r'(?<=[a-z])Name(?![a-z])', 'flags': 0}]}) == []


def test_starter_has_prompts_and_no_account_voice_defaults():
    import config
    assert all(not value['voice_id'] for value in config.IDENTITIES.values())
    assert all((config.PROMPTS_DIR / f'{name.lower()}.md').is_file() for name in config.IDENTITIES)
    assert json.loads((config.BASE_DIR / 'mcp-servers.json').read_text())['mcpServers'] == {}


def test_missing_optional_visual_reference_is_safe():
    from services.world_feed_visuals import reference
    assert reference({'id': 'starter-world', 'story_branch': ''}) is None
