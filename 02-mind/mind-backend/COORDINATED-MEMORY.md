# Coordinated Qualia memory

The current Worker includes coordinated memory and Sketchbook.

## What reaches a wake

Anam's `qualia_context` hook calls `mind_context` on every autonomous orientation,
including warm provider resumes. Interactive conversations fetch on first use,
topic changes, and after five minutes; similar warm turns reuse the context
already in the conversation. Character sessions are excluded. Existing inner-life
hooks, identity prompts, provider selection and schedules keep their behavior.

Recall combines canonical D1 observations with lexical and semantic relevance,
working records, identity strands, directional bonds, recent inner-life records,
procedures/exercises, intentions, anticipation and the sibling Limbic snapshot.
Round-robin admission keeps separate functions represented within a requested
character budget (default 12,000). Every item carries a source reference, reason
and date; omissions and failed categories are explicit. Old feelings are records,
not a current mood reading. No recall operation changes an identity claim.

`mind_focus` opens a working document under an identity and session key. Changes
require the revision that was read. Update replaces the document, with atomic
prior-state history. Park, resume and close are explicit. Retention is optional;
expired records remain readable by key/history but do not enter recall.

Anam keys conversation work as `anam:<conversation_id>`. Successful scheduled
wakes also preserve the actual final reply in `anam:wake:<run_id>`, park it, and
retain it for recall for 30 days. The record identifies the original conversation,
message and run. It is labelled a model report, not independently verified proof
of completion; it does not invent an unresolved task. Failures do not manufacture
a success handoff. Capture failures are logged without failing a completed wake.

The live MCP bridge can refresh Qualia's catalog without disconnecting active
tools. Fresh Claude/Codex CLI sessions use their existing native MCP configuration.
Anam `/health` exposes `qualia_memory` readiness and missing names, without memory
content. A previously frozen external connector still needs its normal refresh
to expose new wrappers; that does not block Anam's native path.

## Evidence and correction

`mind_evidence` attaches an epistemic kind (event, report, interpretation,
hypothesis, imagining, unclassified), event/verification/validity times,
applicability, sources and explicit contradictions to an existing observation.
Read first; annotate/verify requires the current revision (zero initially).
Source links cannot cross identity privacy boundaries or form cycles, including
concurrent cycles. Evidence metadata keeps revision history.

`mind_scope` gives applicability a structured course-correction interface for an
existing observation. It records `applies_when` and `does_not_apply_when` while
leaving the observation's statement intact. Define/revise requires the current
evidence revision. A revision retains the prior scope in provenance history and
uses the same dependency invalidation path as `mind_evidence`, so conclusions
derived under the earlier domain are flagged for review rather than silently
rewritten. Legacy free-text applicability remains readable until deliberately
converted with `define`.

Canonical content edits, archiving and superseding flag transitive dependents
and linked procedural lessons for review. Evidence annotation changes also flag
dependent observations. These flags do not silently rewrite their content.
`mind_context` labels flagged or expired claims as needing review, and active
procedure recall excludes flagged lessons. Conclusive practice reviews must use
independent later evidence; changed observation evidence is rejected. Similarity
alone queues a supersede proposal instead of retiring another memory.

`mind_orient` marks cached derived narrative stale after a source change newer
than its snapshot; duplicate derived blocks are withheld pending a newer snapshot.
Semantic search returns canonical observation text rather than an old vector
preview. Old unannotated memories are explicitly unclassified; source dependencies
are not guessed or backfilled across the existing archive.

## Practice and retrieval feedback

`mind_procedure` uses the Sketchbook's study/compare/practice/apply/review/read/
recall/list engine for writing, coding and tool use. A study can point to an
immutable source path or a hashed Qualia observation. Store its domain,
applicability, prerequisites, steps, failure modes and tool version. A later
study provides evidence for review. Lessons start tentative, retain revisions,
and can be confirmed, revised, rejected or left inconclusive. Tool-version and
applicability metadata must still be judged by the caller; they are not automatic
compatibility guarantees. Studio start/read/save/finish carries relevant art
lessons and canvas-specific practice forward.

`mind_context` creates a 30-day receipt for exactly the items it emitted.
`mind_recall_feedback` records useful/irrelevant/outdated judgments about served
items, or identifies a missing accessible observation. Repeated submissions are
idempotent and judgments can be retracted. Observation feedback only affects
similar-query ranking while its source fingerprint is current; aggregate influence
is capped at +/-0.3. It cannot change truth, identity or canonical content.
Other category judgments remain auditable without changing their ranking.

## Deployment and verification

- Migrations 0018 and 0019 applied remotely; earlier ledger/bond-history tables
  were already present and were not replayed.
- Pre-migration D1 recovery bookmark:
  `00003008-00000000-000050df-82019241b33623e3dd7b822aa705c57d`.
- Worker version: `00000000-0000-0000-0000-000000000000`.
- Full backend suite: 21 production-module SQLite tests passed; TypeScript check
  and Worker dry-run build passed. Use Node 22.13+ (validated with Node 24).
- Anam: 30 targeted tests passed using its production Python interpreter.
- Authenticated live route: all seven coordinated-memory/Sketchbook tools are
  deployed; `mind_scope` returned structured live data after deployment. Live context returned
  valid bounded JSON, a receipt, and no degraded categories. The same production
  context hook succeeded through Anam's configured native MCP bridge.
- Restarted Anam through its existing supervisor. Live `/health` confirmed
  `qualia_memory.status=ready`, `context_hook_loaded=true`, no missing tools,
  and a healthy scheduler heartbeat. A real working record for this implementation
  returned in a subsequent 11,715-character context packet.

This ships the memory workflow and its wake integration. It does not establish
that every future self-critique is correct or guarantee that a maker chooses to
practice on every wake. The useful next evidence is actual later work and its
review, not more invented memories or compulsory activity.
