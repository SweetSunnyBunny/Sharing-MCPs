# Alexa code guide

[Cloud service map](../../README.md)

Read [src/index.ts](src/index.ts) first. The Worker receives an Alexa request,
returns a quick acknowledgement, then asks Anam's Avery route in the background.
Home Assistant notification delivers the completed reply to the Echo.

The initial acknowledgement and later spoken result have different timing
constraints. Follow both paths when changing response handling. Read
`wrangler.toml` alongside the entry point for bindings and runtime setup.

`package.json` provides `dev`, `deploy` and `tail`. The local type check is
`node node_modules/typescript/bin/tsc --noEmit`; there is no typecheck npm alias.
Deployment and live voice tests operate the configured service or devices.
