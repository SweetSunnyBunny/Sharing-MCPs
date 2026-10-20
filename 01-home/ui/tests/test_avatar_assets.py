import json
import struct

import pytest

from services import avatar_assets


def write_glb(path, document):
    payload = json.dumps(document, separators=(",", ":")).encode("utf-8")
    payload += b" " * ((4 - len(payload) % 4) % 4)
    total = 12 + 8 + len(payload)
    path.write_bytes(
        struct.pack("<4sII", b"glTF", 2, total)
        + struct.pack("<II", len(payload), 0x4E4F534A)
        + payload
    )


def rigged_document(*, animations=True, facial=True):
    nodes = [{"name": f"joint_{index}"} for index in range(20)]
    nodes.extend([{"name": "Jaw"}, {"name": "LeftEye"}, {"name": "RightEye"}])
    mesh = {"primitives": [{}]}
    if facial:
        mesh["primitives"][0]["targets"] = [
            {"POSITION": 0}, {"POSITION": 1}, {"POSITION": 2}, {"POSITION": 3}
        ]
        mesh["extras"] = {
            "targetNames": ["jawOpen", "eyeBlinkLeft", "viseme_PP", "browInnerUp"]
        }
    return {
        "asset": {"version": "2.0", "generator": "test"},
        "scenes": [{"nodes": [0]}],
        "nodes": nodes,
        "meshes": [mesh],
        "skins": [{"joints": list(range(20))}],
        "animations": [{"name": "idle"}] if animations else [],
        "materials": [{}],
        "textures": [{}],
    }


def test_inspect_glb_reports_performance_capabilities(tmp_path):
    model = tmp_path / "avatar.glb"
    write_glb(model, rigged_document())

    result = avatar_assets.inspect_glb(model)

    assert result["counts"]["max_skin_joints"] == 20
    assert result["counts"]["animations"] == 1
    assert result["counts"]["morph_targets"] == 4
    assert result["capabilities"]["body_rig"] is True
    assert result["capabilities"]["facial_rig"] is True
    assert result["capabilities"]["eye_control"] is True
    assert result["capabilities"]["blink_control"] is True
    assert result["capabilities"]["speech_visemes"] is True
    assert result["capabilities"]["expression_control"] is True
    assert result["capabilities"]["realtime_performance_ready"] is True


def test_body_only_glb_is_honest_about_missing_face_and_motion(tmp_path):
    model = tmp_path / "avatar.glb"
    write_glb(model, rigged_document(animations=False, facial=False))

    result = avatar_assets.inspect_glb(model)

    assert result["capabilities"]["body_rig"] is True
    assert result["capabilities"]["animation_clips"] is False
    assert result["capabilities"]["facial_rig"] is False
    assert result["capabilities"]["realtime_performance_ready"] is False
    assert any("No animation clips" in warning for warning in result["warnings"])
    assert any("No morph targets" in warning for warning in result["warnings"])


def test_canonical_manifest_is_discovered(tmp_path, monkeypatch):
    root = tmp_path / "tripo"
    identity_root = root / "avery"
    master = identity_root / "master" / "avery_master_v3.glb"
    master.parent.mkdir(parents=True)
    write_glb(master, rigged_document(animations=False, facial=False))
    (identity_root / "manifest.json").write_text(
        json.dumps(
            {
                "schema": avatar_assets.AVATAR_SCHEMA,
                "identity": "avery",
                "display_name": "Avery",
                "version": "v3",
                "assets": {"master_glb": "master/avery_master_v3.glb"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(avatar_assets, "TRIPO_DIR", root)

    avatars = avatar_assets.list_avatars()

    assert len(avatars) == 1
    assert avatars[0]["identity"] == "avery"
    assert avatars[0]["version"] == "v3"
    assert avatars[0]["model_url"] == "/api/avatars/avery/model"


def test_manifest_cannot_escape_tripo_root(tmp_path, monkeypatch):
    root = tmp_path / "tripo"
    identity_root = root / "avery"
    identity_root.mkdir(parents=True)
    (identity_root / "manifest.json").write_text(
        json.dumps({"assets": {"master_glb": "../../outside.glb"}}),
        encoding="utf-8",
    )
    (tmp_path / "outside.glb").write_bytes(b"not important")
    monkeypatch.setattr(avatar_assets, "TRIPO_DIR", root)

    with pytest.raises(avatar_assets.AvatarAssetError, match="escapes"):
        avatar_assets.get_avatar("avery")


def test_invalid_identity_is_rejected():
    with pytest.raises(avatar_assets.AvatarAssetError):
        avatar_assets.safe_identity("../avery")
