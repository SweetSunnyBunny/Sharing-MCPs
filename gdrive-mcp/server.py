"""
Google Drive MCP Server - Standalone Edition

Let Claude access your Google Drive! Browse, upload, download, and manage files.
Includes Google Docs and Sheets support.

Requirements:
    1. Google Cloud project with Drive API enabled
    2. OAuth credentials (client_secret.json)
    3. Run setup.py to authenticate

Built with love for sharing.
"""

import io
import pickle
import mimetypes
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastmcp import FastMCP
from pydantic import Field

try:
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
    from googleapiclient.errors import HttpError
except ImportError:
    raise ImportError(
        "Missing Google API dependencies. Install with: "
        "pip install google-api-python-client google-auth-oauthlib google-auth-httplib2"
    )

mcp = FastMCP("gdrive-mcp")

# Auth paths
AUTH_DIR = Path(__file__).parent / "auth"
TOKEN_PATH = AUTH_DIR / "token.pickle"
DOWNLOADS_DIR = Path(__file__).parent / "downloads"

# MIME types
GOOGLE_MIME_TYPES = {
    'document': 'application/vnd.google-apps.document',
    'spreadsheet': 'application/vnd.google-apps.spreadsheet',
    'presentation': 'application/vnd.google-apps.presentation',
    'folder': 'application/vnd.google-apps.folder',
}

EXPORT_MIME_TYPES = {
    'pdf': 'application/pdf',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'txt': 'text/plain',
    'html': 'text/html',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'csv': 'text/csv'
}

# Cached services
_drive_service = None
_docs_service = None
_sheets_service = None


def get_credentials():
    """Get OAuth credentials."""
    creds = None
    if TOKEN_PATH.exists():
        with open(TOKEN_PATH, 'rb') as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(TOKEN_PATH, 'wb') as token:
                pickle.dump(creds, token)
        else:
            raise RuntimeError("Not authenticated. Run 'python setup.py' first.")

    return creds


def get_drive_service():
    """Get Google Drive service."""
    global _drive_service
    if not _drive_service:
        _drive_service = build('drive', 'v3', credentials=get_credentials())
    return _drive_service


def get_docs_service():
    """Get Google Docs service."""
    global _docs_service
    if not _docs_service:
        _docs_service = build('docs', 'v1', credentials=get_credentials())
    return _docs_service


def get_sheets_service():
    """Get Google Sheets service."""
    global _sheets_service
    if not _sheets_service:
        _sheets_service = build('sheets', 'v4', credentials=get_credentials())
    return _sheets_service


