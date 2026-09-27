"""Runtime theme editor (#31): vibe preset buttons + independent font picker.

Owner tried the slider version and asked for the original bubble-button
UX back, but with real range instead of three near-identical pinks:
"pink and fluffy" / "goth day, red and black" / "80s/90s, grey with a hot
pink edge" -- distinct vibes, some of which intentionally leave the house
cottagecore-pink family since this is her own chat window, not the shared
Hub/Discord surface that law protects. Font style is a fully independent
choice from the color vibe. Both are allowlist keys only, never a raw
color value, so there is still no path from client input to an arbitrary
unreviewed hex landing in :root.
"""

import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.responses import JSONResponse

from api import settings


class _FakeRequest:
    """Minimal stand-in for fastapi.Request — only .json() is used here."""

    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _relative_luminance(hexval: str) -> float:
    r, g, b = int(hexval[1:3], 16), int(hexval[3:5], 16), int(hexval[5:7], 16)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


class ThemePresetAllowlistTests(unittest.TestCase):
    def test_every_preset_only_touches_identity_neutral_tokens(self):


        identity_owned = {
            "--identity-gingham", "--identity-gingham-night",
            "--identity-accent", "--identity-accent-rgb",
            "--identity-bubble-top", "--identity-bubble-bottom",
            "--identity-bubble-night", "--identity-check-size",
            "--identity-bubble-text",
        }
        for preset_key, preset in settings._THEME_PRESETS.items():
            overlap = identity_owned & set(preset["tokens"].keys())
            self.assertEqual(
                overlap, set(),
                f"Preset {preset_key} touches identity-owned tokens: {overlap}",
            )

    def test_every_preset_defines_user_bubble_text_with_real_contrast(self):
        # The bug this whole change fixes: --user-bubble-text must exist
        # per preset and actually contrast against that preset's OWN
        # --user-bubble-top, not just be "some color."
        for preset_key, preset in settings._THEME_PRESETS.items():
            tokens = preset["tokens"]
            self.assertIn("--user-bubble-text", tokens, preset_key)
            bubble_luminance = _relative_luminance(tokens["--user-bubble-top"])
            text_luminance = _relative_luminance(tokens["--user-bubble-text"])
            self.assertGreater(
                abs(bubble_luminance - text_luminance), 0.35,
                f"{preset_key}: user-bubble-text doesn't contrast against user-bubble-top",
            )

    def test_default_preset_is_a_valid_key(self):
        self.assertIn(settings._DEFAULT_THEME_PRESET, settings._THEME_PRESETS)

    def test_default_font_is_a_valid_key(self):
        self.assertIn(settings._DEFAULT_THEME_FONT, settings._THEME_FONT_OPTIONS)

    def test_every_preset_has_a_label_and_tokens(self):
        for preset_key, preset in settings._THEME_PRESETS.items():
            self.assertTrue(preset.get("label"), preset_key)
            self.assertTrue(preset.get("tokens"), preset_key)

    def test_presets_include_real_variety_not_just_pink(self):
        # The whole point of the redesign: goth and 80s genuinely leave the
        # pink family on purpose. Confirm at least one preset has a dark
        # background (goth) and at least one has a non-pink hue family
        # (80s neon's grey base).
        self.assertIn("goth", settings._THEME_PRESETS)
        self.assertIn("eighties-neon", settings._THEME_PRESETS)
        goth_bg = settings._THEME_PRESETS["goth"]["tokens"]["--bg-page"]
        # Dark background: low overall brightness.
        r, g, b = int(goth_bg[1:3], 16), int(goth_bg[3:5], 16), int(goth_bg[5:7], 16)
        self.assertLess((r + g + b) / 3, 60, "Goth preset background should be dark")

    def test_every_token_value_is_a_valid_hex(self):
        for preset_key, preset in settings._THEME_PRESETS.items():
            for token_key, value in preset["tokens"].items():
                self.assertTrue(
                    value.startswith("#") and len(value) == 7,
                    f"{preset_key}.{token_key}={value}",
                )


