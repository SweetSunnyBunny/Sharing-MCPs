// Shared HTTP helpers for external API calls

export interface FetchResult {
  ok: boolean;
  status: number;
  data: unknown;
  text?: string;
}

/** JSON fetch with timeout. */
export async function jsonFetch(
  url: string,
  opts: {
    method?: string;
    headers?: Record<string, string>;
    body?: unknown;
    timeout?: number;
  } = {},
): Promise<FetchResult> {
  const { method = 'GET', headers = {}, body, timeout = 30_000 } = opts;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);

  const init: RequestInit = {
    method,
    headers: {
      Accept: 'application/json',
      'User-Agent': 'social-backend/1.0',
      ...headers,
    },
    signal: controller.signal,
  };

  if (body !== undefined) {
    init.headers = { ...init.headers as Record<string, string>, 'Content-Type': 'application/json' };
    init.body = JSON.stringify(body);
  }

  try {
    const res = await fetch(url, init);
    clearTimeout(timer);

    const text = await res.text();
    let data: unknown;
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }

    return { ok: res.ok, status: res.status, data, text };
  } catch (err) {
    clearTimeout(timer);
    throw err;
  }
}

/** Raw fetch that returns text/bytes. */
export async function rawFetch(
  url: string,
  opts: {
    headers?: Record<string, string>;
    timeout?: number;
    maxBytes?: number;
  } = {},
): Promise<{ status: number; contentType: string; text: string; finalUrl: string }> {
  const { headers = {}, timeout = 25_000, maxBytes = 5 * 1024 * 1024 } = opts;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);

  try {
    const res = await fetch(url, {
      headers: {
        'User-Agent': 'social-backend/1.0',
        ...headers,
      },
      signal: controller.signal,
      redirect: 'follow',
    });
    clearTimeout(timer);

    const contentType = res.headers.get('Content-Type') || '';
    const buf = await res.arrayBuffer();
    if (buf.byteLength > maxBytes) {
      throw new Error(`Response too large (${buf.byteLength} > ${maxBytes} bytes)`);
    }

    const text = new TextDecoder('utf-8').decode(buf);
    return { status: res.status, contentType, text, finalUrl: res.url || url };
  } catch (err) {
    clearTimeout(timer);
    throw err;
  }
}

/** Build URL with query params. */
export function buildUrl(base: string, params: Record<string, string | number | boolean | undefined | null>): string {
  const url = new URL(base);
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') {
      url.searchParams.set(k, String(v));
    }
  }
  return url.toString();
}
