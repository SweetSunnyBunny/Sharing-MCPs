# Anam tests

## The one rule: never write the live `data/` folder

Tests must never touch the live runtime (`data/anam.db`, `data/images/`, logs, the
Vault). `tests/conftest.py` enforces this for the Python suite: before `config` is
imported anywhere it points `ANAM_DATA_DIR` and `ANAM_DB_PATH` at a throwaway temp
directory, so migrations, log rotation, and generated media land there instead.
Any new test or helper must keep going through `config` paths (never a hardcoded
`data/...` path) so that redirect keeps working. Browser tests only talk to a
disposable preview server or to synthetic pages, never to the live server on 8790.

## Python suite

Use the same Python the stack runs on (the system Python 3.11 named by `PY_EXE` in
`C:/path/to\example.com\scripts\common-env.bat`), not the stale repo `.venv`:

```powershell
cd C:/Apps/anam
python -m pytest tests -q -p no:cacheprovider          # whole suite
python -m pytest tests -x -q -p no:cacheprovider       # stop on first failure
python -m pytest tests -q -p no:cacheprovider -k timer # a subset by keyword
```

## JavaScript tests (Node, no browser)

```powershell
node --test tests/test_world_feed_ui.cjs tests/test_frontend_loading.mjs
```

`tests/test_gateway_worker.mjs` imports
`data/gateway-connector-staging/cloudflare/workers/mcp-worker.js`, which is
gitignored, so it only runs on this machine (`node --test tests/test_gateway_worker.mjs`).

## Browser tests (Playwright)

These need a Playwright install. The one on this machine lives inside the global
`@playwright/mcp` package.

**`test_chat_reading_browser.cjs`** loads Playwright from `ANAM_PLAYWRIGHT_MODULE`
and runs against synthetic messages only (no server needed):

```powershell
$env:ANAM_PLAYWRIGHT_MODULE = "C:/Users/YOU/AppData/Roaming/npm/node_modules/@playwright/mcp/node_modules/playwright"
node tests/test_chat_reading_browser.cjs
```

**The three World Feed browser tests** use `require('playwright')`, so point
`NODE_PATH` at the same install, and start the preview server first.
`scripts/preview_world_feed.py` serves the World Feed on loopback against a
temporary copy of the database, so edits, follows, and alerts never reach the live
DB. Each test reads `WORLD_FEED_PREVIEW` if set; otherwise it uses the port below.

| Test | Default preview port |
| --- | --- |
| `test_world_feed_dms_browser.cjs` | 8897 |
| `test_world_feed_upload_browser.cjs` | 8898 |
| `test_world_feed_notifications_browser.cjs` | 8898 |

```powershell
# terminal 1 (and a second one with --port 8898)
python scripts/preview_world_feed.py --port 8897

# terminal 2
$env:NODE_PATH = "C:/Users/YOU/AppData/Roaming/npm/node_modules/@playwright/mcp/node_modules"
node tests/test_world_feed_dms_browser.cjs
node tests/test_world_feed_upload_browser.cjs
node tests/test_world_feed_notifications_browser.cjs
```

The World Feed browser tests launch Chrome (`channel: 'chrome'`) headless.
