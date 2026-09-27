#!/usr/bin/env node

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { createServer } from "http";
import { z } from "zod";
import {
  readNote,
  writeNote,
  appendToNote,
  deleteNote,
  moveNote,
  listNotes,
  listFolders,
  createFolder,
  searchNotes,
  searchByTag,
  getRecentNotes,
  listAllTags,
  getBacklinks,
  getOutgoingLinks,
  getFrontmatter,
  updateFrontmatter,
  listTemplates,
  createFromTemplate,
  createDailyNote,
  addJournalEntry,
  getVaultStats,
} from "./tools.js";
import { getVaultPath } from "./utils.js";

// Create the MCP server
const server = new McpServer({
  name: "obsidian-vault",
  version: "1.0.0",
  description: "MCP server for Obsidian vault access - your-main memory vault",
});

// ============================================================================
// CORE FILE TOOLS
// ============================================================================

server.tool(
  "read_note",
  "Read a note from the vault, including its content, frontmatter, links, and tags",
  {
    path: z.string().describe("Path to the note (relative to vault root, .md extension optional)"),
  },
  async ({ path }) => {
    try {
      const result = await readNote(path);
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Error reading note: ${(error as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

server.tool(
  "write_note",
  "Create or update a note in the vault",
  {
    path: z.string().describe("Path for the note (relative to vault root)"),
    content: z.string().describe("Content of the note (markdown)"),
    createFrontmatter: z.boolean().optional().describe("Auto-generate frontmatter if true"),
    frontmatter: z.record(z.unknown()).optional().describe("Custom frontmatter fields to include"),
    overwrite: z.boolean().optional().describe("If false, fail if file exists"),
  },
  async ({ path, content, createFrontmatter, frontmatter, overwrite }) => {
    try {
      const result = await writeNote(path, content, {
        createFrontmatter,
        frontmatter: frontmatter as Record<string, unknown>,
        overwrite,
      });
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Error writing note: ${(error as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

server.tool(
  "append_to_note",
  "Append content to an existing note",
  {
    path: z.string().describe("Path to the note"),
    content: z.string().describe("Content to append"),
    addTimestamp: z.boolean().optional().describe("Add timestamp before appended content"),
    separator: z.string().optional().describe("Separator between existing content and new content"),
  },
  async ({ path, content, addTimestamp, separator }) => {
    try {
      const result = await appendToNote(path, content, { addTimestamp, separator });
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Error appending to note: ${(error as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

server.tool(
  "delete_note",
  "Delete a note from the vault",
  {
    path: z.string().describe("Path to the note to delete"),
  },
  async ({ path }) => {
    try {
      const result = await deleteNote(path);
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Error deleting note: ${(error as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

server.tool(
  "move_note",
  "Move or rename a note within the vault",
  {
    sourcePath: z.string().describe("Current path of the note"),
    destPath: z.string().describe("New path for the note"),
  },
  async ({ sourcePath, destPath }) => {
    try {
      const result = await moveNote(sourcePath, destPath);
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Error moving note: ${(error as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

server.tool(
  "list_notes",
  "List notes in a directory",
  {
    path: z.string().optional().describe("Directory path (defaults to vault root)"),
    recursive: z.boolean().optional().describe("Include subdirectories"),
    includeContent: z.boolean().optional().describe("Include frontmatter in results"),
  },
  async ({ path, recursive, includeContent }) => {
    try {
      const result = await listNotes(path, { recursive, includeContent });
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Error listing notes: ${(error as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

server.tool(
  "list_folders",
  "List folders in the vault",
  {
    path: z.string().optional().describe("Directory to list folders from (defaults to vault root)"),
  },
  async ({ path }) => {
    try {
      const result = await listFolders(path);
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Error listing folders: ${(error as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

server.tool(
  "create_folder",
  "Create a new folder in the vault",
  {
    path: z.string().describe("Path for the new folder"),
  },
  async ({ path }) => {
    try {
      const result = await createFolder(path);
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Error creating folder: ${(error as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ============================================================================
// SEARCH TOOLS
// ============================================================================

server.tool(
  "search_notes",
  "Full-text search across notes in the vault",
  {
    query: z.string().describe("Search query"),
    caseSensitive: z.boolean().optional().describe("Case-sensitive search"),
    includeContent: z.boolean().optional().describe("Include frontmatter in results"),
    maxResults: z.number().optional().describe("Maximum number of results to return"),
    searchPath: z.string().optional().describe("Limit search to this directory"),
  },
  async ({ query, caseSensitive, includeContent, maxResults, searchPath }) => {
    try {
      const result = await searchNotes(query, {
        caseSensitive,
        includeContent,
        maxResults,
        searchPath,
      });
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Error searching notes: ${(error as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

server.tool(
  "search_by_tag",
  "Find notes with a specific tag",
  {
    tag: z.string().describe("Tag to search for (with or without #)"),
    includeContent: z.boolean().optional().describe("Include frontmatter in results"),
    maxResults: z.number().optional().describe("Maximum number of results"),
  },
  async ({ tag, includeContent, maxResults }) => {
    try {
      const result = await searchByTag(tag, { includeContent, maxResults });
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Error searching by tag: ${(error as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

server.tool(
  "get_recent_notes",
  "Get recently modified notes",
  {
    limit: z.number().optional().describe("Maximum number of notes to return (default 20)"),
    days: z.number().optional().describe("Only include notes modified within this many days"),
  },
  async ({ limit, days }) => {
    try {
      const result = await getRecentNotes({ limit, days });
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Error getting recent notes: ${(error as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

server.tool(
  "list_tags",
  "List all unique tags in the vault with counts",
  {},
  async () => {
    try {
      const result = await listAllTags();
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Error listing tags: ${(error as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ============================================================================
// LINK TOOLS
// ============================================================================

server.tool(
  "get_backlinks",
  "Find notes that link to a specific note",
  {
    path: z.string().describe("Path to the note to find backlinks for"),
  },
  async ({ path }) => {
    try {
      const result = await getBacklinks(path);
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Error getting backlinks: ${(error as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

server.tool(
  "get_outgoing_links",
  "Get all links from a note and check if they exist",
  {
    path: z.string().describe("Path to the note to get links from"),
  },
  async ({ path }) => {
    try {
      const result = await getOutgoingLinks(path);
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Error getting outgoing links: ${(error as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ============================================================================
// FRONTMATTER TOOLS
// ============================================================================

server.tool(
  "get_frontmatter",
  "Get the frontmatter/metadata from a note",
  {
    path: z.string().describe("Path to the note"),
  },
  async ({ path }) => {
    try {
      const result = await getFrontmatter(path);
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Error getting frontmatter: ${(error as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

server.tool(
  "update_frontmatter",
  "Update frontmatter fields on a note (merges with existing)",
  {
    path: z.string().describe("Path to the note"),
    updates: z.record(z.unknown()).describe("Fields to update or add"),
  },
  async ({ path, updates }) => {
    try {
      const result = await updateFrontmatter(path, updates as Record<string, unknown>);
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Error updating frontmatter: ${(error as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ============================================================================
// TEMPLATE & JOURNAL TOOLS
// ============================================================================

server.tool(
  "list_templates",
  "List available templates in the vault",
  {},
  async () => {
    try {
      const result = await listTemplates();
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Error listing templates: ${(error as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

server.tool(
  "create_from_template",
  "Create a new note from a template",
  {
    template: z.string().describe("Name of the template to use"),
    destPath: z.string().describe("Path for the new note"),
    variables: z.record(z.string()).optional().describe("Variables to replace in template ({{key}} format)"),
  },
  async ({ template, destPath, variables }) => {
    try {
      const result = await createFromTemplate(template, destPath, variables);
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Error creating from template: ${(error as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

server.tool(
  "create_daily_note",
  "Create or get today's daily note",
  {
    date: z.string().optional().describe("Date for the note (YYYY-MM-DD format, defaults to today)"),
    template: z.string().optional().describe("Template to use for new daily notes"),
    folder: z.string().optional().describe("Folder for daily notes (default: 'Daily Notes')"),
  },
  async ({ date, template, folder }) => {
    try {
      const dateObj = date ? new Date(date) : undefined;
      const result = await createDailyNote({ date: dateObj, template, folder });
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Error creating daily note: ${(error as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

server.tool(
  "add_journal_entry",
  "Add a timestamped journal entry to today's daily note or a specified journal file",
  {
    content: z.string().describe("The journal entry content"),
    journalPath: z.string().optional().describe("Path to journal file (defaults to today's daily note)"),
    author: z.string().optional().describe("Author of the entry (defaults to 'Claude')"),
  },
  async ({ content, journalPath, author }) => {
    try {
      const result = await addJournalEntry(content, { journalPath, author });
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Error adding journal entry: ${(error as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

// ============================================================================
// VAULT TOOLS
// ============================================================================

server.tool(
  "get_vault_stats",
  "Get statistics about the vault (note count, tag count, etc.)",
  {},
  async () => {
    try {
      const result = await getVaultStats();
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    } catch (error) {
      return {
        content: [
          {
            type: "text" as const,
            text: `Error getting vault stats: ${(error as Error).message}`,
          },
        ],
        isError: true,
      };
    }
  }
);

server.tool(
  "get_vault_path",
  "Get the current vault path",
  {},
  async () => {
    return {
      content: [
        {
          type: "text" as const,
          text: JSON.stringify({ vaultPath: getVaultPath() }, null, 2),
        },
      ],
    };
  }
);

// ============================================================================
// SERVER STARTUP
// ============================================================================

async function main() {
  console.error(`Obsidian MCP Server starting...`);
  console.error(`Vault path: ${getVaultPath()}`);

  const port = parseInt(process.env.MCP_HTTP_PORT || "0");

  if (port > 0) {
    const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
    await server.connect(transport);

    const httpServer = createServer(async (req, res) => {
      if (req.url === "/mcp") {
        await transport.handleRequest(req, res);
      } else {
        res.writeHead(404);
        res.end("Not found");
      }
    });

    httpServer.listen(port, "0.0.0.0", () => {
      console.error(`Obsidian MCP Server running on http://0.0.0.0:${port}/mcp`);
    });
  } else {
    const transport = new StdioServerTransport();
    await server.connect(transport);
    console.error("Obsidian MCP Server running on stdio");
  }
}

main().catch((error) => {
  console.error("Fatal error:", error);
  process.exit(1);
});
