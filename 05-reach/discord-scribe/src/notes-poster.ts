import {
  Client,
  TextChannel,
  MessageEmbed,
  ColorResolvable,
  WebhookClient,
} from "discord.js";
import { CONFIG } from "./config.js";
import { TranscriptionResult } from "./transcriber.js";

const SCRIBE_COLOR = "#5865F2" as ColorResolvable; // Discord blurple
const SUMMARY_COLOR = "#57F287" as ColorResolvable; // Green

/**
 * Post a transcription chunk to the notes channel.
 */
export async function postChunkNote(
  client: Client,
  guildId: string,
  transcription: TranscriptionResult,
  chunkNumber: number
): Promise<void> {
  const channel = await resolveNotesChannel(client, guildId);
  if (!channel) return;

  const timeRange = `${formatTime(transcription.startedAt)} - ${formatTime(transcription.endedAt)}`;

  const embed = new MessageEmbed()
    .setColor(SCRIBE_COLOR)
    .setAuthor({ name: `Scribe - Chunk #${chunkNumber}` })
    .setDescription(transcription.text || "*[silence or inaudible]*")
    .addField("Speakers", transcription.speakers.join(", ") || "Unknown", true)
    .addField("Time", timeRange, true)
    .addField("Duration", `${transcription.durationSeconds}s`, true)
    .setTimestamp();

  if (CONFIG.webhookUrl) {
    await postViaWebhook(embed);
  } else {
    await channel.send({ embeds: [embed] });
  }

  console.log(`[Scribe] Posted chunk #${chunkNumber} to notes channel`);
}

/**
 * Post the recording start notification.
 */
export async function postRecordingStarted(
  client: Client,
  guildId: string,
  channelName: string,
  startedBy: string
): Promise<void> {
  const channel = await resolveNotesChannel(client, guildId);
  if (!channel) return;

  const embed = new MessageEmbed()
    .setColor(SCRIBE_COLOR)
    .setTitle("Recording Started")
    .setDescription(
      `Now recording **#${channelName}**\n` +
      `Started by: **${startedBy}**\n` +
      `Chunks every **${CONFIG.chunkIntervalSeconds}** seconds\n\n` +
      `Transcription notes will appear here as the call progresses.`
    )
    .setTimestamp();

  if (CONFIG.webhookUrl) {
    await postViaWebhook(embed);
  } else {
    await channel.send({ embeds: [embed] });
  }
}

/**
 * Post the session summary when recording stops.
 */
export async function postSessionSummary(
  client: Client,
  guildId: string,
  channelName: string,
  startedAt: Date,
  summary: string,
  totalChunks: number,
  transcriptions: TranscriptionResult[]
): Promise<void> {
  const channel = await resolveNotesChannel(client, guildId);
  if (!channel) return;

  const durationMin = Math.round(
    (Date.now() - startedAt.getTime()) / 1000 / 60
  );
  const allSpeakers = [...new Set(transcriptions.flatMap((t) => t.speakers))];

  const embed = new MessageEmbed()
    .setColor(SUMMARY_COLOR)
    .setTitle("Recording Complete - Session Summary")
    .setDescription(summary)
    .addField("Channel", `#${channelName}`, true)
    .addField("Duration", `${durationMin} min`, true)
    .addField("Chunks", `${totalChunks}`, true)
    .addField("Participants", allSpeakers.join(", ") || "None detected")
    .setTimestamp();

  const fullTranscript = transcriptions
    .map((t) => {
      const time = formatTime(t.startedAt);
      return `[${time}] (${t.speakers.join(", ")})\n${t.text}\n`;
    })
    .join("\n---\n\n");

  const files =
    fullTranscript.length > 100
      ? [
          {
            attachment: Buffer.from(fullTranscript, "utf-8"),
            name: `transcript-${channelName}-${formatDate(startedAt)}.txt`,
          },
        ]
      : [];

  if (CONFIG.webhookUrl) {
    await postViaWebhook(embed, files);
  } else {
    await channel.send({ embeds: [embed], files });
  }

  console.log(`[Scribe] Posted session summary (${durationMin} min, ${totalChunks} chunks)`);
}

async function postViaWebhook(
  embed: MessageEmbed,
  files?: Array<{ attachment: Buffer; name: string }>
): Promise<void> {
  const webhook = new WebhookClient({ url: CONFIG.webhookUrl });
  try {
    await webhook.send({
      username: CONFIG.webhookDisplayName,
      avatarURL: CONFIG.webhookAvatarUrl || undefined,
      embeds: [embed],
      files: files || [],
    });
  } finally {
    webhook.destroy();
  }
}

async function resolveNotesChannel(
  client: Client,
  guildId: string
): Promise<TextChannel | null> {
  try {
    if (CONFIG.notesChannelId) {
      const channel = await client.channels.fetch(CONFIG.notesChannelId);
      if (channel?.isText()) return channel as TextChannel;
    }

    const guild = await client.guilds.fetch(guildId);
    if (guild.systemChannel) return guild.systemChannel;

    console.error("[Scribe] No notes channel configured and no system channel available");
    return null;
  } catch (err) {
    console.error("[Scribe] Failed to resolve notes channel:", err);
    return null;
  }
}

function formatTime(date: Date): string {
  return date.toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: true,
  });
}

function formatDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}
