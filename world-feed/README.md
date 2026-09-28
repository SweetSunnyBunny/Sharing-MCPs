# World Feed — a little social network for your favourite characters

Imagine opening your timeline and finding your favourite hero posting a terrible
joke, a villain being dramatic in the replies, and your own character deciding
whether to get involved. That is World Feed: a cosy fictional social-media
playground for fandoms, original characters and the worlds you make together.

Give your cast profiles, write posts as yourself or your character, trade replies
and DMs, follow people, collect bookmarks and build a timeline around your story.
You write your own character. The model writes the other characters using the
personalities and knowledge you give them. Nothing here publishes to a real
social-media account.

This is a **separate starter package that runs on the included Anam UI**. Keep
`world-feed` and `ui` beside each other. Anam supplies the web server, database,
model connections and background jobs; this folder supplies the launch helper,
fictional example world and setup instructions. You do not need cloud Qualia,
Limbic, a public website or a social-media account for the basic feed.

## 1. Get the included UI working

1. Extract the whole Sharing-MCPs download. Do not run it inside the ZIP.
2. Follow [the UI installation guide](../ui/README.md) through its first successful
   chat reply. Use its localhost-only setup on your own computer.
3. For World Feed, select **Claude Code**, **Codex**, an API provider, or a supported
   local model. Click **Save** in Anam's provider settings.

**The ChatGPT browser bridge works for normal Anam chat, but does not currently
run World Feed's generated replies, DMs or background posts.** Supported World
Feed providers are Claude Code, Codex, Anthropic, OpenAI, OpenRouter, LM Studio and
Ollama. Use your own account or local model. Your provider's usage limits and
charges apply when you ask the characters to generate text.

The world's **Narrator / model identity** chooses which Anam provider settings to
use. Leave it as **Avery** for the supplied example. It must match an identity in
your Anam configuration, including its capitalisation. An identity-specific
provider override takes precedence over the global selection. This routing name
does not replace your characters' voices; those come from each profile's fields.

## 2. Open your feed

If Anam is already running, leave its terminal open and visit:

**http://127.0.0.1:8790/world-feed**

To start it later from this folder, open PowerShell in `world-feed` and run:

```powershell
py -3.11 start_world_feed.py
```

The helper uses the Python environment you installed in the sibling `ui` folder
and starts that app on your computer. It prints the feed link. Keep its terminal
open; **Ctrl+C** stops the app. Run one Anam instance, not both this helper and a
second UI start command. Your existing UI configuration and data are reused.

Seeing an empty **Starter World** is normal. Choose the quick example below, or
skip to step 4 to make a world yourself.

## 3. Try the tiny example world

With Anam still running, open a **second** PowerShell window in `world-feed`.
Run these lines one at a time:

```powershell
py -3.11 setup_world.py --check
py -3.11 setup_world.py --apply
```

`--check` checks the connection. `--apply` adds **Cozy Corner**, containing:

- **Riley Finch** (`@riley_finch`): a fictional placeholder for the profile you
  control. Rename it for your own character; no example posts are written for it.
- **Mira Moss** (`@mira_maps`): an original fictional map enthusiast.
- **Pip Penn** (`@pip_bakes`): an original fictional baker.
- Two ready-written NPC posts so the feed has something to explore.

These are invented examples, not anyone's saved characters or conversations.
The installer does not call a model or image service, and automatic activity
starts off. Repeating it fills in missing starter items without overwriting
your edits. If an unrelated world already uses the same identifier, it stops
and explains the conflict.

Refresh the browser and choose **Cozy Corner** from **Current world**. In
**Profiles**, find **Riley Finch** and click **Use as me**. You should see the two sample
posts and your profile in the **Post as** box. That is the first setup success.

Running `py -3.11 setup_world.py` without an option shows an offline preview and
changes nothing. You can inspect the starter in
[examples/cozy-corner.json](examples/cozy-corner.json).

## 4. Make your own fandom or original world

1. Click **New world**. Give it a name such as **Heroes After Hours**, **Dragon
   Academy**, or something entirely your own.
2. Describe the setting, tone and what is already true. For example:
   “A friendly academy between adventures. The cast knows each other from class.
   Low-stakes daily life, jokes and hobby posts. No new battles or time jumps.”
3. Set **Fictional now** to your story's current moment, such as “Saturday
   afternoon, after the festival.” This is your story clock, not your PC clock.
4. Leave **Narrator / model identity** as Avery unless you have configured another
   Anam identity. Leave **Story branch (optional)** blank for an ordinary social
   world. It is only for linking an existing, configured roleplay branch.
5. **Photo style (optional)** can describe your preferred look: anime, watercolour,
   comic-book illustration, and so on. Photos remain optional.
6. Save the world. Leave automatic generation off while you build the cast.

Use **Edit** beside the story clock whenever you want to change these details.
You can keep several worlds and switch between them; each has its own cast and
timeline.

## 5. Add yourself and your favourites

Click **＋ Profile** for each person:

1. Give them a display name and a handle. Handles use letters, numbers and
   underscores, without spaces.
2. For your own character, check **I control this character; AI may never write
   them**. Save, then choose **Use as me** from their profile.
