// The Worker cannot write a Windows path itself. Use the existing authenticated
// machine-agent bridge, and require the filesystem tool's own success receipt.

export interface ImageArchiveConfig {
  machineAgentUrl?: string;
  machineAgentApiKey?: string;
  imageArchiveDir?: string;
}

export async function archiveGeneratedImage(
  rawBase64: string,
  outputFormat: string,
  identity: unknown,
  config?: ImageArchiveConfig,
): Promise<string> {
  const archiveDir = config?.imageArchiveDir?.trim().replace(/[\\/]+$/, '');
  if (!config?.machineAgentUrl || !config.machineAgentApiKey || !archiveDir) {
    return 'WARNING: Local image backup is not configured; image was NOT saved to the vault.';
  }
  const safeIdentity = (typeof identity === 'string' ? identity : 'default')
    .replace(/[^A-Za-z0-9_-]/g, '_').slice(0, 80) || 'default';
  const extension = outputFormat === 'jpeg' ? 'jpg' : outputFormat === 'webp' ? 'webp' : 'png';
  const separator = archiveDir.includes('\\') ? '\\' : '/';
  const path = `${archiveDir}${separator}gpt_image_${safeIdentity}_${Date.now()}_${crypto.randomUUID()}.${extension}`;
  // Reuse the same path on a transport retry: one generation, one archive file.
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const response = await fetch(`${config.machineAgentUrl.replace(/\/$/, '')}/tools/invoke`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${config.machineAgentApiKey}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          tool: 'fs_write_binary',
          arguments: { path, base64_content: rawBase64, create_dirs: true },
        }),
        signal: AbortSignal.timeout(20000),
      });
      const receipt = await response.json() as {
        ok?: boolean;
        result?: { success?: boolean; path?: string };
      };
      if (response.ok && receipt.ok === true && receipt.result?.success === true
          && receipt.result.path === path) {
        return `Saved to: ${path}`;
      }
    } catch {
      // A failed backup must not conceal the generated image or a Discord post.
    }
  }
  return 'WARNING: Local image backup failed; vault save is UNCONFIRMED. Keep the returned image or Discord attachment.';
}
