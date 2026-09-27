import * as fs from "fs/promises";
import * as path from "path";
import { glob } from "glob";
import {
  getVaultPath,
  normalizePath,
  isWithinVault,
  getRelativePath,
  ensureMarkdownExtension,
  parseFrontmatter,
  stringifyFrontmatter,
  extractWikiLinks,
  extractTags,
  fileExists,
  ensureDirectory,
  getFileStats,
  formatDateForDailyNote,
  generateTimestamp,
  sanitizeFilename,
  createDefaultFrontmatter,
} from "./utils.js";

// ============================================================================
// CORE FILE OPERATIONS
// ============================================================================

/**
 * Read a note from the vault
 */
export async function readNote(notePath: string): Promise<{
  path: string;
  content: string;
  frontmatter: Record<string, unknown>;
  body: string;
  links: string[];
  tags: string[];
}> {
  const fullPath = normalizePath(ensureMarkdownExtension(notePath));

  if (!isWithinVault(fullPath)) {
    throw new Error("Path is outside the vault");
  }

  const content = await fs.readFile(fullPath, "utf-8");
  const { data: frontmatter, content: body } = parseFrontmatter(content);
  const links = extractWikiLinks(content);
  const tags = extractTags(body, frontmatter);

  return {
    path: getRelativePath(fullPath),
    content,
    frontmatter,
    body,
    links,
    tags,
  };
}

/**
 * Write/create a note in the vault
 */
export async function writeNote(
  notePath: string,
  content: string,
  options?: {
    createFrontmatter?: boolean;
    frontmatter?: Record<string, unknown>;
    overwrite?: boolean;
  }
): Promise<{ path: string; created: boolean }> {
  const fullPath = normalizePath(ensureMarkdownExtension(notePath));

  if (!isWithinVault(fullPath)) {
    throw new Error("Path is outside the vault");
  }

  const exists = await fileExists(fullPath);

  if (exists && options?.overwrite === false) {
    throw new Error("File already exists and overwrite is false");
  }

  // Ensure parent directory exists
  await ensureDirectory(path.dirname(fullPath));

  let finalContent = content;

  // Add frontmatter if requested
  if (options?.createFrontmatter || options?.frontmatter) {
    const { data: existingFrontmatter, content: bodyContent } = parseFrontmatter(content);
    const frontmatter = {
      ...createDefaultFrontmatter({ title: path.basename(notePath, ".md") }),
      ...existingFrontmatter,
      ...options?.frontmatter,
      modified: new Date().toISOString(),
    };
    finalContent = stringifyFrontmatter(frontmatter, bodyContent);
  }

  await fs.writeFile(fullPath, finalContent, "utf-8");

  return {
    path: getRelativePath(fullPath),
    created: !exists,
  };
}

/**
 * Append content to an existing note
 */
export async function appendToNote(
  notePath: string,
  content: string,
  options?: {
    addTimestamp?: boolean;
    separator?: string;
  }
): Promise<{ path: string; success: boolean }> {
  const fullPath = normalizePath(ensureMarkdownExtension(notePath));

  if (!isWithinVault(fullPath)) {
    throw new Error("Path is outside the vault");
  }

  const exists = await fileExists(fullPath);
  if (!exists) {
    throw new Error("Note does not exist. Use writeNote to create it first.");
  }

  const existingContent = await fs.readFile(fullPath, "utf-8");
  const separator = options?.separator ?? "\n\n";
  const timestamp = options?.addTimestamp ? `\n\n---\n*${generateTimestamp()}*\n\n` : "";

  const newContent = existingContent + separator + timestamp + content;

  // Update modified date in frontmatter if it exists
  const { data: frontmatter, content: body } = parseFrontmatter(newContent);
  if (Object.keys(frontmatter).length > 0) {
    frontmatter.modified = new Date().toISOString();
    await fs.writeFile(fullPath, stringifyFrontmatter(frontmatter, body), "utf-8");
  } else {
    await fs.writeFile(fullPath, newContent, "utf-8");
  }

  return {
    path: getRelativePath(fullPath),
    success: true,
  };
}

