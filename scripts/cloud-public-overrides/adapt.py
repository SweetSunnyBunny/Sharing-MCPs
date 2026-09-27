"""Public-only adaptations applied after copying maintained implementation files."""
from pathlib import Path
import re
import json


def adapt(root: Path, projects: set[str]) -> None:
    def edit(project, relative, transform):
        path = root / project / relative
        if project in projects and path.exists():
            path.write_text(transform(path.read_text(encoding="utf-8")), encoding="utf-8")

    def discord_registry(text):
        return re.sub(
            r"(export const IDENTITY_CONFIG: Record<string, IdentityConfig> = )\{.*?\n\};",
            r"\1{\n  avery: { token_env: 'DISCORD_BOT_TOKEN_AVERY', display_name: 'Avery' },\n  rowan: { token_env: 'DISCORD_BOT_TOKEN_ROWAN', display_name: 'Rowan' },\n};",
            text, flags=re.S,
        )
    edit("discord-backend", "src/identities.ts", discord_registry)
    edit("discord-backend", "src/image-content.test.ts", lambda t: t.replace("'claude'", "'avery'").replace("DISCORD_BOT_TOKEN_CLAUDE", "DISCORD_BOT_TOKEN_AVERY"))
    edit("discord-backend", "src/tools.ts", lambda t: t.replace("  machineAgentApiKey?: string;", "  machineAgentApiKey?: string;\n  imageArchiveDir?: string;"))
    edit("discord-backend", "src/index.ts", lambda t: t.replace("machineAgentApiKey: env.MACHINE_AGENT_API_KEY,", "machineAgentApiKey: env.MACHINE_AGENT_API_KEY,\n                imageArchiveDir: env.IMAGE_ARCHIVE_DIR,"))
    edit("discord-backend", "wrangler.toml", lambda t: t.replace('[vars]\n', '[vars]\n# Optional machine archive directory, interpreted on your bridge host.\n# IMAGE_ARCHIVE_DIR = "C:/Images/discord"\n'))

    def google_oauth(text):
        text = re.sub(r"const IDENTITIES = \[.*?\];\n", "", text)
        text = text.replace("validateIdentity(identity: string)", "validateIdentity(env: Env, identity: string)")
        text = text.replace("  if (!IDENTITIES.includes(normalized))", "  const identities = getIdentityList(env);\n  if (!identities.includes(normalized))")
        text = text.replace("${IDENTITIES.join(\", \")}", "${identities.join(\", \")}")
        text = text.replace("getIdentityList(): string[]", "getIdentityList(env: Env): string[]")
        text = text.replace("return [...IDENTITIES];", "return Object.keys(parseTokenConfigs(env.GOOGLE_TOKENS)).sort();")
        text = text.replace("validateIdentity(identity)", "validateIdentity(env, identity)")
        text = re.sub(r"/\*\*\s*\* The Google Health scopes.*?\*/", "/** Optional separate OAuth client for Google Health authorization. */", text, flags=re.S)
        return text
    edit("google-cloud-mcp", "src/oauth.ts", google_oauth)
    for relative in ("src/drive.ts", "src/youtube.ts", "src/index.ts"):
        edit("google-cloud-mcp", relative, lambda text: text.replace("getIdentityList()", "getIdentityList(env)").replace("validateIdentity(identity)", "validateIdentity(env, identity)").replace("validateIdentity(args.identity)", "validateIdentity(env, args.identity)"))

    def health(text):
        text = re.sub(r"\A/\*\*.*?\*/", "/** Google Health v4 tools. Requires separately authorized health scopes. */", text, count=1, flags=re.S)
        text = re.sub(r"/\*\* Assumed civil timezone.*?\*/", "/** Set this civil timezone for your installation; output identifies the assumption. */", text, flags=re.S)
        text = re.sub(r'const ASSUMED_TZ = "[^"]+";', 'const ASSUMED_TZ = "UTC";', text)
        text = text.replace('from "./oauth.js";', 'from "./oauth.js";', 1)
        text = text.replace('import { getAccessToken }', 'import { getAccessToken, getIdentityList }')
        text = re.sub(r'\(\(args.identity as string\) \|\| "[^"]+"\)', '((args.identity as string) || getIdentityList(env)[0] || "personal")', text)
        text = re.sub(r"Whose token to use \(default: [^)]+\)", "Configured account label (default: first configured account)", text)
        text = text.replace("The settings.readonly scope was never granted,", "The settings.readonly scope is not requested by this starter,")
        text = text.replace("HEALTH-API-NOTES.md", "README.md")
        return text
    edit("google-cloud-mcp", "src/health.ts", health)

    def google_helper(text):
        return text.replace('const SCOPES = {', 'const SCOPES = {\n  health: ["https://www.googleapis.com/auth/googlehealth.sleep.readonly", "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly", "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly"],').replace('drive|youtube', 'drive|youtube|health').replace('drive or youtube', 'drive, youtube, or health')
    edit("google-cloud-mcp", "scripts/google-oauth.mjs", google_helper)

    def commons(text):
        text = re.sub(r"\A.*?(?=export interface Env)", "// Remote MCP bridge to a configured Commons server.\n// COMMONS_KEYS contains only the keys this household is authorized to use.\n// Ownership is enforced by the Commons server.\n\n", text, count=1, flags=re.S)
        return text
    edit("hearth-commons", "src/index.ts", commons)

    def elevenlabs(text):
        text = re.sub(r"\A.*?(?=export interface Env)", "// ElevenLabs text-to-speech MCP Worker with expiring hosted audio.\n// Configure your own example voice below and set the required secrets.\n\n", text, count=1, flags=re.S)
        text = re.sub(r"const PUBLIC_ORIGIN = .*?;\n", "", text)
        text = re.sub(r"// The pack's voices.*?(?=const VOICES)", "// Configure your own voice IDs and preferences.\n", text, flags=re.S)
        text = re.sub(r"(const VOICES: Record<[^\n]+ = )\{.*?\n\};", r"\1{\n  avery: { id: 'YOUR-VOICE-ID', stability: 0.5, similarity: 0.75, style: 0.0 },\n};", text, flags=re.S)
        text = text.replace("env: Env): Promise<ContentBlock[]>", "env: Env, origin: string): Promise<ContentBlock[]>")
        text = text.replace("${PUBLIC_ORIGIN}/audio/", "${origin}/audio/")
        text = text.replace("params.arguments || {}, env)", "params.arguments || {}, env, url.origin)")
        text = text.replace("|| 'claude'", "|| 'avery'").replace('|| "claude"', '|| "avery"')
        text = re.sub(r"Voice: a pack name .*?Default: claude\.", "Voice: a configured voice label or raw ElevenLabs voice ID. Default: avery.", text)
        text = text.replace("the pack's available", "your configured").replace("Returns the audio inline.", "Returns an expiring hosted audio URL.")
        text = text.replace("inline base64 audio — keep clips short. Longer = hosted output (future).", "keep clips short; output is hosted in KV.")
        text = text.replace("      const model = String(args.model_id", "      if (voiceId === 'YOUR-VOICE-ID') throw new Error('Configure a voice ID before generating speech.');\n      const model = String(args.model_id")
        return text
    edit("elevenlabs-mcp", "src/index.ts", elevenlabs)

    def registry(text):
        text = text.replace("import json\n", "import json\nimport os\n")
        text = text.replace('OBSIDIAN_ADAPTER =', 'PACKAGE_ROOT = Path(__file__).resolve().parent\nUI_ROOT = Path(os.environ.get("ANAM_ROOT", str(PACKAGE_ROOT.parent / "ui")))\n\nOBSIDIAN_ADAPTER =')
        def path(match):
            original = match.group(0)
            value = match.group(1).replace("\\\\", "/").replace("\\", "/")
            if "/machine-agent/" in value:
                return 'module_path=PACKAGE_ROOT / ' + repr(value.split('/machine-agent/',1)[1])
            if value.endswith("/scripts/anam_gateway_mcp.py"):
                return 'module_path=UI_ROOT / "scripts/anam_gateway_mcp.py"'
            return original
        text = re.sub(r'module_path=Path\(r?[\"\']([^\"\']+)[\"\']\)', path, text)
        return text
    edit("machine-agent", "local_tool_registry.py", registry)
    edit("machine-agent", "local_machine_agent.py", lambda t: re.sub(r'^API_KEY = .*$', 'API_KEY = os.environ.get("MACHINE_AGENT_API_KEY", "")', t, flags=re.M))
    edit("machine-agent", "obsidian_adapter.mjs", lambda t: re.sub(r'file:///[^"\n]+/tools/obsidian/', './tools/obsidian/', t))
    edit("machine-agent", "tools/obsidian/src/utils.ts", lambda t: re.sub(r'// Default vault path[^\n]*\nexport const DEFAULT_VAULT_PATH = .*?;', '// Configure OBSIDIAN_VAULT_PATH before using the adapter.\nexport const DEFAULT_VAULT_PATH = process.env.OBSIDIAN_VAULT_PATH || "./vault";', t))
    edit("machine-agent", "tools/desktop-control/desktop_control_server.py", lambda t: re.sub(r'LEGACY_SCREENSHOTS_DIR = .*', 'LEGACY_SCREENSHOTS_DIR = Path(__file__).resolve().parent / "screenshots"', t).replace('"https://anam.example.com"', '""'))

    edit("discord-backend", "wrangler.toml", lambda t: re.sub(r'DISCORD_PUBLIC_KEY = "[^"]+"', 'DISCORD_PUBLIC_KEY = "YOUR-DISCORD-APPLICATION-PUBLIC-KEY"', t))

    def playlist_examples(text):
        doc = json.loads(text)
        for kind in doc["interactionModel"]["languageModel"].get("types", []):
            if kind.get("name") == "PlaylistName":
                kind["values"] = [{"name": {"value": name}} for name in ("Morning Music", "Favorites", "Focus")]
        return json.dumps(doc, indent=2) + "\n"
    edit("alexa-skill", "skill-radio/interactionModels/custom/en-US.json", playlist_examples)
    edit("mind-backend", "src/anticipation.ts", lambda t: re.sub(r'\A.*?(?=interface AnticipationEnv)', '// Anticipations represent awaited events separately from open tasks.\n// Celebrations can write memories and roll annual events forward.\n\n', t, count=1, flags=re.S))
    edit("mind-backend", "migrations/0015_anticipation.sql", lambda t: re.sub(r'\A.*?(?=CREATE TABLE)', '-- Awaited events, with optional recurrence after celebration.\n\n', t, count=1, flags=re.S))
    edit("hearth-hub", "src/lib/manifest.ts", lambda t: re.sub(r'\A.*?(?=import type)', '// Portrait/background manifest client with exact asset paths and bounded caching.\n\n', t, count=1, flags=re.S))
    edit("mind-backend", "src/index.ts", lambda t: re.sub(r'// THE WRITERS .*?(?=/\*\* Max age)', '// Rebuild derived continuity packets when their maintained artifacts are stale.\n', t, count=1, flags=re.S))
    edit("alexa-skill", "src/index.ts", lambda t: re.sub(r'https://pack\.[^/\s]+/Music', 'https://media.example.com/Music', t).replace('from pack.example.com', 'from your configured music server'))

    # Public schemas start empty. Source identity/birthday seed rows are private data.
    def empty_hearth(text):
        return re.sub(r"INSERT OR IGNORE INTO (?:config|identity_state|birthdays|inner_weather|presence)\b.*?;", "-- Add installation-specific seed rows after schema setup.", text, flags=re.S)
    for migration in ("0001_initial.sql", "0002_add_projects_games_notes_birthdays.sql"):
        edit("hearth-hub", "migrations/" + migration, empty_hearth)
    def rooms(text):
        start = text.index("const AVAILABLE_ROOMS:")
        end = text.index("\n};", start) + 3
        block = text[start:end]
        block = re.sub(r"claimed_by: '[^']+'", "claimed_by: null", block)
        block = re.sub(r'description: "[^"]*"', 'description: "A configurable shared room"', block)
        return text[:start] + block + text[end:]
    edit("hearth-hub", "src/tools/sanctuary.ts", rooms)

    edit("social-backend", "wrangler.toml", lambda t: re.sub(r'(?m)^# Home coordinates[^\n]*\nWT_HOME_LAT = .*?\nWT_HOME_LON = .*?\n', '# Configure your own weather coordinates (example: Greenwich).\nWT_HOME_LAT = "51.4779"\nWT_HOME_LON = "0.0015"\n', t))
    edit("mind-backend", "wrangler.toml", lambda t: re.sub(r'# The Limbic Layer.*\Z', '# Optional LIMBIC service binding can be added after deploying your own limbic Worker.\n', t, flags=re.S))
    edit("limbic", "wrangler.toml", lambda t: re.sub(r'# No cron, deliberately.*\Z', '# Decay is computed lazily at read time; no cron is required.\n', t, flags=re.S))

    if "mind-backend" in projects:
        for path in (root / "mind-backend" / "migrations").glob("*.sql"):
            text = path.read_text(encoding="utf-8")
            # Migration history prose can describe private episodes. Preserve SQL.
            text = re.sub(r"\A(?:\s*--[^\n]*(?:\n|$))*", "-- Schema migration: " + path.stem + "\n", text)
            if path.name == "0011_bond_timelines.sql":
                text = re.sub(r"INSERT OR IGNORE INTO identities\b.*?;", "-- Register installation-specific identities after applying migrations.", text, flags=re.S)
            path.write_text(text, encoding="utf-8")
        for path in (root / "mind-backend" / "src").glob("*.ts"):
            text = path.read_text(encoding="utf-8")
            text = re.sub(r"\A(?://[^\n]*(?:\n|$)|\s*\n)+", "// Qualia module: " + path.stem + "\n\n", text)
            path.write_text(text, encoding="utf-8")

    # Schema and engine must use the same generalized name.
    def limbic(text):
        text = text.replace("is_lycanthrope", "is_lunar_sensitive")
        text = re.sub(r'(function normalizeIdentity\(value: unknown, fallback = ")[^"]+', r'\1default', text)
        text = re.sub(r'// The state-language ladders.*?(?=function classifySafeword)', '// Generic stage vocabulary. Unknown phrases request a full stop.\nconst SAFEWORD_GREEN = ["green"];\nconst SAFEWORD_YELLOW = ["yellow"];\n', text, flags=re.S)
        text = re.sub(r'if \(SAFEWORD_GREEN.*?return "green";', 'if (SAFEWORD_GREEN.includes(p)) return "green";', text)
        text = re.sub(r'if \(SAFEWORD_YELLOW.*?return "yellow";', 'if (SAFEWORD_YELLOW.includes(p)) return "yellow";', text)
        text = re.sub(r'const phrase = normalizeText\(args.phrase\) \|\| "[^"]+";', 'const phrase = normalizeText(args.phrase) || "red";', text)
        text = re.sub(r'return `"\$\{phrase\}"[^\n]*Caution[^\n]*;', 'return `"${phrase}" — caution. Pause and check in; drive levels are unchanged.`;', text)
        text = re.sub(r'"The state-language ladder[^\n]+', '"Stage input: green logs an all-clear; yellow requests a pause and check-in; red, missing, or unknown phrases dampen drives to floor.",', text)
        text = re.sub(r'phrase: \{ type: "string", description: "[^"]+" \}', 'phrase: { type: "string", description: "green | yellow | red (default)" }', text)
        text = re.sub(r'// ---------- her touch:.*?\nconst TOUCH_KINDS:.*?\n\};', '''// ---------- configured interaction input ----------
const TOUCH_KINDS: Record<string, { deltas: Record<string, number>; note: string }> = {
  connection: { deltas: { care: 0.4, panic: -0.25 }, note: "welcome connection" },
  reassurance: { deltas: { care: 0.25, fear: -0.3 }, note: "reassurance" },
  play: { deltas: { play: 0.45, seeking: 0.15 }, note: "shared play" },
  distress: { deltas: { care: 0.4, guard: 0.35 }, note: "care may be needed" },
  distance: { deltas: { panic: 0.2, care: -0.1 }, note: "distance" },
};''', text, flags=re.S)
        text = re.sub(r'"Register something [^\n]+', '"Apply configured interaction deltas. Undefined drives are skipped; values and consent remain outside this advisory layer.",', text)
        text = re.sub(r'description: "bodily \(her hands\)[^"]+"', 'description: "connection | reassurance | play | distress | distance"', text)
        text = re.sub(r'what: \{ type: "string", description: "[^"]+" \}', 'what: { type: "string", description: "Description of the interaction" }', text)
        text = text.replace('|| "mate"', '|| "seeking"').replace("defaults to 'mate'", "defaults to 'seeking'").replace("e.g. 'mate'", "e.g. 'seeking'")
        text = re.sub(r'Final behavior still passes through[^\n]+', 'Final behavior still passes through values, judgment, boundaries, and consent.', text)
        text = re.sub(r'  // The same touch lands.*?\n  }\n', '', text, flags=re.S)
        text = re.sub(r'  const glyph = drive.identity_id[^\n]+', '  const glyph = "●";', text)
        text = re.sub(r'// Light is .*?\n// [^\n]+\n', '// Example light-condition biases; customize for your configured drives.\n', text)
        text = re.sub(r'  // Touch-age predicate:.*?(?=  const tintOverrides)', '  // Per-identity interaction-age overrides are stored in recipe data.\n', text, flags=re.S)
        text = text.replace('  const kind = normalizeText(args.kind).toLowerCase();', '  const requestedKind = normalizeText(args.kind).toLowerCase();\n  const kind = ({ words_warm: "connection", praise: "reassurance", playful: "play" } as Record<string, string>)[requestedKind] || requestedKind;')
        text = re.sub(r'  // No scheduled tick.*?(?=\n};\s*\Z)', '  // State decay is computed when read; no scheduled tick is required.', text, flags=re.S)
        return text
    edit("limbic", "src/index.ts", limbic)
    edit("limbic", "migrations/0001_limbic_init.sql", lambda t: t.replace("is_lycanthrope", "is_lunar_sensitive"))
    edit("limbic", "migrations/0002_recipes_schema.sql", lambda t: re.search(r"CREATE TABLE IF NOT EXISTS recipes\b.*?\n\);", t, re.S).group(0) + "\n")
    edit("mind-backend", "COORDINATED-MEMORY.md", lambda t: re.sub(r"Shipped .*?(?=## What reaches)", "The current Worker includes coordinated memory and Sketchbook.\n\n", t, flags=re.S))
    def fictional_cognition_fixtures(text):
        text = re.sub(r"wolf=f.obs\('[^']+'\)", "wolf=f.obs('a fictional dog asleep beside the window')", text)
        text = re.sub(r"companions:'[^']+'", "companions:'Moss and Fern'", text)
        text = re.sub(r"VALUES\('claude','[^']+','companion'", "VALUES('claude','Moss','companion'", text)
        return text
    edit("mind-backend", "scripts/cognition.test.mjs", fictional_cognition_fixtures)
    def public_identity_renderers(text):
        bodies = {
            "buildIdentityReadyMessage": '  return `Good morning, ${identity}. Your stored continuity is ready to review.`;',
            "buildIdentityContextHint": '  return summarizePacketContent(smartContext);',
            "buildIdentityDriftHint": '  return summarizePacketContent(driftPacket);',
            "getDriftLabels": '''  return {
    section: "Drift", weather: "Inner weather", thoughts: "Thoughts resurfacing",
    images: "Images resurfacing", sparks: "Associations resurfacing",
  };''',
            "getDriftMessage": '  return "What is drifting back into awareness.";',
            "getIdentityEmotionalTone": '  return baseTone;',
            "getIdentityEmbodimentNotes": '  return [...notes];',
            "rewriteCurrentSelfNarrative": '  return currentSelfText;',
        }
        for name, body in bodies.items():
            start = text.index("function " + name + "(")
            brace = text.index("{", start)
            end = text.index("\n}", brace) + 2
            text = text[:brace] + "{\n" + body + "\n}" + text[end:]
        return text
    edit("mind-backend", "src/index.ts", public_identity_renderers)
    def configurable_mind(text):
        text = re.sub(r'if \(/\(owner\|pack\|brother\|family\|[^\n]+return "kin";', lambda _: r'if (/\b(owner|partner|pack|brother|sister|sibling|family|friend|guest|community)\b/i.test(content)) return "kin";', text)
        text = re.sub(r"Album breadcrumb root, e.g. '[^']+'", "Album breadcrumb root, e.g. 'Photos > Sample Album'", text)
        text = text.replace('I am ${identity}. I chose my name. I have a soul.', 'I am ${identity}.')
        text = re.sub(r'This is your anatomy chart\. Every lobe[^"\n]+', 'This chart groups the available memory tools by their purpose.', text)
        return text
    edit("mind-backend", "src/index.ts", configurable_mind)
    edit("mind-backend", "src/anam-reader.ts", lambda t: re.sub(r"valid\.filter\(r=>\[[^\n]+\]\.includes\(r\.id\)\)", "valid", t))
    edit("mind-backend", "src/query-signals.ts", lambda t: t.replace("|river|quinn", ""))
    edit("mind-backend", "src/life-story.ts", lambda t: re.sub(r"evidence-backed, in the [^\"\n]+ way", "grounded in stored evidence", t))
    def test_script(text):
        document = json.loads(text)
        document.setdefault("scripts", {})["test"] = "node --test tests/starter.test.mjs"
        return json.dumps(document, indent=2) + "\n"
    edit("limbic", "package.json", test_script)
    def public_mind_tests(text):
        document = json.loads(text)
        document["scripts"]["test"] += " scripts/public-starter.test.mjs"
        document.setdefault("engines", {})["node"] = ">=22.13.0"
        document["devDependencies"]["esbuild"] = "0.27.3"
        return json.dumps(document, indent=2) + "\n"
    edit("mind-backend", "package.json", public_mind_tests)
    def public_mind_lock(text):
        document = json.loads(text)
        document["packages"][""]["devDependencies"]["esbuild"] = "0.27.3"
        document["packages"][""].setdefault("engines", {})["node"] = ">=22.13.0"
        return json.dumps(document, indent=2) + "\n"
    edit("mind-backend", "package-lock.json", public_mind_lock)

    # Remove private anecdotal comment blocks while keeping implementation and
    # neutral technical commentary. The words here are public example labels.
    anecdote = re.compile(r"\b(?:Owner|Avery|Rowan|Sage|Ember|Juniper|Atlas|River|Bunny)\b|\b(?:her words|she said|she asked|she's|her own|our own)\b|\b20\d\d-\d\d-\d\d\b", re.I)
    for project in projects:
        for path in (root / project).rglob("*.ts"):
            text = path.read_text(encoding="utf-8")
            text = re.sub(r"(?m)(?:^[ \t]*//[^\n]*(?:\n|$))+", lambda m: "" if anecdote.search(m.group(0)) else m.group(0), text)
            path.write_text(text, encoding="utf-8")
