import * as fs from 'fs';
import * as path from 'path';

// Registers the /vibe slash command for your Discord application.
// Requires DISCORD_BOT_TOKEN and DISCORD_APP_ID as environment variables
// or in a .dev.vars file.
//
// Usage: DISCORD_APP_ID=your_app_id DISCORD_BOT_TOKEN=your_token npx tsx src/register-commands.ts

const APP_ID = process.env.DISCORD_APP_ID;
const COMMAND_NAME = 'vibe';
const COMMAND_DESCRIPTION = "Get a one-sentence summary of the channel's current vibe.";

async function registerCommand() {
    let token = process.env.DISCORD_BOT_TOKEN;

    if (!token) {
        try {
            const devVars = fs.readFileSync(path.join(__dirname, '..', '.dev.vars'), 'utf-8');
            const match = devVars.match(/DISCORD_BOT_TOKEN="?([^"\n]+)"?/);
            if (match) {
                token = match[1];
            }
        } catch (e) {
            // Ignore
        }
    }

    if (!token) {
        console.error('Error: DISCORD_BOT_TOKEN environment variable is required.');
        process.exit(1);
    }

    if (!APP_ID) {
        console.error('Error: DISCORD_APP_ID environment variable is required.');
        console.error('Find it at: https://discord.com/developers/applications');
        process.exit(1);
    }

    const url = `https://discord.com/api/v10/applications/${APP_ID}/commands`;

    const payload = {
        name: COMMAND_NAME,
        description: COMMAND_DESCRIPTION,
        type: 1, // CHAT_INPUT (slash command)
    };

    console.log(`Registering /${COMMAND_NAME} command...`);

    try {
        const response = await fetch(url, {
            method: 'POST',
            headers: {
                'Authorization': `Bot ${token}`,
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(payload),
        });

        if (response.ok) {
            const data = await response.json();
            console.log('Command registered successfully!');
            console.log(data);
        } else {
            console.error('Failed to register command');
            const error = await response.text();
            console.error(`Status: ${response.status} ${response.statusText}`);
            console.error(error);
        }
    } catch (error) {
        console.error('Error registering command:', error);
    }
}

registerCommand();