/**
 * Delete a note from the vault
 */
export async function deleteNote(notePath: string): Promise<{ path: string; deleted: boolean }> {
  const fullPath = normalizePath(ensureMarkdownExtension(notePath));

  if (!isWithinVault(fullPath)) {
    throw new Error("Path is outside the vault");
  }

  const exists = await fileExists(fullPath);
  if (!exists) {
    return { path: getRelativePath(fullPath), deleted: false };
  }

  await fs.unlink(fullPath);
  return { path: getRelativePath(fullPath), deleted: true };
}

/**
 * Move/rename a note
 */
export async function moveNote(
  sourcePath: string,
  destPath: string
): Promise<{ sourcePath: string; destPath: string; moved: boolean }> {
  const fullSource = normalizePath(ensureMarkdownExtension(sourcePath));
  const fullDest = normalizePath(ensureMarkdownExtension(destPath));

  if (!isWithinVault(fullSource) || !isWithinVault(fullDest)) {
    throw new Error("Path is outside the vault");
  }

  const exists = await fileExists(fullSource);
  if (!exists) {
    throw new Error("Source note does not exist");
  }

  // Ensure destination directory exists
  await ensureDirectory(path.dirname(fullDest));

  await fs.rename(fullSource, fullDest);

  return {
    sourcePath: getRelativePath(fullSource),
    destPath: getRelativePath(fullDest),
    moved: true,
  };
}

/**
 * List notes in a directory
 */
export async function listNotes(
  dirPath?: string,
  options?: {
    recursive?: boolean;
    includeContent?: boolean;
  }
): Promise<
  Array<{
    path: string;
    name: string;
    stats?: { created: Date; modified: Date; size: number };
    frontmatter?: Record<string, unknown>;
  }>
> {
  const vaultPath = getVaultPath();
  const searchPath = dirPath ? normalizePath(dirPath) : vaultPath;

  if (!isWithinVault(searchPath)) {
    throw new Error("Path is outside the vault");
  }

  const pattern = options?.recursive ? "**/*.md" : "*.md";
  const files = await glob(pattern, {
    cwd: searchPath,
    absolute: true,
    nodir: true,
  });

  const results = await Promise.all(
    files.map(async (filePath) => {
      const stats = await getFileStats(filePath);
      const result: {
        path: string;
        name: string;
        stats?: { created: Date; modified: Date; size: number };
        frontmatter?: Record<string, unknown>;
      } = {
        path: getRelativePath(filePath),
        name: path.basename(filePath, ".md"),
        stats: stats || undefined,
      };

      if (options?.includeContent) {
        try {
          const content = await fs.readFile(filePath, "utf-8");
          const { data } = parseFrontmatter(content);
          result.frontmatter = data;
        } catch {
          // Skip files we can't read
        }
      }

      return result;
    })
  );

  return results.sort((a, b) => a.path.localeCompare(b.path));
}

/**
 * List folders in the vault
 */
export async function listFolders(dirPath?: string): Promise<
  Array<{
    path: string;
    name: string;
    noteCount: number;
  }>
> {
  const vaultPath = getVaultPath();
  const searchPath = dirPath ? normalizePath(dirPath) : vaultPath;

  if (!isWithinVault(searchPath)) {
    throw new Error("Path is outside the vault");
  }

  const entries = await fs.readdir(searchPath, { withFileTypes: true });
  const folders = entries.filter((entry) => entry.isDirectory() && !entry.name.startsWith("."));

  const results = await Promise.all(
    folders.map(async (folder) => {
      const folderPath = path.join(searchPath, folder.name);
      const files = await glob("**/*.md", { cwd: folderPath, nodir: true });

      return {
        path: getRelativePath(folderPath),
        name: folder.name,
        noteCount: files.length,
      };
    })
  );

  return results.sort((a, b) => a.name.localeCompare(b.name));
}

/**
 * Create a folder
 */
