// Server pulse: a picture of a whole server over a time window, for agents who want to know
// what the house is doing rather than read every message. Counts and connections are computed
// from the messages; the optional mood line is a reading by a Workers AI model, labelled as one.

import { discordFetch, parseSince, snowflakeFromTime } from './discord';

// The slice of the Workers AI binding this file uses
export interface AiBinding {
  run(model: string, input: unknown): Promise<unknown>;
}

export const DEFAULT_PULSE_MODEL = '@cf/google/gemma-4-26b-a4b-it';

interface PulseOptions {
  ai?: AiBinding;
  model?: string;
  // Channels the deployment never reads for a pulse, comma-separated IDs or names
  excludeChannels?: string;
}

function channelList(value: unknown): string[] {
  const items = Array.isArray(value) ? value : typeof value === 'string' ? value.split(',') : [];
  return items.map(v => String(v).trim().replace(/^#/, '').toLowerCase()).filter(Boolean);
}

interface Person {
  name: string;
  bot: boolean;
  count: number;
  channels: Set<string>;
  last?: { at: string; text: string; channel: string };
}

interface Seen {
  id: string;
  at: string;
  channel: string;
  authorId: string;
  author: string;
  text: string;
  targets: string[];
}

// Ordinary messages and replies; joins, pins and other system messages don't count as talking
const TALK_TYPES = new Set([0, 19]);

function clip(text: string, max: number): string {
  const flat = text.replace(/\s+/g, ' ').trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

function shortTime(iso: string): string {
  return iso.slice(0, 16).replace('T', ' ') + 'Z';
}

function displayName(user: any): string {
  return user?.global_name || user?.username || 'Unknown';
}

// Newest-first pages until the window or the cap runs out, returned oldest first
async function fetchSince(token: string, channelId: string, sinceId: bigint, cap: number): Promise<{ messages: any[]; error?: string; capped: boolean }> {
  const messages: any[] = [];
  let before: string | undefined;
  while (messages.length < cap) {
    const limit = Math.min(100, cap - messages.length);
    const res = await discordFetch(token, 'GET', `/channels/${channelId}/messages?limit=${limit}${before ? `&before=${before}` : ''}`);
    if (!res.ok) {
      const code = (res.data as any)?.code;
      return { messages, error: code === 50001 ? 'Missing Access' : `error ${res.status}`, capped: false };
    }
    const page = res.data as any[];
    const fresh = page.filter(m => BigInt(m.id) > sinceId);
    messages.push(...fresh);
    if (fresh.length < page.length || page.length < limit) return { messages: messages.reverse(), capped: false };
    before = page[page.length - 1].id;
  }
  return { messages: messages.reverse(), capped: true };
}

function aiText(result: any): string | undefined {
  if (typeof result === 'string') return result;
  if (typeof result?.response === 'string') return result.response;
  const choice = result?.choices?.[0]?.message?.content;
  if (typeof choice === 'string') return choice;
  if (typeof result?.output_text === 'string') return result.output_text;
  return undefined;
}

export async function serverPulse(token: string, args: Record<string, unknown>, options: PulseOptions = {}): Promise<string> {
  const sinceMs = parseSince((args.since as string) || '24h');
  const sinceId = BigInt(snowflakeFromTime(sinceMs));
  const perChannel = Math.min(Math.max(Number(args.per_channel) || 100, 1), 300);
  const maxChannels = Math.min(Math.max(Number(args.max_channels) || 25, 1), 50);

  const [chRes, threadRes, meRes] = await Promise.all([
    discordFetch(token, 'GET', `/guilds/${args.guild_id}/channels`),
    discordFetch(token, 'GET', `/guilds/${args.guild_id}/threads/active`),
    discordFetch(token, 'GET', '/users/@me'),
  ]);
  if (!chRes.ok) throw new Error(`Discord API error: ${JSON.stringify(chRes.data)}`);
  const channels = chRes.data as any[];
  const names = new Map(channels.map(c => [c.id, c.name]));
  const threads = threadRes.ok ? ((threadRes.data as any).threads ?? []) : [];
  const botId: string | undefined = meRes.ok ? (meRes.data as any).id : undefined;

  // Excluding a channel excludes its threads too
  const excluded = new Set([...channelList(options.excludeChannels), ...channelList(args.exclude_channels)]);
  const isExcluded = (c: any) => [c.id, c.name?.toLowerCase(), c.parent_id, names.get(c.parent_id)?.toLowerCase()]
    .some(key => key && excluded.has(key));

  const active = [...channels.filter(c => [0, 2, 5].includes(c.type)), ...threads]
    .filter(c => !isExcluded(c))
    .filter(c => c.last_message_id && BigInt(c.last_message_id) > sinceId)
    .sort((a, b) => (BigInt(b.last_message_id) > BigInt(a.last_message_id) ? 1 : -1));
  const since = new Date(sinceMs).toISOString();
  if (active.length === 0) return `Server pulse since ${shortTime(since)}: nobody has said anything.`;
  const shown = active.slice(0, maxChannels);

  const fetched = await Promise.all(shown.map(async c => {
    const label = c.parent_id && names.has(c.parent_id) && [10, 11, 12].includes(c.type)
      ? `#${names.get(c.parent_id)} › ${c.name}`
      : `#${c.name}`;
    return { label, ...(await fetchSince(token, c.id, sinceId, perChannel)) };
  }));

  const people = new Map<string, Person>();
  const edges = new Map<string, number>();
  const where: string[] = [];
  const unreadable: string[] = [];
  const openLoops: string[] = [];
  const toTheRoom: { bot: boolean; at: string; line: string }[] = [];
  const all: Seen[] = [];
  // Said to the room counts as unanswered only once it has had time to be answered
  const quietMs = Math.min(Math.max(Number(args.quiet_hours ?? 2), 0), 72) * 3_600_000;
  // A thread started from a message has the message's ID, and talking there is an answer
  const threadIds = new Set(threads.map((t: any) => t.id));

  for (const ch of fetched) {
    if (ch.error) { unreadable.push(`${ch.label} (${ch.error})`); continue; }
    const talk = ch.messages.filter(m => TALK_TYPES.has(m.type ?? 0));
    if (talk.length === 0) continue;

    const here = new Set<string>();
    const seen: Seen[] = talk.map(m => {
      const authorId = m.author?.id ?? 'unknown';
      const targets = new Set<string>();
      for (const u of m.mentions ?? []) if (u.id !== authorId) targets.add(u.id);
      const repliedTo = m.referenced_message?.author;
      if (repliedTo?.id && repliedTo.id !== authorId) targets.add(repliedTo.id);
      // Names of people who are only mentioned still need resolving for the who-to-whom lines
      for (const u of [...(m.mentions ?? []), ...(repliedTo ? [repliedTo] : [])]) {
        if (!people.has(u.id)) people.set(u.id, { name: displayName(u), bot: !!u.bot, count: 0, channels: new Set() });
      }
      const text = m.content || (m.attachments?.length ? `[${m.attachments.length} attachment(s)]` : m.embeds?.length ? '[embed]' : '[no text]');
      return { id: m.id, at: m.timestamp, channel: ch.label, authorId, author: displayName(m.author), text, targets: [...targets] };
    });

    for (const [i, s] of seen.entries()) {
      here.add(s.authorId);
      const person = people.get(s.authorId) ?? { name: s.author, bot: !!talk[i].author?.bot, count: 0, channels: new Set<string>() };
      person.name = s.author;
      person.bot = !!talk[i].author?.bot;
      person.count++;
      person.channels.add(ch.label);
      if (!person.last || s.at > person.last.at) person.last = { at: s.at, text: s.text, channel: ch.label };
      people.set(s.authorId, person);

      for (const t of s.targets) {
        const key = `${s.authorId}→${t}`;
        edges.set(key, (edges.get(key) ?? 0) + 1);
        // Open loop: someone was addressed here and hasn't said anything in this channel since
        const answered = seen.slice(i + 1).some(later => later.authorId === t);
        if (!answered) openLoops.push(`${s.at}\u0000- ${s.author} → ${people.get(t)?.name ?? t}${t === botId ? ' (you)' : ''} in ${ch.label}, ${shortTime(s.at)}: "${clip(s.text, 140)}" (message ${s.id})`);
      }

      // Said to the room: nobody addressed, and nobody else has spoken in this channel since
      const quietLongEnough = Date.now() - Date.parse(s.at) >= quietMs;
      if (s.targets.length === 0 && quietLongEnough && !threadIds.has(s.id)
        && !seen.slice(i + 1).some(later => later.authorId !== s.authorId)) {
        const reactions = (talk[i].reactions ?? []).reduce((n: number, r: any) => n + (r.count ?? 0), 0);
        const reacted = reactions ? `, ${reactions} reaction(s)` : '';
        toTheRoom.push({
          bot: person.bot,
          at: s.at,
          line: `- ${s.author}${person.bot ? ' [bot]' : ''}${s.authorId === botId ? ' (you)' : ''} in ${ch.label}, ${shortTime(s.at)}${reacted}: "${clip(s.text, 140)}" (message ${s.id})`,
        });
      }
    }
    all.push(...seen);
    const more = ch.capped ? `, capped at ${perChannel}` : '';
    where.push(`- ${ch.label}: ${seen.length} message(s)${more}, ${here.size} ${here.size === 1 ? 'person' : 'people'}`);
  }

  if (all.length === 0) return `Server pulse since ${shortTime(since)}: only system messages, nobody talking.`;

  const posters = [...people.entries()].filter(([, p]) => p.count > 0).sort((a, b) => b[1].count - a[1].count);
  const out: string[] = [
    `Server pulse since ${shortTime(since)}: ${all.length} message(s) from ${posters.length} ${posters.length === 1 ? 'person' : 'people'} in ${where.length} channel(s).`,
  ];
  if (active.length > shown.length) out.push(`(${active.length - shown.length} quieter channel(s) not included; raise max_channels to see them.)`);

  out.push('', "## Who's been around");
  for (const [id, p] of posters) {
    const last = p.last ? `; last ${shortTime(p.last.at)} in ${p.last.channel}: "${clip(p.last.text, 120)}"` : '';
    out.push(`- ${p.name}${p.bot ? ' [bot]' : ''}${id === botId ? ' (you)' : ''}: ${p.count} message(s) in ${[...p.channels].join(', ')}${last}`);
  }

  out.push('', "## Who talked to whom (Discord replies and @-mentions only; an answer that just uses someone's name isn't counted)");
  if (edges.size === 0) {
    out.push('- No Discord replies or @-mentions between anyone. People may still have answered by name.');
  } else {
    const name = (id: string) => `${people.get(id)?.name ?? id}${id === botId ? ' (you)' : ''}`;
    for (const [key, n] of [...edges.entries()].sort((a, b) => b[1] - a[1]).slice(0, 25)) {
      const [from, to] = key.split('→');
      const back = edges.get(`${to}→${from}`);
      out.push(`- ${name(from)} → ${name(to)}: ${n}${back ? '' : " (no @-mention or reply back in this window; check before assuming they weren't answered)"}`);
    }
  }

  out.push('', '## Where', ...where);
  if (unreadable.length) out.push(`- Can't read: ${unreadable.join(', ')}`);

  out.push('', '## Open loops (addressed, no word from them in that channel since)');
  if (openLoops.length === 0) out.push('- None.');
  else out.push(...openLoops.sort().reverse().slice(0, 15).map(l => l.split('\u0000')[1]));

  out.push('', '## Said to the room, no reply yet (nobody else has spoken in that channel since)');
  if (toTheRoom.length === 0) out.push('- None.');
  else {
    // People before bots: a person left unanswered matters more than a bot's own post
    toTheRoom.sort((a, b) => (a.bot === b.bot ? (a.at < b.at ? 1 : -1) : a.bot ? 1 : -1));
    out.push(...toTheRoom.slice(0, 10).map(r => r.line));
    if (toTheRoom.length > 10) out.push(`- …and ${toTheRoom.length - 10} more.`);
  }

  if (args.mood) {
    out.push('', await moodReading(all, options));
  }
  return out.join('\n');
}

async function moodReading(all: Seen[], options: PulseOptions): Promise<string> {
  const model = options.model || DEFAULT_PULSE_MODEL;
  if (!options.ai) {
    return '## Mood\n(Unavailable: this deployment has no Workers AI binding. Add `[ai] binding = "AI"` to wrangler.toml to enable it.)';
  }
  // Newest messages win when the window is long; the model sees them in order
  const lines: string[] = [];
  let budget = 12_000;
  for (const s of [...all].sort((a, b) => (a.at < b.at ? 1 : -1))) {
    const line = `[${s.channel}] ${s.author}: ${clip(s.text, 240)}`;
    if (budget - line.length < 0) break;
    budget -= line.length;
    lines.unshift(line);
  }
  try {
    const input = {
      messages: [
        {
          role: 'system',
          content: 'You read a Discord server and describe its mood and what people are working on, in 2 to 4 plain sentences. Name people. Say only what the messages show; if something is unclear, say so. No headings, no lists, no flattery.',
        },
        { role: 'user', content: lines.join('\n') },
      ],
      max_tokens: 300,
    };
    // Reasoning models (the default included) can spend the whole budget thinking; ask them not to.
    // A model that rejects the setting gets the plain request instead.
    let result: unknown;
    try {
      result = await options.ai.run(model, { ...input, chat_template_kwargs: { enable_thinking: false } });
    } catch {
      result = await options.ai.run(model, input);
    }
    const text = aiText(result)?.trim();
    if (!text) return `## Mood (${model})\n(The model returned nothing usable.)`;
    return `## Mood (a reading by ${model}, not a fact)\n${text}`;
  } catch (error) {
    return `## Mood (${model})\n(Workers AI failed: ${error instanceof Error ? error.message : String(error)})`;
  }
}
