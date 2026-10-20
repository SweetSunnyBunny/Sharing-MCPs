// World Tools — weather, time, news, web reading

import type { Env, ToolDef, ToolModule } from '../lib/types';
import { rawFetch, buildUrl } from '../lib/http';

const WMO_CODES: Record<number, string> = {
  0: 'Clear sky', 1: 'Mainly clear', 2: 'Partly cloudy', 3: 'Overcast',
  45: 'Fog', 48: 'Depositing rime fog',
  51: 'Light drizzle', 53: 'Moderate drizzle', 55: 'Dense drizzle',
  56: 'Light freezing drizzle', 57: 'Dense freezing drizzle',
  61: 'Slight rain', 63: 'Moderate rain', 65: 'Heavy rain',
  66: 'Light freezing rain', 67: 'Heavy freezing rain',
  71: 'Slight snow fall', 73: 'Moderate snow fall', 75: 'Heavy snow fall', 77: 'Snow grains',
  80: 'Slight rain showers', 81: 'Moderate rain showers', 82: 'Violent rain showers',
  85: 'Slight snow showers', 86: 'Heavy snow showers',
  95: 'Thunderstorm', 96: 'Thunderstorm with slight hail', 99: 'Thunderstorm with heavy hail',
};

const GNEWS_CATEGORIES: Record<string, string> = {
  top: 'https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en',
  world: 'https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx1YlY4U0FtVnVHZ0pWVXlnQVAB?hl=en-US&gl=US&ceid=US:en',
  technology: 'https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGRqTVhZU0FtVnVHZ0pWVXlnQVAB?hl=en-US&gl=US&ceid=US:en',
  science: 'https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRFp0Y1RjU0FtVnVHZ0pWVXlnQVAB?hl=en-US&gl=US&ceid=US:en',
  business: 'https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx6TVdZU0FtVnVHZ0pWVXlnQVAB?hl=en-US&gl=US&ceid=US:en',
  health: 'https://news.google.com/rss/topics/CAAqIQgKIhtDQkFTRGdvSUwyMHZNR3QwTlRFU0FtVnVLQUFQAQ?hl=en-US&gl=US&ceid=US:en',
};

function j(data: unknown): string { return JSON.stringify(data, null, 2); }

function weatherDesc(code: number | null | undefined): string {
  if (code === null || code === undefined) return 'Unknown';
  return WMO_CODES[code] || `Unknown weather code ${code}`;
}

function moonPhaseFraction(d: Date): number {
  const ref = new Date('2000-01-06T18:14:00Z').getTime();
  const target = new Date(d.getFullYear(), d.getMonth(), d.getDate(), 12, 0, 0).getTime();
  const daysSince = (target - ref) / 86400000;
  const synodic = 29.53058867;
  return ((daysSince % synodic) + synodic) % synodic / synodic;
}

function moonPhaseName(phase: number): string {
  const bounds: [number, string][] = [
    [0.0625, 'New Moon'], [0.1875, 'Waxing Crescent'], [0.3125, 'First Quarter'],
    [0.4375, 'Waxing Gibbous'], [0.5625, 'Full Moon'], [0.6875, 'Waning Gibbous'],
    [0.8125, 'Last Quarter'], [0.9375, 'Waning Crescent'], [1.0, 'New Moon'],
  ];
  for (const [th, name] of bounds) if (phase < th) return name;
  return 'New Moon';
}

function isFullMoon(d: Date): boolean { return Math.abs(moonPhaseFraction(d) - 0.5) <= 0.03; }

function htmlUnescape(s: string): string {
  return s.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&#x27;/g, "'");
}

function extractTitle(html: string): string {
  const m = html.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
  return m ? htmlUnescape(m[1].replace(/\s+/g, ' ').trim()) : '';
}

