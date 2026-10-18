"ChatGPT device-code login for the house's OWN image road (separate from Codex CLI's ~/.codex/auth.json,"
import json, os, pathlib, sys, time, urllib.error, urllib.request
from datetime import datetime, timezone
from urllib.parse import urlencode

ISSUER = "https://auth.openai.com"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
TOKEN_URL = f"{ISSUER}/oauth/token"
PRIVATE = pathlib.Path(os.environ.get("PHOTOS_CHATGPT_AUTH_FILE", str(pathlib.Path(__file__).resolve().parent / "data" / "private" / "chatgpt_image_oauth.json"))).expanduser().parent
OUT = pathlib.Path(os.environ.get("PHOTOS_CHATGPT_AUTH_FILE", str(PRIVATE / "chatgpt_image_oauth.json"))).expanduser()
PENDING = PRIVATE / "chatgpt_image_login_pending.json"
UA = "anam-image-login/1.0"


def post(url, payload, form=False):
    body = urlencode(payload).encode() if form else json.dumps(payload).encode()
    ctype = "application/x-www-form-urlencoded" if form else "application/json"
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": ctype, "Accept": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def start():
    status, dev = post(f"{ISSUER}/api/accounts/deviceauth/usercode", {"client_id": CLIENT_ID})
    if status != 200 or not dev.get("user_code") or not dev.get("device_auth_id"):
        print("DEVICE_CODE_FAILED", status, {k: dev.get(k) for k in ("error", "message") if k in dev})
        return 1
    PRIVATE.mkdir(parents=True, exist_ok=True)
    PENDING.write_text(json.dumps({"device_auth_id": dev["device_auth_id"], "user_code": dev["user_code"],
                                   "started": time.time()}), encoding="utf-8")
    print(f"OPEN: {ISSUER}/codex/device")
    print(f"CODE: {dev['user_code']}")
    return 0


def finish(wait_s=90):
    if not PENDING.exists():
        print("NO_PENDING_LOGIN (run start first)")
        return 1
    pend = json.loads(PENDING.read_text(encoding="utf-8"))
    if time.time() - pend["started"] > 15 * 60:
        print("PENDING_EXPIRED (code older than 15 min; run start again)")
        return 1
    deadline, code_resp = time.monotonic() + wait_s, None
    while time.monotonic() < deadline:
        st, resp = post(f"{ISSUER}/api/accounts/deviceauth/token",
                        {"device_auth_id": pend["device_auth_id"], "user_code": pend["user_code"]})
        if st == 200:
            code_resp = resp
            break
        if st not in (403, 404):
            print("POLL_FAILED", st)
            return 1
        time.sleep(4)
    if code_resp is None:
        print("NOT_APPROVED_YET (approve the code, then run finish again)")
        return 2
    st, tokens = post(TOKEN_URL, {
        "grant_type": "authorization_code", "code": code_resp.get("authorization_code", ""),
        "redirect_uri": f"{ISSUER}/deviceauth/callback", "client_id": CLIENT_ID,
        "code_verifier": code_resp.get("code_verifier", "")}, form=True)
    if st != 200 or not tokens.get("access_token"):
        print("EXCHANGE_FAILED", st)
        return 1
    OUT.write_text(json.dumps({
        "access_token": tokens["access_token"], "refresh_token": tokens.get("refresh_token", ""),
        "id_token": tokens.get("id_token", ""), "obtained_at": datetime.now(timezone.utc).isoformat(),
        "client_id": CLIENT_ID}, indent=2), encoding="utf-8")
    PENDING.unlink(missing_ok=True)
    print("SAVED_OK", OUT, "| refresh_token:", bool(tokens.get("refresh_token")))
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "start"
    sys.exit(start() if cmd == "start" else finish())