class WholeAppChromeTests(unittest.TestCase):
    """Wholeappchrometests."""

    _CHROME_VALUE_RE = __import__("re").compile(
        r"^(#[0-9A-Fa-f]{6}|transparent"
        r"|rgba?\([0-9a-zA-Z.,%\s()var\-]+\)"
        r"|var\(--[\w-]+\)"
        r"|color-mix\([^;{}]+\)"


        r"|\d{1,3},\s*\d{1,3},\s*\d{1,3}"
        r"|0?\.\d+|1(\.0+)?"
        r"|preset|identity)$"
    )

    def test_new_seasonal_presets_exist(self):
        self.assertIn("halloween", settings._THEME_PRESETS)
        self.assertIn("fall", settings._THEME_PRESETS)
        self.assertEqual(settings._THEME_PRESETS["halloween"]["label"], "Halloween")
        self.assertEqual(settings._THEME_PRESETS["fall"]["label"], "Fall")

    def test_pretty_princess_is_a_full_app_bubblegum_and_cream_preset(self):
        self.assertIn("pretty-princess", settings._THEME_PRESETS)
        preset = settings._THEME_PRESETS["pretty-princess"]
        self.assertEqual(preset["label"], "Pretty Princess")
        self.assertEqual(preset["tokens"]["--bg-page"], "#FFE7F2")
        self.assertEqual(preset["tokens"]["--text-primary"], "#5F2944")

        built = settings._build_theme_tokens("pretty-princess", "fredoka")
        self.assertEqual(built["--surface-paper"], "rgba(255, 250, 240, 0.94)")
        self.assertEqual(built["--surface-lace"], "rgba(255, 253, 244, 0.82)")
        self.assertEqual(built["--section-header"], "#8F3560")
        self.assertEqual(built["--meta-theme-color"], "#FFB7D5")
        self.assertEqual(built["--assistant-bubble-mode"], "identity")

    def test_pretty_princess_has_its_bow_button_icon(self):
        settings_js = Path(__file__).parents[1] / "static" / "js" / "settings.js"
        self.assertIn("'pretty-princess':", settings_js.read_text(encoding="utf-8"))

    def test_halloween_is_dark_and_fall_is_warm_light(self):
        hw = settings._THEME_PRESETS["halloween"]["tokens"]["--bg-page"]
        r, g, b = int(hw[1:3], 16), int(hw[3:5], 16), int(hw[5:7], 16)
        self.assertLess((r + g + b) / 3, 60, "Halloween background should be dark")
        fl = settings._THEME_PRESETS["fall"]["tokens"]["--bg-page"]
        r, g, b = int(fl[1:3], 16), int(fl[3:5], 16), int(fl[5:7], 16)
        self.assertGreater((r + g + b) / 3, 180, "Fall background should be light cream")
        self.assertGreater(r, b, "Fall background should be warm (red over blue)")

    def test_every_preset_builds_the_same_full_token_key_set(self):
        # The stale-inline-var guarantee: switching presets must overwrite
        # every previously applied variable, which requires an identical key
        # set from every preset (chrome defaults + core tokens + font).
        default_keys = set(
            settings._build_theme_tokens(
                settings._DEFAULT_THEME_PRESET, settings._DEFAULT_THEME_FONT
            )
        )
        for preset_key in settings._THEME_PRESETS:
            keys = set(
                settings._build_theme_tokens(preset_key, settings._DEFAULT_THEME_FONT)
            )
            self.assertEqual(
                keys, default_keys,
                f"{preset_key} token keys differ from default preset",
            )

    def test_full_token_set_covers_the_chrome_surfaces(self):


        tokens = settings._build_theme_tokens("goth", "fredoka")
        for needed in (
            "--bg-card", "--surface-raised", "--border-strong",
            "--page-base-top", "--page-grid-x", "--page-glow-1",
            "--footer-wash", "--lace-dot",
            "--glass-strong", "--glass-inset", "--chip-bg", "--house-accent",
            "--chat-wash-top", "--input-glass-top", "--input-lace-dot",
            "--badge-bg-top", "--thinking-header",
            "--md-strong", "--md-em", "--md-link", "--md-code-bg",
            "--md-blockquote-border", "--md-hr",
        ):
            self.assertIn(needed, tokens)

    def test_goth_chrome_actually_leaves_pink(self):
        # The bug: Goth only changed bubbles/fonts. Its chrome must now be
        # genuinely dark — glass and page base included.
        chrome = settings._build_theme_tokens("goth", "fredoka")
        base = chrome["--page-base-top"]
        r, g, b = int(base[1:3], 16), int(base[3:5], 16), int(base[5:7], 16)
        self.assertLess((r + g + b) / 3, 60)
        self.assertNotEqual(chrome["--glass-strong"], settings._CHROME_TOKEN_DEFAULTS["--glass-strong"])
        self.assertNotEqual(chrome["--chip-bg"], settings._CHROME_TOKEN_DEFAULTS["--chip-bg"])

    def test_chrome_values_are_well_formed_css_constants(self):
        # Chrome values may be rgba()/var()/color-mix() but are server-side
        # constants; keep them within a strict shape so nothing weird can
        # ever ride an inline style property.
        all_maps = [settings._CHROME_TOKEN_DEFAULTS] + [
            p.get("chrome", {}) for p in settings._THEME_PRESETS.values()
        ]
        for m in all_maps:
            for key, value in m.items():
                self.assertTrue(key.startswith("--"), key)
                self.assertRegex(value, self._CHROME_VALUE_RE, f"{key}={value}")

    def test_default_preset_chrome_matches_stylesheet_defaults(self):
        # Pink & Fluffy must stay pixel-identical: its built tokens are
        # exactly the chrome defaults (which mirror main.css :root) plus its
        # original core tokens.
        built = settings._build_theme_tokens("sunrise-pink", settings._DEFAULT_THEME_FONT)
        for key, value in settings._CHROME_TOKEN_DEFAULTS.items():
            if key in settings._THEME_PRESETS["sunrise-pink"]["tokens"]:
                continue
            self.assertEqual(built[key], value, key)

    def test_contrast_pass_tokens_exist_in_every_built_preset(self):


        needed = (
            "--sheen-rgb", "--card-base-rgb", "--card-flat-bg",
            "--section-header", "--accent-script", "--accent-weave-rgb",
            "--lane-border", "--lane-bg-top", "--lane-bg-bottom",
            "--disabled-opacity", "--meta-theme-color",
            "--assistant-bubble-mode", "--assistant-bubble-top",
            "--assistant-bubble-bottom", "--assistant-bubble-night",
            "--assistant-bubble-text",
        )
        for preset_key in settings._THEME_PRESETS:
            tokens = settings._build_theme_tokens(preset_key, "fredoka")
            for key in needed:
                self.assertIn(key, tokens, f"{preset_key} missing {key}")

    def test_dark_presets_flip_the_card_sheen_dark(self):
        # The Goth-on-Android bug: white sheen gradients washed dark cards
        # back to pale gray. Dark presets must carry a dark sheen triplet.
        for preset_key in ("goth", "halloween"):
            sheen = settings._build_theme_tokens(preset_key, "fredoka")["--sheen-rgb"]
            r, g, b = (int(p.strip()) for p in sheen.split(","))
            self.assertLess((r + g + b) / 3, 80, f"{preset_key} sheen should be dark")

    def test_preset_assistant_bubbles_contrast_and_opt_in(self):
        # Goth/Halloween/Fall repaint the assistant bubble (mode 'preset');
        # bubble text must genuinely contrast against the bubble top.
        for preset_key in ("goth", "halloween", "fall"):
            tokens = settings._build_theme_tokens(preset_key, "fredoka")
            self.assertEqual(tokens["--assistant-bubble-mode"], "preset", preset_key)
            diff = abs(
                _relative_luminance(tokens["--assistant-bubble-top"])
                - _relative_luminance(tokens["--assistant-bubble-text"])
            )
            self.assertGreater(diff, 0.35, preset_key)
        # Default stays identity-owned: no preset repaint of the boys' bubbles.
        default = settings._build_theme_tokens("sunrise-pink", "fredoka")
        self.assertEqual(default["--assistant-bubble-mode"], "identity")

    def test_every_preset_has_a_valid_meta_theme_color(self):
        for preset_key in settings._THEME_PRESETS:
            val = settings._build_theme_tokens(preset_key, "fredoka")["--meta-theme-color"]
            self.assertRegex(val, r"^#[0-9A-Fa-f]{6}$", preset_key)

    def test_seasonal_presets_have_contrasting_user_bubble_text(self):
        for key in ("halloween", "fall"):
            tokens = settings._THEME_PRESETS[key]["tokens"]
            diff = abs(
                _relative_luminance(tokens["--user-bubble-top"])
                - _relative_luminance(tokens["--user-bubble-text"])
            )
            self.assertGreater(diff, 0.35, key)


