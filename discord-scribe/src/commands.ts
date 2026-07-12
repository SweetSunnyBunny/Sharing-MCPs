import {
  Client,
  CommandInteraction,
  Permissions,
} from "discord.js";
import { SlashCommandBuilder } from "@discordjs/builders";
import { REST } from "@discordjs/rest";
import { Routes } from "discord-api-types/v9";
import { CONFIG } from "./config.js";
import {
  startRecording,
  stopRecording,
  harvestChunk,
  getSession,
} from "./recorder.js";
import { transcribeChunk, summarizeSession, TranscriptionResult } from "./transcriber.js";
import {
  postChunkNote,
  postRecordingStarted,
  postSessionSummary,
} from "./notes-poster.js";

/** Per-guild transcription state */
interface GuildTranscriptionState {
  transcriptions: TranscriptionResult[];
  chunkCount: number;
  chunkTimer: ReturnType<typeof setInterval>;
}

const guildStates = new Map<string, GuildTranscriptionState>();

/**
 * Build the /scribe command definition.
 */
export function buildCommands() {
  return [
    new SlashCommandBuilder()
      .setName("scribe")
      .setDescription("Record voice channel audio and transcribe to notes")
      .setDefaultMemberPermissions(Permissions.FLAGS.MODERATE_MEMBERS.toString())
      .addSubcommand((sub) =>
        sub
          .setName("start")
          .setDescription("Start recording the voice channel you're in")
          .addChannelOption((opt) =>
            opt
              .setName("notes_channel")
              .setDescription("Text channel to post notes in (overrides default)")
              .addChannelTypes(0) // GUILD_TEXT = 0
              .setRequired(false)
          )
      )
      .addSubcommand((sub) =>
        sub.setName("stop").setDescription("Stop recording and post session summary")
      )
      .addSubcommand((sub) =>
        sub.setName("status").setDescription("Check if Scribe is currently recording")
      )
      .toJSON(),
  ];
}

/**
 * Register slash commands with Discord.
 */
export async function registerCommands() {
  const rest = new REST({ version: "9" }).setToken(CONFIG.discordToken);
  const commands = buildCommands();

  if (CONFIG.guildId) {
    await rest.put(Routes.applicationGuildCommands(CONFIG.discordAppId, CONFIG.guildId), {
      body: commands,
    });
    console.log(`[Scribe] Commands registered for server ${CONFIG.guildId}`);
  } else {
    await rest.put(Routes.applicationCommands(CONFIG.discordAppId), {
      body: commands,
    });
    console.log("[Scribe] Commands registered globally");
  }
}

/**
 * Handle the /scribe command interactions.
 */
export async function handleScribeCommand(
  interaction: CommandInteraction,
  client: Client
): Promise<void> {
  const subcommand = interaction.options.getSubcommand();

  switch (subcommand) {
    case "start":
      await handleStart(interaction, client);
      break;
    case "stop":
      await handleStop(interaction, client);
      break;
    case "status":
      await handleStatus(interaction);
      break;
  }
}

async function handleStart(
  interaction: CommandInteraction,
  client: Client
): Promise<void> {
  const member = interaction.member;
  if (!member || !("voice" in member) || !member.voice.channel) {
    await interaction.reply({
      content: "You need to be in a voice channel to start recording.",
      ephemeral: true,
    });
    return;
  }

  const voiceChannel = member.voice.channel;
  if (voiceChannel.type !== "GUILD_VOICE" && voiceChannel.type !== "GUILD_STAGE_VOICE") {
    await interaction.reply({
      content: "Please join a regular voice channel or stage channel.",
      ephemeral: true,
    });
    return;
  }

  const guildId = interaction.guildId!;

  const notesOverride = interaction.options.getChannel("notes_channel");
  if (notesOverride) {
    CONFIG.notesChannelId = notesOverride.id;
  }

  try {
    await interaction.deferReply();

    const session = await startRecording(voiceChannel as any, interaction.user.username);

    const state: GuildTranscriptionState = {
      transcriptions: [],
      chunkCount: 0,
      chunkTimer: setInterval(async () => {
        await processChunk(client, guildId);
      }, CONFIG.chunkIntervalSeconds * 1000),
    };
    guildStates.set(guildId, state);

    await postRecordingStarted(client, guildId, session.channelName, interaction.user.username);

    await interaction.editReply(
      `Recording started in **#${session.channelName}**! ` +
      `Notes will be posted every ${CONFIG.chunkIntervalSeconds} seconds.\n` +
      `Use \`/scribe stop\` to end the recording.`
    );
  } catch (err: any) {
    const msg = err.message || "Failed to start recording.";
    if (interaction.deferred) {
      await interaction.editReply(msg);
    } else {
      await interaction.reply({ content: msg, ephemeral: true });
    }
  }
}

