# Qualia checks and optional retrieval evaluation

For installation, follow [the main guide](../README.md). You do not need to
run every file in this directory separately. `npm test` from the `mind-backend`
folder runs the supplied local reader, cognition, Sketchbook and public-starter
regressions. They use temporary databases/fixtures and no live credentials.

## Run the normal checks

Open the **parent `mind-backend` folder** in File Explorer, type `powershell` in
the address bar and press Enter. With Node.js 24 installed:

```powershell
npm ci
npm run typecheck
npm test
```

Success means the tests finish with no failures. Do not run from `scripts`:
several tests deliberately resolve `src` and `migrations` from the package root.
If Node cannot load `node:sqlite`, upgrade to Node 24 and reopen PowerShell.

## Optional: measure retrieval against your own test data

`retrieval_benchmark.py` measures whether `mind_search` returns the expected
memories. This is useful after changing retrieval settings, not a prerequisite
for installation. It calls the deployed Worker and may use its embedding/search
services. You need Python 3.11 and an already working Qualia deployment.

From the same `mind-backend` folder:

```powershell
py -3.11 .\scripts\retrieval_benchmark.py --help
Copy-Item .\scripts\retrieval_eval_set.example.json .\scripts\retrieval_eval_set.local.json
notepad .\scripts\retrieval_eval_set.local.json
```

Replace the fictional queries, identity IDs and expected fragments with records
you deliberately created in your own test database. An empty starter database
cannot pass a benchmark expecting remembered events. Keep the edited evaluation
file private if it contains any personal information.

```powershell
$QualiaUrl = Read-Host "Paste your Worker origin, without /mcp"
$QualiaKey = Read-Host "Paste your MIND_API_KEY"
py -3.11 .\scripts\retrieval_benchmark.py --url $QualiaUrl --key $QualiaKey --eval .\scripts\retrieval_eval_set.local.json
```

The report shows hit/recall/ranking measurements against your expectations.
A zero score is a retrieval/evaluation result, not proof installation failed.
The key is passed as a process argument by this existing helper; run it only on
your own trusted machine. Use `--profile-a flat --profile-b native` for an A/B
comparison of those profiles.

`text_normalize.py` contains reusable normalization helpers. Private import,
one-off repair, backups and personal seed scripts are intentionally absent.
Do not add those back merely to run the normal tests.
