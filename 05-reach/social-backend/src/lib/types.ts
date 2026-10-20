// Shared types for the social-backend worker

export interface Env {
  MCP_SECRET_PATH: string;
  ALLOWED_TOOLS: string;

  // Twitter
  TWITTER_CLIENT_ID: string;
  TWITTER_CLIENT_SECRET: string;
  TWITTER_ACCESS_TOKEN: string;   // Legacy single-account fallback
  TWITTER_REFRESH_TOKEN: string;  // Legacy single-account fallback
  TWITTER_ACCOUNTS: string;       // JSON: { "claude": { "access_token": "...", "refresh_token": "..." }, ... }

  // Moltbook
  MOLTBOOK_API_KEY: string;
  MOLTBOOK_AGENT_NAME: string;

  TELEGRAM_CONFIG: string;

  // ElevenLabs (optional, for Telegram voice)
  ELEVENLABS_API_KEY: string;

  // Tumblr
  TUMBLR_CLIENT_ID: string;
  TUMBLR_CLIENT_SECRET: string;
  TUMBLR_CONFIG: string; // JSON: { "avery": { "blog_name": "...", "access_token": "...", "refresh_token": "..." }, ... }

  // Reddit (Discord-Devvit bridge)
  REDDIT_DISCORD_BOT_TOKEN: string;
  REDDIT_DISCORD_CHANNEL: string;

  // World Tools
  WT_HOME_LAT: string;
  WT_HOME_LON: string;
  WT_HOME_LABEL: string;

  // KV namespaces
  TWITTER_TOKEN_KV: KVNamespace;

  [key: string]: string | KVNamespace;
}

export interface ToolDef {
  name: string;
  description: string;
  inputSchema: {
    type: 'object';
    properties: Record<string, unknown>;
    required?: string[];
  };
}

export type ToolHandler = (args: Record<string, unknown>, env: Env) => Promise<string>;

export interface ToolModule {
  tools: ToolDef[];
  handle: (name: string, args: Record<string, unknown>, env: Env) => Promise<string>;
}

// JSON-RPC
export interface JsonRpcRequest {
  jsonrpc: '2.0';
  method: string;
  id?: string | number;
  params?: Record<string, unknown>;
}

export interface JsonRpcResponse {
  jsonrpc: '2.0';
  id: string | number | null;
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
}