export async function createFolder(folderPath: string): Promise<{ path: string; created: boolean }> {
  const fullPath = normalizePath(folderPath);

  if (!isWithinVault(fullPath)) {
    throw new Error("Path is outside the vault");
  }

  const exists = await fileExists(fullPath);
  if (exists) {
    return { path: getRelativePath(fullPath), created: false };
  }

  await ensureDirectory(fullPath);
  return { path: getRelativePath(fullPath), created: true };
}

// ============================================================================
// SEARCH OPERATIONS
// ============================================================================

/**
 * Full-text search across notes
 */
export async function searchNotes(
  query: string,
  options?: {
    caseSensitive?: boolean;
    includeContent?: boolean;
    maxResults?: number;
    searchPath?: string;
  }
): Promise<
  Array<{
    path: string;
    name: string;
    matches: Array<{ line: number; text: string }>;
    frontmatter?: Record<string, unknown>;
  }>
> {
  const vaultPath = getVaultPath();
  const searchPath = options?.searchPath ? normalizePath(options.searchPath) : vaultPath;

  if (!isWithinVault(searchPath)) {
    throw new Error("Path is outside the vault");
  }

  const files = await glob("**/*.md", {
    cwd: searchPath,
    absolute: true,
    nodir: true,
  });

  const results: Array<{
    path: string;
    name: string;
    matches: Array<{ line: number; text: string }>;
    frontmatter?: Record<string, unknown>;
  }> = [];

  const searchRegex = new RegExp(
    query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"),
    options?.caseSensitive ? "g" : "gi"
  );

  for (const filePath of files) {
    try {
      const content = await fs.readFile(filePath, "utf-8");
      const lines = content.split("\n");
      const matches: Array<{ line: number; text: string }> = [];

      lines.forEach((line, index) => {
        if (searchRegex.test(line)) {
          matches.push({ line: index + 1, text: line.trim() });
          searchRegex.lastIndex = 0; // Reset regex state
        }
      });

      if (matches.length > 0) {
        const result: {
          path: string;
          name: string;
          matches: Array<{ line: number; text: string }>;
          frontmatter?: Record<string, unknown>;
        } = {
          path: getRelativePath(filePath),
          name: path.basename(filePath, ".md"),
          matches,
        };

        if (options?.includeContent) {
          const { data } = parseFrontmatter(content);
          result.frontmatter = data;
        }

        results.push(result);
      }
    } catch {
      // Skip files we can't read
    }

    if (options?.maxResults && results.length >= options.maxResults) {
      break;
    }
  }

  return results;
}

/**
 * Search notes by tag
 */
export async function searchByTag(
  tag: string,
  options?: {
    includeContent?: boolean;
    maxResults?: number;
  }
): Promise<
  Array<{
    path: string;
    name: string;
    tags: string[];
    frontmatter?: Record<string, unknown>;
  }>
> {
  const vaultPath = getVaultPath();
  const files = await glob("**/*.md", {
    cwd: vaultPath,
    absolute: true,
    nodir: true,
  });

  const results: Array<{
    path: string;
    name: string;
    tags: string[];
    frontmatter?: Record<string, unknown>;
  }> = [];

  const normalizedTag = tag.startsWith("#") ? tag.slice(1) : tag;

  for (const filePath of files) {
    try {
      const content = await fs.readFile(filePath, "utf-8");
      const { data: frontmatter, content: body } = parseFrontmatter(content);
      const tags = extractTags(body, frontmatter);

      if (tags.some((t) => t.toLowerCase() === normalizedTag.toLowerCase())) {
        const result: {
          path: string;
          name: string;
          tags: string[];
          frontmatter?: Record<string, unknown>;
        } = {
          path: getRelativePath(filePath),
          name: path.basename(filePath, ".md"),
          tags,
        };

        if (options?.includeContent) {
          result.frontmatter = frontmatter;
        }

        results.push(result);
      }
    } catch {
      // Skip files we can't read
    }

    if (options?.maxResults && results.length >= options.maxResults) {
      break;
    }
  }

  return results;
}

/**
 * Get recently modified notes
 */
export async function getRecentNotes(
  options?: {
    limit?: number;
    days?: number;
  }
): Promise<
  Array<{
    path: string;
    name: string;
    modified: Date;
    frontmatter?: Record<string, unknown>;
  }>
