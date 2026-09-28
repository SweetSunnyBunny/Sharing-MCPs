# Refresh and verification tools

**Installing the applications? You can skip this folder.** Start with
[START_HERE.md](../START_HERE.md). These are maintainer tools for checking and
packaging a clean distribution.

## Check an untouched download

1. Install Python 3.11.
2. Open PowerShell in the main `Sharing-MCPs` folder, one level above this folder.
3. Run:

```powershell
py -3.11 scripts\verify_setups.py
```

Success prints a verified file count and 32 setup folders. A changed file, added
`.env`, cache or database makes it fail deliberately. Do not delete configuration
from a working installation to satisfy this distribution check.

## Build a ZIP of the reviewed files

From the same folder, after the verification passes:

```powershell
py -3.11 scripts\build_clean_archive.py --output ..\Sharing-MCPs-clean.zip
```

The ZIP appears beside the collection folder. The tool refuses to overwrite an
existing archive; choose a new name when needed. A successful build prints its
path and file count. It includes the verified manifest and excludes Git history.

## Reference for maintainers

`verify_setups.py` checks the hashes in the reviewed snapshot manifest and
rejects unreviewed additions or runtime files across the collection, including
its helpers and root files. Only the repository's own Git metadata is excluded.
Run it from any working directory. Hash verification confirms file integrity;
new content still needs its own privacy review.

`build_clean_archive.py --output PATH` verifies the manifest and packages only
its listed files, plus the manifest itself. Choose an output path outside the
repository. Git history, real env files, caches, and unreviewed additions are
not copied into the archive. Generic configuration examples remain included.

The public exporters deliberately contain no installation's private identity
map or credentials. Keep the source installation, private profiles, and export
staging directory separate from this repository.

## UI

The UI exporter is in `../ui/scripts/export_shareable_ui.py`. It requires explicit
`--src`, `--dst`, and `--profile` arguments and supports `--dry-run`.
The private JSON profile holds reviewed regex replacements, forbidden patterns,
exclusions, a binary allowlist, and an external directory of reviewed generic
replacement files. See its module documentation and `../ui/SHARING-NOTES.md`.

## Cloud setups

`export_shareable_setups.py` uses `cloud-public-overrides/` beside it for reusable
generic documentation, configuration, schema adaptations, and identity defaults.
Keep both together.

Supply these environment variables through your private shell configuration:

- `CLOUD_SETUP_SOURCE`: maintained cloud service source directory.
- `MACHINE_AGENT_SOURCE`: maintained machine-agent source directory.
- `COMMONS_WORKER_SOURCE`: maintained Commons connector Worker directory.
- `ELEVENLABS_WORKER_SOURCE`: maintained ElevenLabs Worker directory.
- `LIMBIC_SETUP_SOURCE`: maintained Limbic Worker directory.
- `SHARE_PRIVATE_REPLACEMENTS`: private JSON replacement map used by the exporter.
- `CLOUD_SETUP_DEST`: an empty, separate staging directory for the cloud collection.

Run `python scripts/export_shareable_setups.py --dry-run`, then the same command
without `--dry-run` to produce the staged collection. Review, test, and scan that
collection before replacing the corresponding folders here.

The cloud exporter writes a collection README and manages its destination tree.
Point it at a separate staging directory, rather than this repository root or a
live installation. This repository contains other independent MCP packages and
keeps the UI separate from the cloud setups.

After a reviewed refresh, regenerate the snapshot manifest from the reviewed
files and retain a record of the checks performed. A successful hash check from
an older snapshot does not certify newly generated content.
