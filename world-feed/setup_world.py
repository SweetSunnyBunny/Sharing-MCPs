"""Preview or add the fictional Cozy Corner starter through Anam's REST API."""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MARKER = "sharing-mcps-cozy-corner"
EXAMPLE = Path(__file__).resolve().parent / "examples" / "cozy-corner.json"
API = "/api/world-feed"
IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9_-]{0,79}\Z")


class SetupError(Exception):
    """A safe, user-facing setup error; never contains server bodies or secrets."""


class ApiError(SetupError):
    def __init__(self, status: int, missing_item=False):
        self.status = status
        self.missing_item = missing_item
        hints = {
            401: "Authentication required. Set your own ANAM_API_KEY in this PowerShell session, or use the UI's localhost-only setup mode.",
            403: "Access denied. Check the UI's authentication settings.",
            404: "The requested World Feed item does not exist.",
            409: "An item already uses that ID or handle. No existing item was overwritten; resolve the collision in World Feed before retrying.",
        }
        super().__init__(hints.get(status, f"The UI returned HTTP {status}. Check the UI terminal, then retry."))


def _object(value, allowed, required, label):
    if not isinstance(value, dict) or set(value) - allowed or required - set(value):
        raise SetupError(f"Invalid {label}: missing or unknown fields.")