> {
  const vaultPath = getVaultPath();
  const limit = options?.limit ?? 20;
  const days = options?.days;

  const files = await glob("**/*.md", {
    cwd: vaultPath,
    absolute: true,
    nodir: true,
  });

  const notesWithStats = await Promise.all(
    files.map(async (filePath) => {
      const stats = await getFileStats(filePath);
      return { filePath, stats };
    })
  );

  let filtered = notesWithStats.filter((n) => n.stats !== null);

  if (days) {
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - days);
    filtered = filtered.filter((n) => n.stats!.modified >= cutoff);
  }

  filtered.sort((a, b) => b.stats!.modified.getTime() - a.stats!.modified.getTime());

  const results = await Promise.all(
    filtered.slice(0, limit).map(async ({ filePath, stats }) => {
      const content = await fs.readFile(filePath, "utf-8");
      const { data: frontmatter } = parseFrontmatter(content);

      return {
        path: getRelativePath(filePath),
        name: path.basename(filePath, ".md"),
        modified: stats!.modified,
        frontmatter,
      };
    })
  );

  return results;
}

/**
 * List all unique tags in the vault
 */
export async function listAllTags(): Promise<
  Array<{
    tag: string;
    count: number;
  }>
> {
  const vaultPath = getVaultPath();
  const files = await glob("**/*.md", {
    cwd: vaultPath,
    absolute: true,
    nodir: true,
  });

  const tagCounts = new Map<string, number>();

  for (const filePath of files) {
    try {
      const content = await fs.readFile(filePath, "utf-8");
      const { data: frontmatter, content: body } = parseFrontmatter(content);
      const tags = extractTags(body, frontmatter);

      for (const tag of tags) {
        tagCounts.set(tag, (tagCounts.get(tag) || 0) + 1);
      }
    } catch {
      // Skip files we can't read
    }
  }

  return Array.from(tagCounts.entries())
    .map(([tag, count]) => ({ tag, count }))
    .sort((a, b) => b.count - a.count);
}

// ============================================================================
// LINK OPERATIONS
// ============================================================================

/**
 * Get backlinks to a note (what links to this note)
 */
export async function getBacklinks(notePath: string): Promise<
  Array<{
    path: string;
    name: string;
    context: string[];
  }>
> {
  const vaultPath = getVaultPath();
  const targetName = path.basename(notePath, ".md");

  const files = await glob("**/*.md", {
    cwd: vaultPath,
    absolute: true,
    nodir: true,
  });

  const backlinks: Array<{
    path: string;
    name: string;
    context: string[];
  }> = [];

  for (const filePath of files) {
    try {
      const content = await fs.readFile(filePath, "utf-8");
      const links = extractWikiLinks(content);

      if (links.some((link) => link.toLowerCase() === targetName.toLowerCase())) {
        // Find the lines containing the links for context
        const lines = content.split("\n");
        const context: string[] = [];
        const linkRegex = new RegExp(`\\[\\[${targetName}(?:\\|[^\\]]+)?\\]\\]`, "gi");

        lines.forEach((line) => {
          if (linkRegex.test(line)) {
            context.push(line.trim());
            linkRegex.lastIndex = 0;
          }
        });

        backlinks.push({
          path: getRelativePath(filePath),
          name: path.basename(filePath, ".md"),
          context,
        });
      }
    } catch {
      // Skip files we can't read
    }
  }

  return backlinks;
}

/**
 * Get outgoing links from a note
 */
export async function getOutgoingLinks(notePath: string): Promise<
  Array<{
    link: string;
    exists: boolean;
    path?: string;
  }>
> {
  const fullPath = normalizePath(ensureMarkdownExtension(notePath));

  if (!isWithinVault(fullPath)) {
    throw new Error("Path is outside the vault");
  }

  const content = await fs.readFile(fullPath, "utf-8");
  const links = extractWikiLinks(content);
  const vaultPath = getVaultPath();

  const results = await Promise.all(
    links.map(async (link) => {
      // Try to find the linked file
      const possiblePaths = await glob(`**/${link}.md`, {
        cwd: vaultPath,
        absolute: true,
        nodir: true,
      });

      if (possiblePaths.length > 0) {
        return {
          link,
          exists: true,
          path: getRelativePath(possiblePaths[0]),
        };
      }

      return { link, exists: false };
    })
  );

  return results;
}