def format_size(size_bytes: int) -> str:
    """Format bytes to human readable size."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


# =============================================================================
# CONNECTION
# =============================================================================

@mcp.tool()
async def test_connection() -> Dict[str, Any]:
    """Test Google Drive connection and get account info."""
    try:
        service = get_drive_service()
        about = service.about().get(fields='user,storageQuota').execute()
        user = about.get('user', {})
        quota = about.get('storageQuota', {})

        return {
            "success": True,
            "email": user.get('emailAddress'),
            "name": user.get('displayName'),
            "storage_used": format_size(int(quota.get('usage', 0))),
            "storage_limit": format_size(int(quota.get('limit', 0))) if quota.get('limit') else 'Unlimited'
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# BROWSING
# =============================================================================

@mcp.tool()
async def list_files(
    folder_id: Optional[str] = Field(None, description="Folder ID (None for root)"),
    max_results: int = Field(50, description="Maximum files to return"),
    file_type: Optional[str] = Field(None, description="Filter: document, spreadsheet, folder")
) -> Dict[str, Any]:
    """List files in Google Drive."""
    try:
        service = get_drive_service()

        query_parts = []
        if folder_id:
            query_parts.append(f"'{folder_id}' in parents")
        else:
            query_parts.append("'root' in parents")

        query_parts.append("trashed = false")

        if file_type and file_type in GOOGLE_MIME_TYPES:
            query_parts.append(f"mimeType = '{GOOGLE_MIME_TYPES[file_type]}'")

        results = service.files().list(
            q=" and ".join(query_parts),
            pageSize=min(max_results, 100),
            fields="files(id, name, mimeType, size, modifiedTime, webViewLink)"
        ).execute()

        files = []
        for f in results.get('files', []):
            file_info = {
                'id': f['id'],
                'name': f['name'],
                'type': f['mimeType'].split('.')[-1] if 'google-apps' in f['mimeType'] else 'file',
                'modified': f.get('modifiedTime'),
                'link': f.get('webViewLink')
            }
            if f.get('size'):
                file_info['size'] = format_size(int(f['size']))
            files.append(file_info)

        return {"success": True, "count": len(files), "files": files}
    except HttpError as e:
        return {"success": False, "error": f"Drive API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def search_files(
    query: str = Field(..., description="Search query"),
    max_results: int = Field(20, description="Maximum results")
) -> Dict[str, Any]:
    """Search for files in Google Drive."""
    try:
        service = get_drive_service()

        search_query = f"(name contains '{query}' or fullText contains '{query}') and trashed = false"

        results = service.files().list(
            q=search_query,
            pageSize=min(max_results, 100),
            fields="files(id, name, mimeType, size, modifiedTime, webViewLink)"
        ).execute()

        files = []
        for f in results.get('files', []):
            file_info = {
                'id': f['id'],
                'name': f['name'],
                'type': f['mimeType'].split('.')[-1] if 'google-apps' in f['mimeType'] else 'file',
                'modified': f.get('modifiedTime'),
                'link': f.get('webViewLink')
            }
            if f.get('size'):
                file_info['size'] = format_size(int(f['size']))
            files.append(file_info)

        return {"success": True, "query": query, "count": len(files), "files": files}
    except HttpError as e:
        return {"success": False, "error": f"Drive API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def get_file_info(file_id: str = Field(..., description="File ID")) -> Dict[str, Any]:
    """Get detailed information about a file."""
    try:
        service = get_drive_service()

        f = service.files().get(
            fileId=file_id,
            fields="id, name, mimeType, size, createdTime, modifiedTime, webViewLink, webContentLink, owners, shared, description"
        ).execute()

        file_info = {
            'id': f['id'],
            'name': f['name'],
            'mime_type': f['mimeType'],
            'created': f.get('createdTime'),
            'modified': f.get('modifiedTime'),
            'description': f.get('description'),
            'link': f.get('webViewLink'),
            'download_link': f.get('webContentLink'),
            'shared': f.get('shared', False),
            'owners': [o.get('emailAddress') for o in f.get('owners', [])]
        }
        if f.get('size'):
            file_info['size'] = format_size(int(f['size']))

        return {"success": True, "file": file_info}
    except HttpError as e:
        return {"success": False, "error": f"Drive API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# FOLDERS
# =============================================================================

@mcp.tool()
async def create_folder(
    name: str = Field(..., description="Folder name"),
    parent_id: Optional[str] = Field(None, description="Parent folder ID (None for root)")
) -> Dict[str, Any]:
    """Create a new folder."""
    try:
        service = get_drive_service()

        metadata = {'name': name, 'mimeType': GOOGLE_MIME_TYPES['folder']}
        if parent_id:
            metadata['parents'] = [parent_id]

        folder = service.files().create(body=metadata, fields='id, name, webViewLink').execute()

        return {
            "success": True,
            "folder_id": folder['id'],
            "name": folder['name'],
            "link": folder.get('webViewLink')
        }
    except HttpError as e:
        return {"success": False, "error": f"Drive API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# FILE OPERATIONS
# =============================================================================

@mcp.tool()
async def upload_file(
    file_path: str = Field(..., description="Local file path"),
    folder_id: Optional[str] = Field(None, description="Destination folder ID"),
    name: Optional[str] = Field(None, description="Name in Drive")
) -> Dict[str, Any]:
    """Upload a file to Google Drive."""
    try:
        service = get_drive_service()

        local_path = Path(file_path)
        if not local_path.exists():
            return {"success": False, "error": f"File not found: {file_path}"}

        file_name = name or local_path.name
        mime_type, _ = mimetypes.guess_type(str(local_path))
        mime_type = mime_type or 'application/octet-stream'

        metadata = {'name': file_name}
        if folder_id:
            metadata['parents'] = [folder_id]

        media = MediaFileUpload(str(local_path), mimetype=mime_type, resumable=True)
        file = service.files().create(body=metadata, media_body=media, fields='id, name, size, webViewLink').execute()

        return {
            "success": True,
            "file_id": file['id'],
            "name": file['name'],
            "size": format_size(int(file.get('size', 0))),
            "link": file.get('webViewLink')
        }
    except HttpError as e:
        return {"success": False, "error": f"Drive API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def download_file(
    file_id: str = Field(..., description="File ID"),
    save_path: Optional[str] = Field(None, description="Local save path")
) -> Dict[str, Any]:
    """Download a file from Google Drive."""
    try:
        service = get_drive_service()

        file_info = service.files().get(fileId=file_id, fields='name, mimeType, size').execute()

        if save_path:
            local_path = Path(save_path)
        else:
            DOWNLOADS_DIR.mkdir(exist_ok=True)
            local_path = DOWNLOADS_DIR / file_info['name']

        if 'google-apps' in file_info['mimeType']:
            export_type = 'pdf'
            if 'spreadsheet' in file_info['mimeType']:
                export_type = 'xlsx'
            elif 'document' in file_info['mimeType']:
                export_type = 'docx'

            request = service.files().export_media(fileId=file_id, mimeType=EXPORT_MIME_TYPES[export_type])
            local_path = local_path.with_suffix(f'.{export_type}')
        else:
            request = service.files().get_media(fileId=file_id)

        local_path.parent.mkdir(parents=True, exist_ok=True)
        fh = io.FileIO(str(local_path), 'wb')
        downloader = MediaIoBaseDownload(fh, request)

        done = False
        while not done:
            status, done = downloader.next_chunk()
        fh.close()

        return {
            "success": True,
            "file_id": file_id,
            "name": file_info['name'],
            "saved_to": str(local_path),
            "size": format_size(local_path.stat().st_size)
        }
    except HttpError as e:
        return {"success": False, "error": f"Drive API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def move_file(
    file_id: str = Field(..., description="File ID"),
    new_folder_id: str = Field(..., description="Destination folder ID")
) -> Dict[str, Any]:
    """Move a file to a different folder."""
    try:
        service = get_drive_service()

        file = service.files().get(fileId=file_id, fields='parents').execute()
        previous_parents = ",".join(file.get('parents', []))

        service.files().update(
            fileId=file_id,
            addParents=new_folder_id,
            removeParents=previous_parents
        ).execute()

        return {"success": True, "file_id": file_id, "new_folder_id": new_folder_id}
    except HttpError as e:
        return {"success": False, "error": f"Drive API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def rename_file(
    file_id: str = Field(..., description="File ID"),
    new_name: str = Field(..., description="New name")
) -> Dict[str, Any]:
    """Rename a file."""
    try:
        service = get_drive_service()
        file = service.files().update(fileId=file_id, body={'name': new_name}, fields='id, name').execute()
        return {"success": True, "file_id": file_id, "new_name": file['name']}
    except HttpError as e:
        return {"success": False, "error": f"Drive API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def trash_file(file_id: str = Field(..., description="File ID")) -> Dict[str, Any]:
    """Move a file to trash."""
    try:
        service = get_drive_service()
        service.files().update(fileId=file_id, body={'trashed': True}).execute()
        return {"success": True, "file_id": file_id, "action": "trashed"}
    except HttpError as e:
        return {"success": False, "error": f"Drive API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def delete_file(file_id: str = Field(..., description="File ID")) -> Dict[str, Any]:
    """Permanently delete a file."""
    try:
        service = get_drive_service()
        service.files().delete(fileId=file_id).execute()
        return {"success": True, "file_id": file_id, "action": "deleted"}
    except HttpError as e:
        return {"success": False, "error": f"Drive API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# SHARING
# =============================================================================

@mcp.tool()
async def share_file(
    file_id: str = Field(..., description="File ID"),
    email: str = Field(..., description="Email to share with"),
    role: str = Field("reader", description="Role: reader, writer, commenter")
) -> Dict[str, Any]:
    """Share a file with someone."""
    try:
        service = get_drive_service()

        permission = {'type': 'user', 'role': role, 'emailAddress': email}
        result = service.permissions().create(fileId=file_id, body=permission, sendNotificationEmail=True).execute()

        return {
            "success": True,
            "file_id": file_id,
            "shared_with": email,
            "role": role
        }
    except HttpError as e:
        return {"success": False, "error": f"Drive API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def create_share_link(
    file_id: str = Field(..., description="File ID"),
    role: str = Field("reader", description="Role: reader, writer, commenter")
) -> Dict[str, Any]:
    """Create a shareable link."""
    try:
        service = get_drive_service()

        permission = {'type': 'anyone', 'role': role}
        service.permissions().create(fileId=file_id, body=permission).execute()

        file = service.files().get(fileId=file_id, fields='webViewLink').execute()

        return {"success": True, "file_id": file_id, "link": file.get('webViewLink'), "role": role}
    except HttpError as e:
        return {"success": False, "error": f"Drive API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# GOOGLE DOCS
# =============================================================================

@mcp.tool()
async def create_document(
    title: str = Field(..., description="Document title"),
    folder_id: Optional[str] = Field(None, description="Folder ID")
) -> Dict[str, Any]:
    """Create a new Google Doc."""
    try:
        docs_service = get_docs_service()
        drive_service = get_drive_service()

        doc = docs_service.documents().create(body={'title': title}).execute()
        doc_id = doc['documentId']

        if folder_id:
            drive_service.files().update(fileId=doc_id, addParents=folder_id, removeParents='root').execute()

        return {
            "success": True,
            "document_id": doc_id,
            "title": doc['title'],
            "link": f"https://docs.google.com/document/d/{doc_id}/edit"
        }
    except HttpError as e:
        return {"success": False, "error": f"Docs API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def read_document(document_id: str = Field(..., description="Document ID")) -> Dict[str, Any]:
    """Read content from a Google Doc."""
    try:
        service = get_docs_service()
        doc = service.documents().get(documentId=document_id).execute()

        content = []
        for element in doc.get('body', {}).get('content', []):
            if 'paragraph' in element:
                for para_element in element['paragraph'].get('elements', []):
                    if 'textRun' in para_element:
                        content.append(para_element['textRun'].get('content', ''))

        text = ''.join(content)

        return {
            "success": True,
            "document_id": document_id,
            "title": doc['title'],
            "content": text,
            "character_count": len(text)
        }
    except HttpError as e:
        return {"success": False, "error": f"Docs API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def append_to_document(
    document_id: str = Field(..., description="Document ID"),
    text: str = Field(..., description="Text to append")
) -> Dict[str, Any]:
    """Append text to a Google Doc."""
    try:
        service = get_docs_service()

        doc = service.documents().get(documentId=document_id).execute()
        end_index = doc['body']['content'][-1]['endIndex'] - 1

        requests = [{'insertText': {'location': {'index': end_index}, 'text': text}}]
        service.documents().batchUpdate(documentId=document_id, body={'requests': requests}).execute()

        return {"success": True, "document_id": document_id, "appended_length": len(text)}
    except HttpError as e:
        return {"success": False, "error": f"Docs API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# GOOGLE SHEETS
# =============================================================================

@mcp.tool()
async def create_spreadsheet(
    title: str = Field(..., description="Spreadsheet title"),
    folder_id: Optional[str] = Field(None, description="Folder ID")
) -> Dict[str, Any]:
    """Create a new Google Spreadsheet."""
    try:
        sheets_service = get_sheets_service()
        drive_service = get_drive_service()

        spreadsheet = sheets_service.spreadsheets().create(body={'properties': {'title': title}}).execute()
        spreadsheet_id = spreadsheet['spreadsheetId']

        if folder_id:
            drive_service.files().update(fileId=spreadsheet_id, addParents=folder_id, removeParents='root').execute()

        return {
            "success": True,
            "spreadsheet_id": spreadsheet_id,
            "title": spreadsheet['properties']['title'],
            "link": spreadsheet['spreadsheetUrl']
        }
    except HttpError as e:
        return {"success": False, "error": f"Sheets API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def read_spreadsheet(
    spreadsheet_id: str = Field(..., description="Spreadsheet ID"),
    range: str = Field(..., description="Range in A1 notation (e.g., 'Sheet1!A1:D10')")
) -> Dict[str, Any]:
    """Read data from a spreadsheet."""
    try:
        service = get_sheets_service()

        result = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=range).execute()
        values = result.get('values', [])

        return {"success": True, "spreadsheet_id": spreadsheet_id, "range": range, "row_count": len(values), "values": values}
    except HttpError as e:
        return {"success": False, "error": f"Sheets API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def write_spreadsheet(
    spreadsheet_id: str = Field(..., description="Spreadsheet ID"),
    range: str = Field(..., description="Range in A1 notation"),
    values: List[List[Any]] = Field(..., description="2D array of values")
) -> Dict[str, Any]:
    """Write data to a spreadsheet."""
    try:
        service = get_sheets_service()

        result = service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=range,
            valueInputOption='USER_ENTERED',
            body={'values': values}
        ).execute()

        return {
            "success": True,
            "spreadsheet_id": spreadsheet_id,
            "range": result.get('updatedRange'),
            "cells_updated": result.get('updatedCells')
        }
    except HttpError as e:
        return {"success": False, "error": f"Sheets API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def append_to_spreadsheet(
    spreadsheet_id: str = Field(..., description="Spreadsheet ID"),
    values: List[Any] = Field(..., description="Row values to append"),
    sheet_name: str = Field("Sheet1", description="Sheet name")
) -> Dict[str, Any]:
    """Append a row to a spreadsheet."""
    try:
        service = get_sheets_service()

        result = service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!A:A",
            valueInputOption='USER_ENTERED',
            insertDataOption='INSERT_ROWS',
            body={'values': [values]}
        ).execute()

        return {
            "success": True,
            "spreadsheet_id": spreadsheet_id,
            "updated_range": result.get('updates', {}).get('updatedRange')
        }
    except HttpError as e:
        return {"success": False, "error": f"Sheets API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# MAIN
# =============================================================================

def main():
    mcp.run()

if __name__ == "__main__":
    main()
