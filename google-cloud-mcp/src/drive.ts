/**
 * Google Drive, Docs, Sheets, and Calendar tools — ported from gdrive-multibot
 */

import { Env, googleGet as _googleGet, googlePost as _googlePost, googlePatch as _googlePatch, googleDelete as _googleDelete, googlePut as _googlePut, googleFetch as _googleFetch, validateIdentity, getIdentityList } from "./oauth.js";

const DRIVE_API = "https://www.googleapis.com/drive/v3";
const DOCS_API = "https://docs.googleapis.com/v1/documents";
const SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets";
const CALENDAR_API = "https://www.googleapis.com/calendar/v3";
const SERVICE = "drive";

// Service-bound wrappers so every call uses the drive refresh token
const googleGet = <T = unknown>(env: Env, identity: string, url: string) => _googleGet<T>(env, identity, url, SERVICE);
const googlePost = <T = unknown>(env: Env, identity: string, url: string, body: unknown) => _googlePost<T>(env, identity, url, body, SERVICE);
const googlePatch = <T = unknown>(env: Env, identity: string, url: string, body: unknown) => _googlePatch<T>(env, identity, url, body, SERVICE);
const googleDelete = (env: Env, identity: string, url: string) => _googleDelete(env, identity, url, SERVICE);
const googlePut = <T = unknown>(env: Env, identity: string, url: string, body: unknown) => _googlePut<T>(env, identity, url, body, SERVICE);
const googleFetch = (env: Env, identity: string, url: string, options: RequestInit = {}) => _googleFetch(env, identity, url, options, SERVICE);

function qs(params: Record<string, string | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined) p.append(k, v);
  }
  return p.toString();
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1073741824) return `${(bytes / 1048576).toFixed(1)} MB`;
  return `${(bytes / 1073741824).toFixed(2)} GB`;
}

// ============ Tool Definitions ============

