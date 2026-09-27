-- Schema migration: 0019_coordinated_memory
CREATE TABLE IF NOT EXISTS mind_focus (
 id TEXT PRIMARY KEY, identity_id TEXT NOT NULL REFERENCES identities(id),
 session_key TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','parked','closed')),
 document TEXT NOT NULL DEFAULT '{}', expires_at TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(identity_id,session_key)
);
CREATE TABLE IF NOT EXISTS mind_focus_history (
 id INTEGER PRIMARY KEY AUTOINCREMENT, focus_id TEXT NOT NULL REFERENCES mind_focus(id),
 identity_id TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL,
 document TEXT NOT NULL, expires_at TEXT, recorded_at TEXT NOT NULL,
 UNIQUE(focus_id,revision)
);
CREATE TABLE IF NOT EXISTS memory_provenance (
 observation_id INTEGER PRIMARY KEY REFERENCES observations(id), identity_id TEXT NOT NULL,
 revision INTEGER NOT NULL DEFAULT 1, epistemic_kind TEXT NOT NULL DEFAULT 'unclassified',
 event_at TEXT, verified_at TEXT, valid_until TEXT, applicability TEXT,
 needs_review INTEGER NOT NULL DEFAULT 0, reason TEXT, last_operation TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_provenance_history (
 id INTEGER PRIMARY KEY AUTOINCREMENT, identity_id TEXT NOT NULL,
 observation_id INTEGER NOT NULL, revision INTEGER NOT NULL,
 snapshot TEXT NOT NULL, source_ids TEXT NOT NULL, recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_dependencies (
 observation_id INTEGER NOT NULL REFERENCES observations(id),
 source_id INTEGER NOT NULL REFERENCES observations(id),
 PRIMARY KEY(observation_id,source_id), CHECK(observation_id != source_id)
);
CREATE INDEX IF NOT EXISTS idx_memory_dependency_source ON memory_dependencies(source_id);
CREATE TABLE IF NOT EXISTS memory_disagreements (
 observation_id INTEGER NOT NULL REFERENCES observations(id), other_id INTEGER NOT NULL REFERENCES observations(id),
 reason TEXT NOT NULL, PRIMARY KEY(observation_id,other_id), CHECK(observation_id != other_id)
);
CREATE TABLE IF NOT EXISTS memory_invalidations (
 id INTEGER PRIMARY KEY AUTOINCREMENT, identity_id TEXT,
 source_id INTEGER NOT NULL, changed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_invalidations_identity ON memory_invalidations(identity_id,changed_at);
CREATE TABLE IF NOT EXISTS recall_receipts (
 id TEXT PRIMARY KEY, identity_id TEXT NOT NULL, session_key TEXT,
 query TEXT NOT NULL, sources TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recall_feedback (
 id TEXT PRIMARY KEY, identity_id TEXT NOT NULL, receipt_id TEXT NOT NULL REFERENCES recall_receipts(id),
 source_ref TEXT NOT NULL, judgment TEXT NOT NULL CHECK(judgment IN ('useful','irrelevant','outdated','missing')),
 reason TEXT NOT NULL, source_fingerprint TEXT, created_at TEXT NOT NULL, retracted_at TEXT,
 UNIQUE(receipt_id,source_ref)
);
CREATE INDEX IF NOT EXISTS idx_recall_feedback_identity ON recall_feedback(identity_id,source_ref);

-- Validate again inside the transaction: concurrent annotators cannot form a cycle.
CREATE TRIGGER IF NOT EXISTS mind_dependency_cycle BEFORE INSERT ON memory_dependencies
WHEN EXISTS (WITH RECURSIVE ancestors(id) AS (
 SELECT source_id FROM memory_dependencies WHERE observation_id=NEW.source_id
 UNION SELECT d.source_id FROM memory_dependencies d JOIN ancestors a ON d.observation_id=a.id
) SELECT 1 FROM ancestors WHERE id=NEW.observation_id)
BEGIN SELECT RAISE(ABORT,'Evidence dependencies cannot contain a cycle'); END;

CREATE TRIGGER IF NOT EXISTS mind_provenance_history BEFORE UPDATE ON memory_provenance
BEGIN
 INSERT INTO memory_provenance_history(identity_id,observation_id,revision,snapshot,source_ids,recorded_at)
 VALUES(OLD.identity_id,OLD.observation_id,OLD.revision,
 json_object('epistemic_kind',OLD.epistemic_kind,'event_at',OLD.event_at,'verified_at',OLD.verified_at,
 'valid_until',OLD.valid_until,'applicability',OLD.applicability,'needs_review',OLD.needs_review,'reason',OLD.reason),
 (SELECT json_group_array(source_id) FROM memory_dependencies WHERE observation_id=OLD.observation_id),
 strftime('%Y-%m-%dT%H:%M:%fZ','now'));
END;

-- Canonical edits, archiving, superseding and rollback all travel this path.
-- No inferred correction overwrites a claim. Descendants become reviewable.
CREATE TRIGGER IF NOT EXISTS mind_observation_changed
AFTER UPDATE OF content, archived_at, superseded_by ON observations
WHEN OLD.content IS NOT NEW.content OR OLD.archived_at IS NOT NEW.archived_at OR OLD.superseded_by IS NOT NEW.superseded_by
BEGIN
 INSERT INTO memory_invalidations(identity_id,source_id,changed_at)
 VALUES (NEW.identity_id,NEW.id,strftime('%Y-%m-%dT%H:%M:%fZ','now'));
 UPDATE memory_provenance SET needs_review=1, revision=revision+1,
 reason='Source observation changed; verify against the current evidence',
 updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
 WHERE observation_id IN (
   WITH RECURSIVE affected(id) AS (
     SELECT NEW.id UNION SELECT d.observation_id FROM memory_dependencies d JOIN affected a ON d.source_id=a.id
   ) SELECT id FROM affected
 );
 UPDATE art_lessons SET metadata=json_set(COALESCE(metadata,'{}'),'$.needs_review',1),
 updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
 WHERE study_id IN (SELECT id FROM art_studies WHERE observation_id IN (
   WITH RECURSIVE affected(id) AS (
     SELECT NEW.id UNION SELECT d.observation_id FROM memory_dependencies d JOIN affected a ON d.source_id=a.id
   ) SELECT id FROM affected
 )) OR id IN (SELECT e.lesson_id FROM art_experiments e JOIN art_studies s ON s.id=e.evidence_study_id WHERE s.observation_id=NEW.id);
END;
