import { registerCommands } from "./commands.js";

console.log("[Scribe] Registering slash commands...");
registerCommands()
  .then(() => {
    console.log("[Scribe] Done!");
    process.exit(0);
  })
  .catch((err) => {
    console.error("[Scribe] Failed to register commands:", err);
    process.exit(1);
  });
