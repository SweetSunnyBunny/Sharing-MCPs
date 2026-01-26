# Google Drive MCP Server

Let Claude access your Google Drive! Browse, upload, download, and manage files. Includes Google Docs and Sheets support.

## What It Does

- **Drive** - Browse, search, upload, download files
- **Docs** - Create, read, and append to Google Docs
- **Sheets** - Create, read, and write to spreadsheets
- **Sharing** - Share files and create shareable links

---

## Setup (One-Time)

### Step 1: Create Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project (or use existing)
3. Enable these APIs:
   - **Google Drive API**
   - **Google Docs API**
   - **Google Sheets API**

### Step 2: Create OAuth Credentials

1. Go to APIs & Services > Credentials
2. Click **Create Credentials > OAuth client ID**
3. Configure OAuth consent screen if prompted
4. Application type: **Desktop app**
5. Download the JSON file
6. Create an `auth` folder and save as `auth/client_secret.json`

### Step 3: Install & Authenticate

```bash
pip install -r requirements.txt
python setup.py
```

### Step 4: Run the Server

```bash
python run_server.py
```

---

## Available Tools

### Drive - Browsing
| Tool | Description |
|------|-------------|
| `test_connection` | Check connection and storage info |
| `list_files` | List files in a folder |
| `search_files` | Search for files |
| `get_file_info` | Get file details |

### Drive - Folders
| Tool | Description |
|------|-------------|
| `create_folder` | Create a new folder |

### Drive - File Operations
| Tool | Description |
|------|-------------|
| `upload_file` | Upload a file |
| `download_file` | Download a file |
| `move_file` | Move to another folder |
| `rename_file` | Rename a file |
| `trash_file` | Move to trash |
| `delete_file` | Permanently delete |

### Drive - Sharing
| Tool | Description |
|------|-------------|
| `share_file` | Share with email |
| `create_share_link` | Create shareable link |

### Google Docs
| Tool | Description |
|------|-------------|
| `create_document` | Create new Doc |
| `read_document` | Read Doc content |
| `append_to_document` | Append text |

### Google Sheets
| Tool | Description |
|------|-------------|
| `create_spreadsheet` | Create new Sheet |
| `read_spreadsheet` | Read range |
| `write_spreadsheet` | Write to range |
| `append_to_spreadsheet` | Append row |

---

## Example Usage

**"What files do I have?"**
```
Claude uses list_files() to show your Drive contents
```

**"Create a document called 'Meeting Notes'"**
```
Claude uses create_document(title="Meeting Notes")
```

**"Add this to my spreadsheet"**
```
Claude uses append_to_spreadsheet() to add a row
```

---

## License

MIT - Do whatever you want with it!

Built with love for sharing.
