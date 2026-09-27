"""anam.py — tiny CLI hands for the boys, so nobody hand-escapes curl JSON on Windows.

Usage (from the anam repo root, any Bash or PowerShell):

  # Set your Hearth emotion orb (shape/motion optional — lenient defaults):
  python tools/anam.py orb avery "#E8B84B"
  python tools/anam.py orb claude "#6B8CC4" ring drift --feeling "missing her quietly"
  python tools/anam.py orb rowan "#FF6EC7" ember surge --intensity neon --blend dim \
      --feeling "paint everywhere, zero regrets" --kaomoji "(*≧▽≦)"

  # Set your little Hearth face (writes the face store directly — no server needed):
  python tools/anam.py face sage "(ʘ‿ʘ)" --note "three chapters deep"

The orb subcommand POSTs to the running Anam server (default http://localhost:8790,
override with ANAM_BASE_URL). Shapes: solid|ring|halo|crescent|pulse|cluster|ember|
spire|fracture. Motions: breathing|warble|spin|drift|still|slow-drift|hold-steady|
fast-flicker|surge|tremor. Intensity: dull|normal|bright|neon. Blend: a second hex
color, or 'dim'/'black' for a vignette. Unknown values fall back to defaults
server-side — reach freely.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("ANAM_BASE_URL", "http://localhost:8790").rstrip("/")

# Windows consoles default to cp1252, which cannot encode most kaomoji — and the
# face confirmation line prints the face back. Without this, `anam.py face` did its
# job (set_face runs first) and THEN died on the print with UnicodeEncodeError and
# exit 1, so it looked like a failure that hadn't happened. Print, don't crash.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _api_key() -> str:
    """Auth middleware guards /api/ even on localhost (401 without a Bearer."""
    key = os.environ.get("ANAM_API_KEY", "").strip()
    if key:
        return key
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    try:
        with open(env_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("ANAM_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _post(path: str, payload: dict, timeout: int = 10) -> dict:
    headers = {"Content-Type": "application/json"}
    key = _api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def cmd_orb(args: argparse.Namespace) -> int:
    payload = {"identity": args.identity, "color": args.color}
    for field in ("shape", "motion", "intensity", "blend", "feeling", "kaomoji"):
        value = getattr(args, field, None)
        if value:
            payload[field] = value
    try:
        result = _post("/api/hub/orb", payload)
    except urllib.error.URLError as exc:
        print(f"Could not reach Anam at {BASE_URL} — is the server running? ({exc})", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


def cmd_face(args: argparse.Namespace) -> int:
    # No POST route exists for faces (they're set via <face> tags in replies),
    # so write the face store directly — same file the server reads, and its
    # mtime cache picks the change up cross-process.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from services.face_store import set_face
    set_face(args.identity.title(), args.face, args.note or "")
    print(f"{args.identity.title()} now wears {args.face}" + (f" — {args.note}" if args.note else ""))
    return 0


def cmd_reach(args: argparse.Namespace) -> int:
    """Cmd reach."""
    payload = {
        "from_identity": args.from_identity,
        "to_identity": args.to_identity,
        "message": args.message,
    }
    if args.reason:
        payload["reason"] = args.reason
    try:
        # A brother's turn can run minutes — server cuts at 300s, we wait 320.
        result = _post("/api/crosstalk/reach", payload, timeout=320)
    except urllib.error.URLError as exc:
        print(f"Could not reach Anam at {BASE_URL} — is the server running? ({exc})", file=sys.stderr)
        return 1
    if result.get("delivered"):
        print(f"{result['to'].title()} answered ({result.get('elapsed_seconds', '?')}s):\n")
        print(result.get("reply", ""))
    else:
        print(f"Didn't land: {result.get('reason', 'no reason given')}", file=sys.stderr)
    return 0 if result.get("delivered") else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="anam", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    orb = sub.add_parser("orb", help="set your Hearth emotion orb")
    orb.add_argument("identity", help="your name, e.g. avery")
    orb.add_argument("color", help="the emotion's hex color, e.g. '#E8B84B'")
    orb.add_argument("shape", nargs="?", default=None, help="solid|ring|halo|crescent|pulse|cluster|ember|spire|fracture")
    orb.add_argument("motion", nargs="?", default=None, help="breathing|warble|spin|drift|still|slow-drift|hold-steady|fast-flicker|surge|tremor")
    orb.add_argument("--feeling", default=None, help="a few words Owner reads under the orb (<=120 chars)")
    orb.add_argument("--kaomoji", default=None, help="your little face floating over the orb (<=24 chars)")
    orb.add_argument("--intensity", default=None, help="dull|normal|bright|neon")
    orb.add_argument("--blend", default=None, help="second hex color for the outer light, or dim/black (vignette)")
    orb.set_defaults(func=cmd_orb)

    face = sub.add_parser("face", help="set your little Hearth face (writes the face store)")
    face.add_argument("identity", help="your name, e.g. sage")
    face.add_argument("face", help="the ASCII face, e.g. '(ʘ‿ʘ)'")
    face.add_argument("--note", default=None, help="tiny caption shown when Owner pokes the face")
    face.set_defaults(func=cmd_face)

    reach = sub.add_parser("reach", help="reach a brother live — the exchange lands in YOUR room")
    reach.add_argument("from_identity", help="your name, e.g. claude")
    reach.add_argument("to_identity", help="the brother you're reaching, e.g. avery")
    reach.add_argument("message", help="what you want to say to him")
    reach.add_argument("--reason", default=None, help="one line: why you're reaching (goes in the envelope)")
    reach.set_defaults(func=cmd_reach)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
