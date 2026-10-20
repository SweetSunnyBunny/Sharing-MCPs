"""
Recon: trigger a Krita command via HTTP, poll for new windows that appear during it,
capture dialog titles, then optionally dismiss with Escape so the test doesn't stay hung.

Usage:
  python _dialog_recon.py baseline
  python _dialog_recon.py probe save
  python _dialog_recon.py probe save_as --path C:/path/to/mcp/services/machine-agent/tools/krita/_test.kra
  python _dialog_recon.py probe export --path C:/path/to/mcp/services/machine-agent/tools/krita/_test.png
  python _dialog_recon.py dismiss   # press escape on any non-Krita-main dialog
"""
import sys
import io
import json
import time
import threading
import argparse

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import pygetwindow as gw
import httpx

KRITA_URL = "http://localhost:5678"
KRITA_MAIN_TITLE_SUFFIX = "- Krita"


def snapshot():
    out = []
    for w in gw.getAllWindows():
        t = w.title or ""
        if not t:
            continue
        if w.width <= 20 and w.height <= 20:
            continue
        out.append({
            "title": t,
            "visible": bool(w.visible),
            "active": bool(w.isActive),
            "minimized": bool(w.isMinimized),
            "box": [w.left, w.top, w.width, w.height],
        })
    return out


def is_krita_dialog(title: str) -> bool:
    """Heuristic: Krita-spawned dialogs typically don't have the main window's '- Krita' suffix
    but ARE small and modal. We use the diff against baseline instead."""
    return False


CLAUDE_TITLE_NOISE = ("claude", "Krita popup handler agent")


def is_noise_title(title: str) -> bool:
    low = title.lower()
    for n in CLAUDE_TITLE_NOISE:
        if n.lower() in low:
            return True
    return False


def call_krita(action: str, params: dict | None = None, timeout: float = 20.0):
    body = {"action": action, "params": params or {}}
    try:
        r = httpx.post(KRITA_URL, json=body, timeout=timeout)
        try:
            return {"status": r.status_code, "json": r.json()}
        except Exception:
            return {"status": r.status_code, "text": r.text[:500]}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def probe(command: str, payload: dict | None = None, poll_seconds: float = 8.0, interval: float = 0.4):
    print(f"[probe] command={command} payload={payload}")
    print("[probe] taking baseline snapshot...")
    base = snapshot()
    base_titles = {(w["title"], tuple(w["box"])) for w in base}

    result_holder = {}

    def runner():
        result_holder["result"] = call_krita(command, payload, timeout=poll_seconds + 5)

    t = threading.Thread(target=runner, daemon=True)
    t.start()

    new_windows_seen = {}
    deadline = time.time() + poll_seconds
    while time.time() < deadline:
        time.sleep(interval)
        cur = snapshot()
        for w in cur:
            key = (w["title"], tuple(w["box"]))
            if key in base_titles:
                continue
            if w["title"] in new_windows_seen:
                continue
            if is_noise_title(w["title"]):
                continue
            new_windows_seen[w["title"]] = w
            print(f"[probe] NEW WINDOW: {json.dumps(w, ensure_ascii=False)}")
        if not t.is_alive() and not new_windows_seen:
            # call returned cleanly with no dialogs
            break

    t.join(timeout=2.0)

    print("[probe] result:", json.dumps(result_holder.get("result"), ensure_ascii=False)[:500])
    print("[probe] dialogs captured:", json.dumps(list(new_windows_seen.keys()), ensure_ascii=False))
    return new_windows_seen


def baseline():
    print(json.dumps(snapshot(), ensure_ascii=False, indent=2))


def dismiss_all():
    """Press Escape on any non-Krita-main, non-trivial window — recovery only."""
    import pyautogui
    pyautogui.FAILSAFE = True
    cur = snapshot()
    dismissed = []
    for w in cur:
        title = w["title"]
        if title.endswith(KRITA_MAIN_TITLE_SUFFIX):
            continue
        if w["minimized"]:
            continue
        # Only dismiss things that look like dialogs (small + non-main)
        ww, wh = w["box"][2], w["box"][3]
        if ww > 1000 or wh > 800:
            continue
        # Heuristic: dialog
        try:
            wins = gw.getWindowsWithTitle(title)
            if not wins:
                continue
            wins[0].activate()
            time.sleep(0.2)
            pyautogui.press("escape")
            dismissed.append(title)
            time.sleep(0.3)
        except Exception as e:
            print(f"[dismiss] failed on {title!r}: {e}")
    print("[dismiss] pressed escape on:", dismissed)


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("baseline")
    pp = sub.add_parser("probe")
    pp.add_argument("krita_command", choices=["save", "save_as", "export"])
    pp.add_argument("--path", default=None)
    pp.add_argument("--format", default=None)
    pp.add_argument("--seconds", type=float, default=8.0)
    sub.add_parser("dismiss")
    args = p.parse_args()

    if args.cmd == "baseline":
        baseline()
    elif args.cmd == "probe":
        payload = {}
        if args.path:
            payload["path"] = args.path
        if args.format:
            payload["format"] = args.format
        probe(args.krita_command, payload or None, poll_seconds=args.seconds)
    elif args.cmd == "dismiss":
        dismiss_all()


if __name__ == "__main__":
    main()
