import {
  joinVoiceChannel,
  VoiceConnection,
  VoiceConnectionStatus,
  EndBehaviorType,
  entersState,
  getVoiceConnection,
} from "@discordjs/voice";
import { VoiceChannel, GuildMember } from "discord.js";
import prism from "prism-media";
import { Writable } from "node:stream";
import { Buffer } from "node:buffer";

/** Raw PCM audio buffered per-user */
interface UserAudioBuffer {
  pcmChunks: Buffer[];
  displayName: string;
  userId: string;
}

export interface AudioChunk {
  /** Combined PCM audio from all speakers for this chunk period */
  pcmBuffer: Buffer;
  /** Who was speaking during this chunk */
  speakers: string[];
  /** When this chunk started */
  startedAt: Date;
  /** When this chunk ended */
  endedAt: Date;
}

export interface RecordingSession {
  guildId: string;
  channelId: string;
  channelName: string;
  startedAt: Date;
  startedBy: string;
  connection: VoiceConnection;
  userBuffers: Map<string, UserAudioBuffer>;
  chunkStartedAt: Date;
  isActive: boolean;
}

/** Active recording sessions keyed by guild ID */
const sessions = new Map<string, RecordingSession>();

const SAMPLE_RATE = 48000;
const CHANNELS = 2;
const BYTES_PER_SAMPLE = 2; // 16-bit PCM

/**
 * Start recording in a voice channel.
 * Returns the session if successful.
 */
export async function startRecording(
  voiceChannel: VoiceChannel,
  startedBy: string
): Promise<RecordingSession> {
  const guildId = voiceChannel.guild.id;

  if (sessions.has(guildId)) {
    throw new Error("Already recording in this server. Use `/scribe stop` first.");
  }

  const connection = joinVoiceChannel({
    channelId: voiceChannel.id,
    guildId: guildId,
    adapterCreator: voiceChannel.guild.voiceAdapterCreator,
    selfDeaf: false, // Must not be deafened to receive audio
    selfMute: true,  // We don't need to speak
  });

  // Wait for the connection to be ready
  try {
    await entersState(connection, VoiceConnectionStatus.Ready, 20_000);
  } catch {
    connection.destroy();
    throw new Error("Failed to connect to voice channel within 20 seconds.");
  }

  const session: RecordingSession = {
    guildId,
    channelId: voiceChannel.id,
    channelName: voiceChannel.name,
    startedAt: new Date(),
    startedBy,
    connection,
    userBuffers: new Map(),
    chunkStartedAt: new Date(),
    isActive: true,
  };

  // Subscribe to audio from the voice connection
  const receiver = connection.receiver;

  receiver.speaking.on("start", (userId) => {
    if (!session.isActive) return;

    // Don't re-subscribe if we're already listening to this user
    if (session.userBuffers.has(userId)) return;

    const member = voiceChannel.guild.members.cache.get(userId);
    const displayName = member?.displayName || userId;

    const userBuffer: UserAudioBuffer = {
      pcmChunks: [],
      displayName,
      userId,
    };
    session.userBuffers.set(userId, userBuffer);

    // Create an audio stream for this user
    const audioStream = receiver.subscribe(userId, {
      end: {
        behavior: EndBehaviorType.Manual,
      },
    });

    // Decode opus to PCM
    const decoder = new prism.opus.Decoder({
      rate: SAMPLE_RATE,
      channels: CHANNELS,
      frameSize: 960,
    });

    const pcmCollector = new Writable({
      write(chunk: Buffer, _encoding, callback) {
        if (session.isActive) {
          const existing = session.userBuffers.get(userId);
          if (existing) {
            existing.pcmChunks.push(Buffer.from(chunk));
          }
        }
        callback();
      },
    });

    audioStream.pipe(decoder).pipe(pcmCollector);

    audioStream.on("error", (err) => {
      console.error(`Audio stream error for ${displayName}:`, err.message);
    });

    decoder.on("error", (err) => {
      console.error(`Decoder error for ${displayName}:`, err.message);
    });
  });

  sessions.set(guildId, session);
  console.log(`[Scribe] Recording started in #${voiceChannel.name} by ${startedBy}`);
  return session;
}