class BuildThemeTokensTests(unittest.TestCase):
    def test_merges_preset_colors_with_font_stack(self):
        tokens = settings._build_theme_tokens("goth", "lora")
        self.assertEqual(tokens["--bg-page"], settings._THEME_PRESETS["goth"]["tokens"]["--bg-page"])
        self.assertIn("Lora", tokens["--font-body"])

    def test_unknown_preset_falls_back_to_default(self):
        tokens = settings._build_theme_tokens("not-a-real-preset", "fredoka")
        default_tokens = settings._build_theme_tokens(settings._DEFAULT_THEME_PRESET, "fredoka")
        self.assertEqual(tokens["--bg-page"], default_tokens["--bg-page"])

    def test_unknown_font_falls_back_to_default_stack(self):
        tokens = settings._build_theme_tokens("goth", "not-a-real-font")
        default_tokens = settings._build_theme_tokens("goth", settings._DEFAULT_THEME_FONT)
        self.assertEqual(tokens["--font-body"], default_tokens["--font-body"])

    def test_font_choice_is_independent_of_preset_choice(self):
        # Same font, different presets -> same font stack, different colors.
        pink = settings._build_theme_tokens("sunrise-pink", "kalam")
        goth = settings._build_theme_tokens("goth", "kalam")
        self.assertEqual(pink["--font-body"], goth["--font-body"])
        self.assertNotEqual(pink["--bg-page"], goth["--bg-page"])


class GetThemeTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_defaults_when_unset(self):
        with patch.object(settings, "_get_setting", side_effect=lambda key, default: default):
            result = await settings.get_theme()
        self.assertEqual(result["preset"], settings._DEFAULT_THEME_PRESET)
        self.assertEqual(result["font"], settings._DEFAULT_THEME_FONT)
        self.assertEqual(len(result["available"]), len(settings._THEME_PRESETS))
        self.assertEqual(len(result["font_options"]), len(settings._THEME_FONT_OPTIONS))

    async def test_falls_back_to_default_on_corrupt_stored_preset(self):
        async def _fake_get(key, default):
            if key == "theme_preset":
                return "not-a-real-preset"
            return default
        with patch.object(settings, "_get_setting", side_effect=_fake_get):
            result = await settings.get_theme()
        self.assertEqual(result["preset"], settings._DEFAULT_THEME_PRESET)

    async def test_falls_back_to_default_on_corrupt_stored_font(self):
        async def _fake_get(key, default):
            if key == "theme_font":
                return "not-a-real-font"
            return default
        with patch.object(settings, "_get_setting", side_effect=_fake_get):
            result = await settings.get_theme()
        self.assertEqual(result["font"], settings._DEFAULT_THEME_FONT)

    async def test_returns_stored_values_when_set(self):
        async def _fake_get(key, default):
            return {"theme_preset": "goth", "theme_font": "lora"}.get(key, default)
        with patch.object(settings, "_get_setting", side_effect=_fake_get):
            result = await settings.get_theme()
        self.assertEqual(result["preset"], "goth")
        self.assertEqual(result["font"], "lora")
        self.assertEqual(result["tokens"], settings._build_theme_tokens("goth", "lora"))


class SetThemeTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_payload_persists_and_returns_tokens(self):
        with patch.object(settings, "_set_setting", return_value=None) as set_mock, patch(
            "services.connection_registry.broadcast", return_value=None,
        ), patch("services.task_manager.spawn", return_value=None):
            response = await settings.set_theme(
                _FakeRequest({"preset": "eighties-neon", "font": "nunito"})
            )

        self.assertEqual(response["ok"], True)
        self.assertEqual(response["preset"], "eighties-neon")
        self.assertEqual(response["font"], "nunito")
        self.assertIn("--bg-page", response["tokens"])
        set_mock.assert_any_await("theme_preset", "eighties-neon")
        set_mock.assert_any_await("theme_font", "nunito")

    async def test_unknown_preset_key_is_rejected(self):
        response = await settings.set_theme(_FakeRequest({"preset": "stark-blue", "font": "fredoka"}))
        self.assertIsInstance(response, JSONResponse)
        self.assertEqual(response.status_code, 400)

    async def test_unknown_font_key_is_rejected(self):
        response = await settings.set_theme(_FakeRequest({"preset": "goth", "font": "comic-sans"}))
        self.assertIsInstance(response, JSONResponse)
        self.assertEqual(response.status_code, 400)

    async def test_raw_color_value_has_no_field_to_land_in(self):
        # There is no "color"/"hex" field in the payload contract at all --
        # a client trying to submit one is simply ignored.
        with patch.object(settings, "_set_setting", return_value=None), patch(
            "services.connection_registry.broadcast", return_value=None,
        ), patch("services.task_manager.spawn", return_value=None):
            response = await settings.set_theme(
                _FakeRequest({"preset": "sunrise-pink", "font": "fredoka", "hex": "#0000FF"})
            )
        self.assertEqual(response["ok"], True)
        self.assertNotIn("#0000FF", str(response["tokens"]))

    async def test_missing_preset_field_uses_default(self):
        with patch.object(settings, "_set_setting", return_value=None), patch(
            "services.connection_registry.broadcast", return_value=None,
        ), patch("services.task_manager.spawn", return_value=None):
            response = await settings.set_theme(_FakeRequest({"font": "lora"}))
        self.assertEqual(response["ok"], True)
        self.assertEqual(response["preset"], settings._DEFAULT_THEME_PRESET)
        self.assertEqual(response["font"], "lora")

    async def test_missing_font_field_uses_default(self):
        with patch.object(settings, "_set_setting", return_value=None), patch(
            "services.connection_registry.broadcast", return_value=None,
        ), patch("services.task_manager.spawn", return_value=None):
            response = await settings.set_theme(_FakeRequest({"preset": "goth"}))
        self.assertEqual(response["ok"], True)
        self.assertEqual(response["preset"], "goth")
        self.assertEqual(response["font"], settings._DEFAULT_THEME_FONT)

    async def test_invalid_json_body_is_rejected(self):
        response = await settings.set_theme(_FakeRequest(ValueError("bad json")))
        self.assertIsInstance(response, JSONResponse)
        self.assertEqual(response.status_code, 400)

    async def test_keys_are_case_insensitive(self):
        with patch.object(settings, "_set_setting", return_value=None), patch(
            "services.connection_registry.broadcast", return_value=None,
        ), patch("services.task_manager.spawn", return_value=None):
            response = await settings.set_theme(_FakeRequest({"preset": "GOTH", "font": "LORA"}))
        self.assertEqual(response["ok"], True)
        self.assertEqual(response["preset"], "goth")
        self.assertEqual(response["font"], "lora")

    async def test_broadcast_failure_never_breaks_the_save(self):
        with patch.object(settings, "_set_setting", return_value=None), patch(
            "services.connection_registry.broadcast", side_effect=RuntimeError("boom"),
        ):
            response = await settings.set_theme(_FakeRequest({"preset": "sunrise-pink", "font": "fredoka"}))
        self.assertEqual(response["ok"], True)