// ============================================================================
// FRONTMATTER OPERATIONS
// ============================================================================

/**
 * Get frontmatter from a note
 */
export async function getFrontmatter(notePath: string): Promise<{
  path: string;
  frontmatter: Record<string, unknown>;
}> {
  const fullPath = normalizePath(ensureMarkdownExtension(notePath));

  if (!isWithinVault(fullPath)) {
    throw new Error("Path is outside the vault");
  }

  const content = await fs.readFile(fullPath, "utf-8");
  const { data: frontmatter } = parseFrontmatter(content);

  return {
    path: getRelativePath(fullPath),
    frontmatter,
  };
}

/**
 * Update frontmatter fields (merge with existing)
 */
export async function updateFrontmatter(
  notePath: string,
  updates: Record<string, unknown>
): Promise<{
  path: string;
  frontmatter: Record<string, unknown>;
}> {
  const fullPath = normalizePath(ensureMarkdownExtension(notePath));

  if (!isWithinVault(fullPath)) {
    throw new Error("Path is outside the vault");
  }

  const content = await fs.readFile(fullPath, "utf-8");
  const { data: existingFrontmatter, content: body } = parseFrontmatter(content);

  const newFrontmatter = {
    ...existingFrontmatter,
    ...updates,
    modified: new Date().toISOString(),
  };

  const newContent = stringifyFrontmatter(newFrontmatter, body);
  await fs.writeFile(fullPath, newContent, "utf-8");

  return {
    path: getRelativePath(fullPath),
    frontmatter: newFrontmatter,
  };
}

// ============================================================================
// TEMPLATE & JOURNAL OPERATIONS
// ============================================================================

/**
 * List available templates
 */
export async function listTemplates(): Promise<
  Array<{
    path: string;
    name: string;
  }>
> {
  const vaultPath = getVaultPath();
  const templatePaths = ["Templates", "templates", "_templates"];

  for (const templateDir of templatePaths) {
    const templatePath = path.join(vaultPath, templateDir);
    if (await fileExists(templatePath)) {
      const files = await glob("*.md", {
        cwd: templatePath,
        absolute: true,
        nodir: true,
      });

      return files.map((filePath) => ({
        path: getRelativePath(filePath),
        name: path.basename(filePath, ".md"),
      }));
    }
  }

  return [];
}

/**
 * Create a note from a template
 */
export async function createFromTemplate(
  templateName: string,
  destPath: string,
  variables?: Record<string, string>
): Promise<{ path: string; created: boolean }> {
  const vaultPath = getVaultPath();
  const templatePaths = ["Templates", "templates", "_templates"];

  let templateContent: string | null = null;

  for (const templateDir of templatePaths) {
    const templatePath = path.join(
      vaultPath,
      templateDir,
      ensureMarkdownExtension(templateName)
    );
    if (await fileExists(templatePath)) {
      templateContent = await fs.readFile(templatePath, "utf-8");
      break;
    }
  }

  if (!templateContent) {
    throw new Error(`Template "${templateName}" not found`);
  }

  // Replace variables in template
  let processedContent = templateContent;
  if (variables) {
    for (const [key, value] of Object.entries(variables)) {
      processedContent = processedContent.replace(
        new RegExp(`{{${key}}}`, "g"),
        value
      );
    }
  }

  // Replace common date variables
  const now = new Date();
  processedContent = processedContent
    .replace(/{{date}}/g, formatDateForDailyNote(now))
    .replace(/{{time}}/g, now.toTimeString().split(" ")[0])
    .replace(/{{datetime}}/g, generateTimestamp(now))
    .replace(/{{title}}/g, path.basename(destPath, ".md"));

  return writeNote(destPath, processedContent, { createFrontmatter: true });
}

/**
 * Create or get today's daily note
 */