/**
 * Harvest the current audio chunk from all users and reset buffers.
 * Returns null if there was no audio in this period.
 */
export function harvestChunk(guildId: string): AudioChunk | null {
  const session = sessions.get(guildId);
  if (!session || !session.isActive) return null;

  const now = new Date();
  const speakers: string[] = [];
  const allPcm: Buffer[] = [];

  for (const [userId, userBuf] of session.userBuffers) {
    if (userBuf.pcmChunks.length > 0) {
      speakers.push(userBuf.displayName);
      allPcm.push(...userBuf.pcmChunks);
      // Reset this user's buffer for the next chunk
      userBuf.pcmChunks = [];
    }
  }

  if (allPcm.length === 0) return null;

  const chunk: AudioChunk = {
    pcmBuffer: mixPcmBuffers(allPcm),
    speakers,
    startedAt: session.chunkStartedAt,
    endedAt: now,
  };

  // Reset chunk timer
  session.chunkStartedAt = now;

  return chunk;
}

/**
 * Stop recording and return any remaining audio chunk.
 */
export function stopRecording(guildId: string): { session: RecordingSession; finalChunk: AudioChunk | null } {
  const session = sessions.get(guildId);
  if (!session) {
    throw new Error("No active recording in this server.");
  }

  session.isActive = false;

  // Harvest any remaining audio
  const finalChunk = harvestChunk(guildId);

  // Disconnect from voice
  const existingConnection = getVoiceConnection(guildId);
  if (existingConnection) {
    existingConnection.destroy();
  }

  sessions.delete(guildId);
  console.log(`[Scribe] Recording stopped in #${session.channelName}`);

  return { session, finalChunk };
}

/**
 * Get the active session for a guild (if any).
 */
export function getSession(guildId: string): RecordingSession | undefined {
  return sessions.get(guildId);
}

/**
 * Mix multiple PCM buffers together by summing samples (clamped to int16 range).
 * This is a simple additive mix - all speakers combined into one track.
 */
function mixPcmBuffers(buffers: Buffer[]): Buffer {
  if (buffers.length === 0) return Buffer.alloc(0);
  if (buffers.length === 1) return buffers[0];

  // Find the longest buffer
  const maxLen = Math.max(...buffers.map((b) => b.length));
  const mixed = Buffer.alloc(maxLen);

  for (let i = 0; i < maxLen; i += 2) {
    let sum = 0;
    for (const buf of buffers) {
      if (i + 1 < buf.length) {
        sum += buf.readInt16LE(i);
      }
    }
    // Clamp to int16 range
    sum = Math.max(-32768, Math.min(32767, sum));
    mixed.writeInt16LE(sum, i);
  }

  return mixed;
}

/**
 * Convert raw PCM buffer to WAV format for Whisper API.
 */
export function pcmToWav(pcm: Buffer): Buffer {
  const dataSize = pcm.length;
  const header = Buffer.alloc(44);

  // RIFF header
  header.write("RIFF", 0);
  header.writeUInt32LE(36 + dataSize, 4);
  header.write("WAVE", 8);

  // fmt chunk
  header.write("fmt ", 12);
  header.writeUInt32LE(16, 16);           // chunk size
  header.writeUInt16LE(1, 20);            // PCM format
  header.writeUInt16LE(CHANNELS, 22);     // channels
  header.writeUInt32LE(SAMPLE_RATE, 24);  // sample rate
  header.writeUInt32LE(SAMPLE_RATE * CHANNELS * BYTES_PER_SAMPLE, 28); // byte rate
  header.writeUInt16LE(CHANNELS * BYTES_PER_SAMPLE, 32); // block align
  header.writeUInt16LE(BYTES_PER_SAMPLE * 8, 34);        // bits per sample

  // data chunk
  header.write("data", 36);
  header.writeUInt32LE(dataSize, 40);

  return Buffer.concat([header, pcm]);
}
