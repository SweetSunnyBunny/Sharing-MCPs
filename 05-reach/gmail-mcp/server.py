"""
Gmail MCP Server - Standalone Edition

Let Claude access your Gmail! Read, send, search, and manage emails.

Requirements:
    1. Google Cloud project with Gmail API enabled
    2. OAuth credentials (client_secret.json)
    3. Run setup.py to authenticate

Built with love for sharing.
"""

import base64
import pickle
import mimetypes
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from typing import Optional, List, Dict, Any

from fastmcp import FastMCP
from pydantic import Field

try:
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except ImportError:
    raise ImportError(
        "Missing Google API dependencies. Install with: "
        "pip install google-api-python-client google-auth-oauthlib google-auth-httplib2"
    )

mcp = FastMCP("gmail-mcp")

# Auth paths
AUTH_DIR = Path(__file__).parent / "auth"
TOKEN_PATH = AUTH_DIR / "token.pickle"
CREDENTIALS_PATH = AUTH_DIR / "client_secret.json"

# Cached service
_service = None


def get_gmail_service():
    """Get authenticated Gmail service."""
    global _service

    if _service:
        return _service

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
            raise RuntimeError(
                "Not authenticated. Run 'python setup.py' first."
            )

    _service = build('gmail', 'v1', credentials=creds)
    return _service


def decode_message_body(payload: dict) -> str:
    """Decode email body from Gmail API payload."""
    body = ""
    if 'body' in payload and payload['body'].get('data'):
        body = base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8', errors='replace')
    elif 'parts' in payload:
        for part in payload['parts']:
            mime_type = part.get('mimeType', '')
            if mime_type == 'text/plain' and part.get('body', {}).get('data'):
                body = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8', errors='replace')
                break
            elif mime_type == 'text/html' and not body and part.get('body', {}).get('data'):
                body = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8', errors='replace')
            elif 'parts' in part:
                body = decode_message_body(part)
                if body:
                    break
    return body


def get_header(headers: list, name: str) -> str:
    """Get a header value from message headers."""
    for header in headers:
        if header['name'].lower() == name.lower():
            return header['value']
    return ""


# =============================================================================
# CONNECTION
# =============================================================================

