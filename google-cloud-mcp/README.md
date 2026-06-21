# Google Cloud MCP Server

A self-hosted MCP server for Google Drive, Docs, Sheets, Calendar, and YouTube. It runs as a Cloudflare Worker, stores short-lived access tokens in D1, and keeps OAuth refresh tokens in encrypted Worker secrets.

This is intended for personal or small trusted deployments. Each person deploys their own copy into their own Cloudflare and Google Cloud accounts.

## Included tools

- Google Drive: browse, search, organize, share, trash, and delete files
- Google Docs: create, read, edit, replace text, and export
- Google Sheets: create spreadsheets and read, write, or append ranges
- Google Calendar: list, create, update, and delete events
- YouTube: search, inspect videos and comments, and manage playlists, ratings, subscriptions, and comments
- Multiple Google accounts through user-defined identity labels such as `personal` and `work`

The server exposes MCP over stateless HTTP at `/mcp` and `/mcp/<secret>`.

## Prerequisites

- Node.js 22 or newer
- A Cloudflare account with Workers and D1 available
- A Google account and Google Cloud project
- A local browser for the one-time OAuth authorization

## 1. Install the project

```bash
git clone <this-repository-url>
cd Sharing-MCPs/google-cloud-mcp
npm ci
npx wrangler login
```

Confirm that Wrangler shows the Cloudflare account you intend to use:

```bash
npx wrangler whoami
```

## 2. Configure Google Cloud

In the [Google Cloud Console](https://console.cloud.google.com/):

1. Create or select a project.
2. Enable the APIs you plan to use:
   - Google Drive API
   - Google Docs API
   - Google Sheets API
   - Google Calendar API
   - YouTube Data API v3
3. Open **Google Auth Platform** and configure the consent screen.
4. If the app is in testing mode, add every Google account you will authorize as a test user.
5. Create an OAuth client with application type **Desktop app**.
6. Download its JSON file to `scripts/client_secret.json`.

Google may require additional consent-screen configuration or app verification if you publish the OAuth app to other people. Testing mode with explicit test users is simpler for initial setup, but Google normally expires refresh tokens after seven days for external apps left in testing mode when non-basic scopes are requested. Move a stable personal deployment to production when you are ready, subject to Google's consent and verification requirements.

## 3. Generate refresh tokens

Choose any lowercase label for each Google account. Run the helper once per identity and service group:

```bash
npm run oauth -- --identity personal --service drive
npm run oauth -- --identity personal --service youtube
```

The `drive` authorization covers Drive, Docs, Sheets, and Calendar. The `youtube` authorization is separate. To add another account, repeat the commands with another label:

```bash
npm run oauth -- --identity work --service drive
```

The helper opens Google authorization in your browser and merges refresh tokens into `scripts/google_tokens.json`. Both credential files are ignored by git. Do not commit or share them.

## 4. Create the D1 database

```bash
npx wrangler d1 create google-cloud-mcp-tokens
```

Copy the `database_id` from Wrangler's output into `wrangler.toml`, replacing the all-zero placeholder. Then apply the migration:

```bash
npx wrangler d1 migrations apply google-cloud-mcp-tokens --remote
```

## 5. Set Worker secrets

Set each secret interactively. Wrangler prompts for the value without putting it in `wrangler.toml`.

```bash
npx wrangler secret put MCP_SECRET_PATH
npx wrangler secret put GOOGLE_CLIENT_ID
npx wrangler secret put GOOGLE_CLIENT_SECRET
npx wrangler secret put GOOGLE_TOKENS
```

Use:

- `MCP_SECRET_PATH`: a long random value, generated for example with `openssl rand -base64 32` or a password manager
- `GOOGLE_CLIENT_ID`: `client_id` from `scripts/client_secret.json`
- `GOOGLE_CLIENT_SECRET`: `client_secret` from that same file
- `GOOGLE_TOKENS`: the complete JSON contents of `scripts/google_tokens.json`

You can inspect configured secret names, but not their values, with:

```bash
npx wrangler secret list
```

## 6. Choose which tools to expose

`wrangler.toml` defaults to:

```toml
ALLOWED_TOOLS = "*"
```

For least privilege, replace `*` with comma-separated exact names or prefix patterns. Example:

```toml
ALLOWED_TOOLS = "gdrive_*,gdocs_*,gsheets_*,gcal_*,youtube_search,youtube_get_video"
```

Tool families are `gdrive_*`, `gdocs_*`, `gsheets_*`, `gcal_*`, and `youtube_*`. Changes to this variable require another deployment.

## 7. Validate and deploy

```bash
npm run typecheck
npx wrangler deploy --dry-run
npm run deploy
```

Wrangler prints a URL similar to:

```text
https://google-cloud-mcp.<your-workers-subdomain>.workers.dev
```

Check the public health endpoint:

```bash
curl https://google-cloud-mcp.<your-workers-subdomain>.workers.dev/health
```

It should return JSON containing `"status":"ok"` and a tool count.

## 8. Connect an MCP client

The simplest endpoint is:

```text
https://google-cloud-mcp.<your-workers-subdomain>.workers.dev/mcp/<MCP_SECRET_PATH>
```

Use that URL as a remote HTTP/streamable-HTTP MCP server in your client. A generic configuration shape is:

```json
{
  "mcpServers": {
    "google-cloud": {
      "url": "https://google-cloud-mcp.<your-workers-subdomain>.workers.dev/mcp/<MCP_SECRET_PATH>"
    }
  }
}
```

Client configuration formats differ. If your client supports custom HTTP headers, avoid putting the secret in the URL and use `/mcp` with:

```text
Authorization: Bearer <MCP_SECRET_PATH>
```

## Updating tokens or code

If you add an identity or replace a refresh token, run the OAuth helper again and update only the token secret:

```bash
npx wrangler secret put GOOGLE_TOKENS
```

Paste the newly generated `scripts/google_tokens.json` when prompted. For code or `ALLOWED_TOOLS` changes, run `npm run deploy`.

Tail production logs while troubleshooting:

```bash
npm run tail
```

## Security notes

- Never commit `client_secret.json`, `google_tokens.json`, `.dev.vars`, or copied secret values.
- Treat the MCP secret as a password. Anyone who has it can invoke every allowed tool as every configured identity.
- Prefer an explicit `ALLOWED_TOOLS` list, especially before enabling destructive Drive, Calendar, or YouTube operations.
- Use separate Cloudflare and Google projects for testing if you do not want test activity touching your primary deployment.
- Rotate `MCP_SECRET_PATH` immediately if it appears in logs, screenshots, shell history, or a shared client configuration.

## Troubleshooting

**`redirect_uri_mismatch`**: use a Desktop app OAuth client, not a web application client, and rerun the helper.

**`access_denied` or blocked consent screen**: add the Google account as a test user and verify that the required API is enabled.

**Google returned no refresh token**: revoke the app under your Google Account's third-party access settings, then run the OAuth helper again.

**`D1_ERROR: no such table: tokens`**: apply the remote D1 migration from step 4.

**`Unknown identity`**: use one of the labels in `scripts/google_tokens.json`, then ensure the latest JSON was uploaded to the `GOOGLE_TOKENS` secret.

**A tool is missing**: check `ALLOWED_TOOLS` and deploy again.
