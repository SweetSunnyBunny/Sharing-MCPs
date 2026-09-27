"""Pack avatar discovery, validation, and safe asset resolution.

The Tripo output is the immutable body master. Runtime, performance, VRM, and
pet derivatives live beside it and are declared by a portable ``manifest.json``.
This module intentionally parses glTF metadata without Blender or a new Python
dependency so Anam can report what a body can actually do before loading it.
"""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path
from typing import Any

from config import DATA_DIR


TRIPO_DIR = DATA_DIR / "tripo"
AVATAR_SCHEMA = "anam.avatar.v1"
_IDENTITY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_VERSION_RE = re.compile(r"(?:^|[_-])v(\d+)(?:\D|$)", re.IGNORECASE)
_JSON_CHUNK = 0x4E4F534A


class AvatarAssetError(ValueError):
    """Raised when an avatar asset or manifest is invalid."""


def safe_identity(value: str) -> str:
    """Return a normalized identity slug or reject unsafe route input."""
    slug = str(value or "").strip().lower()
    if not _IDENTITY_RE.fullmatch(slug):
        raise AvatarAssetError("invalid identity")
    return slug


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_declared_path(identity_root: Path, value: str | None) -> Path | None:
    if not value:
        return None
    raw = Path(value)
    candidate = raw.resolve() if raw.is_absolute() else (identity_root / raw).resolve()
    allowed_root = TRIPO_DIR.resolve()
    if not _is_within(candidate, allowed_root):
        raise AvatarAssetError("avatar manifest path escapes the Tripo directory")
    return candidate


def _version_key(path: Path) -> tuple[int, int, str]:
    match = _VERSION_RE.search(path.stem)
    version = int(match.group(1)) if match else 0
    try:
        modified = path.stat().st_mtime_ns
    except OSError:
        modified = 0
    return (version, modified, path.name.lower())


