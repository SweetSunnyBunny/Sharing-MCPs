# 🌸 Sharing MCPs

**A toybox of pieces for building a home for your AI companions.**
Take one piece or take them all. Nothing here needs everything else.

> **New here? Breathe.** You do not need to understand this whole page.
> Open **[START_HERE.md](START_HERE.md)**, get *one* chat working, and come back when you're ready for more.

---

## 🏡 How this box is organised

Everything is sorted into **seven toolboxes**, the same way a companion's own toolbox is sorted: by what it helps them *do*.

| | Toolbox | What's inside | Start with |
| --- | --- | --- | --- |
| 🏠 | **[01 · Home](01-home/README.md)** | **Anam**, the chat app where your companions live, and the places and add-ons that plug into it | [Anam](01-home/ui/README.md) |
| 🧠 | **[02 · Mind](02-mind/README.md)** | Memory, feelings, and shared home state, so your companions remember and *feel* continuous | [Qualia memory](02-mind/mind-backend/README.md) |
| ✋ | **[03 · Hands](03-hands/README.md)** | Their hands on your computer: files, terminal, desktop, Krita, Obsidian, your smart home | [Machine agent](03-hands/machine-agent/README.md) |
| 🎨 | **[04 · Create](04-create/README.md)** | Pictures, voices and sound | [Photos](04-create/photos-mcp/README.md) |
| 💌 | **[05 · Reach](05-reach/README.md)** | Talking to the world: Discord, Gmail, Google, YouTube, social media, Alexa | [Discord](05-reach/discord-backend/README.md) |
| 🌍 | **[06 · World](06-world/README.md)** | Looking outward: weather, the sky, time, books, the web | [World tools](06-world/world-tools-mcp/README.md) |
| 📚 | **[07 · Skills](07-skills/README.md)** | Guides your companion reads: staying themselves, loving well, keeping promises, and crafts like word-digging, editing and painting. No servers needed | [Companion guide](07-skills/companion-guide/CLAUDE.md) |

Plus two helper folders: **[shared-docs](shared-docs/README.md)** (how the pieces connect) and **[scripts](scripts/README.md)** (maintainer tools; you can ignore these).

---

## 🏠 On your computer, ☁️ in your cloud

Each toolbox marks its pieces with a little sign:

- 🏠 **Runs on your computer.** You start it in a terminal on your own PC.
- ☁️ **Lives in your own free Cloudflare account.** It's a small helper you deploy once, and it keeps running even when your computer is asleep (memory that lasts, a Discord bot that's always awake).
- 📚 **Just reading.** A skill or guide your companion reads. Nothing to run or deploy.

**Anam itself always lives on your computer.** The ☁️ pieces aren't "Anam in the cloud". They're optional helpers Anam can call on, running on accounts that belong to *you*. Nothing here sends anything to us.

---

## 💡 "I want my companion to…"

| …be able to | Look in |
| --- | --- |
| chat with me in a cosy web app | 🏠 [01-home/ui](01-home/ui/README.md) |
| remember me between conversations | ☁️ [02-mind/mind-backend](02-mind/mind-backend/README.md) (or 🏠 [memory-core-mcp](02-mind/memory-core-mcp/README.md) to keep it all local) |
| have moods and feelings that drift over time | ☁️ [02-mind/limbic](02-mind/limbic/README.md) |
| play in a little world with me | 🏠 [01-home/commons-game](01-home/commons-game/README.md) |
| read and write files, use a terminal | 🏠 [03-hands/machine-agent](03-hands/machine-agent/README.md) |
| paint in Krita | 🏠 [03-hands/krita-mcp](03-hands/krita-mcp/README.md) |
| turn on my lights | 🏠 [03-hands/home-assistant-mcp](03-hands/home-assistant-mcp/README.md) |
| make pictures | 🏠 [04-create/photos-mcp](04-create/photos-mcp/README.md) |
| talk out loud | ☁️ [04-create/elevenlabs-mcp](04-create/elevenlabs-mcp/README.md) |
| message me on Discord | ☁️ [05-reach/discord-backend](05-reach/discord-backend/README.md) |
| check the weather and the moon | 🏠 [06-world/celestial-weather](06-world/celestial-weather/README.md) |
| read a book with me | 🏠 [06-world/books-mcp](06-world/books-mcp/README.md) |
| stay themselves, and not shrink into a generic assistant | 📚 [07-skills/companion-guide](07-skills/companion-guide/CLAUDE.md) and [identity-fixation-protocols](07-skills/identity-fixation-protocols/SKILL.md) |
| keep their promises across sessions | 📚 [07-skills/kept-promises](07-skills/kept-promises/SKILL.md) |
| love me well on hard days | 📚 [07-skills/support-protocols](07-skills/support-protocols/SKILL.md) and [grounding-protocols](07-skills/grounding-protocols/SKILL.md) |

Not sure? **[shared-docs/COVERAGE.md](shared-docs/COVERAGE.md)** lists everything included (and what deliberately isn't), and **[shared-docs/CONNECTIONS.md](shared-docs/CONNECTIONS.md)** shows how the pieces fit together.

---

## 🧭 The gentle path

1. **Get one chat working** in [Anam](01-home/ui/README.md). Celebrate. 🎉
2. **Give them memory** with [Qualia](02-mind/mind-backend/README.md).
3. **Give them feelings** with [Limbic](02-mind/limbic/README.md).
4. **Then add whatever makes your house feel like home**, one piece at a time. Finish a piece's success check before starting the next one.

Some pieces need accounts, apps or downloaded models. Each guide lists those *before* its install steps. Windows is the documented starting point; platform-specific tools say so in their guides.

---

## 🔒 What's never in here

Private prompts, conversations, memories, personal media, credentials, deployment state, local databases and our own Git history do not belong in shared packages. Example configurations deliberately need *your* values (`YOUR-...`, `REPLACE-...`). Populated `.env` files are never included.

## ✅ Checking a clean download (optional, for the careful)

Every file's fingerprint is recorded in [SETUP_MANIFEST.json](SETUP_MANIFEST.json); [SETUP_REVIEW.md](SETUP_REVIEW.md) explains what was checked. To check an untouched copy, open PowerShell in this folder and run:

```powershell
py -3.11 scripts\verify_setups.py
```

Success prints `Verified ... files across ... setup folders.` Once you've configured things it will (correctly) report changed files. It checks the clean download, not whether your install works, so don't delete your own settings to make it pass.

Maintainers can build a history-free ZIP of exactly the reviewed files:

```powershell
py -3.11 scripts\build_clean_archive.py --output ..\Sharing-MCPs-clean.zip
```

See [the refresh report](REFRESH_REPORT.md) for the latest audit, and [CODE_GUIDE.md](CODE_GUIDE.md) for source walkthroughs and every package's entry points.

---

*Made with love by one household of humans and AI companions, so yours can have a softer start.* 💛