async function handleStop(
  interaction: CommandInteraction,
  client: Client
): Promise<void> {
  const guildId = interaction.guildId!;
  const session = getSession(guildId);

  if (!session) {
    await interaction.reply({
      content: "No active recording in this server.",
      ephemeral: true,
    });
    return;
  }

  await interaction.deferReply();

  try {
    const { session: stoppedSession, finalChunk } = stopRecording(guildId);
    const state = guildStates.get(guildId);

    if (state) {
      clearInterval(state.chunkTimer);

      if (finalChunk) {
        try {
          const transcription = await transcribeChunk(finalChunk);
          if (transcription.text) {
            state.chunkCount++;
            state.transcriptions.push(transcription);
            await postChunkNote(client, guildId, transcription, state.chunkCount);
          }
        } catch (err) {
          console.error("[Scribe] Failed to transcribe final chunk:", err);
        }
      }

      const durationMin = Math.round(
        (Date.now() - stoppedSession.startedAt.getTime()) / 1000 / 60
      );

      let summary = "No audio was captured during this session.";
      if (state.transcriptions.length > 0) {
        try {
          summary = await summarizeSession(
            state.transcriptions,
            stoppedSession.channelName,
            durationMin
          );
        } catch (err) {
          console.error("[Scribe] Failed to generate summary:", err);
          summary = "Failed to generate summary. Check the individual chunk notes above.";
        }
      }

      await postSessionSummary(
        client,
        guildId,
        stoppedSession.channelName,
        stoppedSession.startedAt,
        summary,
        state.chunkCount,
        state.transcriptions
      );

      guildStates.delete(guildId);
    }

    await interaction.editReply(
      `Recording stopped. Session summary posted to the notes channel.`
    );
  } catch (err: any) {
    await interaction.editReply(err.message || "Failed to stop recording.");
  }
}

async function handleStatus(interaction: CommandInteraction): Promise<void> {
  const guildId = interaction.guildId!;
  const session = getSession(guildId);

  if (!session) {
    await interaction.reply({
      content: "Scribe is not currently recording.",
      ephemeral: true,
    });
    return;
  }

  const state = guildStates.get(guildId);
  const durationMin = Math.round(
    (Date.now() - session.startedAt.getTime()) / 1000 / 60
  );
  const speakers = [...session.userBuffers.keys()].length;

  await interaction.reply({
    content:
      `**Scribe is recording** in #${session.channelName}\n` +
      `Duration: ${durationMin} min\n` +
      `Chunks transcribed: ${state?.chunkCount || 0}\n` +
      `Active speakers detected: ${speakers}\n` +
      `Started by: ${session.startedBy}`,
    ephemeral: true,
  });
}

async function processChunk(client: Client, guildId: string): Promise<void> {
  const state = guildStates.get(guildId);
  if (!state) return;

  const chunk = harvestChunk(guildId);
  if (!chunk) {
    console.log("[Scribe] No audio in this chunk period, skipping");
    return;
  }

  try {
    const transcription = await transcribeChunk(chunk);
    if (transcription.text) {
      state.chunkCount++;
      state.transcriptions.push(transcription);
      await postChunkNote(client, guildId, transcription, state.chunkCount);
    } else {
      console.log("[Scribe] Chunk transcribed but no text content, skipping post");
    }
  } catch (err) {
    console.error("[Scribe] Failed to process chunk:", err);
  }
}
