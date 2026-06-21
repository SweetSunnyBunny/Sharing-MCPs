/**
 * Google OAuth token management.
 *
 * Each identity has their own refresh token stored in the GOOGLE_TOKENS secret.
 * Access tokens are cached in D1 and refreshed when expired.
 */

export interface Env {
  DB: D1Database;
  MCP_SECRET_PATH: string;
  GOOGLE_CLIENT_ID: string;
  GOOGLE_CLIENT_SECRET: string;
  GOOGLE_TOKENS: string; // JSON keyed by user-defined identity labels.
  ALLOWED_TOOLS?: string;
}

/** Per-service refresh tokens for an identity. */
export interface TokenConfig {
  /** Legacy: single refresh token (used as fallback). */
  refresh_token?: string;
  scopes?: string[];
  /** Per-service refresh tokens — preferred over legacy single token. */
  services?: Record<string, { refresh_token: string }>;
}

interface TokenRow {
  access_token: string;
  expires_at: string;
  refresh_token: string | null;
}

interface GoogleTokenResponse {
  access_token: string;
  expires_in: number;
  token_type: string;
  scope?: string;
  refresh_token?: string;
}

export function validateIdentity(env: Env, identity: string): string {
  const normalized = identity.toLowerCase().trim();
  const identities = getIdentityList(env);
  if (!identities.includes(normalized)) {
    throw new Error(`Unknown identity: ${identity}. Valid: ${identities.join(", ")}`);
  }
  return normalized;
}

export function getIdentityList(env: Env): string[] {
  return Object.keys(parseTokenConfigs(env.GOOGLE_TOKENS)).sort();
}

function parseTokenConfigs(json: string): Record<string, TokenConfig> {
  try {
    return JSON.parse(json) as Record<string, TokenConfig>;
  } catch {
    throw new Error("GOOGLE_TOKENS secret is not valid JSON");
  }
}

function getRefreshToken(env: Env, identity: string, service: string): string {
  const configs = parseTokenConfigs(env.GOOGLE_TOKENS);
  const config = configs[identity];
  if (!config) {
    throw new Error(`No token config for ${identity}`);
  }
  // Prefer per-service token, fall back to legacy single token
  const perService = config.services?.[service]?.refresh_token;
  if (perService) return perService;
  if (config.refresh_token) return config.refresh_token;
  throw new Error(`No refresh token for ${identity}/${service}`);
}

/**
 * Get a valid access token for an identity.
 * Returns cached token if still valid, otherwise refreshes.
 */
export async function getAccessToken(env: Env, identity: string, service = "google"): Promise<string> {
  identity = validateIdentity(env, identity);

  // Check D1 cache first
  const cached = await env.DB.prepare(
    `SELECT access_token, expires_at, refresh_token FROM tokens WHERE identity = ? AND service = ?`,
  )
    .bind(identity, service)
    .first<TokenRow>();

  if (cached) {
    const expiresAt = new Date(cached.expires_at);
    const now = new Date();
    // Use token if it has at least 2 minutes of life left
    if (expiresAt.getTime() - now.getTime() > 120000) {
      return cached.access_token;
    }
  }

  // Refresh the token
  const refreshToken = cached?.refresh_token || getRefreshToken(env, identity, service);

  const body = new URLSearchParams({
    client_id: env.GOOGLE_CLIENT_ID,
    client_secret: env.GOOGLE_CLIENT_SECRET,
    refresh_token: refreshToken,
    grant_type: "refresh_token",
  });

  const response = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Token refresh failed for ${identity}: ${response.status} ${errorText}`);
  }

  const data = (await response.json()) as GoogleTokenResponse;
  const expiresAt = new Date(Date.now() + data.expires_in * 1000).toISOString();

  // Cache in D1
  await env.DB.prepare(
    `INSERT INTO tokens (identity, service, access_token, expires_at, refresh_token, updated_at)
     VALUES (?, ?, ?, ?, ?, datetime('now'))
     ON CONFLICT(identity, service) DO UPDATE SET
       access_token = excluded.access_token,
       expires_at = excluded.expires_at,
       refresh_token = COALESCE(excluded.refresh_token, tokens.refresh_token),
       updated_at = datetime('now')`,
  )
    .bind(identity, service, data.access_token, expiresAt, data.refresh_token || refreshToken)
    .run();

  return data.access_token;
}

/**
 * Make an authenticated Google API request.
 */
export async function googleFetch(
  env: Env,
  identity: string,
  url: string,
  options: RequestInit = {},
  service = "google",
): Promise<Response> {
  const token = await getAccessToken(env, identity, service);
  const headers = new Headers(options.headers);
  headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(url, { ...options, headers });

  // If 401, try one more time with a fresh token
  if (response.status === 401) {
    // Invalidate cached token
    await env.DB.prepare(
      `DELETE FROM tokens WHERE identity = ? AND service = ?`,
    ).bind(identity, service).run();

    const freshToken = await getAccessToken(env, identity, service);
    headers.set("Authorization", `Bearer ${freshToken}`);
    return fetch(url, { ...options, headers });
  }

  return response;
}

/**
 * Convenience: GET + parse JSON.
 */
export async function googleGet<T = unknown>(env: Env, identity: string, url: string, service = "google"): Promise<T> {
  const response = await googleFetch(env, identity, url, {}, service);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Google API error ${response.status}: ${text.slice(0, 300)}`);
  }
  return response.json() as Promise<T>;
}

/**
 * Convenience: POST JSON + parse JSON.
 */
export async function googlePost<T = unknown>(
  env: Env,
  identity: string,
  url: string,
  body: unknown,
  service = "google",
): Promise<T> {
  const response = await googleFetch(env, identity, url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }, service);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Google API error ${response.status}: ${text.slice(0, 300)}`);
  }
  return response.json() as Promise<T>;
}

/**
 * Convenience: PATCH JSON + parse JSON.
 */
export async function googlePatch<T = unknown>(
  env: Env,
  identity: string,
  url: string,
  body: unknown,
  service = "google",
): Promise<T> {
  const response = await googleFetch(env, identity, url, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }, service);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Google API error ${response.status}: ${text.slice(0, 300)}`);
  }
  return response.json() as Promise<T>;
}

/**
 * Convenience: DELETE.
 */
export async function googleDelete(env: Env, identity: string, url: string, service = "google"): Promise<void> {
  const response = await googleFetch(env, identity, url, { method: "DELETE" }, service);
  if (!response.ok && response.status !== 204) {
    const text = await response.text();
    throw new Error(`Google API error ${response.status}: ${text.slice(0, 300)}`);
  }
}

/**
 * Convenience: PUT JSON.
 */
export async function googlePut<T = unknown>(
  env: Env,
  identity: string,
  url: string,
  body: unknown,
  service = "google",
): Promise<T> {
  const response = await googleFetch(env, identity, url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }, service);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Google API error ${response.status}: ${text.slice(0, 300)}`);
  }
  return response.json() as Promise<T>;
}