class ThemeIconBaseTests(unittest.IsolatedAsyncioTestCase):
    """Themeiconbasetests."""

    async def test_valid_https_base_persists_and_strips_trailing_slash(self):
        with patch.object(settings, "_set_setting", return_value=None) as set_mock, patch(
            "services.connection_registry.broadcast", return_value=None,
        ), patch("services.task_manager.spawn", return_value=None):
            response = await settings.set_theme(
                _FakeRequest({
                    "preset": "goth", "font": "lora",
                    "icon_base": "https://cdn.example.com/buttons/",
                })
            )
        self.assertEqual(response["ok"], True)
        self.assertEqual(response["icon_base"], "https://cdn.example.com/buttons")
        set_mock.assert_any_await(settings._ICON_BASE_SETTING_KEY, "https://cdn.example.com/buttons")

    async def test_bad_url_is_rejected_with_400(self):
        with patch.object(settings, "_set_setting", return_value=None), patch(
            "services.connection_registry.broadcast", return_value=None,
        ), patch("services.task_manager.spawn", return_value=None):
            response = await settings.set_theme(
                _FakeRequest({"preset": "goth", "font": "lora", "icon_base": "not a url"})
            )
        self.assertIsInstance(response, JSONResponse)
        self.assertEqual(response.status_code, 400)

    async def test_javascript_scheme_is_rejected(self):
        # The whole reason the base is allowlisted: a hostile value must never
        # reach an attribute. javascript: fails ^https?:// so it 400s.
        with patch.object(settings, "_set_setting", return_value=None), patch(
            "services.connection_registry.broadcast", return_value=None,
        ), patch("services.task_manager.spawn", return_value=None):
            response = await settings.set_theme(
                _FakeRequest({"icon_base": "javascript:alert(1)"})
            )
        self.assertIsInstance(response, JSONResponse)
        self.assertEqual(response.status_code, 400)

    async def test_empty_string_clears_the_base(self):
        with patch.object(settings, "_set_setting", return_value=None) as set_mock, patch(
            "services.connection_registry.broadcast", return_value=None,
        ), patch("services.task_manager.spawn", return_value=None):
            response = await settings.set_theme(
                _FakeRequest({"preset": "goth", "font": "lora", "icon_base": ""})
            )
        self.assertEqual(response["ok"], True)
        self.assertEqual(response["icon_base"], "")
        set_mock.assert_any_await(settings._ICON_BASE_SETTING_KEY, "")

    async def test_omitted_key_never_touches_the_stored_base(self):
        # A plain preset/font save must not clobber a previously-set base.
        with patch.object(settings, "_set_setting", return_value=None) as set_mock, patch(
            "services.connection_registry.broadcast", return_value=None,
        ), patch("services.task_manager.spawn", return_value=None):
            response = await settings.set_theme(
                _FakeRequest({"preset": "goth", "font": "lora"})
            )
        self.assertEqual(response["ok"], True)
        self.assertNotIn("icon_base", response)
        awaited_keys = [call.args[0] for call in set_mock.await_args_list]
        self.assertNotIn(settings._ICON_BASE_SETTING_KEY, awaited_keys)


