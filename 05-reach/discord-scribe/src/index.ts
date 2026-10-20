import { Client, Intents, Interaction } from "discord.js";
import { CONFIG } from "./config.js";
import { handleScribeCommand } from "./commands.js";

const client = new Client({
  intents: [
    Intents.FLAGS.GUILDS,
    Intents.FLAGS.GUILD_VOICE_STATES,
    Intents.FLAGS.GUILD_MEMBERS,
  ],
});

client.once("ready", () => {
  console.log(`[Scribe] Logged in as ${client.user?.tag}`);
  console.log(`[Scribe] Chunk interval: ${CONFIG.chunkIntervalSeconds}s`);
  console.log(`[Scribe] Notes channel: ${CONFIG.notesChannelId || "(server system channel)"}`);
  console.log(`[Scribe] Webhook: ${CONFIG.webhookUrl ? "configured" : "not set (using bot messages)"}`);
  console.log("[Scribe] Ready to record. Use /scribe start in a voice channel.");
});

client.on("interactionCreate", async (interaction: Interaction) => {
  if (!interaction.isCommand()) return;
  if (interaction.commandName !== "scribe") return;

  try {
    await handleScribeCommand(interaction, client);
  } catch (err) {
    console.error("[Scribe] Command error:", err);
    const reply = interaction.deferred || interaction.replied
      ? interaction.editReply.bind(interaction)
      : interaction.reply.bind(interaction);
    await reply({ content: "Something went wrong. Check the bot logs." });
  }
});

// Graceful shutdown
process.on("SIGINT", () => {
  console.log("[Scribe] Shutting down...");
  client.destroy();
  process.exit(0);
});

process.on("SIGTERM", () => {
  console.log("[Scribe] Shutting down...");
  client.destroy();
  process.exit(0);
});

client.login(CONFIG.discordToken);