def _text(value, label, limit=4000, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise SetupError(f"Invalid {label}: expected {'optional ' if empty else ''}text, at most {limit} characters.")


def _id(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise SetupError("Invalid starter ID: use lowercase letters, numbers, underscores or hyphens.")


def validate_example(data):
    _object(data, {"schema_version", "world", "profiles", "posts"},
            {"schema_version", "world", "profiles", "posts"}, "starter")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise SetupError("Unsupported starter schema version.")
    w = data["world"]
    fields = {"id", "slug", "name", "description", "story_identity", "story_branch",
              "fictional_now", "clock_label", "posting_enabled", "metadata"}
    _object(w, fields, fields, "world")
    if w["id"] != "cozy-corner" or w["slug"] != "cozy-corner":
        raise SetupError("This starter must use the cozy-corner world ID and slug.")
    for key in ("name", "description", "fictional_now", "clock_label"):
        _text(w[key], key)
    if w["story_identity"] != "Avery" or w["story_branch"] != "" or w["posting_enabled"] is not False:
        raise SetupError("The starter must use Avery, no story branch, and disabled automatic posting.")
    expected = {"starter": {"id": MARKER, "version": 1},
                "activity": {"daily_limit": 12, "auto_publish": False, "scene_reactions": False},
                "photos": {"enabled": False, "daily_limit": 1, "profile_ids": []}}
    # JSON comparison distinguishes booleans from numeric lookalikes.
    if json.dumps(w["metadata"], sort_keys=True) != json.dumps(expected, sort_keys=True):
        raise SetupError("Starter metadata must retain its marker and disabled activity/photo settings.")
    if not isinstance(data["profiles"], list) or len(data["profiles"]) != 3:
        raise SetupError("The starter requires one player and two original fictional NPCs.")
    profiles = {}
    handles = set()
    for p in data["profiles"]:
        allowed = {"id", "handle", "display_name", "account_type", "bio", "posting_style",
                   "prompt_notes", "is_user_controlled", "is_active", "metadata"}
        _object(p, allowed, allowed - {"posting_style", "prompt_notes"}, "profile")
        _id(p["id"])
        if not p["id"].startswith("cozy-corner-") or p["id"] in profiles:
            raise SetupError("Profile IDs must be unique cozy-corner IDs.")
        if not isinstance(p["handle"], str) or not re.fullmatch(r"[A-Za-z0-9_]{1,32}", p["handle"]):
            raise SetupError("A profile handle is invalid.")
        if p["handle"].casefold() in handles:
            raise SetupError("Profile handles must be unique.")
        handles.add(p["handle"].casefold())
        for key in ("display_name", "bio", "posting_style", "prompt_notes"):
            if key in p:
                _text(p[key], key)
        if type(p["is_user_controlled"]) is not bool or p["is_active"] is not True:
            raise SetupError("Profiles require explicit player control and active status.")
        if p["account_type"] != ("user" if p["is_user_controlled"] else "character"):
            raise SetupError("Player/NPC profile types do not match their control flags.")
        if p["metadata"] != {"starter_fixture": MARKER}:
            raise SetupError("A profile is missing its starter marker.")
        profiles[p["id"]] = p
    if sum(p["is_user_controlled"] for p in profiles.values()) != 1:
        raise SetupError("Exactly one starter profile must be controlled by the player.")
    if not isinstance(data["posts"], list) or len(data["posts"]) != 2:
        raise SetupError("The starter requires exactly two NPC fixture posts.")
    post_ids = set()
    for p in data["posts"]:
        fields = {"id", "author_profile_id", "body", "post_type", "canon_level", "canon_status", "origin", "metadata"}
        _object(p, fields, fields, "post")
        _id(p["id"])
        if not p["id"].startswith("cozy-corner-") or p["id"] in post_ids:
            raise SetupError("Post IDs must be unique cozy-corner IDs.")
        post_ids.add(p["id"])
        author = profiles.get(p["author_profile_id"])
        if author is None or author["is_user_controlled"]:
            raise SetupError("Fixture posts may only be authored by the two NPCs.")
        _text(p["body"], "post body")
        if [p[k] for k in ("post_type", "canon_level", "canon_status", "origin")] != ["post", "ambient", "approved", "system"]:
            raise SetupError("Fixture posts must be approved ambient system examples.")
        if p["metadata"] != {"starter_fixture": MARKER, "history_batch": "cozy-corner-starter"}:
            raise SetupError("A fixture post is missing its starter/history marker.")
    return data


def load_example():
    try:
        return validate_example(json.loads(EXAMPLE.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError) as exc:
        raise SetupError("Cannot read the bundled examples/cozy-corner.json. Restore the starter file and retry.") from exc


def validate_url(value, key=""):
    try:
        u = urlsplit(value)
        port = u.port
        host = u.hostname
    except ValueError as exc:
        raise SetupError("Invalid UI URL. Use an origin such as http://127.0.0.1:8790.") from exc
    if (u.scheme not in {"http", "https"} or not host or u.username is not None or u.password is not None
            or u.query or u.fragment or u.path not in {"", "/"} or any(c.isspace() for c in value)):
        raise SetupError("Use only the UI's HTTP(S) origin, without credentials, a path, query or fragment.")
    if port is not None and not 1 <= port <= 65535:
        raise SetupError("The UI URL has an invalid port.")
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host.lower() == "localhost"
    if u.scheme == "http" and not loopback:
        raise SetupError("Use HTTPS for a remote UI; plain HTTP is supported only on localhost.")
    if "\r" in key or "\n" in key:
        raise SetupError("ANAM_API_KEY contains an invalid newline. Set it again without line breaks.")
    return value.rstrip("/")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, url, key=""):
        self.url = validate_url(url, key)
        self.key = key
        self.opener = build_opener(_NoRedirect())

    def request(self, method, route, payload=None):
        if method not in {"GET", "POST"} or not route.startswith(API + "/"):
            raise SetupError("Unsupported setup request.")
        headers = {"Accept": "application/json"}
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        try:
            with self.opener.open(Request(self.url + route, data=body, headers=headers, method=method), timeout=20) as response:
                raw = response.read(2_000_001)
        except HTTPError as exc:
            if 300 <= exc.code < 400:
                raise SetupError("The UI redirected this request. Use its final origin and configure API authentication; setup will not forward credentials through a redirect.") from None
            missing_item = False
            if exc.code == 404:
                try:
                    detail = json.loads(exc.read(4096)).get("detail")
                    missing_item = detail in {"Story world not found", "Profile not found", "Post not found"}
                except (ValueError, TypeError, AttributeError):
                    pass
            raise ApiError(exc.code, missing_item=missing_item) from None
        except (URLError, TimeoutError, OSError):
            raise SetupError("Cannot reach the UI. Start the sibling ui server, check its address, then retry.") from None
        if len(raw) > 2_000_000:
            raise SetupError("The UI response was unexpectedly large.")
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError):
            raise SetupError("The server did not return JSON. Check the UI address and authentication.") from None
        if not isinstance(result, dict):
            raise SetupError("The server returned an unexpected response.")
        return result

    def optional(self, route):
        try:
            return self.request("GET", route)
        except ApiError as exc:
            if exc.status == 404 and exc.missing_item:
                return None
            if exc.status == 404:
                raise SetupError("This address did not recognize the World Feed API. Check that the sibling ui server is running at this origin.") from None
            raise


def _owned_world(response):
    world = response.get("world", {})
    metadata = world.get("metadata") or {}
    if not isinstance(metadata, dict) or metadata.get("starter") != {"id": MARKER, "version": 1}:
        raise SetupError("A different world already uses cozy-corner. Setup will not change it. Use that world or choose a new world in the UI.")
    return world


def inspect(client, data):
    response = client.optional(f"{API}/worlds/{data['world']['id']}")
    if response is not None:
        _owned_world(response)
    return response


def apply(client, data):
    world_id = data["world"]["id"]
    existing = inspect(client, data)
    # Preflight global IDs and handles before making any changes.
    profiles, posts = {}, {}
    for kind, items, target in (("profiles", data["profiles"], profiles), ("posts", data["posts"], posts)):
        for item in items:
            found = client.optional(f"{API}/{kind}/{item['id']}")
            if found is not None and (existing is None or found.get("world_id") != world_id):
                raise SetupError(f"A starter {kind[:-1]} ID is already used outside this starter. Nothing was overwritten.")
            target[item["id"]] = found
    if existing is not None:
        all_profiles = client.request("GET", f"{API}/worlds/{world_id}/profiles?include_inactive=true").get("profiles", [])
        for planned in data["profiles"]:
            if profiles[planned["id"]] is None and any(p.get("handle", "").casefold() == planned["handle"].casefold() for p in all_profiles):
                raise SetupError("A missing starter profile's handle is now in use. Keep your edited cast; setup will not overwrite it.")
    else:
        worlds = client.request("GET", API + "/worlds").get("worlds", [])
        if any(w.get("slug") == data["world"]["slug"] for w in worlds):
            raise SetupError("Another world already uses the cozy-corner slug. Nothing was overwritten.")
    counts = {"worlds": 0, "profiles": 0, "posts": 0, "protected_posts_skipped": 0}
    if existing is None:
        client.request("POST", API + "/worlds", data["world"])
        counts["worlds"] += 1
    for p in data["profiles"]:
        if profiles[p["id"]] is None:
            profiles[p["id"]] = client.request("POST", f"{API}/worlds/{world_id}/profiles", p)
            counts["profiles"] += 1
    for p in data["posts"]:
        if posts[p["id"]] is not None:
            continue
        # Re-read authors so a user changing control flags during setup wins.
        author = client.request("GET", f"{API}/profiles/{p['author_profile_id']}")
        if author.get("world_id") != world_id:
            raise SetupError("A fixture author no longer belongs to Cozy Corner. Retry after checking the cast.")
        if author.get("is_user_controlled") or not author.get("is_active", True):
            counts["protected_posts_skipped"] += 1
            continue
        client.request("POST", f"{API}/worlds/{world_id}/posts", p)
        counts["posts"] += 1
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Read-only API check; creates nothing")
    mode.add_argument("--apply", action="store_true", help="Add missing starter items without changing existing records")
    parser.add_argument("--url", default="http://127.0.0.1:8790", help="Your Anam UI origin")
    args = parser.parse_args(argv)
    try:
        data = load_example()
        url = validate_url(args.url, os.environ.get("ANAM_API_KEY", ""))
        if not args.check and not args.apply:
            print("Preview only; no connection or changes made.")
            print("Cozy Corner: Riley Finch (@riley_finch, fictional player placeholder), Mira Moss (@mira_maps), Pip Penn (@pip_bakes), and two NPC example posts.")
            print("Automatic posts, automatic publication, scene reactions and automatic photos start off. No post is written as the player.")
            print("Start the sibling ui server, then run: python setup_world.py --check")
            print("To add this starter: python setup_world.py --apply")
            return 0
        client = Client(url, os.environ.get("ANAM_API_KEY", ""))
        if args.check:
            existing = inspect(client, data)
            print("World Feed API reachable. " + ("Cozy Corner is already present; existing edits will be kept." if existing else "Cozy Corner has not been added yet."))
            print("Read-only check complete. No model, photo or setup request was made.")
        else:
            counts = apply(client, data)
            print(f"Cozy Corner ready: added {counts['worlds']} world, {counts['profiles']} profiles and {counts['posts']} example posts. Existing records were kept.")
            if counts["protected_posts_skipped"]:
                print("Skipped example posts for profiles you made player-controlled or inactive.")
            print("Open /world-feed on your UI, choose Cozy Corner, then Profiles > Riley Finch > Use as me.")
            print("This importer did not run a model or generate photos. Existing automation settings were not changed.")
        return 0
    except SetupError as exc:
        print(f"Setup stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
