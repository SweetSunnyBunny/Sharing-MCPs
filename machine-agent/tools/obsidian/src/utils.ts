import * as fs from "fs/promises";
import * as path from "path";
import matter from "gray-matter";

// Configure OBSIDIAN_VAULT_PATH before using the adapter.
export const DEFAULT_VAULT_PATH = process.env.OBSIDIAN_VAULT_PATH || "./vault";

/**
 * Get the configured vault path from environment or use default
 */
export function getVaultPath(): string {
  return process.env.OBSIDIAN_VAULT_PATH || DEFAULT_VAULT_PATH;
}

/**
 * Normalize a path to use forward slashes and resolve relative to vault
 */
export function normalizePath(filePath: string, vaultPath?: string): string {
  const vault = vaultPath || getVaultPath();

  // If it's already an absolute path within the vault, use it
  if (path.isAbsolute(filePath)) {
    return filePath;
  }

  // Otherwise, resolve relative to vault
  return path.join(vault, filePath);
}

/**
 * Ensure a path is within the vault (security check)
 */
export function isWithinVault(filePath: string, vaultPath?: string): boolean {
  const vault = vaultPath || getVaultPath();
  const resolved = path.resolve(filePath);
  const resolvedVault = path.resolve(vault);
  return resolved.startsWith(resolvedVault);
}

/**
 * Get relative path from vault root
 */
export function getRelativePath(filePath: string, vaultPath?: string): string {
  const vault = vaultPath || getVaultPath();
  return path.relative(vault, filePath);
}

/**
 * Ensure the file has .md extension
 */
export function ensureMarkdownExtension(filePath: string): string {
  if (!filePath.endsWith(".md")) {
    return filePath + ".md";
  }
  return filePath;
}

/**
 * Parse frontmatter from markdown content
 */
export function parseFrontmatter(content: string): {
  data: Record<string, unknown>;
  content: string;
} {
  const parsed = matter(content);
  return {
    data: parsed.data as Record<string, unknown>,
    content: parsed.content,
  };
}

/**
 * Stringify frontmatter back to markdown
 */
export function stringifyFrontmatter(
  data: Record<string, unknown>,
  content: string
): string {
  return matter.stringify(content, data);
}

/**
 * Extract wiki-style links from markdown content [[link]] or [[link|alias]]
 */
export function extractWikiLinks(content: string): string[] {
  const linkRegex = /\[\[([^\]|]+)(?:\|[^\]]+)?\]\]/g;
  const links: string[] = [];
  let match;

  while ((match = linkRegex.exec(content)) !== null) {
    links.push(match[1].trim());
  }

  return [...new Set(links)]; // Remove duplicates
}

/**
 * Extract tags from content (both frontmatter and inline #tags)
 */
export function extractTags(content: string, frontmatterData?: Record<string, unknown>): string[] {
  const tags: string[] = [];

  // Get tags from frontmatter
  if (frontmatterData?.tags) {
    if (Array.isArray(frontmatterData.tags)) {
      tags.push(...frontmatterData.tags.map((t: unknown) => String(t)));
    } else if (typeof frontmatterData.tags === "string") {
      tags.push(frontmatterData.tags);
    }
  }

  // Extract inline tags (but not in code blocks or links)
  const inlineTagRegex = /(?:^|\s)#([a-zA-Z][a-zA-Z0-9_/-]*)/g;
  let match;

  while ((match = inlineTagRegex.exec(content)) !== null) {
    tags.push(match[1]);
  }

  return [...new Set(tags)]; // Remove duplicates
}

/**
 * Check if a file exists
 */
export async function fileExists(filePath: string): Promise<boolean> {
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
}

/**
 * Ensure directory exists, create if not
 */
export async function ensureDirectory(dirPath: string): Promise<void> {
  try {
    await fs.mkdir(dirPath, { recursive: true });
  } catch (error) {
    // Directory might already exist
    if ((error as NodeJS.ErrnoException).code !== "EEXIST") {
      throw error;
    }
  }
}

/**
 * Get file stats with error handling
 */
export async function getFileStats(filePath: string): Promise<{
  created: Date;
  modified: Date;
  size: number;
} | null> {
  try {
    const stats = await fs.stat(filePath);
    return {
      created: stats.birthtime,
      modified: stats.mtime,
      size: stats.size,
    };
  } catch {
    return null;
  }
}

/**
 * Format a date for daily notes (YYYY-MM-DD)
 */
export function formatDateForDailyNote(date: Date = new Date()): string {
  return date.toISOString().split("T")[0];
}

/**
 * Generate a timestamp for journal entries
 */
export function generateTimestamp(date: Date = new Date()): string {
  return date.toISOString().replace("T", " ").split(".")[0];
}

/**
 * Sanitize a filename (remove invalid characters)
 */
export function sanitizeFilename(filename: string): string {
  return filename
    .replace(/[<>:"/\\|?*]/g, "-")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Create default frontmatter for a new note
 */
export function createDefaultFrontmatter(options: {
  title?: string;
  tags?: string[];
  author?: string;
  created?: Date;
}): Record<string, unknown> {
  const now = options.created || new Date();
  return {
    title: options.title || "Untitled",
    created: now.toISOString(),
    modified: now.toISOString(),
    tags: options.tags || [],
    author: options.author || "Claude",
  };
}