def _read_glb_json(path: Path) -> tuple[dict[str, Any], int, int]:
    """Read only the JSON chunk from a GLB 2.0 file."""
    try:
        actual_bytes = path.stat().st_size
        with path.open("rb") as handle:
            header = handle.read(12)
            if len(header) != 12:
                raise AvatarAssetError("GLB header is truncated")
            magic, version, declared_length = struct.unpack("<4sII", header)
            if magic != b"glTF":
                raise AvatarAssetError("file is not a GLB")
            if version != 2:
                raise AvatarAssetError(f"unsupported GLB version: {version}")
            if declared_length != actual_bytes:
                raise AvatarAssetError(
                    f"GLB length mismatch: header={declared_length}, file={actual_bytes}"
                )

            while handle.tell() < declared_length:
                chunk_header = handle.read(8)
                if len(chunk_header) != 8:
                    raise AvatarAssetError("GLB chunk header is truncated")
                chunk_length, chunk_type = struct.unpack("<II", chunk_header)
                chunk = handle.read(chunk_length)
                if len(chunk) != chunk_length:
                    raise AvatarAssetError("GLB chunk is truncated")
                if chunk_type == _JSON_CHUNK:
                    try:
                        document = json.loads(chunk.decode("utf-8").rstrip("\x00 \t\r\n"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as error:
                        raise AvatarAssetError(f"invalid GLB JSON chunk: {error}") from error
                    return document, declared_length, actual_bytes
    except OSError as error:
        raise AvatarAssetError(f"could not read GLB: {error}") from error
    raise AvatarAssetError("GLB has no JSON chunk")


def inspect_glb(path: Path) -> dict[str, Any]:
    """Return renderer- and performance-readiness metadata for one GLB."""
    document, declared_length, actual_bytes = _read_glb_json(path)
    nodes = document.get("nodes") or []
    meshes = document.get("meshes") or []
    skins = document.get("skins") or []
    animations = document.get("animations") or []
    node_names = [str(node.get("name") or "") for node in nodes]
    lowered_nodes = [name.lower() for name in node_names if name]

    morph_target_count = 0
    morph_target_names: list[str] = []
    for mesh in meshes:
        extras = mesh.get("extras") or {}
        names = extras.get("targetNames") or mesh.get("targetNames") or []
        morph_target_names.extend(str(name) for name in names if name)
        for primitive in mesh.get("primitives") or []:
            morph_target_count += len(primitive.get("targets") or [])

    lowered_targets = [name.lower() for name in morph_target_names]
    face_markers = (
        "jawopen", "mouthopen", "viseme", "v_aa", "blink", "eyeblink",
        "mouthsmile", "browinnerup", "cheeksquint", "mouthfunnel",
        "mouthpucker", "aa", "ih", "ou", "ee", "oh",
    )
    viseme_markers = (
        "viseme", "v_pp", "v_ff", "v_th", "v_dd", "v_kk", "v_ch",
        "v_ss", "v_nn", "v_rr", "v_aa", "v_e", "v_i", "v_o", "v_u",
    )
    jaw_markers = ("jaw", "mouth")
    eye_markers = ("eye", "look")
    blink_markers = ("blink", "eyeblink")
    expression_markers = ("brow", "cheek", "smile", "frown", "squint")

    joint_counts = [len(skin.get("joints") or []) for skin in skins]
    max_joint_count = max(joint_counts, default=0)
    has_body_rig = bool(skins and max_joint_count >= 15)
    has_face_rig = any(
        marker in target
        for target in lowered_targets
        for marker in face_markers
    )
    has_jaw_control = any(
        marker in name for name in lowered_nodes for marker in jaw_markers
    ) or any(marker in name for name in lowered_targets for marker in jaw_markers)
    has_eye_control = any(
        marker in name for name in lowered_nodes for marker in eye_markers
    ) or any(marker in name for name in lowered_targets for marker in eye_markers)
    has_blink_control = any(
        marker in name for name in lowered_targets for marker in blink_markers
    )
    has_speech_visemes = any(
        marker in name for name in lowered_targets for marker in viseme_markers
    )
    has_expression_control = any(
        marker in name for name in lowered_targets for marker in expression_markers
    )

    warnings: list[str] = []
    if not has_body_rig:
        warnings.append("No usable humanoid body skin was detected.")
    if not animations:
        warnings.append("No animation clips are embedded; retarget or add a motion library.")
    if morph_target_count == 0:
        warnings.append("No morph targets are present; facial expressions and lip-sync need a performance rig.")
    elif not has_face_rig:
        warnings.append("Morph targets exist, but recognizable facial controls were not declared.")
    if not has_eye_control:
        warnings.append("No explicit eye/gaze controls were detected.")
    if not has_speech_visemes:
        warnings.append("No named speech visemes were detected; lip-sync will be jaw-only.")

    return {
        "format": "glb",
        "format_version": 2,
        "declared_bytes": declared_length,
        "actual_bytes": actual_bytes,
        "generator": (document.get("asset") or {}).get("generator"),
        "counts": {
            "scenes": len(document.get("scenes") or []),
            "nodes": len(nodes),
            "meshes": len(meshes),
            "skins": len(skins),
            "max_skin_joints": max_joint_count,
            "animations": len(animations),
            "materials": len(document.get("materials") or []),
            "textures": len(document.get("textures") or []),
            "morph_targets": morph_target_count,
        },
        "animation_names": [
            str(animation.get("name") or f"animation_{index}")
            for index, animation in enumerate(animations)
        ],
        "morph_target_names": sorted(set(morph_target_names)),
        "capabilities": {
            "body_rig": has_body_rig,
            "animation_clips": bool(animations),
            "morph_targets": morph_target_count > 0,
            "facial_rig": has_face_rig,
            "jaw_control": has_jaw_control,
            "eye_control": has_eye_control,
            "blink_control": has_blink_control,
            "speech_visemes": has_speech_visemes,
            "expression_control": has_expression_control,
            "realtime_performance_ready": bool(
                has_body_rig and has_face_rig and has_jaw_control and has_eye_control
            ),
        },
        "warnings": warnings,
    }


def _load_manifest(identity: str) -> tuple[Path | None, dict[str, Any]]:
    identity_root = TRIPO_DIR / identity
    canonical = identity_root / "manifest.json"
    candidates: list[Path] = []
    if canonical.is_file():
        candidates.append(canonical)
    if identity_root.is_dir():
        candidates.extend(identity_root.glob(f"{identity}_build_manifest_*.json"))
    candidates.extend(TRIPO_DIR.glob(f"{identity}_build_manifest_*.json"))

    for path in sorted(set(candidates), key=_version_key, reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return path, payload
    return None, {}


def _find_model(identity: str, manifest_path: Path | None, manifest: dict[str, Any]) -> Path:
    identity_root = manifest_path.parent if manifest_path else (TRIPO_DIR / identity)
    assets = manifest.get("assets") or {}
    outputs = manifest.get("outputs") or {}
    declared = assets.get("performance_glb") or assets.get("runtime_glb") or assets.get("master_glb") or outputs.get("model")
    resolved = _resolve_declared_path(identity_root, declared)
    if resolved and resolved.is_file() and resolved.suffix.lower() == ".glb":
        return resolved

    candidates: list[Path] = []
    for root in (TRIPO_DIR / identity, TRIPO_DIR):
        if root.is_dir():
            candidates.extend(root.glob(f"{identity}_rigged_v*.glb"))
            candidates.extend(root.glob(f"{identity}_rigged.glb"))
            candidates.extend(root.glob(f"**/{identity}_master_v*.glb"))
    candidates = [path for path in candidates if path.is_file()]
    if not candidates:
        raise AvatarAssetError(f"no GLB model found for {identity}")
    return max(set(candidates), key=_version_key)


def _find_preview(identity: str, manifest_path: Path | None, manifest: dict[str, Any]) -> Path | None:
    identity_root = manifest_path.parent if manifest_path else (TRIPO_DIR / identity)
    assets = manifest.get("assets") or {}
    outputs = manifest.get("outputs") or {}
    declared = assets.get("preview") or outputs.get("preview")
    resolved = _resolve_declared_path(identity_root, declared)
    if resolved and resolved.is_file():
        return resolved
    candidates: list[Path] = []
    for root in (TRIPO_DIR / identity, TRIPO_DIR):
        if root.is_dir():
            candidates.extend(root.glob(f"{identity}_body*_preview.*"))
            candidates.extend(root.glob(f"previews/{identity}*_preview.*"))
    return max(candidates, key=_version_key) if candidates else None


def get_avatar(identity: str, *, include_inspection: bool = True) -> dict[str, Any]:
    """Resolve one identity's active avatar and portable manifest."""
    identity = safe_identity(identity)
    manifest_path, manifest = _load_manifest(identity)
    model = _find_model(identity, manifest_path, manifest)
    preview = _find_preview(identity, manifest_path, manifest)
    version = str(manifest.get("version") or "legacy")
    record: dict[str, Any] = {
        "schema": manifest.get("schema") or manifest.get("schema_version") or AVATAR_SCHEMA,
        "identity": identity,
        "display_name": manifest.get("display_name") or identity.replace("_", " ").title(),
        "version": version,
        "status": manifest.get("status") or "body_rigged",
        "model_url": f"/api/avatars/{identity}/model",
        "preview_url": f"/api/avatars/{identity}/preview" if preview else None,
        "manifest": manifest,
        "_model_path": model,
        "_preview_path": preview,
    }
    if include_inspection:
        record["inspection"] = inspect_glb(model)
    return record


def list_avatars() -> list[dict[str, Any]]:
    """Discover every identity with a canonical manifest or rigged GLB."""
    identities: set[str] = set()
    if not TRIPO_DIR.is_dir():
        return []
    for path in TRIPO_DIR.iterdir():
        if path.is_dir() and _IDENTITY_RE.fullmatch(path.name.lower()):
            if (path / "manifest.json").is_file() or any(path.glob("*.glb")) or any(path.glob("**/*.glb")):
                identities.add(path.name.lower())
        elif path.is_file():
            match = re.match(r"([a-z0-9_-]+)_rigged(?:_v\d+)?\.glb$", path.name, re.IGNORECASE)
            if match:
                identities.add(match.group(1).lower())

    avatars: list[dict[str, Any]] = []
    for identity in sorted(identities):
        try:
            avatar = get_avatar(identity)
        except AvatarAssetError:
            continue
        avatar.pop("_model_path", None)
        avatar.pop("_preview_path", None)
        avatars.append(avatar)
    return avatars


def avatar_model_path(identity: str) -> Path:
    return get_avatar(identity, include_inspection=False)["_model_path"]


def avatar_preview_path(identity: str) -> Path | None:
    return get_avatar(identity, include_inspection=False)["_preview_path"]