export const DRIVE_TOOLS = [
  // Drive
  { name: "gdrive_list_identities", description: "List available Drive identities.", inputSchema: { type: "object" as const, properties: {}, required: [] as string[] } },
  { name: "gdrive_test_connection", description: "Test Drive connection.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const } }, required: ["identity"] } },
  { name: "gdrive_get_storage_quota", description: "Get storage quota info.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const } }, required: ["identity"] } },
  { name: "gdrive_list_files", description: "List files in Drive.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, max_results: { type: "number" as const }, file_type: { type: "string" as const }, order_by: { type: "string" as const } }, required: ["identity"] } },
  { name: "gdrive_search", description: "Search files by name or content.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, query: { type: "string" as const }, max_results: { type: "number" as const } }, required: ["identity", "query"] } },
  { name: "gdrive_get_file_info", description: "Get detailed file metadata.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, file_id: { type: "string" as const } }, required: ["identity", "file_id"] } },
  { name: "gdrive_list_shared_with_me", description: "List files shared with this identity.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, max_results: { type: "number" as const } }, required: ["identity"] } },
  { name: "gdrive_create_folder", description: "Create a folder.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, name: { type: "string" as const }, parent_id: { type: "string" as const } }, required: ["identity", "name"] } },
  { name: "gdrive_list_folder", description: "List contents of a folder.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, folder_id: { type: "string" as const } }, required: ["identity", "folder_id"] } },
  { name: "gdrive_copy_file", description: "Copy a file.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, file_id: { type: "string" as const }, new_name: { type: "string" as const } }, required: ["identity", "file_id"] } },
  { name: "gdrive_move_file", description: "Move a file to a different folder.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, file_id: { type: "string" as const }, new_parent_id: { type: "string" as const } }, required: ["identity", "file_id", "new_parent_id"] } },
  { name: "gdrive_rename_file", description: "Rename a file.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, file_id: { type: "string" as const }, new_name: { type: "string" as const } }, required: ["identity", "file_id", "new_name"] } },
  { name: "gdrive_trash_file", description: "Move a file to trash.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, file_id: { type: "string" as const } }, required: ["identity", "file_id"] } },
  { name: "gdrive_delete_file", description: "Permanently delete a file.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, file_id: { type: "string" as const } }, required: ["identity", "file_id"] } },
  { name: "gdrive_share_file", description: "Share a file with someone.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, file_id: { type: "string" as const }, email: { type: "string" as const }, role: { type: "string" as const } }, required: ["identity", "file_id", "email"] } },
  { name: "gdrive_create_share_link", description: "Create a shareable link.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, file_id: { type: "string" as const }, role: { type: "string" as const } }, required: ["identity", "file_id"] } },
  { name: "gdrive_get_permissions", description: "Get file permissions.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, file_id: { type: "string" as const } }, required: ["identity", "file_id"] } },
  { name: "gdrive_unshare_file", description: "Remove a permission from a file.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, file_id: { type: "string" as const }, permission_id: { type: "string" as const } }, required: ["identity", "file_id", "permission_id"] } },
  // Docs
  { name: "gdocs_create_document", description: "Create a new Google Doc.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, title: { type: "string" as const }, content: { type: "string" as const } }, required: ["identity", "title"] } },
  { name: "gdocs_get_document", description: "Get document metadata.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, document_id: { type: "string" as const } }, required: ["identity", "document_id"] } },
  { name: "gdocs_read_content", description: "Read document text content.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, document_id: { type: "string" as const } }, required: ["identity", "document_id"] } },
  { name: "gdocs_append_text", description: "Append text to end of document.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, document_id: { type: "string" as const }, text: { type: "string" as const } }, required: ["identity", "document_id", "text"] } },
  { name: "gdocs_insert_text", description: "Insert text at a position.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, document_id: { type: "string" as const }, text: { type: "string" as const }, index: { type: "number" as const } }, required: ["identity", "document_id", "text", "index"] } },
  { name: "gdocs_replace_text", description: "Find and replace text in a document.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, document_id: { type: "string" as const }, find: { type: "string" as const }, replace: { type: "string" as const } }, required: ["identity", "document_id", "find", "replace"] } },
  { name: "gdocs_export_as", description: "Export document as PDF, docx, txt, etc.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, document_id: { type: "string" as const }, format: { type: "string" as const } }, required: ["identity", "document_id", "format"] } },
  // Sheets
  { name: "gsheets_create_spreadsheet", description: "Create a new spreadsheet.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, title: { type: "string" as const } }, required: ["identity", "title"] } },
  { name: "gsheets_get_spreadsheet", description: "Get spreadsheet metadata.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, spreadsheet_id: { type: "string" as const } }, required: ["identity", "spreadsheet_id"] } },
  { name: "gsheets_read_range", description: "Read data from a range.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, spreadsheet_id: { type: "string" as const }, range: { type: "string" as const } }, required: ["identity", "spreadsheet_id", "range"] } },
  { name: "gsheets_write_range", description: "Write data to a range.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, spreadsheet_id: { type: "string" as const }, range: { type: "string" as const }, values: { type: "array" as const } }, required: ["identity", "spreadsheet_id", "range", "values"] } },
  { name: "gsheets_append_row", description: "Append a row to a sheet.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, spreadsheet_id: { type: "string" as const }, range: { type: "string" as const }, values: { type: "array" as const } }, required: ["identity", "spreadsheet_id", "range", "values"] } },
  // Calendar
  { name: "gcal_today_events", description: "Get today's calendar events.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const } }, required: ["identity"] } },
  { name: "gcal_upcoming_events", description: "Get upcoming events.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, max_results: { type: "number" as const }, days_ahead: { type: "number" as const } }, required: ["identity"] } },
  { name: "gcal_create_event", description: "Create a calendar event.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, summary: { type: "string" as const }, start: { type: "string" as const }, end: { type: "string" as const }, description: { type: "string" as const }, location: { type: "string" as const } }, required: ["identity", "summary", "start", "end"] } },
  { name: "gcal_update_event", description: "Update a calendar event.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, event_id: { type: "string" as const }, summary: { type: "string" as const }, start: { type: "string" as const }, end: { type: "string" as const }, description: { type: "string" as const } }, required: ["identity", "event_id"] } },
  { name: "gcal_delete_event", description: "Delete a calendar event.", inputSchema: { type: "object" as const, properties: { identity: { type: "string" as const }, event_id: { type: "string" as const } }, required: ["identity", "event_id"] } },
];

// ============ Docs Helper ============

function extractDocText(doc: Record<string, unknown>): string {
  const body = doc.body as { content?: Array<{ paragraph?: { elements?: Array<{ textRun?: { content: string } }> } }> } | undefined;
  if (!body?.content) return "";
  const parts: string[] = [];
  for (const block of body.content) {
    if (block.paragraph?.elements) {
      for (const el of block.paragraph.elements) {
        if (el.textRun?.content) parts.push(el.textRun.content);
      }
    }
  }
  return parts.join("");
}

// ============ Handler ============

export async function handleDrive(env: Env, name: string, args: Record<string, unknown>): Promise<string> {
  const identity = typeof args.identity === "string" ? validateIdentity(env, args.identity) : "";
  const FIELDS = "id,name,mimeType,size,modifiedTime,createdTime,parents,webViewLink,owners";

  switch (name) {
    // ==================== DRIVE ====================
    case "gdrive_list_identities":
      return JSON.stringify({ identities: getIdentityList(env) });

    case "gdrive_test_connection": {
      const about = await googleGet<{ user: { displayName: string; emailAddress: string } }>(env, identity, `${DRIVE_API}/about?fields=user`);
      return JSON.stringify({ identity, user: about.user, status: "connected" });
    }

    case "gdrive_get_storage_quota": {
      const about = await googleGet<{ storageQuota: { limit: string; usage: string; usageInDrive: string } }>(env, identity, `${DRIVE_API}/about?fields=storageQuota`);
      const q = about.storageQuota;
      return JSON.stringify({ identity, limit: formatSize(Number(q.limit)), used: formatSize(Number(q.usage)), drive_used: formatSize(Number(q.usageInDrive)) });
    }

    case "gdrive_list_files": {
      const maxResults = Math.min(Number(args.max_results) || 20, 100);
      const orderBy = typeof args.order_by === "string" ? args.order_by : "modifiedTime desc";
      let q = "trashed = false";
      if (args.file_type === "document") q += " and mimeType = 'application/vnd.google-apps.document'";
      else if (args.file_type === "spreadsheet") q += " and mimeType = 'application/vnd.google-apps.spreadsheet'";
      else if (args.file_type === "folder") q += " and mimeType = 'application/vnd.google-apps.folder'";
      const params = qs({ q, pageSize: String(maxResults), orderBy, fields: `files(${FIELDS})` });
      const result = await googleGet<{ files: unknown[] }>(env, identity, `${DRIVE_API}/files?${params}`);
      return JSON.stringify({ identity, count: result.files.length, files: result.files });
    }

    case "gdrive_search": {
      const maxResults = Math.min(Number(args.max_results) || 20, 100);
      const q = `name contains '${String(args.query).replace(/'/g, "\\'")}' and trashed = false`;
      const params = qs({ q, pageSize: String(maxResults), fields: `files(${FIELDS})` });
      const result = await googleGet<{ files: unknown[] }>(env, identity, `${DRIVE_API}/files?${params}`);
      return JSON.stringify({ identity, query: args.query, count: result.files.length, files: result.files });
    }

    case "gdrive_get_file_info": {
      const file = await googleGet(env, identity, `${DRIVE_API}/files/${args.file_id}?fields=${FIELDS},description,permissions`);
      return JSON.stringify(file);
    }

    case "gdrive_list_shared_with_me": {
      const maxResults = Math.min(Number(args.max_results) || 20, 100);
      const params = qs({ q: "sharedWithMe = true and trashed = false", pageSize: String(maxResults), fields: `files(${FIELDS})` });
      const result = await googleGet<{ files: unknown[] }>(env, identity, `${DRIVE_API}/files?${params}`);
      return JSON.stringify({ identity, count: result.files.length, files: result.files });
    }

    case "gdrive_create_folder": {
      const body: Record<string, unknown> = { name: String(args.name), mimeType: "application/vnd.google-apps.folder" };
      if (args.parent_id) body.parents = [args.parent_id];
      const folder = await googlePost(env, identity, `${DRIVE_API}/files`, body);
      return JSON.stringify({ identity, created: true, folder });
    }

    case "gdrive_list_folder": {
      const q = `'${args.folder_id}' in parents and trashed = false`;
      const params = qs({ q, pageSize: "100", fields: `files(${FIELDS})` });
      const result = await googleGet<{ files: unknown[] }>(env, identity, `${DRIVE_API}/files?${params}`);
      return JSON.stringify({ identity, folder_id: args.folder_id, count: result.files.length, files: result.files });
    }

    case "gdrive_copy_file": {
      const body: Record<string, unknown> = {};
      if (args.new_name) body.name = args.new_name;
      const copy = await googlePost(env, identity, `${DRIVE_API}/files/${args.file_id}/copy`, body);
      return JSON.stringify({ identity, copied: true, file: copy });
    }

    case "gdrive_move_file": {
      const file = await googleGet<{ parents: string[] }>(env, identity, `${DRIVE_API}/files/${args.file_id}?fields=parents`);
      const oldParents = (file.parents || []).join(",");
      const params = qs({ addParents: String(args.new_parent_id), removeParents: oldParents, fields: FIELDS });
      const moved = await googlePatch(env, identity, `${DRIVE_API}/files/${args.file_id}?${params}`, {});
      return JSON.stringify({ identity, moved: true, file: moved });
    }

    case "gdrive_rename_file": {
      const renamed = await googlePatch(env, identity, `${DRIVE_API}/files/${args.file_id}?fields=${FIELDS}`, { name: String(args.new_name) });
      return JSON.stringify({ identity, renamed: true, file: renamed });
    }

    case "gdrive_trash_file": {
      await googlePatch(env, identity, `${DRIVE_API}/files/${args.file_id}`, { trashed: true });
      return JSON.stringify({ identity, file_id: args.file_id, trashed: true });
    }

    case "gdrive_delete_file": {
      await googleDelete(env, identity, `${DRIVE_API}/files/${args.file_id}`);
      return JSON.stringify({ identity, file_id: args.file_id, deleted: true });
    }

    case "gdrive_share_file": {
      const role = typeof args.role === "string" ? args.role : "reader";
      const perm = await googlePost(env, identity, `${DRIVE_API}/files/${args.file_id}/permissions`, {
        type: "user", role, emailAddress: String(args.email),
      });
      return JSON.stringify({ identity, shared: true, permission: perm });
    }

    case "gdrive_create_share_link": {
      const role = typeof args.role === "string" ? args.role : "reader";
      await googlePost(env, identity, `${DRIVE_API}/files/${args.file_id}/permissions`, { type: "anyone", role });
      const file = await googleGet<{ webViewLink: string }>(env, identity, `${DRIVE_API}/files/${args.file_id}?fields=webViewLink`);
      return JSON.stringify({ identity, file_id: args.file_id, link: file.webViewLink });
    }

    case "gdrive_get_permissions": {
      const result = await googleGet<{ permissions: unknown[] }>(env, identity, `${DRIVE_API}/files/${args.file_id}/permissions?fields=permissions(id,type,role,emailAddress,displayName)`);
      return JSON.stringify({ identity, file_id: args.file_id, permissions: result.permissions });
    }

    case "gdrive_unshare_file": {
      await googleDelete(env, identity, `${DRIVE_API}/files/${args.file_id}/permissions/${args.permission_id}`);
      return JSON.stringify({ identity, file_id: args.file_id, permission_id: args.permission_id, removed: true });
    }

    // ==================== DOCS ====================
    case "gdocs_create_document": {
      const doc = await googlePost<{ documentId: string; title: string }>(env, identity, DOCS_API, { title: String(args.title) });
      if (args.content) {
        await googlePost(env, identity, `${DOCS_API}/${doc.documentId}:batchUpdate`, {
          requests: [{ insertText: { location: { index: 1 }, text: String(args.content) } }],
        });
      }
      return JSON.stringify({ identity, created: true, document_id: doc.documentId, title: doc.title });
    }

    case "gdocs_get_document": {
      const doc = await googleGet(env, identity, `${DOCS_API}/${args.document_id}`);
      return JSON.stringify(doc);
    }

    case "gdocs_read_content": {
      const doc = await googleGet<Record<string, unknown>>(env, identity, `${DOCS_API}/${args.document_id}`);
      const text = extractDocText(doc);
      return JSON.stringify({ identity, document_id: args.document_id, title: doc.title, content: text.slice(0, 10000) });
    }

    case "gdocs_append_text": {
      const doc = await googleGet<Record<string, unknown>>(env, identity, `${DOCS_API}/${args.document_id}`);
      const body = doc.body as { content?: Array<{ endIndex?: number }> } | undefined;
      const endIndex = body?.content?.slice(-1)[0]?.endIndex || 1;
      await googlePost(env, identity, `${DOCS_API}/${args.document_id}:batchUpdate`, {
        requests: [{ insertText: { location: { index: Math.max(1, endIndex - 1) }, text: String(args.text) } }],
      });
      return JSON.stringify({ identity, document_id: args.document_id, appended: true });
    }

    case "gdocs_insert_text": {
      await googlePost(env, identity, `${DOCS_API}/${args.document_id}:batchUpdate`, {
        requests: [{ insertText: { location: { index: Number(args.index) || 1 }, text: String(args.text) } }],
      });
      return JSON.stringify({ identity, document_id: args.document_id, inserted: true });
    }

    case "gdocs_replace_text": {
      const result = await googlePost<{ replies: Array<{ replaceAllText: { occurrencesChanged: number } }> }>(
        env, identity, `${DOCS_API}/${args.document_id}:batchUpdate`, {
          requests: [{ replaceAllText: { containsText: { text: String(args.find), matchCase: true }, replaceText: String(args.replace) } }],
        },
      );
      const changed = result.replies?.[0]?.replaceAllText?.occurrencesChanged || 0;
      return JSON.stringify({ identity, document_id: args.document_id, replaced: changed });
    }

    case "gdocs_export_as": {
      const mimeMap: Record<string, string> = { pdf: "application/pdf", docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", txt: "text/plain", html: "text/html" };
      const mime = mimeMap[String(args.format)] || mimeMap.pdf;
      const response = await googleFetch(env, identity, `${DRIVE_API}/files/${args.document_id}/export?mimeType=${encodeURIComponent(mime)}`);
      if (!response.ok) throw new Error(`Export failed: ${response.status}`);
      const size = response.headers.get("content-length");
      return JSON.stringify({ identity, document_id: args.document_id, format: args.format, exported: true, size: size ? formatSize(Number(size)) : "unknown" });
    }

    // ==================== SHEETS ====================
    case "gsheets_create_spreadsheet": {
      const ss = await googlePost<{ spreadsheetId: string; properties: { title: string } }>(env, identity, SHEETS_API, { properties: { title: String(args.title) } });
      return JSON.stringify({ identity, created: true, spreadsheet_id: ss.spreadsheetId, title: ss.properties.title });
    }

    case "gsheets_get_spreadsheet": {
      const ss = await googleGet(env, identity, `${SHEETS_API}/${args.spreadsheet_id}?fields=spreadsheetId,properties,sheets.properties`);
      return JSON.stringify(ss);
    }

    case "gsheets_read_range": {
      const result = await googleGet<{ values?: unknown[][] }>(env, identity, `${SHEETS_API}/${args.spreadsheet_id}/values/${encodeURIComponent(String(args.range))}`);
      return JSON.stringify({ identity, spreadsheet_id: args.spreadsheet_id, range: args.range, values: result.values || [] });
    }

    case "gsheets_write_range": {
      const result = await googlePut(env, identity,
        `${SHEETS_API}/${args.spreadsheet_id}/values/${encodeURIComponent(String(args.range))}?valueInputOption=USER_ENTERED`,
        { values: args.values },
      );
      return JSON.stringify({ identity, spreadsheet_id: args.spreadsheet_id, written: true, result });
    }

    case "gsheets_append_row": {
      const result = await googlePost(env, identity,
        `${SHEETS_API}/${args.spreadsheet_id}/values/${encodeURIComponent(String(args.range))}:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS`,
        { values: Array.isArray(args.values) && Array.isArray((args.values as unknown[])[0]) ? args.values : [args.values] },
      );
      return JSON.stringify({ identity, spreadsheet_id: args.spreadsheet_id, appended: true, result });
    }

    // ==================== CALENDAR ====================
    case "gcal_today_events": {
      const now = new Date();
      const startOfDay = new Date(now.getFullYear(), now.getMonth(), now.getDate()).toISOString();
      const endOfDay = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1).toISOString();
      const params = qs({ timeMin: startOfDay, timeMax: endOfDay, singleEvents: "true", orderBy: "startTime" });
      const result = await googleGet<{ items: unknown[] }>(env, identity, `${CALENDAR_API}/calendars/primary/events?${params}`);
      return JSON.stringify({ identity, date: startOfDay.slice(0, 10), count: (result.items || []).length, events: result.items || [] });
    }

    case "gcal_upcoming_events": {
      const maxResults = Math.min(Number(args.max_results) || 10, 50);
      const daysAhead = Number(args.days_ahead) || 7;
      const now = new Date();
      const end = new Date(now.getTime() + daysAhead * 86400000);
      const params = qs({ timeMin: now.toISOString(), timeMax: end.toISOString(), singleEvents: "true", orderBy: "startTime", maxResults: String(maxResults) });
      const result = await googleGet<{ items: unknown[] }>(env, identity, `${CALENDAR_API}/calendars/primary/events?${params}`);
      return JSON.stringify({ identity, count: (result.items || []).length, events: result.items || [] });
    }

    case "gcal_create_event": {
      const event: Record<string, unknown> = {
        summary: String(args.summary),
        start: { dateTime: String(args.start) },
        end: { dateTime: String(args.end) },
      };
      if (args.description) event.description = String(args.description);
      if (args.location) event.location = String(args.location);
      const created = await googlePost(env, identity, `${CALENDAR_API}/calendars/primary/events`, event);
      return JSON.stringify({ identity, created: true, event: created });
    }

    case "gcal_update_event": {
      const updates: Record<string, unknown> = {};
      if (args.summary) updates.summary = String(args.summary);
      if (args.start) updates.start = { dateTime: String(args.start) };
      if (args.end) updates.end = { dateTime: String(args.end) };
      if (args.description) updates.description = String(args.description);
      const updated = await googlePatch(env, identity, `${CALENDAR_API}/calendars/primary/events/${args.event_id}`, updates);
      return JSON.stringify({ identity, updated: true, event: updated });
    }

    case "gcal_delete_event": {
      await googleDelete(env, identity, `${CALENDAR_API}/calendars/primary/events/${args.event_id}`);
      return JSON.stringify({ identity, event_id: args.event_id, deleted: true });
    }

    default:
      throw new Error(`Unknown Drive/Docs/Sheets/Calendar tool: ${name}`);
  }
}
