import { config } from "dotenv";
config();

function required(key: string): string {
  const value = process.env[key];
  if (!value) {
    throw new Error(`Missing required environment variable: ${key}`);
  }
  return value;
}

export const CONFIG = {
  // Discord
  discordToken: required("DISCORD_BOT_TOKEN"),
  discordAppId: required("DISCORD_APP_ID"),
  guildId: process.env.DISCORD_GUILD_ID || "",
  notesChannelId: process.env.NOTES_CHANNEL_ID || "",

  // Claude Code CLI (uses OAuth - no API key needed)
  claudeCmd: process.env.CLAUDE_CMD || "claude",
  claudeModel: process.env.CLAUDE_MODEL || "claude-sonnet-4-6",

  // Local Whisper (for transcription)
  whisperModel: process.env.WHISPER_MODEL || "medium",
  whisperDevice: process.env.WHISPER_DEVICE || "cpu",
  whisperComputeType: process.env.WHISPER_COMPUTE_TYPE || "int8",
  pythonPath: process.env.PYTHON_PATH || "python",

  // Recording
  chunkIntervalSeconds: parseInt(process.env.CHUNK_INTERVAL_SECONDS || "180", 10),

  // Notes posting
  webhookUrl: process.env.NOTES_WEBHOOK_URL || "",
  webhookDisplayName: process.env.WEBHOOK_DISPLAY_NAME || "Scribe",
  webhookAvatarUrl: process.env.WEBHOOK_AVATAR_URL || "",
};
