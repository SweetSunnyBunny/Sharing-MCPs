# Discord code guide

[Cloud service map](../../README.md)

Start at [src/index.ts](src/index.ts) for authentication, JSON-RPC and Discord
interactions. [tools.ts](src/tools.ts) defines tool schemas and dispatch;
[discord.ts](src/discord.ts) performs REST calls, and
[identities.ts](src/identities.ts) resolves identity configuration.

[image-content.ts](src/image-content.ts) creates native MCP image content.
[image-archive.ts](src/image-archive.ts) asks the authenticated machine bridge to
archive images and requires its success receipt. The two `image*.test.ts` files
cover those boundaries using fixtures.

Run `npm run typecheck` and `npm test`. `npm run register:vibe` changes Discord
command registration. `src/verify-image-archive.ts` writes through a live
filesystem bridge even though its generated image is mocked; it is an integration
operation, separate from the fixture tests.
