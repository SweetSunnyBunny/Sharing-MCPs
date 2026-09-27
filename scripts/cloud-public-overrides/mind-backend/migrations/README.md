# Qualia schema migrations — use during database setup

These SQL files create the database structure; they do not contain someone's
memory archive. The [main setup guide](../README.md) includes this step in order.
For a brand-new installation, apply **all 19 numbered migrations**, then your
starter identities. You do not open each SQL file and paste it manually.

## Fresh cloud database

Open the **parent `mind-backend` folder** in File Explorer, type `powershell` in
the address bar and press Enter. Install dependencies and sign in as described
in the main guide. Run `npx wrangler d1 create qualia` if you have not yet created
the database, and put its ID in the parent's `wrangler.toml`.

Then run from that parent folder:

```powershell
npx wrangler d1 migrations apply qualia --remote
npx wrangler d1 migrations list qualia --remote
npx wrangler d1 execute qualia --remote --file .\examples\starter-identities.sql
```

Review the fictional labels in the example before the final command. Success
means Wrangler has no pending migrations and the starter command finishes
without a SQL error. The first migration creates core tables; later ones add
retrieval, cognition, relationships, creative work, anticipation, mutation
tracking, Sketchbook and coordinated memory through `0019`.

## Local development

Use the same commands with `--local` instead of `--remote`. Local and remote D1
are different databases: successfully migrating one does not migrate the other.
The local database lives in `.wrangler` and is not part of the shared starter.

## Existing database

Back up your own database and inspect pending migrations before an upgrade.
Wrangler records applied migrations and skips them on later runs. Do not edit
already-applied migration SQL to force a retry, and do not run the initial
schema over an unrelated legacy database. This package is not an automatic
importer for the old local Qualia data format.

- **Wrong database / missing tables:** check `database_id`, current package
  folder and whether the command used `--remote` or `--local`.
- **Duplicate column/table conflict:** stop and compare migration history with
  the schema in a backed-up test copy before changing anything.
- **Identity foreign-key error in a later tool:** apply your own registered
  identities; schema creation alone does not create personal profiles.
