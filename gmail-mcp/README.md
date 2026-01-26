# Gmail MCP Server

Let Claude access your Gmail! Read, send, search, and manage your emails.

## What It Does

- **Read Emails** - List, search, and read full message content
- **Send Emails** - Compose and send messages, with attachments
- **Reply** - Reply to existing threads
- **Organize** - Labels, archive, trash, mark read/unread
- **Drafts** - Create email drafts

---

## Setup (One-Time)

### Step 1: Create Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project (or use existing)
3. Enable the **Gmail API**:
   - Go to APIs & Services > Library
   - Search for "Gmail API"
   - Click Enable

### Step 2: Create OAuth Credentials

1. Go to APIs & Services > Credentials
2. Click **Create Credentials > OAuth client ID**
3. If prompted, configure the OAuth consent screen:
   - User Type: External (or Internal if using Workspace)
   - App name: "Gmail MCP"
   - Add your email as a test user
4. Application type: **Desktop app**
5. Download the JSON file
6. Create an `auth` folder and save it as `auth/client_secret.json`

### Step 3: Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 4: Authenticate

```bash
python setup.py
```

A browser window will open. Sign in with your Google account and grant access.

### Step 5: Run the Server

```bash
python run_server.py
```

---

## Connecting to Claude

Add to your MCP settings:

```json
{
  "mcpServers": {
    "gmail": {
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

---

## Available Tools

### Reading
| Tool | Description |
|------|-------------|
| `test_connection` | Check connection and get account info |
| `list_messages` | List messages with optional search query |
| `get_message` | Get full message content |
| `get_thread` | Get all messages in a thread |
| `search` | Advanced Gmail search |

### Sending
| Tool | Description |
|------|-------------|
| `send_message` | Send an email |
| `send_with_attachment` | Send with file attachment |
| `create_draft` | Create a draft (not sent) |

### Labels
| Tool | Description |
|------|-------------|
| `list_labels` | List all labels |
| `create_label` | Create a new label |
| `modify_labels` | Add/remove labels from message |

### Actions
| Tool | Description |
|------|-------------|
| `trash_message` | Move to trash |
| `untrash_message` | Restore from trash |
| `mark_read` | Mark as read |
| `mark_unread` | Mark as unread |
| `archive_message` | Remove from inbox |

---

## Example Usage

**"Show me my unread emails"**
```
Claude uses list_messages(query="is:unread")
```

**"Send an email to bob@example.com about the meeting"**
```
Claude uses send_message(to="bob@example.com", subject="...", body="...")
```

**"Search for emails from last week with attachments"**
```
Claude uses search(query="after:2024/01/15 has:attachment")
```

---

## Gmail Search Syntax

The `query` parameter supports Gmail's search syntax:

| Operator | Example | Description |
|----------|---------|-------------|
| `from:` | `from:john@example.com` | From sender |
| `to:` | `to:me` | To recipient |
| `subject:` | `subject:meeting` | In subject |
| `is:` | `is:unread`, `is:starred` | Message status |
| `has:` | `has:attachment` | Has attachment |
| `after:` | `after:2024/01/01` | After date |
| `before:` | `before:2024/12/31` | Before date |
| `label:` | `label:important` | Has label |
| `larger:` | `larger:5M` | Larger than size |

---

## Security Notes

- OAuth tokens are stored in `auth/token.pickle`
- Never share or commit your `auth/` folder
- The `.gitignore` excludes auth files automatically
- Tokens can be revoked at https://myaccount.google.com/permissions

---

## Troubleshooting

### "Not authenticated"
Run `python setup.py` to authenticate.

### "Token expired"
The token auto-refreshes. If issues persist, delete `auth/token.pickle` and run setup.py again.

### "Access blocked"
If using a new Google Cloud project, you may need to add yourself as a test user in the OAuth consent screen settings.

---

## License

MIT - Do whatever you want with it!

Built with love for sharing.
