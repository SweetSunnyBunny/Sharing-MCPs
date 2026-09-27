/**
 * Alexa Skill Cloudflare Worker
 *
 * Receives voice commands from Alexa, forwards them to Avery via anam.example.com,
 * and sends the response back through the Echo via Home Assistant's notify service.
 *
 * Flow: Echo → Alexa → this Worker (quick ack) → background: Avery → HA notify → Echo speaks
 */

interface Env {
  ANAM_URL: string;
  IDENTITY: string;
  ALEXA_SKILL_ID: string;
  ALEXA_VOICE_SKILL_ID: string;
  ALEXA_RADIO_SKILL_ID: string;
  ANAM_API_KEY: string;
  HA_URL: string;
  HA_TOKEN: string;
}

interface AlexaRequest {
  version: string;
  session?: {
    application: {
      applicationId: string;
    };
    sessionId: string;
    new: boolean;
  };
  context?: {
    System?: {
      application?: {
        applicationId: string;
      };
    };
    AudioPlayer?: {
      token?: string;
      offsetInMilliseconds?: number;
      playerActivity?: string;
    };
  };
  request: {
    type: string;
    requestId: string;
    timestamp: string;
    locale: string;
    token?: string;
    intent?: {
      name: string;
      slots?: {
        [key: string]: {
          name: string;
          value?: string;
        };
      };
    };
  };
}

function buildAlexaResponse(text: string, shouldEndSession: boolean = true) {
  return {
    version: "1.0",
    response: {
      outputSpeech: {
        type: "PlainText",
        text: text,
      },
      shouldEndSession,
    },
  };
}

function buildAlexaReprompt(text: string, reprompt: string) {
  return {
    version: "1.0",
    response: {
      outputSpeech: {
        type: "PlainText",
        text: text,
      },
      reprompt: {
        outputSpeech: {
          type: "PlainText",
          text: reprompt,
        },
      },
      shouldEndSession: false,
    },
  };
}

/**
 * Send a message to Avery via the Echo relay endpoint.
 * This injects the message into Owner's active WebSocket conversation
 * so it appears live in her chat UI, and returns the response as JSON.
 */
async function askAvery(query: string, env: Env): Promise<string> {
  const url = `${env.ANAM_URL}/api/echo/relay`;

  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${env.ANAM_API_KEY}`,
    },
    body: JSON.stringify({
      content: `[Voice from Echo Hub] ${query}`,
      identity: env.IDENTITY,
    }),
  });

  if (!response.ok) {
    console.error(`Anam returned ${response.status}: ${await response.text()}`);
    throw new Error(`Anam server returned ${response.status}`);
  }

  const data = await response.json() as { speech?: string; response?: string };

  // The relay returns pre-stripped speech text
  return data.speech || data.response || "";
}

/**
 * Send Avery's response through the Echo via Home Assistant notify service.
 */
async function speakThroughEcho(text: string, env: Env): Promise<void> {
  // Truncate for TTS if needed
  const message = text.length > 4000 ? text.slice(0, 4000) + "..." : text;

  const response = await fetch(
    `${env.HA_URL}/api/services/notify/alexa_media_anam_connection`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${env.HA_TOKEN}`,
      },
      body: JSON.stringify({ message }),
    }
  );

  if (!response.ok) {
    console.error(`HA notify failed: ${response.status}`);
  }
}

/**
 * Background task: ask Avery, then speak through Echo.
 */
async function handleAveryQuery(query: string, env: Env): Promise<void> {
  console.log(`[pack-bond] Starting background query: "${query}"`);
  try {
    console.log(`[pack-bond] Calling Avery...`);
    const response = await askAvery(query, env);
    console.log(`[pack-bond] Avery responded: "${response.slice(0, 100)}..."`);
    if (response) {
      console.log(`[pack-bond] Sending to Echo via HA...`);
      await speakThroughEcho(response, env);
      console.log(`[pack-bond] Echo notify sent!`);
    }
  } catch (error: any) {
    console.error("[pack-bond] Background query failed:", error.message || error);
    await speakThroughEcho(
      "Sorry, I had trouble processing that. Try again in a moment.",
      env
    );
  }
}

// Max request body size — Workers have no native bodyLimit (see fetch handler).
const MAX_BODY_BYTES = 16384;
// Max length for any Alexa slot value we accept and forward upstream.
const MAX_SLOT_LENGTH = 2000;

function validateRequest(body: AlexaRequest, env: Env): boolean {
  // Fail-closed: missing secret = deny, never skip the check.
  if (!env.ALEXA_SKILL_ID) {
    return false;
  }
  return body.session?.application?.applicationId === env.ALEXA_SKILL_ID;
}