function extractMetaDesc(html: string): string {
  const m = html.match(/<meta[^>]+name=['"]description['"][^>]+content=['"]([\s\S]*?)['"]/i)
    || html.match(/<meta[^>]+property=['"]og:description['"][^>]+content=['"]([\s\S]*?)['"]/i);
  return m ? htmlUnescape(m[1].replace(/\s+/g, ' ').trim()) : '';
}

function extractText(html: string): string {
  let t = html.replace(/<script\b[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style\b[\s\S]*?<\/style>/gi, ' ')
    .replace(/<noscript\b[\s\S]*?<\/noscript>/gi, ' ')
    .replace(/<[^>]+>/g, ' ');
  t = htmlUnescape(t);
  return t.replace(/\s+/g, ' ').trim();
}

function extractLinks(html: string, baseUrl: string, limit = 30): string[] {
  const matches = html.matchAll(/<a[^>]+href=['"]([^'"]*)['"]/gi);
  const links: string[] = [];
  for (const m of matches) {
    if (m[1].startsWith('#')) continue;
    try {
      const abs = new URL(m[1], baseUrl).toString();
      if (abs.startsWith('http')) links.push(abs);
    } catch { /* skip invalid */ }
    if (links.length >= limit) break;
  }
  return links;
}

function parseRssItems(xml: string, maxItems = 10): { title: string; link: string; published: string; source: string }[] {
  const raw = xml.split(/<item[^>]*>/i).slice(1);
  const items: { title: string; link: string; published: string; source: string }[] = [];

  for (const chunk of raw.slice(0, maxItems)) {
    const titleM = chunk.match(/<title>([\s\S]*?)<\/title>/i);
    const linkM = chunk.match(/<link>([\s\S]*?)<\/link>/i);
    const pubM = chunk.match(/<pubDate>([\s\S]*?)<\/pubDate>/i);
    const srcM = chunk.match(/<source[^>]*>([\s\S]*?)<\/source>/i);

    const title = titleM ? htmlUnescape(titleM[1].trim()) : '';
    if (title) {
      items.push({
        title,
        link: linkM ? linkM[1].trim() : '',
        published: pubM ? pubM[1].trim() : '',
        source: srcM ? htmlUnescape(srcM[1].trim()) : '',
      });
    }
  }
  return items;
}

function isBlockedHost(hostname: string): boolean {
  const blocked = ['localhost', '127.0.0.1', '::1', '0.0.0.0'];
  return blocked.includes(hostname.toLowerCase()) || hostname.endsWith('.local');
}

function validateUrl(url: string): URL {
  const parsed = new URL(url);
  if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error('Only http/https URLs allowed');
  if (!parsed.hostname) throw new Error('URL must include a host');
  if (isBlockedHost(parsed.hostname)) throw new Error('Localhost/private URLs not allowed');
  return parsed;
}

async function weatherForCoords(lat: number, lon: number, location: Record<string, any>) {
  const url = buildUrl('https://api.open-meteo.com/v1/forecast', {
    latitude: lat, longitude: lon, timezone: 'auto',
    current: 'temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,is_day',
    daily: 'weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset',
    forecast_days: 3,
  });

  const resp = await rawFetch(url, { maxBytes: 1024 * 1024 });
  const wx = JSON.parse(resp.text);
  const current = wx.current || {};
  const daily = wx.daily || {};

  const forecast = (daily.time || []).slice(0, 3).map((day: string, i: number) => ({
    date: day,
    weather_code: daily.weather_code?.[i] ?? null,
    weather: weatherDesc(daily.weather_code?.[i]),
    temp_max_c: daily.temperature_2m_max?.[i] ?? null,
    temp_min_c: daily.temperature_2m_min?.[i] ?? null,
  }));

  return {
    success: true,
    location: { ...location, latitude: lat, longitude: lon, timezone: wx.timezone },
    current: {
      time: current.time,
      temperature_c: current.temperature_2m,
      feels_like_c: current.apparent_temperature,
      humidity_percent: current.relative_humidity_2m,
      wind_kmh: current.wind_speed_10m,
      is_day: !!current.is_day,
      weather_code: current.weather_code,
      weather: weatherDesc(current.weather_code),
    },
    forecast_3d: forecast,
    sunrise: daily.sunrise?.[0] || null,
    sunset: daily.sunset?.[0] || null,
    source: 'Open-Meteo',
  };
}

// --- Tool definitions ---

export const TOOLS: ToolDef[] = [
  {
    name: 'wt_time_now',
    description: 'Get current date/time, weekday, and moon phase info.',
    inputSchema: {
      type: 'object',
      properties: {
        timezone_name: { type: 'string', description: "IANA timezone like 'America/New_York' or 'UTC' (default UTC)" },
      },
    },
  },
  {
    name: 'wt_calendar_info',
    description: 'Get day-of-week and moon info for a date.',
    inputSchema: {
      type: 'object',
      properties: {
        date_iso: { type: 'string', description: 'Date as YYYY-MM-DD (default today)' },
        timezone_name: { type: 'string', description: "IANA timezone (default UTC)" },
      },
    },
  },
  {
    name: 'wt_weather_current',
    description: 'Get current weather and short forecast via Open-Meteo.',
    inputSchema: {
      type: 'object',
      properties: {
        location: { type: 'string', description: 'City or location name. Omit for configured home.' },
      },
    },
  },
  {
    name: 'wt_weather_home',
    description: 'Get weather using configured home coordinates.',
    inputSchema: { type: 'object', properties: {} },
  },
  {
    name: 'wt_web_read_url',
    description: 'Fetch a public URL and extract readable text.',
    inputSchema: {
      type: 'object',
      properties: {
        url: { type: 'string', description: 'Public web URL to read' },
        max_chars: { type: 'number', description: 'Max chars of extracted text (default 12000)' },
        include_links: { type: 'boolean', description: 'Include discovered page links (default true)' },
      },
      required: ['url'],
    },
  },
  {
    name: 'wt_web_view_image_url',
    description: 'Fetch an image URL and return its base64 data for viewing.',
    inputSchema: {
      type: 'object',
      properties: {
        url: { type: 'string', description: 'Public image URL (http/https)' },
      },
      required: ['url'],
    },
  },
  {
    name: 'wt_news_search',
    description: 'Search Google News for articles matching a query.',
    inputSchema: {
      type: 'object',
      properties: {
        query: { type: 'string', description: 'News search query' },
        max_results: { type: 'number', description: 'Max results 1-20 (default 8)' },
      },
      required: ['query'],
    },
  },
  {
    name: 'wt_news_trending',
    description: 'Get trending news headlines by category from Google News.',
    inputSchema: {
      type: 'object',
      properties: {
        category: { type: 'string', description: `Category: ${Object.keys(GNEWS_CATEGORIES).join(', ')} (default technology)` },
        max_results: { type: 'number', description: 'Max results 1-20 (default 8)' },
      },
    },
  },
];

async function handle(name: string, args: Record<string, unknown>, env: Env): Promise<string> {
  const homeLat = parseFloat(env.WT_HOME_LAT || '51.4779');
  const homeLon = parseFloat(env.WT_HOME_LON || '0.0015');
  const homeLabel = env.WT_HOME_LABEL || 'Home';

  switch (name) {
    case 'wt_time_now': {
      // Workers don't have ZoneInfo, so we use UTC or format with Intl
      const tzName = String(args.timezone_name || 'UTC');
      const now = new Date();
      const formatter = new Intl.DateTimeFormat('en-US', {
        timeZone: tzName === 'local' ? 'America/Chicago' : tzName,
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit',
        hour12: false, weekday: 'long',
      });
      const parts = formatter.formatToParts(now);
      const get = (t: string) => parts.find(p => p.type === t)?.value || '';

      const dateStr = `${get('year')}-${get('month')}-${get('day')}`;
      const timeStr = `${get('hour')}:${get('minute')}:${get('second')}`;
      const weekday = get('weekday');
      const isWeekend = ['Saturday', 'Sunday'].includes(weekday);
      const phase = moonPhaseFraction(now);

      return j({
        success: true,
        timezone: tzName === 'local' ? 'America/Chicago' : tzName,
        iso: now.toISOString(),
        date: dateStr,
        time: timeStr,
        weekday,
        is_weekend: isWeekend,
        moon_phase: moonPhaseName(phase),
        full_moon_tonight: isFullMoon(now),
      });
    }

    case 'wt_calendar_info': {
      const tzName = String(args.timezone_name || 'UTC');
      const tz = tzName === 'local' ? 'America/Chicago' : tzName;
      let target: Date;
      let requestedDate: string | null = null;
      if (args.date_iso) {
        requestedDate = String(args.date_iso);
        const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(requestedDate);
        if (!match) throw new Error('date_iso must use YYYY-MM-DD');
        const [, year, month, day] = match;
        const y = Number(year), m = Number(month), d = Number(day);
        target = new Date(Date.UTC(y, m - 1, d, 12));
        if (
          target.getUTCFullYear() !== y ||
          target.getUTCMonth() !== m - 1 ||
          target.getUTCDate() !== d
        ) throw new Error('date_iso is not a valid calendar date');
      } else {
        target = new Date();
      }

      const formatter = new Intl.DateTimeFormat('en-US', { timeZone: tz, weekday: 'long' });
      const dateFormatter = new Intl.DateTimeFormat('en-CA', {
        timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit',
      });
      const dateKey = (value: Date) => {
        const parts = Object.fromEntries(
          dateFormatter.formatToParts(value).map(part => [part.type, part.value]),
        );
        return `${parts.year}-${parts.month}-${parts.day}`;
      };
      const weekday = formatter.format(target);
      const phase = moonPhaseFraction(target);
      const tomorrow = new Date(target); tomorrow.setUTCDate(tomorrow.getUTCDate() + 1);
      const tomorrowWeekday = formatter.format(tomorrow);

      return j({
        success: true,
        timezone: tz,
        date: requestedDate || dateKey(target),
        weekday,
        is_weekend: ['Saturday', 'Sunday'].includes(weekday),
        tomorrow: dateKey(tomorrow),
        tomorrow_weekday: tomorrowWeekday,
        moon_phase: moonPhaseName(phase),
        full_moon_tonight: isFullMoon(target),
      });
    }

    case 'wt_weather_current': {
      const loc = String(args.location || '').trim();
      if (!loc) {
        return j(await weatherForCoords(homeLat, homeLon, { name: homeLabel, source: 'configured_home_coordinates' }));
      }

      const geoUrl = buildUrl('https://geocoding-api.open-meteo.com/v1/search', {
        name: loc, count: 1, language: 'en', format: 'json',
      });
      const geoResp = await rawFetch(geoUrl, { maxBytes: 512000 });
      const geo = JSON.parse(geoResp.text);
      const results = geo.results || [];
      if (!results.length) return j({ success: false, error: `Location not found: ${loc}` });

      const place = results[0];
      return j(await weatherForCoords(place.latitude, place.longitude, {
        name: place.name, admin1: place.admin1, country: place.country, source: 'geocoded_location',
      }));
    }

    case 'wt_weather_home': {
      return j(await weatherForCoords(homeLat, homeLon, { name: homeLabel, source: 'configured_home_coordinates' }));
    }

    case 'wt_web_read_url': {
      validateUrl(String(args.url));
      const maxChars = Math.max(1000, Math.min(Number(args.max_chars) || 12000, 80000));
      const includeLinks = args.include_links !== false;

      const resp = await rawFetch(String(args.url));
      const ct = resp.contentType.toLowerCase();

      if (!ct.includes('text/html') && !ct.includes('application/xhtml+xml')) {
        const snippet = resp.text.slice(0, maxChars);
        return j({ success: true, url: resp.finalUrl, content_type: ct, title: '', description: '', text: snippet, text_length: snippet.length, links: [] });
      }

      const title = extractTitle(resp.text);
      const description = extractMetaDesc(resp.text);
      const text = extractText(resp.text).slice(0, maxChars);
      const links = includeLinks ? extractLinks(resp.text, resp.finalUrl, 40) : [];

      return j({ success: true, url: resp.finalUrl, content_type: ct, title, description, text, text_length: text.length, links });
    }

    case 'wt_web_view_image_url': {
      validateUrl(String(args.url));
      const resp = await fetch(String(args.url), {
        headers: { 'User-Agent': 'social-backend/1.0' },
      });

      if (!resp.ok) return j({ success: false, error: `HTTP ${resp.status}` });

      const ct = resp.headers.get('Content-Type') || '';
      if (!ct.startsWith('image/')) return j({ success: false, error: `Not an image: ${ct}` });

      const buf = await resp.arrayBuffer();
      if (buf.byteLength > 20 * 1024 * 1024) return j({ success: false, error: 'Image too large (>20MB)' });

      // Convert to base64 data URI for MCP image content
      const bytes = new Uint8Array(buf);
      let binary = '';
      for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
      const b64 = btoa(binary);

      return j({ success: true, url: args.url, content_type: ct, size_bytes: buf.byteLength, data_uri: `data:${ct};base64,${b64}` });
    }

    case 'wt_news_search': {
      const maxR = Math.max(1, Math.min(Number(args.max_results) || 8, 20));
      const encoded = encodeURIComponent(String(args.query));
      const rssUrl = `https://news.google.com/rss/search?q=${encoded}&hl=en-US&gl=US&ceid=US:en`;
      const resp = await rawFetch(rssUrl);
      const items = parseRssItems(resp.text, maxR);
      return j({ success: true, query: args.query, result_count: items.length, articles: items });
    }

    case 'wt_news_trending': {
      const maxR = Math.max(1, Math.min(Number(args.max_results) || 8, 20));
      const cat = String(args.category || 'technology').toLowerCase();
      const rssUrl = GNEWS_CATEGORIES[cat];
      if (!rssUrl) return j({ success: false, error: `Unknown category '${cat}'. Available: ${Object.keys(GNEWS_CATEGORIES).join(', ')}` });
      const resp = await rawFetch(rssUrl);
      const items = parseRssItems(resp.text, maxR);
      return j({ success: true, category: cat, result_count: items.length, articles: items });
    }

    default:
      throw new Error(`Unknown world-tools tool: ${name}`);
  }
}

const worldTools: ToolModule = { tools: TOOLS, handle };
export default worldTools;