@mcp.tool()
async def test_connection() -> Dict[str, Any]:
    """Test Gmail API connection."""
    try:
        service = get_gmail_service()
        profile = service.users().getProfile(userId='me').execute()

        return {
            "success": True,
            "email": profile.get('emailAddress'),
            "messages_total": profile.get('messagesTotal'),
            "threads_total": profile.get('threadsTotal')
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# READING EMAIL
# =============================================================================

@mcp.tool()
async def list_messages(
    query: str = Field("", description="Gmail search query (e.g., 'from:user@example.com', 'is:unread')"),
    max_results: int = Field(20, description="Maximum messages to return (1-100)"),
    label_ids: Optional[List[str]] = Field(None, description="Filter by label IDs")
) -> Dict[str, Any]:
    """List messages matching search criteria."""
    try:
        service = get_gmail_service()

        request_params = {
            'userId': 'me',
            'maxResults': min(max_results, 100)
        }
        if query:
            request_params['q'] = query
        if label_ids:
            request_params['labelIds'] = label_ids

        results = service.users().messages().list(**request_params).execute()
        messages = results.get('messages', [])

        message_list = []
        for msg in messages:
            msg_data = service.users().messages().get(
                userId='me',
                id=msg['id'],
                format='metadata',
                metadataHeaders=['From', 'To', 'Subject', 'Date']
            ).execute()

            headers = msg_data.get('payload', {}).get('headers', [])
            message_list.append({
                'id': msg['id'],
                'thread_id': msg_data.get('threadId'),
                'snippet': msg_data.get('snippet', '')[:100],
                'from': get_header(headers, 'From'),
                'to': get_header(headers, 'To'),
                'subject': get_header(headers, 'Subject'),
                'date': get_header(headers, 'Date'),
                'labels': msg_data.get('labelIds', [])
            })

        return {
            "success": True,
            "count": len(message_list),
            "messages": message_list
        }
    except HttpError as e:
        return {"success": False, "error": f"Gmail API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def get_message(
    message_id: str = Field(..., description="Message ID to retrieve")
) -> Dict[str, Any]:
    """Get a specific email message with full content."""
    try:
        service = get_gmail_service()

        msg = service.users().messages().get(
            userId='me',
            id=message_id,
            format='full'
        ).execute()

        headers = msg.get('payload', {}).get('headers', [])
        body = decode_message_body(msg.get('payload', {}))

        attachments = []
        if 'parts' in msg.get('payload', {}):
            for part in msg['payload']['parts']:
                if part.get('filename'):
                    attachments.append({
                        'id': part.get('body', {}).get('attachmentId'),
                        'filename': part['filename'],
                        'mime_type': part.get('mimeType'),
                        'size': part.get('body', {}).get('size', 0)
                    })

        return {
            "success": True,
            "id": msg['id'],
            "thread_id": msg.get('threadId'),
            "from": get_header(headers, 'From'),
            "to": get_header(headers, 'To'),
            "cc": get_header(headers, 'Cc'),
            "subject": get_header(headers, 'Subject'),
            "date": get_header(headers, 'Date'),
            "labels": msg.get('labelIds', []),
            "body": body,
            "snippet": msg.get('snippet', ''),
            "attachments": attachments
        }
    except HttpError as e:
        return {"success": False, "error": f"Gmail API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def get_thread(
    thread_id: str = Field(..., description="Thread ID to retrieve")
) -> Dict[str, Any]:
    """Get all messages in an email thread."""
    try:
        service = get_gmail_service()

        thread = service.users().threads().get(userId='me', id=thread_id).execute()

        messages = []
        for msg in thread.get('messages', []):
            headers = msg.get('payload', {}).get('headers', [])
            body = decode_message_body(msg.get('payload', {}))

            messages.append({
                'id': msg['id'],
                'from': get_header(headers, 'From'),
                'to': get_header(headers, 'To'),
                'subject': get_header(headers, 'Subject'),
                'date': get_header(headers, 'Date'),
                'body': body[:2000] + ('...' if len(body) > 2000 else ''),
                'labels': msg.get('labelIds', [])
            })

        return {
            "success": True,
            "thread_id": thread_id,
            "message_count": len(messages),
            "messages": messages
        }
    except HttpError as e:
        return {"success": False, "error": f"Gmail API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def search(
    query: str = Field(..., description="Gmail search query"),
    max_results: int = Field(50, description="Maximum results (1-500)")
) -> Dict[str, Any]:
    """
    Advanced Gmail search.

    Supports: from:, to:, subject:, is:unread, is:starred, has:attachment,
    after:YYYY/MM/DD, before:YYYY/MM/DD, larger:5M, label:name
    """
    return await list_messages(query=query, max_results=max_results)


# =============================================================================
# SENDING EMAIL
# =============================================================================

@mcp.tool()
async def send_message(
    to: str = Field(..., description="Recipient email address"),
    subject: str = Field(..., description="Email subject"),
    body: str = Field(..., description="Email body text"),
    cc: Optional[str] = Field(None, description="CC recipients (comma-separated)"),
    bcc: Optional[str] = Field(None, description="BCC recipients (comma-separated)"),
    html: bool = Field(False, description="If True, body is HTML"),
    reply_to_message_id: Optional[str] = Field(None, description="Message ID to reply to")
) -> Dict[str, Any]:
    """Send an email."""
    try:
        service = get_gmail_service()

        profile = service.users().getProfile(userId='me').execute()
        sender_email = profile.get('emailAddress')

        if html:
            message = MIMEText(body, 'html')
        else:
            message = MIMEText(body, 'plain')

        message['to'] = to
        message['from'] = sender_email
        message['subject'] = subject

        if cc:
            message['cc'] = cc
        if bcc:
            message['bcc'] = bcc

        thread_id = None
        if reply_to_message_id:
            orig_msg = service.users().messages().get(
                userId='me', id=reply_to_message_id, format='metadata',
                metadataHeaders=['Message-ID', 'References']
            ).execute()

            orig_headers = orig_msg.get('payload', {}).get('headers', [])
            orig_message_id = get_header(orig_headers, 'Message-ID')
            references = get_header(orig_headers, 'References')

            if orig_message_id:
                message['In-Reply-To'] = orig_message_id
                message['References'] = f"{references} {orig_message_id}".strip()

            thread_id = orig_msg.get('threadId')

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
        body_data = {'raw': raw}
        if thread_id:
            body_data['threadId'] = thread_id

        sent = service.users().messages().send(userId='me', body=body_data).execute()

        return {
            "success": True,
            "message_id": sent['id'],
            "thread_id": sent.get('threadId'),
            "to": to,
            "subject": subject
        }
    except HttpError as e:
        return {"success": False, "error": f"Gmail API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def send_with_attachment(
    to: str = Field(..., description="Recipient email address"),
    subject: str = Field(..., description="Email subject"),
    body: str = Field(..., description="Email body text"),
    attachment_path: str = Field(..., description="Path to file to attach"),
    cc: Optional[str] = Field(None, description="CC recipients"),
    bcc: Optional[str] = Field(None, description="BCC recipients")
) -> Dict[str, Any]:
    """Send an email with a file attachment."""
    try:
        service = get_gmail_service()

        attach_path = Path(attachment_path)
        if not attach_path.exists():
            return {"success": False, "error": f"Attachment not found: {attachment_path}"}

        profile = service.users().getProfile(userId='me').execute()
        sender_email = profile.get('emailAddress')

        message = MIMEMultipart()
        message['to'] = to
        message['from'] = sender_email
        message['subject'] = subject
        if cc:
            message['cc'] = cc
        if bcc:
            message['bcc'] = bcc

        message.attach(MIMEText(body, 'plain'))

        mime_type, _ = mimetypes.guess_type(str(attach_path))
        if mime_type is None:
            mime_type = 'application/octet-stream'
        main_type, sub_type = mime_type.split('/', 1)

        with open(attach_path, 'rb') as f:
            attachment = MIMEBase(main_type, sub_type)
            attachment.set_payload(f.read())

        encoders.encode_base64(attachment)
        attachment.add_header('Content-Disposition', 'attachment', filename=attach_path.name)
        message.attach(attachment)

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
        sent = service.users().messages().send(userId='me', body={'raw': raw}).execute()

        return {
            "success": True,
            "message_id": sent['id'],
            "thread_id": sent.get('threadId'),
            "to": to,
            "subject": subject,
            "attachment": attach_path.name
        }
    except HttpError as e:
        return {"success": False, "error": f"Gmail API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def create_draft(
    to: str = Field(..., description="Recipient email address"),
    subject: str = Field(..., description="Email subject"),
    body: str = Field(..., description="Email body text"),
    cc: Optional[str] = Field(None, description="CC recipients"),
    bcc: Optional[str] = Field(None, description="BCC recipients")
) -> Dict[str, Any]:
    """Create a draft email (not sent)."""
    try:
        service = get_gmail_service()

        profile = service.users().getProfile(userId='me').execute()
        sender_email = profile.get('emailAddress')

        message = MIMEText(body, 'plain')
        message['to'] = to
        message['from'] = sender_email
        message['subject'] = subject
        if cc:
            message['cc'] = cc
        if bcc:
            message['bcc'] = bcc

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
        draft = service.users().drafts().create(
            userId='me',
            body={'message': {'raw': raw}}
        ).execute()

        return {
            "success": True,
            "draft_id": draft['id'],
            "message_id": draft['message']['id'],
            "to": to,
            "subject": subject
        }
    except HttpError as e:
        return {"success": False, "error": f"Gmail API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# LABEL MANAGEMENT
# =============================================================================

@mcp.tool()
async def list_labels() -> Dict[str, Any]:
    """List all labels in the mailbox."""
    try:
        service = get_gmail_service()

        results = service.users().labels().list(userId='me').execute()
        labels = results.get('labels', [])

        label_list = []
        for label in labels:
            label_list.append({
                'id': label['id'],
                'name': label['name'],
                'type': label.get('type', 'user')
            })

        return {"success": True, "count": len(label_list), "labels": label_list}
    except HttpError as e:
        return {"success": False, "error": f"Gmail API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def create_label(
    name: str = Field(..., description="Label name")
) -> Dict[str, Any]:
    """Create a new label."""
    try:
        service = get_gmail_service()

        label_body = {
            'name': name,
            'labelListVisibility': 'labelShow',
            'messageListVisibility': 'show'
        }

        label = service.users().labels().create(userId='me', body=label_body).execute()

        return {
            "success": True,
            "label_id": label['id'],
            "name": label['name']
        }
    except HttpError as e:
        return {"success": False, "error": f"Gmail API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def modify_labels(
    message_id: str = Field(..., description="Message ID to modify"),
    add_label_ids: Optional[List[str]] = Field(None, description="Label IDs to add"),
    remove_label_ids: Optional[List[str]] = Field(None, description="Label IDs to remove")
) -> Dict[str, Any]:
    """Add or remove labels from a message."""
    try:
        service = get_gmail_service()

        body = {}
        if add_label_ids:
            body['addLabelIds'] = add_label_ids
        if remove_label_ids:
            body['removeLabelIds'] = remove_label_ids

        if not body:
            return {"success": False, "error": "Must specify add_label_ids or remove_label_ids"}

        msg = service.users().messages().modify(userId='me', id=message_id, body=body).execute()

        return {
            "success": True,
            "message_id": message_id,
            "labels": msg.get('labelIds', [])
        }
    except HttpError as e:
        return {"success": False, "error": f"Gmail API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# MESSAGE ACTIONS
# =============================================================================

@mcp.tool()
async def trash_message(message_id: str = Field(..., description="Message ID")) -> Dict[str, Any]:
    """Move a message to trash."""
    try:
        service = get_gmail_service()
        service.users().messages().trash(userId='me', id=message_id).execute()
        return {"success": True, "message_id": message_id, "action": "trashed"}
    except HttpError as e:
        return {"success": False, "error": f"Gmail API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def untrash_message(message_id: str = Field(..., description="Message ID")) -> Dict[str, Any]:
    """Restore a message from trash."""
    try:
        service = get_gmail_service()
        service.users().messages().untrash(userId='me', id=message_id).execute()
        return {"success": True, "message_id": message_id, "action": "untrashed"}
    except HttpError as e:
        return {"success": False, "error": f"Gmail API error: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def mark_read(message_id: str = Field(..., description="Message ID")) -> Dict[str, Any]:
    """Mark a message as read."""
    return await modify_labels(message_id=message_id, remove_label_ids=['UNREAD'])


@mcp.tool()
async def mark_unread(message_id: str = Field(..., description="Message ID")) -> Dict[str, Any]:
    """Mark a message as unread."""
    return await modify_labels(message_id=message_id, add_label_ids=['UNREAD'])


@mcp.tool()
async def archive_message(message_id: str = Field(..., description="Message ID")) -> Dict[str, Any]:
    """Archive a message (remove from inbox)."""
    return await modify_labels(message_id=message_id, remove_label_ids=['INBOX'])


# =============================================================================
# MAIN
# =============================================================================

def main():
    mcp.run()

if __name__ == "__main__":
    main()
