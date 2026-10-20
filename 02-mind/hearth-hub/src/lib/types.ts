export interface Env {
  DB: D1Database;
  MCP_SECRET_PATH: string;
  ASSET_CDN_BASE: string;
  WT_HOME_LAT: string;
  WT_HOME_LON: string;
  ELEVENLABS_API_KEY: string;
  ANAM_API_KEY: string;
  ANAM_API_URL?: string;
  ENABLED_TOOLS: string[];
  [key: string]: unknown;
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
