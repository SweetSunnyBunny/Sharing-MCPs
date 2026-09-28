# Alexa skill starter — connect an Echo to your own services

This is an **Alexa custom-skill scaffold**, not an MCP server. Its main skill
forwards speech to your running Anam UI and uses Home Assistant to speak a reply
on an Echo. Two optional skills play a voice queue or a hosted music station.
There is no standalone chat model, Home Assistant installation or music library
inside this folder.

For the main skill you need Node.js, a Cloudflare account, an Amazon developer
account, an Echo/Alexa test account, your own running Anam UI, and Home Assistant
with a working Alexa notification service. Complete those dependencies first.
The supplied manifests are development examples; this guide does not certify
or publish a skill in Amazon's public catalog.

## 1. Open the right folder and install

Install **Node.js 24** from [nodejs.org](https://nodejs.org/) if you do not have it.
Extract this download before running anything. In File Explorer, open
`Sharing-MCPs\alexa-skill`. Click the address bar, type `powershell`, and press
Enter. Keep using that window for the commands below.

Run these commands one at a time:

```powershell
Get-Location
node --version
npm --version
npm ci
```

The first line must end in `alexa-skill`; Node should report `v24...`.
Wait for installation to finish before continuing. If PowerShell blocks
`npm.ps1`, use `npm.cmd` instead of `npm`, and `npx.cmd` instead of `npx`.
No global Wrangler installation is needed.

Sign in to your own [Cloudflare account](https://dash.cloudflare.com/):

```powershell
npx wrangler login
npx wrangler whoami
```

Approve the browser sign-in. `whoami` must show the account you want to deploy
into. A Worker is the small service this guide installs in that account.

## 2. Check the services the main skill will call

Your Anam UI must be reachable at an HTTPS origin you control and expose
`POST /api/echo/relay` with its own API key. Use a lowercase/configured identity
ID from that UI for `IDENTITY`. A placeholder address cannot run the relay.

Home Assistant must be reachable from the Worker and already able to speak to
your Echo through a notify service. Test that service in Home Assistant first.
In your Home Assistant user profile, create a long-lived access token for this
integration. Keep it private.

Open the local settings/source:

```powershell
notepad .\wrangler.toml
notepad .\src\index.ts
```

In `wrangler.toml`, replace `ANAM_URL` with your actual UI origin and `IDENTITY`
with your configured identity. Under `[vars]`, add `HA_URL` with your Home
Assistant HTTPS origin. In `src/index.ts`, find `speakThroughEcho` and replace
the example `alexa_media_anam_connection` part of its notify URL with your own
Home Assistant notify service name. For example, if HA exposes
`notify.alexa_media_office`, that URL must end in
`/api/services/notify/alexa_media_office`. Save both files.

## 3. Create the development skill and save its ID

1. Open the [Alexa Developer Console](https://developer.amazon.com/alexa/console/ask).
   Create a **Custom** skill using an endpoint you host, with the `en-US`
   language matching the supplied model.
2. In its interaction model JSON editor, paste the contents of
   `skill/interactionModels/custom/en-US.json`. Save and build the model.
   Its example invocation name is **pack bond**; change it in the model if desired.
3. Copy the new application's skill ID from that console. Keep the console
   open: you will set its HTTPS endpoint after deployment.

Save the ID and your two upstream keys in the Worker:

```powershell
npx wrangler secret put ALEXA_SKILL_ID
npx wrangler secret put ANAM_API_KEY
npx wrangler secret put HA_TOKEN
```

Paste the skill ID, Anam key and Home Assistant token respectively. These are
three different values. Always set `ALEXA_SKILL_ID`; the main scaffold's
application-ID check is not enforced when that setting is missing.

## 4. Build and deploy the Worker

Run the checks first. A dry run builds the Worker without deploying it.

```powershell
npx tsc --noEmit
npx wrangler deploy --dry-run
npm run deploy
```

The last command installs the service in your Cloudflare account. Copy the
`https://...workers.dev` address printed at the end; this is your **Worker URL**.
Use that actual address below, without a trailing slash. `example.com` and
`YOUR-*` values in the supplied files are placeholders, not working services.

In your Alexa skill's endpoint settings, choose **HTTPS** and paste the Worker
URL as the default endpoint. Use the certificate option appropriate to the
Cloudflare HTTPS endpoint; the supplied manifest marks it as a wildcard
certificate. Keep the endpoint at the Worker root for the main skill.

`skill/skill.json` is a manifest reference. If importing that manifest through
another tool, first replace its `YOUR-*` endpoint and review its publishing,
privacy and example-text fields for your own deployment. Do not submit the
example manifest unchanged as a public skill.

## 5. First successful check

In the Alexa console's Test section, enable testing for the development skill.
Use its text simulator and enter **open pack bond** (or your chosen invocation
name). A launch reply such as “Hey, I'm here” confirms Alexa reached the Worker.
That first launch does not yet prove Anam or Home Assistant works.

Next try **ask pack bond to say hello**. The skill should acknowledge promptly;
the separate Anam/Home Assistant path then sends the spoken answer to the Echo.
Watch your Worker logs in PowerShell while testing:

```powershell
npx wrangler tail
```

Press Ctrl+C to stop tailing logs. To use your Echo for the development test,
use the Amazon account/device associated with the enabled test skill. There is
no MCP URL to add to a chat client for this package.

## Optional voice-queue skill

Create a second custom skill using `skill-voice/interactionModels/custom/en-US.json`
and the AudioPlayer interface shown in `skill-voice/skill.json`. Set its HTTPS
endpoint to your Worker URL plus **`/voice`**. Save its own skill ID with:

```powershell
npx wrangler secret put ALEXA_VOICE_SKILL_ID
```

It uses your Anam `/api/voice/queue`, `/api/voice/queue/consume` and
`/api/voice/file/<message_id>` endpoints. The Echo must be able to fetch the
returned HTTPS audio URL. With an empty queue, a no-messages reply is expected.
It still needs the correct Anam URL/key; it does not use the HA notify route.

## Optional radio skill

Create a third skill from `skill-radio/interactionModels/custom/en-US.json`
and its AudioPlayer manifest. Use the Worker URL plus **`/radio`** and set
`ALEXA_RADIO_SKILL_ID`. In `src/index.ts`, replace `PACK_RADIO_STATION_URL` and
`PACK_RADIO_AUDIO_BASE` with your own hosted station JSON and audio directory.
The example `media.example.com` addresses deliberately do not play anything.

A minimal station JSON uses this shape, with audio files you host:

```json
{
  "now_spinning": {"file": "sample.mp3"},
  "playlists": {"Focus": {"tracks": ["sample.mp3"]}},
  "meta": {"sample.mp3": {"title": "Sample track"}}
}
```

Update the interaction-model playlist names to match your station, build the
model and deploy the changed Worker. Your own HTTPS audio must be compatible
with Alexa AudioPlayer. No private playlists or music files are provided.

## If something goes wrong

- **`npm.ps1` blocked:** use `npm.cmd` / `npx.cmd` in PowerShell.
- **Skill launch fails:** verify the endpoint path, matching skill ID, language
  and built interaction model. This Worker accepts POST; opening it as a normal
  browser page is not a health test.
- **Acknowledgment but no reply:** check Anam URL/key, then HA URL/token and the
  exact notification-service name. Test each upstream independently.
- **Voice queue is empty:** put a voice message in your own Anam queue first.
- **Radio stops immediately:** check station JSON, playlist names and public
  audio URLs; example addresses cannot stream audio.

The request checks in this scaffold do not implement full Alexa certificate/
signature verification. Add and verify that before treating it as a completed
public-skill deployment. Keep development testing separate from certification.
The populated service credentials and your own skill/account IDs stay private.

## Reading the code

Start with [src/index.ts](src/index.ts), then [wrangler.toml](wrangler.toml). The installation steps above describe the runtime configuration.