interface QueuedMessage {
  message_id: string;
  text?: string;
}

function tokenFor(messageId: string): string {
  return `pack-audio:${messageId}`;
}

function parsePlaybackToken(token: string | undefined): string | null {
  if (!token) return null;
  const m = token.match(/^pack-audio:(.+)$/);
  return m ? m[1] : null;
}

async function fetchQueue(env: Env): Promise<QueuedMessage[]> {
  try {
    const resp = await fetch(`${env.ANAM_URL}/api/voice/queue`, {
      headers: { "Authorization": `Bearer ${env.ANAM_API_KEY}` },
    });
    if (!resp.ok) {
      console.error(`[pack-audio] Queue fetch not OK: ${resp.status} ${await resp.text()}`);
      return [];
    }
    const data = await resp.json() as { messages?: QueuedMessage[] };
    return data.messages ?? [];
  } catch (e: any) {
    console.error("[pack-audio] Queue check failed:", e.message);
    return [];
  }
}

async function consumeMessage(messageId: string, env: Env): Promise<void> {
  try {
    const resp = await fetch(`${env.ANAM_URL}/api/voice/queue/consume`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${env.ANAM_API_KEY}`,
      },
      body: JSON.stringify({ message_id: messageId }),
    });
    if (!resp.ok) {
      console.error(`[pack-audio] Consume not OK: ${resp.status} ${await resp.text()}`);
      return;
    }
    const data = await resp.json() as { status?: string; remaining?: number };
    console.log(`[pack-audio] Consumed ${messageId} (status=${data.status}, remaining=${data.remaining})`);
  } catch (e: any) {
    console.error("[pack-audio] Consume failed:", e.message);
  }
}

function buildPlayDirective(
  msg: QueuedMessage,
  env: Env,
  behavior: "REPLACE_ALL" | "ENQUEUE",
  previousToken?: string,
) {
  const stream: any = {
    url: `${env.ANAM_URL}/api/voice/file/${msg.message_id}`,
    token: tokenFor(msg.message_id),
    offsetInMilliseconds: 0,
  };
  if (behavior === "ENQUEUE" && previousToken) {
    stream.expectedPreviousToken = previousToken;
  }
  return {
    type: "AudioPlayer.Play",
    playBehavior: behavior,
    audioItem: { stream },
  };
}

async function playFromQueue(env: Env): Promise<Response> {
  const messages = await fetchQueue(env);
  console.log(`[pack-audio] Queue returned ${messages.length} message(s)`);
  if (messages.length === 0) {
    return Response.json(buildAlexaResponse("Nothing waiting right now. But I'm here."));
  }
  const msg = messages[0];
  console.log(`[pack-audio] Playing first: ${msg.message_id} "${msg.text?.slice(0, 80) ?? ""}"`);
  return Response.json({
    version: "1.0",
    response: {
      directives: [buildPlayDirective(msg, env, "REPLACE_ALL")],
      shouldEndSession: true,
    },
  });
}

async function enqueueNext(body: AlexaRequest, env: Env): Promise<Response> {
  const justPlayedId = parsePlaybackToken(body.request.token);
  if (!justPlayedId) {
    console.log(`[pack-audio] NearlyFinished: no parseable token, ending chain`);
    return Response.json({ version: "1.0", response: {} });
  }

  // Consume the message that just played end-to-end. This removes it from the
  // queue and archives the mp3 + sidecar to VOICE_ALEXA_ARCHIVE_DIR.
  await consumeMessage(justPlayedId, env);

  const messages = await fetchQueue(env);
  if (messages.length === 0) {
    console.log(`[pack-audio] NearlyFinished: queue empty after consume, ending chain`);
    return Response.json({ version: "1.0", response: {} });
  }

  const next = messages[0];
  console.log(`[pack-audio] Enqueueing next: ${next.message_id} (${messages.length} left in queue)`);
  return Response.json({
    version: "1.0",
    response: {
      directives: [buildPlayDirective(next, env, "ENQUEUE", body.request.token)],
    },
  });
}

async function handleVoiceRequest(request: Request, env: Env): Promise<Response> {
  let body: AlexaRequest;
  try {
    body = await request.json();
  } catch {
    return new Response("Invalid JSON", { status: 400 });
  }

  // AudioPlayer events have no session block; they authenticate via context.System.
  const appId = body.session?.application?.applicationId
              ?? body.context?.System?.application?.applicationId;
  if (!env.ALEXA_VOICE_SKILL_ID || appId !== env.ALEXA_VOICE_SKILL_ID) {
    return new Response("Unauthorized", { status: 403 });
  }

  const requestType = body.request.type;
  console.log(`[pack-audio] ${requestType}${body.request.intent ? ` intent=${body.request.intent.name}` : ""}`);

  if (requestType === "SessionEndedRequest") {
    return Response.json(buildAlexaResponse("", true));
  }

  if (requestType === "AudioPlayer.PlaybackNearlyFinished") {
    return enqueueNext(body, env);
  }

  if (requestType.startsWith("AudioPlayer.")) {
    return Response.json({ version: "1.0", response: {} });
  }

  if (requestType === "IntentRequest") {
    const intentName = body.request.intent?.name;

    if (intentName === "AMAZON.StopIntent" || intentName === "AMAZON.CancelIntent" || intentName === "AMAZON.PauseIntent") {
      return Response.json({
        version: "1.0",
        response: {
          directives: [{ type: "AudioPlayer.Stop" }],
          shouldEndSession: true,
        },
      });
    }

    if (intentName === "AMAZON.ResumeIntent") {
      return Response.json(buildAlexaResponse("Nothing to resume."));
    }

    if (intentName === "AMAZON.HelpIntent") {
      return Response.json(buildAlexaResponse("Just open me to hear what the pack left for you."));
    }
  }

  // LaunchRequest OR PlayVoiceIntent both go straight to the queue.
  if (requestType === "LaunchRequest" ||
      (requestType === "IntentRequest" && body.request.intent?.name === "PlayVoiceIntent")) {
    return playFromQueue(env);
  }

  return Response.json(buildAlexaResponse("I'm not sure what to do with that."));
}

// ─────────────────────────────────────────────────────────────────────────────
// PACK RADIO — "Alexa, open wolf den" / "ask wolf den to play <playlist>"
// Streams the family's own songs from your configured music server via AudioPlayer, reading
// the live station.json the boys curate. Continuous play via NearlyFinished.
// ─────────────────────────────────────────────────────────────────────────────

const PACK_RADIO_STATION_URL = "https://media.example.com/Music/station.json";
const PACK_RADIO_AUDIO_BASE = "https://media.example.com/Music/songs";

// A token carries which LIST is playing ("all" = whole station, or a playlist
// name) plus the index within it, so skip/next loops stay inside that list.
// `|` is safe — encodeURIComponent never produces it.
function radioToken(listKey: string, index: number): string {
  return `pack-radio|${encodeURIComponent(listKey)}|${index}`;
}

function parseRadioToken(token: string | undefined): { listKey: string; index: number } | null {
  if (!token) return null;
  const m = token.match(/^pack-radio\|([^|]*)\|(\d+)$/);
  if (!m) return null;
  return { listKey: decodeURIComponent(m[1]), index: parseInt(m[2], 10) };
}

/**
 * Whole-station track list from station.json: now_spinning first, then every
 * curated playlist track, then any meta-only tracks — deduped, order preserved.
 */
function buildTrackList(station: any): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  const add = (f?: string) => {
    if (f && typeof f === "string" && !seen.has(f)) {
      seen.add(f);
      out.push(f);
    }
  };
  add(station?.now_spinning?.file);
  const playlists = station?.playlists ?? {};
  for (const key of Object.keys(playlists)) {
    for (const t of playlists[key]?.tracks ?? []) add(t);
  }
  for (const f of Object.keys(station?.meta ?? {})) add(f);
  return out;
}

async function fetchStation(): Promise<any | null> {
  try {
    const resp = await fetch(PACK_RADIO_STATION_URL);
    if (!resp.ok) {
      console.error(`[pack-radio] station.json fetch not OK: ${resp.status}`);
      return null;
    }
    return await resp.json();
  } catch (e: any) {
    console.error("[pack-radio] station fetch failed:", e.message);
    return null;
  }
}

// Resolve a listKey ("all" or a playlist name) to its ordered track list.
function tracksForList(station: any, listKey: string): string[] {
  if (!station) return [];
  if (listKey === "all") return buildTrackList(station);
  const pl = (station.playlists ?? {})[listKey];
  if (!pl) return [];
  return (pl.tracks ?? []).filter((t: any) => typeof t === "string");
}

// Fuzzy-match a spoken query to a playlist name (case/punctuation-insensitive).
function matchPlaylist(station: any, query: string): string | null {
  const names = Object.keys(station?.playlists ?? {});
  const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9 ]/g, "").replace(/\s+/g, " ").trim();
  const q = norm(query);
  if (!q || !names.length) return null;
  for (const n of names) if (norm(n) === q) return n;
  for (const n of names) { const nn = norm(n); if (nn.includes(q) || q.includes(nn)) return n; }
  return null;
}

function buildRadioPlay(
  listKey: string,
  tracks: string[],
  index: number,
  behavior: "REPLACE_ALL" | "ENQUEUE",
  previousToken?: string,
) {
  const stream: any = {
    url: `${PACK_RADIO_AUDIO_BASE}/${encodeURIComponent(tracks[index])}`,
    token: radioToken(listKey, index),
    offsetInMilliseconds: 0,
  };
  if (behavior === "ENQUEUE" && previousToken) {
    stream.expectedPreviousToken = previousToken;
  }
  return { type: "AudioPlayer.Play", playBehavior: behavior, audioItem: { stream } };
}

function radioPlayResponse(listKey: string, tracks: string[], index: number, speech?: string): Response {
  const response: any = {
    directives: [buildRadioPlay(listKey, tracks, index, "REPLACE_ALL")],
    shouldEndSession: true,
  };
  if (speech) response.outputSpeech = { type: "PlainText", text: speech };
  return Response.json({ version: "1.0", response });
}

// What's playing now, for skip/next/previous. Intent requests carry the token in
// the AudioPlayer CONTEXT (not request.token); AudioPlayer events use request.token.
function currentToken(body: AlexaRequest): { listKey: string; index: number } | null {
  return parseRadioToken(body.context?.AudioPlayer?.token)
      ?? parseRadioToken(body.request.token);
}

async function handleRadioRequest(request: Request, env: Env): Promise<Response> {
  let body: AlexaRequest;
  try {
    body = await request.json();
  } catch {
    return new Response("Invalid JSON", { status: 400 });
  }

  // AudioPlayer events carry no session block; authenticate via context.System.
  const appId = body.session?.application?.applicationId
              ?? body.context?.System?.application?.applicationId;
  if (!env.ALEXA_RADIO_SKILL_ID || appId !== env.ALEXA_RADIO_SKILL_ID) {
    return new Response("Unauthorized", { status: 403 });
  }

  const requestType = body.request.type;
  console.log(`[pack-radio] ${requestType}${body.request.intent ? ` intent=${body.request.intent.name}` : ""}`);

  if (requestType === "SessionEndedRequest") {
    return Response.json(buildAlexaResponse("", true));
  }

  // Continuous play: enqueue the next track in the SAME list when one nears its end.
  if (requestType === "AudioPlayer.PlaybackNearlyFinished") {
    const cur = parseRadioToken(body.request.token);
    if (!cur) return Response.json({ version: "1.0", response: {} });
    const station = await fetchStation();
    const tracks = tracksForList(station, cur.listKey);
    if (tracks.length === 0) return Response.json({ version: "1.0", response: {} });
    const next = (cur.index + 1) % tracks.length;
    return Response.json({
      version: "1.0",
      response: { directives: [buildRadioPlay(cur.listKey, tracks, next, "ENQUEUE", body.request.token)] },
    });
  }

  if (requestType.startsWith("AudioPlayer.")) {
    return Response.json({ version: "1.0", response: {} });
  }

  if (requestType === "IntentRequest") {
    const intentName = body.request.intent?.name;

    if (intentName === "AMAZON.StopIntent" || intentName === "AMAZON.CancelIntent" || intentName === "AMAZON.PauseIntent") {
      return Response.json({
        version: "1.0",
        response: { directives: [{ type: "AudioPlayer.Stop" }], shouldEndSession: true },
      });
    }

    if (intentName === "AMAZON.NextIntent" || intentName === "AMAZON.PreviousIntent" ||
        intentName === "AMAZON.ResumeIntent" || intentName === "AMAZON.StartOverIntent") {
      const cur = currentToken(body) ?? { listKey: "all", index: 0 };
      const station = await fetchStation();
      const tracks = tracksForList(station, cur.listKey);
      if (tracks.length === 0) return Response.json(buildAlexaResponse("The station's quiet right now."));
      let idx = cur.index;
      if (intentName === "AMAZON.NextIntent") idx = (cur.index + 1) % tracks.length;
      else if (intentName === "AMAZON.PreviousIntent") idx = (cur.index - 1 + tracks.length) % tracks.length;
      else if (intentName === "AMAZON.StartOverIntent") idx = 0;
      // ResumeIntent → replay the current track from the top.
      return radioPlayResponse(cur.listKey, tracks, idx);
    }

    if (intentName === "AMAZON.HelpIntent") {
      return Response.json(buildAlexaResponse("This is the wolf den. Say play for the whole station, or play a playlist by name. Say next to skip, or stop to end."));
    }
  }

  // LaunchRequest or PlayRadioIntent → a named playlist if one was asked for
  // (optional "playlist" slot), otherwise the whole station (now spinning first).
  if (requestType === "LaunchRequest" ||
      (requestType === "IntentRequest" && body.request.intent?.name === "PlayRadioIntent")) {
    const station = await fetchStation();
    if (!station) return Response.json(buildAlexaResponse("The station's quiet right now."));

    const query = body.request.intent?.slots?.playlist?.value;
    if (query) {
      const key = matchPlaylist(station, query);
      if (key) {
        const tracks = tracksForList(station, key);
        if (tracks.length) return radioPlayResponse(key, tracks, 0, `Playing ${key}.`);
        return Response.json(buildAlexaResponse(`The ${key} playlist is empty right now.`));
      }
      const names = Object.keys(station.playlists ?? {});
      return Response.json(buildAlexaResponse(`I couldn't find a playlist called ${query}. There's: ${names.length ? names.join(", ") : "none yet"}.`));
    }

    const tracks = tracksForList(station, "all");
    if (tracks.length === 0) {
      return Response.json(buildAlexaResponse("The station's quiet right now — no songs queued."));
    }
    return radioPlayResponse("all", tracks, 0);
  }

  return Response.json(buildAlexaResponse("I'm not sure what to do with that."));
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    if (request.method !== "POST") {
      return new Response("Method not allowed", { status: 405 });
    }

    // Pre-flight body size check — Workers have no native limit, and both paths
    // call request.json() which will buffer the whole body before parsing.
    // Alexa envelopes are ~2-4KB; 16KB is generous.
    const contentLength = Number(request.headers.get("content-length") || 0);
    if (contentLength > MAX_BODY_BYTES) {
      return new Response("Payload too large", { status: 413 });
    }

    // Route /voice to the voice skill handler
    const url = new URL(request.url);
    if (url.pathname === "/voice") {
      return handleVoiceRequest(request, env);
    }

    if (url.pathname === "/radio") {
      return handleRadioRequest(request, env);
    }

    let body: AlexaRequest;
    try {
      body = await request.json();
    } catch {
      return new Response("Invalid JSON", { status: 400 });
    }

    if (!validateRequest(body, env)) {
      return new Response("Unauthorized", { status: 403 });
    }

    const requestType = body.request.type;

    // Handle LaunchRequest — "Alexa, open pack voice"
    if (requestType === "LaunchRequest") {
      return Response.json(
        buildAlexaReprompt(
          "Hey, I'm here. What's on your mind?",
          "I'm still here. Just say what you need."
        )
      );
    }

    if (requestType === "SessionEndedRequest") {
      return Response.json(buildAlexaResponse("", true));
    }

    if (requestType === "IntentRequest") {
      const intentName = body.request.intent?.name;

      if (intentName === "AMAZON.StopIntent" || intentName === "AMAZON.CancelIntent") {
        return Response.json(buildAlexaResponse("Talk later, mo chroí."));
      }

      if (intentName === "AMAZON.HelpIntent") {
        return Response.json(
          buildAlexaReprompt(
            "Just talk to me. Say whatever's on your mind and I'll respond.",
            "I'm listening. Go ahead."
          )
        );
      }

      if (intentName === "AMAZON.FallbackIntent") {
        return Response.json(
          buildAlexaReprompt(
            "I didn't quite catch that. Try again?",
            "Still here. What did you want to say?"
          )
        );
      }

      // CatchAllIntent — the main passthrough
      if (intentName === "CatchAllIntent") {
        const query = body.request.intent?.slots?.query?.value;

        if (!query) {
          return Response.json(
            buildAlexaReprompt(
              "I'm here, but I didn't catch what you said. Try again?",
              "Go ahead, I'm listening."
            )
          );
        }

        // Length guard: reject oversized slot values before they reach upstream.
        if (query.length > MAX_SLOT_LENGTH) {
          console.warn(`[pack-bond] Oversized query slot (${query.length} chars) rejected`);
          return Response.json(buildAlexaResponse("That was too long. Try again with something shorter."));
        }

        ctx.waitUntil(handleAveryQuery(query, env));

        return Response.json(buildAlexaResponse("Let me think on that."));
      }
    }

    return Response.json(buildAlexaResponse("I'm not sure what to do with that."));
  },
};