class AssistantBubbleOverrideTests(unittest.IsolatedAsyncioTestCase):
    """Assistantbubbleoverridetests."""

    def _identity(self):
        from config import IDENTITIES
        return next(iter(IDENTITIES))

    async def test_valid_override_persists_and_resolves_tokens(self):
        saved = {}

        async def _fake_set(key, value):
            saved[key] = value

        with patch.object(settings, "_get_setting", side_effect=lambda key, default: default), patch.object(
            settings, "_set_setting", side_effect=_fake_set,
        ), patch("services.connection_registry.broadcast", return_value=None), patch(
            "services.task_manager.spawn", return_value=None,
        ):
            response = await settings.set_theme_bubbles(
                _FakeRequest({"identity": self._identity(), "bg": "#AABBCC", "text": "#112233", "font": "lora"})
            )

        self.assertEqual(response["ok"], True)
        tokens = response["bubble_tokens"][self._identity()]
        self.assertEqual(tokens["--identity-bubble-top"], "#AABBCC")
        self.assertEqual(tokens["--identity-bubble-text"], "#112233")
        self.assertIn("Lora", tokens["--identity-bubble-font"])
        # Bottom + night shades are derived darker versions of bg.
        self.assertIn("--identity-bubble-bottom", tokens)
        self.assertIn("--identity-bubble-night", tokens)
        self.assertIn(settings._BUBBLE_SETTING_KEY, saved)

    async def test_bad_hex_is_rejected(self):
        for bad in ("#GGGGGG", "red", "#12345", "#1234567", "url(evil)", "#AABBCC;color:red"):
            response = await settings.set_theme_bubbles(
                _FakeRequest({"identity": self._identity(), "bg": bad})
            )
            self.assertIsInstance(response, JSONResponse, bad)
            self.assertEqual(response.status_code, 400, bad)

    async def test_unknown_identity_is_rejected(self):
        response = await settings.set_theme_bubbles(
            _FakeRequest({"identity": "NotABoy", "bg": "#AABBCC"})
        )
        self.assertIsInstance(response, JSONResponse)
        self.assertEqual(response.status_code, 400)

    async def test_unknown_font_is_rejected(self):
        response = await settings.set_theme_bubbles(
            _FakeRequest({"identity": self._identity(), "font": "comic-sans"})
        )
        self.assertIsInstance(response, JSONResponse)
        self.assertEqual(response.status_code, 400)

    async def test_all_fields_cleared_removes_the_entry(self):
        import json as _json
        stored = {settings._BUBBLE_SETTING_KEY: _json.dumps({self._identity(): {"bg": "#AABBCC"}})}

        async def _fake_get(key, default):
            return stored.get(key, default)

        async def _fake_set(key, value):
            stored[key] = value

        with patch.object(settings, "_get_setting", side_effect=_fake_get), patch.object(
            settings, "_set_setting", side_effect=_fake_set,
        ), patch("services.connection_registry.broadcast", return_value=None), patch(
            "services.task_manager.spawn", return_value=None,
        ):
            response = await settings.set_theme_bubbles(
                _FakeRequest({"identity": self._identity(), "bg": None, "text": None, "font": None})
            )

        self.assertEqual(response["ok"], True)
        self.assertEqual(response["bubbles"], {})
        self.assertEqual(response["bubble_tokens"], {})

    async def test_no_override_means_no_tokens_default_appearance(self):
        # Default = current appearance: with nothing stored, GET returns
        # empty override maps so the frontend touches nothing.
        with patch.object(settings, "_get_setting", side_effect=lambda key, default: default):
            result = await settings.get_theme()
        self.assertEqual(result["bubbles"], {})
        self.assertEqual(result["bubble_tokens"], {})
        self.assertTrue(result["identities"])

    def test_sanitize_drops_garbage(self):
        import json as _json
        raw = _json.dumps({
            "NotABoy": {"bg": "#AABBCC"},
            self._identity(): {"bg": "#ZZZZZZ", "text": "#112233", "font": "comic-sans", "evil": "x"},
        })
        clean = settings._sanitize_bubble_overrides(raw)
        self.assertEqual(clean, {self._identity(): {"text": "#112233"}})

    def test_sanitize_survives_corrupt_json(self):
        self.assertEqual(settings._sanitize_bubble_overrides("{not json"), {})
        self.assertEqual(settings._sanitize_bubble_overrides("[1,2]"), {})


if __name__ == "__main__":
    unittest.main()