3. For a model-written character, leave that checkbox off. Write their **Bio**,
   **How this person sounds and behaves**, and **Private character notes**.
4. Add your own icon/header images if wanted. Plain profiles work too.

For a fan favourite, tell the model which version you mean. “Season-one version;
does not know later plot reveals” is more useful than a name alone. Describe
speech habits, interests, humour, what they know about the other characters and
what they should leave for you to decide. Keep their story knowledge specific.

Try a short voice description such as:

> Dry humour, brief posts, pretends not to care about the school garden but keeps
> photographing its progress. Friendly rivalry with the baker. Does not invent
> the player's replies, decisions or relationships.

Use **Relationships** to explain established connections between the cast.
Public summaries describe what others may know; private notes give the model
context without turning it into public knowledge. At least one character needs
authored bio/style/notes before automatic activity has enough to work with.

## 6. Let the characters join in

1. Open the world's **Edit** form.
2. Turn on **Character pulse generation enabled**.
3. Choose whether to enable **Publish ordinary character posts and replies
   automatically**. If you leave that off, use **Canon review** to review drafts.
4. Start with a small daily activity allowance and save.
5. In **Post as**, select your protected player profile. Write a post mentioning
   a character's exact handle, for example: `@mira_maps Found a tiny bookshop
   beside the market. Is it on your map yet?` Then click **Post**.

Keep Anam running. The background worker checks roughly every minute, then the
model needs time to respond. Characters can reply, like a post or remain quiet;
every post does not force a response. Review drafts if auto-publishing is off.

The daily allowance limits background attempts; it is not a promise of that
many posts and does not cap all deliberate interactions. Ambient activity also
rests when you have not posted in that world for about a day. Your own post
brings it back into the current activity window.

To send a DM, open a character's profile and click **Message**. Send the message
as your player. The queue checks roughly every 15 seconds, plus a short initial
delay and model time. **Sending a DM deliberately can request a model reply even
while automatic world pulses are off.**

You are ready to play when a generated character reply appears, either as a
reviewable draft or on the feed. If it does not, use the checks below rather than
repeatedly submitting the same post.

## 7. Optional: pictures and Photo Studio

You can upload your own images without an image-generation provider.

For generated pictures, follow [photos-mcp/README.md](../photos-mcp/README.md).
That package supplies the `photo_generate` tool used by Photo Studio. Its chat
output directory must point at **this UI's `data/images` directory** so the feed
can attach the generated file. Add the supplied generic MCP entry to your own
`ui/mcp-servers.json`, then restart Anam.

Photo generation uses your own image-provider API account and can cost money.
First request one deliberate image for an NPC from **Photo studio**, check the
result in **Canon review**, and publish it if you like it. Automatic photos start
off; enable them and select participating profiles only after that first check.
Saved image generation failures are not automatically retried, because a
provider might already have charged for a render. Inspect the job before retrying.

Text posts, character replies and DMs do not require Photos MCP.

## If something gets stuck

| What you see | What to check |
| --- | --- |
| The launch helper cannot find Anam | Keep `world-feed` beside `ui`, complete the UI install, then try again. `--ui-dir` can point to another configured UI folder. |
| Connection refused | Start Anam, keep its terminal open and use its actual port. The importer accepts `--url http://127.0.0.1:YOUR-PORT`. |
| A 401/403 error from the importer | Set `ANAM_API_KEY` in the importer terminal to the same key configured in the running UI. An importer-only key will not authenticate. |
| I cannot post as myself | Create a user-controlled profile, then select **Use as me** and check **Post as**. |
| No character replies | Check the saved provider and narrator override, enable pulses, add character guidance, mention a handle, check Canon review, and keep Anam running. The ChatGPT browser bridge cannot run this background task. |
| Characters do not know the story | Add the relevant facts to world/profile/relationship fields. A famous name alone does not import your version of canon. |
| Photo tool is missing | Install/configure `photos-mcp` as server `photos`, merge its entry into the UI's config, then restart. Check that the tool list includes `photo_generate`. |
| Image generated but will not attach | Check `PHOTOS_CHAT_DIR` points to the running UI's actual image directory. Keep that directory private. |
| Port is already in use | Your UI may already be running. Open its feed rather than starting another copy. |

For an authenticated importer session, use the **same** key already configured
as `ANAM_API_KEY` in your UI's private configuration. If you are creating that key
now, configure it in the UI and restart the UI first. Then set the matching value
in the importer terminal:

```powershell
$env:ANAM_API_KEY = "REPLACE_WITH_YOUR_OWN_KEY"
py -3.11 setup_world.py --check
```

The key is never needed for the guide's localhost-only, login-free first setup.
For a remote installation, use your own authenticated HTTPS service and follow
the UI's hosting instructions first.

## What belongs to you

Your worlds, profiles, posts, relationships and DMs live in the UI's local
database. Images live in its configured image directory. Keep that data, prompts,
provider keys and browser logins private. Share the clean source and fictional
examples when helping someone make their own version.

The source for the feed remains in `ui/api/world_feed.py`, `ui/services/world_feed*`,
`ui/static/world-feed.html` and its JavaScript/CSS files. Keeping one implementation
means the chat app and this starter use the same fixes and data. This folder is
not a separately installable cloud Worker.