export async function createDailyNote(options?: {
  date?: Date;
  template?: string;
  folder?: string;
}): Promise<{
  path: string;
  created: boolean;
  content?: string;
}> {
  const date = options?.date || new Date();
  const dateStr = formatDateForDailyNote(date);
  const folder = options?.folder || "Daily Notes";
  const notePath = path.join(folder, `${dateStr}.md`);

  const fullPath = normalizePath(ensureMarkdownExtension(notePath));

  // Check if it already exists
  if (await fileExists(fullPath)) {
    const content = await fs.readFile(fullPath, "utf-8");
    return {
      path: getRelativePath(fullPath),
      created: false,
      content,
    };
  }

  // Create from template if specified
  if (options?.template) {
    const result = await createFromTemplate(options.template, notePath, {
      date: dateStr,
    });
    const content = await fs.readFile(normalizePath(ensureMarkdownExtension(notePath)), "utf-8");
    return { ...result, content };
  }

  // Create with default content
  const defaultContent = `# ${dateStr}\n\n## Notes\n\n`;
  const result = await writeNote(notePath, defaultContent, {
    createFrontmatter: true,
    frontmatter: {
      type: "daily-note",
      date: dateStr,
    },
  });

  return { ...result, content: defaultContent };
}

/**
 * Add a journal entry (append to today's daily note or a journal file)
 */
export async function addJournalEntry(
  content: string,
  options?: {
    journalPath?: string;
    author?: string;
  }
): Promise<{ path: string; success: boolean }> {
  const journalPath = options?.journalPath || `Daily Notes/${formatDateForDailyNote()}.md`;
  const fullPath = normalizePath(ensureMarkdownExtension(journalPath));

  // Create the note if it doesn't exist
  if (!(await fileExists(fullPath))) {
    await createDailyNote();
  }

  const timestamp = generateTimestamp();
  const author = options?.author || "Claude";
  const entry = `### ${timestamp} (${author})\n\n${content}`;

  return appendToNote(journalPath, entry, { separator: "\n\n---\n\n" });
}

// ============================================================================
// VAULT OPERATIONS
// ============================================================================

/**
 * Get vault statistics
 */
export async function getVaultStats(): Promise<{
  totalNotes: number;
  totalFolders: number;
  totalTags: number;
  totalLinks: number;
  recentlyModified: number;
  oldestNote?: { path: string; created: Date };
  newestNote?: { path: string; created: Date };
}> {
  const vaultPath = getVaultPath();

  const files = await glob("**/*.md", {
    cwd: vaultPath,
    absolute: true,
    nodir: true,
  });

  const folders = await glob("**/", {
    cwd: vaultPath,
    absolute: true,
  });

  const allTags = new Set<string>();
  let totalLinks = 0;
  let recentlyModified = 0;
  const oneWeekAgo = new Date();
  oneWeekAgo.setDate(oneWeekAgo.getDate() - 7);

  let oldestNote: { path: string; created: Date } | undefined;
  let newestNote: { path: string; created: Date } | undefined;

  for (const filePath of files) {
    try {
      const content = await fs.readFile(filePath, "utf-8");
      const { data: frontmatter, content: body } = parseFrontmatter(content);
      const tags = extractTags(body, frontmatter);
      const links = extractWikiLinks(content);

      tags.forEach((tag) => allTags.add(tag));
      totalLinks += links.length;

      const stats = await getFileStats(filePath);
      if (stats) {
        if (stats.modified >= oneWeekAgo) {
          recentlyModified++;
        }

        if (!oldestNote || stats.created < oldestNote.created) {
          oldestNote = { path: getRelativePath(filePath), created: stats.created };
        }
        if (!newestNote || stats.created > newestNote.created) {
          newestNote = { path: getRelativePath(filePath), created: stats.created };
        }
      }
    } catch {
      // Skip files we can't read
    }
  }

  return {
    totalNotes: files.length,
    totalFolders: folders.length - 1, // Exclude root
    totalTags: allTags.size,
    totalLinks,
    recentlyModified,
    oldestNote,
    newestNote,
  };
}
