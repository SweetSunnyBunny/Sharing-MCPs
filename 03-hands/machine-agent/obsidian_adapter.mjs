import { getVaultPath } from "./tools/obsidian/dist/utils.js";
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
} from "./tools/obsidian/dist/tools.js";

const TOOL_MAP = {
  read_note: async (args) => readNote(args.path),
  write_note: async (args) =>
    writeNote(args.path, args.content, {
      createFrontmatter: args.createFrontmatter,
      frontmatter: args.frontmatter,
      overwrite: args.overwrite,
    }),
  append_to_note: async (args) =>
    appendToNote(args.path, args.content, {
      addTimestamp: args.addTimestamp,
      separator: args.separator,
    }),
  delete_note: async (args) => deleteNote(args.path),
  move_note: async (args) => moveNote(args.sourcePath, args.destPath),
  list_notes: async (args) =>
    listNotes(args.path, {
      recursive: args.recursive,
      includeContent: args.includeContent,
    }),
  list_folders: async (args) => listFolders(args.path),
  create_folder: async (args) => createFolder(args.path),
  search_notes: async (args) =>
    searchNotes(args.query, {
      caseSensitive: args.caseSensitive,
      includeContent: args.includeContent,
      maxResults: args.maxResults,
      searchPath: args.searchPath,
    }),
  search_by_tag: async (args) =>
    searchByTag(args.tag, {
      includeContent: args.includeContent,
      maxResults: args.maxResults,
    }),
  get_recent_notes: async (args) =>
    getRecentNotes({
      limit: args.limit,
      days: args.days,
    }),
  list_tags: async () => listAllTags(),
  get_backlinks: async (args) => getBacklinks(args.path),
  get_outgoing_links: async (args) => getOutgoingLinks(args.path),
  get_frontmatter: async (args) => getFrontmatter(args.path),
  update_frontmatter: async (args) => updateFrontmatter(args.path, args.updates),
  list_templates: async () => listTemplates(),
  create_from_template: async (args) =>
    createFromTemplate(args.template, args.destPath, args.variables),
  create_daily_note: async (args) =>
    createDailyNote({
      date: args.date ? new Date(args.date) : undefined,
      template: args.template,
      folder: args.folder,
    }),
  add_journal_entry: async (args) =>
    addJournalEntry(args.content, {
      journalPath: args.journalPath,
      author: args.author,
    }),
  get_vault_stats: async () => getVaultStats(),
  get_vault_path: async () => ({ vaultPath: getVaultPath() }),
};

const toolName = process.argv[2];
const argsJson = process.argv[3] || "{}";

try {
  const handler = TOOL_MAP[toolName];
  if (!handler) {
    throw new Error(`Unknown Obsidian tool: ${toolName}`);
  }

  const args = JSON.parse(argsJson);
  const result = await handler(args);
  process.stdout.write(JSON.stringify({ ok: true, result }));
} catch (error) {
  process.stdout.write(
    JSON.stringify({
      ok: false,
      error: error instanceof Error ? error.message : String(error),
    })
  );
  process.exitCode = 1;
}
