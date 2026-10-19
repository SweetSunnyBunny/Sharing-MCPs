# Sharing collection update — 2026-10-19

There are now 37 independent packages. Added [Commons game](commons-game/README.md):
a fresh illustrated main house and garden, plus the existing 39 adventure areas,
their maps and game artwork. Private home content, saves, keys and personal
configuration are excluded. See its [export review](commons-game/EXPORT_NOTES.md).

Commons passed 63 isolated tests, browser/phone checks and a disposable AI
connection check. The assembled package passed privacy and asset checks. The
other 36 packages retain their previous reviewed versions described below.

## Previous collection refresh — 2026-10-18

There are 36 independent packages. Maintained working trees, including uncommitted
code, were checked. Private inventories and recovery copies stay outside this tree.

## Refreshed

- Anam UI: current provider/session/scheduler fixes, archive retrieval, read receipts,
  coloring, stickers, studio provenance, council and SMS API. Generic prompts,
  empty state and no SMS contacts; media housekeeping is opt-in.
- All 12 cloud/support packages. Commons has current OAuth bindings. Qualia includes
  redolence; Limbic retains persistent stop regulation with generic vocabulary.
  ElevenLabs includes current dialogue support.
- Photos includes a separate optional subscription image login and current reference
  handling. API remains the default; subscription has no automatic paid fallback.
- Current standalone Krita save/plugin handling, filesystem editing/previews,
  desktop code and terminal cleanup from the maintained machine tools.
- Audio includes codec fallback, silent-track detection and an optional spectrogram
  shelf while retaining portable accounts and explicitly selected browser profiles.

## Added

home-assistant-mcp, watch-mcp, anam-touch-mcp and crosstalk-mcp each include
configuration examples, dependencies, setup steps and offline contracts.

Existing independent packages were retained and audited. This refresh makes no
claim that every third-party package is at its latest upstream release.

## Verified

| Check | Result |
| --- | --- |
| UI Python | 1,434 passed, 8 skipped, plus 10 subtests |
| World Feed JavaScript and separate starter | 35 and 19 passed |
| Photos and audio/video | 22 and 23 passed |
| Local adapters and machine-agent | 4 and 5 passed |
| Discord, Qualia and Limbic | 18, 30 and 4 passed |
| TypeScript applications/Workers | Ten type checks |
| UI reproduction | Exact source-plus-reviewed-overlay parity |
| Distribution | Privacy scan, Python/JSON parsing, relative links and manifest hashes |
| Clean ZIP | Manifest-only archive, member hashes and integrity checked |

Total: 1,594 passing tests. Skips cover unbundled workstation supervision, private
archive migration and live Photos integration. These are offline checks; no live
device, production data, paid generation, model account or OAuth deployment was
used to validate this distribution.

## Future refreshes

The UI exporter accepts source-hash baselines for reviewed replacements and stops
when their maintained source changes. An old manifest passing cannot certify
source currency. This installation also has a private read-only source checker
that compares maintained trees with this reviewed baseline.

Cloud and UI exporters remain separate. Export to staging, reconcile public
defaults, test and review new content, then regenerate the manifest. Never use
the collection root as a cloud-export destination. Source paths and privacy
profiles stay private.

See [coverage](shared-docs/COVERAGE.md) for deliberate omissions: authored Commons
world, separate phone/browser adapters, other Android apps, whole-stack supervision
and personal skills/content.
