"""Offline checks: no running UI, account, model or photo service is used."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

MODULE = Path(__file__).resolve().parents[1] / "setup_world.py"
spec = importlib.util.spec_from_file_location("world_feed_setup", MODULE)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class FakeClient:
    def __init__(self):
        self.worlds = {}
        self.profiles = {}
        self.posts = {}
        self.calls = []
        self.fail_after = None
        self.writes = 0

    def optional(self, route):
        try:
            return self.request("GET", route)
        except setup.ApiError as exc:
            if exc.status == 404:
                return None
            raise

    def request(self, method, route, payload=None):
        self.calls.append((method, route))
        parts = route.split("?", 1)[0].split("/")[3:]
        if method == "GET":
            kind = parts[0]
            if kind == "worlds" and len(parts) == 1:
                return {"worlds": copy.deepcopy(list(self.worlds.values()))}
            if kind == "worlds":
                world = self.worlds.get(parts[1])
                if world is None:
                    raise setup.ApiError(404)
                profiles = [copy.deepcopy(p) for p in self.profiles.values() if p["world_id"] == parts[1]]
                if len(parts) == 3:
                    return {"profiles": profiles}
                return {"world": copy.deepcopy(world), "profiles": profiles}
            item = getattr(self, kind).get(parts[1])
            if item is None:
                raise setup.ApiError(404)
            return copy.deepcopy(item)
        if method != "POST":
            raise AssertionError("The importer attempted a modifying method other than POST")
        if self.fail_after is not None and self.writes >= self.fail_after:
            raise setup.SetupError("Simulated interrupted setup")
        data = copy.deepcopy(payload)
        if parts == ["worlds"]:
            bucket = self.worlds
        else:
            bucket = getattr(self, parts[2])
            data["world_id"] = parts[1]
        if data["id"] in bucket:
            raise setup.ApiError(409)
        bucket[data["id"]] = data
        self.writes += 1
        return copy.deepcopy(data)


class StarterTests(unittest.TestCase):
    def setUp(self):
        self.data = setup.load_example()
        self.client = FakeClient()

    def test_example_has_protected_player_and_no_automation(self):
        self.assertEqual(sum(p["is_user_controlled"] for p in self.data["profiles"]), 1)
        self.assertFalse(self.data["world"]["posting_enabled"])
        self.assertFalse(self.data["world"]["metadata"]["photos"]["enabled"])
        player = next(p["id"] for p in self.data["profiles"] if p["is_user_controlled"])
        self.assertNotIn(player, [p["author_profile_id"] for p in self.data["posts"]])

    def test_player_placeholder_does_not_protect_ordinary_pronouns(self):
        player = next(p for p in self.data["profiles"] if p["is_user_controlled"])
        ordinary = {"i", "me", "my", "mine", "you", "your", "yours", "we", "us", "our", "ours", "they", "them", "their", "he", "him", "his", "she", "her", "hers", "it", "its"}
        protected = {player["handle"].casefold(), player["display_name"].casefold()}
        protected.update(word.casefold() for word in player["display_name"].split())
        self.assertFalse(protected & ordinary)
        self.assertEqual(player["handle"], "riley_finch")

    def test_fresh_apply_then_repeat_does_not_duplicate(self):
        first = setup.apply(self.client, self.data)
        self.assertEqual(first, {"worlds": 1, "profiles": 3, "posts": 2, "protected_posts_skipped": 0})
        second = setup.apply(self.client, self.data)
        self.assertEqual(sum(second.values()), 0)
        self.assertEqual(self.client.writes, 6)

    def test_retry_after_partial_setup(self):
        self.client.fail_after = 2
        with self.assertRaises(setup.SetupError):
            setup.apply(self.client, self.data)
        self.client.fail_after = None
        result = setup.apply(self.client, self.data)
        self.assertEqual(result["worlds"], 0)
        self.assertEqual(result["profiles"], 2)
        self.assertEqual(result["posts"], 2)

    def test_existing_authored_edits_and_settings_are_preserved(self):
        setup.apply(self.client, self.data)
        self.client.worlds["cozy-corner"]["name"] = "My own world"
        self.client.worlds["cozy-corner"]["posting_enabled"] = True
        self.client.profiles["cozy-corner-mira"]["bio"] = "My edited character"
        self.client.posts["cozy-corner-welcome-map"]["body"] = "My edited post"
        before = copy.deepcopy((self.client.worlds, self.client.profiles, self.client.posts))
        setup.apply(self.client, self.data)
        self.assertEqual(before, (self.client.worlds, self.client.profiles, self.client.posts))

    def test_check_is_get_only_and_does_not_list_seeded_worlds(self):
        self.assertIsNone(setup.inspect(self.client, self.data))
        self.assertEqual(self.client.calls, [("GET", "/api/world-feed/worlds/cozy-corner")])
        self.assertEqual(self.client.writes, 0)

    def test_existing_unmarked_world_stops_before_writes(self):
        self.client.worlds["cozy-corner"] = {"id": "cozy-corner", "metadata": {}}
        with self.assertRaisesRegex(setup.SetupError, "different world"):
            setup.apply(self.client, self.data)
        self.assertEqual(self.client.writes, 0)

    def test_slug_collision_stops_before_writes(self):
        self.client.worlds["my-world"] = {"id": "my-world", "slug": "cozy-corner"}
        with self.assertRaisesRegex(setup.SetupError, "slug"):
            setup.apply(self.client, self.data)
        self.assertEqual(self.client.writes, 0)

    def test_global_profile_collision_stops_before_world_creation(self):
        self.client.profiles["cozy-corner-mira"] = {"id": "cozy-corner-mira", "world_id": "other"}
        with self.assertRaisesRegex(setup.SetupError, "outside"):
            setup.apply(self.client, self.data)
        self.assertEqual(self.client.writes, 0)

    def test_global_post_collision_stops_before_world_creation(self):
        self.client.posts["cozy-corner-welcome-map"] = {"id": "cozy-corner-welcome-map", "world_id": "other"}
        with self.assertRaisesRegex(setup.SetupError, "outside"):
            setup.apply(self.client, self.data)
        self.assertEqual(self.client.writes, 0)

    def test_new_handle_collision_preserves_user_cast(self):
        self.client.worlds["cozy-corner"] = copy.deepcopy(self.data["world"])
        self.client.profiles["my-mira"] = {"id": "my-mira", "world_id": "cozy-corner", "handle": "MIRA_MAPS"}
        with self.assertRaisesRegex(setup.SetupError, "handle"):
            setup.apply(self.client, self.data)
        self.assertEqual(self.client.writes, 0)

    def test_no_fixture_written_as_newly_protected_or_inactive_author(self):
        setup.apply(self.client, self.data)
        self.client.posts.clear()
        self.client.profiles["cozy-corner-mira"]["is_user_controlled"] = True
        self.client.profiles["cozy-corner-pip"]["is_active"] = False
        result = setup.apply(self.client, self.data)
        self.assertEqual(result["protected_posts_skipped"], 2)
        self.assertEqual(self.client.posts, {})

    def test_unknown_payload_fields_cannot_inject_routes_or_media(self):
        for section in ("world", "profiles", "posts"):
            with self.subTest(section=section):
                data = copy.deepcopy(self.data)
                target = data[section] if section == "world" else data[section][0]
                target["route"] = "https://example.invalid/anything"
                with self.assertRaises(setup.SetupError):
                    setup.validate_example(data)

    def test_invalid_id_and_automation_rejected(self):
        cases = [lambda d: d["profiles"][0].update(id="../../secret"),
                 lambda d: d["world"].update(posting_enabled=True),
                 lambda d: d["world"]["metadata"]["activity"].update(auto_publish=True),
                 lambda d: d["posts"][0].update(author_profile_id="cozy-corner-you")]
        for change in cases:
            data = copy.deepcopy(self.data)
            change(data)
            with self.assertRaises(setup.SetupError):
                setup.validate_example(data)

    def test_url_validation_rejects_credentials_and_plain_remote_http(self):
        urls = ["http://user:secret@localhost:8790", "http://example.com", "https://example.com/path",
                "https://example.com/?token=secret", "https://example.com/#secret", "file:///anything",
                "http://localhost:99999", "http://localhost:8790/other"]
        for url in urls:
            with self.subTest(url=url), self.assertRaises(setup.SetupError):
                setup.validate_url(url, "private-key")
        for url in ["http://127.0.0.1:8790", "http://[::1]:8790", "http://localhost:8790", "https://example.com"]:
            self.assertEqual(setup.validate_url(url), url)

    def test_default_preview_does_not_construct_network_client(self):
        with patch.object(setup, "Client", side_effect=AssertionError("Network forbidden")), patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(setup.main([]), 0)

    def test_errors_do_not_print_server_body_or_key(self):
        client = setup.Client("http://127.0.0.1:8790", "private-key")
        error = HTTPError(client.url, 500, "private-key in server error", {}, io.BytesIO(b"private-key"))
        with patch.object(client.opener, "open", side_effect=error):
            with self.assertRaises(setup.ApiError) as caught:
                client.request("GET", setup.API + "/worlds/cozy-corner")
        self.assertNotIn("private-key", str(caught.exception))

    def test_redirects_never_forward_authentication(self):
        handler = setup._NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 302, "", {}, "https://other.invalid"))
        client = setup.Client("http://127.0.0.1:8790", "private-key")
        error = HTTPError(client.url, 302, "", {}, None)
        with patch.object(client.opener, "open", side_effect=error), self.assertRaisesRegex(setup.SetupError, "redirected"):
            client.request("GET", setup.API + "/worlds/cozy-corner")

    def test_check_distinguishes_missing_world_from_wrong_server(self):
        client = setup.Client("http://127.0.0.1:8790")
        for detail, known in [("Story world not found", True), ("Not Found", False)]:
            error = HTTPError(client.url, 404, "", {}, io.BytesIO(json.dumps({"detail": detail}).encode()))
            with patch.object(client.opener, "open", side_effect=error):
                if known:
                    self.assertIsNone(client.optional(setup.API + "/worlds/cozy-corner"))
                else:
                    with self.assertRaisesRegex(setup.SetupError, "did not recognize"):
                        client.optional(setup.API + "/worlds/cozy-corner")


if __name__ == "__main__":
    unittest.main()
