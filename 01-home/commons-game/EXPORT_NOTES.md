# Public release boundary — 2026-10-19

This release has two new public home spaces: the main house and garden. Their
backgrounds were generated specifically for this starter. The other 39 areas
retain the existing adventure maps, geometry, game definitions and artwork.
Only their return paths into the replacement home and local asset URLs change.

The original private home maps, room notes, belongings, named household pets,
character portraits/sprites, conversations, saves, sign-in keys, private dates,
account configuration, deployment addresses and repository history are excluded.
No original home layout or home painting is included. Adventure metadata identifying
household authors and two personal asides in an adventure guide were removed.

The engine uses this installer's own configuration and private storage. The
administrator and reserved companion names are configurable. Default notifications,
Anam narration and external portrait state are off; missing wrist gates refuse
delivery. The browser needs no connection to the original deployment.

## Verification

- 63 isolated Python tests passed: private-file serving, room chat, durability,
  private backup verification, fresh players, retry transactions, fishing, setup
  idempotence, configurable administrator and HTTP authorization.
- All 41 rooms produced serializable game catalogs; every doorway target exists.
- Browser checks covered sign-in, movement, Inventory, house, garden, Crossroads,
  fantasy village, frontier town, spaceship and a phone-size viewport. No page
  errors, missing assets or external network origins were observed.
- A disposable AI connection completed identity, look and move calls, then was revoked.
- Text, image metadata and exact known private values were screened. Python,
  JSON and JavaScript syntax and image integrity were checked.

These checks cover this assembled release. Configured cloud deployment and optional
Anam/model accounts were not exercised. Recheck additions before redistributing a
world that people have used.
