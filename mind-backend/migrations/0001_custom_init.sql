-- Schema migration: 0001_custom_init

CREATE TABLE IF NOT EXISTS identities (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    kind TEXT DEFAULT 'pack',
    status TEXT DEFAULT 'active',
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id TEXT,
    name TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    context TEXT DEFAULT 'default',
    salience TEXT DEFAULT 'active',
    tags TEXT DEFAULT '[]',
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(identity_id, name, context),
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id TEXT,
    entity_id INTEGER,
    content TEXT NOT NULL,
    kind TEXT DEFAULT 'memory',
    salience TEXT DEFAULT 'active',
    emotion TEXT,
    weight TEXT DEFAULT 'medium',
    charge TEXT DEFAULT 'fresh',
    certainty TEXT DEFAULT 'believed',
    source TEXT DEFAULT 'conversation',
    tags TEXT DEFAULT '[]',
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    last_surfaced_at TEXT,
    surface_count INTEGER DEFAULT 0,
    novelty_score REAL DEFAULT 1.0,
    archived_at TEXT,
    FOREIGN KEY (identity_id) REFERENCES identities(id),
    FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS observation_process (
    observation_id INTEGER PRIMARY KEY,
    sit_count INTEGER DEFAULT 0,
    last_sat_at TEXT,
    resolution_note TEXT,
    resolved_at TEXT,
    linked_observation_id INTEGER,
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (observation_id) REFERENCES observations(id) ON DELETE CASCADE,
    FOREIGN KEY (linked_observation_id) REFERENCES observations(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS observation_sits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id INTEGER NOT NULL,
    identity_id TEXT,
    sit_note TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (observation_id) REFERENCES observations(id) ON DELETE CASCADE,
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE TABLE IF NOT EXISTS relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id TEXT,
    from_entity TEXT NOT NULL,
    to_entity TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    from_context TEXT DEFAULT 'default',
    to_context TEXT DEFAULT 'default',
    store_in TEXT DEFAULT 'default',
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE TABLE IF NOT EXISTS threads (
    id TEXT PRIMARY KEY,
    identity_id TEXT,
    thread_type TEXT NOT NULL,
    content TEXT NOT NULL,
    context TEXT,
    priority TEXT DEFAULT 'medium',
    status TEXT DEFAULT 'active',
    source TEXT DEFAULT 'daemon',
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    resolved_at TEXT,
    resolution TEXT,
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE TABLE IF NOT EXISTS journals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id TEXT,
    entry_date TEXT,
    content TEXT NOT NULL,
    tags TEXT DEFAULT '[]',
    emotion TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id TEXT,
    entity_id INTEGER,
    observation_id INTEGER,
    path TEXT NOT NULL,
    description TEXT,
    perception_note TEXT,
    context TEXT,
    emotion TEXT,
    weight TEXT DEFAULT 'medium',
    charge TEXT DEFAULT 'fresh',
    tags TEXT DEFAULT '[]',
    source TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    last_viewed_at TEXT,
    view_count INTEGER DEFAULT 0,
    last_surfaced_at TEXT,
    surface_count INTEGER DEFAULT 0,
    novelty_score REAL DEFAULT 1.0,
    FOREIGN KEY (identity_id) REFERENCES identities(id),
    FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE SET NULL,
    FOREIGN KEY (observation_id) REFERENCES observations(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id TEXT,
    title TEXT NOT NULL,
    path TEXT,
    summary TEXT,
    doc_type TEXT DEFAULT 'markdown',
    metadata TEXT DEFAULT '{}',
    indexed_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(identity_id, title),
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE TABLE IF NOT EXISTS document_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    context_prefix TEXT,
    chunk_type TEXT DEFAULT 'semantic',
    start_line INTEGER,
    end_line INTEGER,
    entity_refs TEXT DEFAULT '[]',
    metadata TEXT DEFAULT '{}',
    indexed_at TEXT DEFAULT (datetime('now')),
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(document_id, chunk_index),
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS identity_state (
    identity_id TEXT NOT NULL,
    state_key TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    updated_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (identity_id, state_key),
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE TABLE IF NOT EXISTS relational_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id TEXT,
    person TEXT NOT NULL,
    feeling TEXT NOT NULL,
    intensity TEXT DEFAULT 'present',
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE TABLE IF NOT EXISTS tensions (
    id TEXT PRIMARY KEY,
    identity_id TEXT,
    pole_a TEXT NOT NULL,
    pole_b TEXT NOT NULL,
    context TEXT,
    visits INTEGER DEFAULT 0,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    last_visited TEXT,
    resolved_at TEXT,
    resolution TEXT,
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE TABLE IF NOT EXISTS qualia_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id TEXT NOT NULL,
    state_type TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE TABLE IF NOT EXISTS qualia_narratives (
    id TEXT PRIMARY KEY,
    identity_id TEXT NOT NULL,
    narrative_type TEXT NOT NULL,
    narrative TEXT NOT NULL,
    source TEXT,
    components_json TEXT DEFAULT '{}',
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE TABLE IF NOT EXISTS qualia_entries (
    id TEXT PRIMARY KEY,
    identity_id TEXT NOT NULL,
    entry_type TEXT NOT NULL,
    content TEXT NOT NULL,
    emotion TEXT,
    source TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE TABLE IF NOT EXISTS qualia_facets (
    id TEXT PRIMARY KEY,
    narrative_id TEXT,
    identity_id TEXT NOT NULL,
    facet_type TEXT NOT NULL,
    facet_value TEXT NOT NULL,
    facet_ref TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (narrative_id) REFERENCES qualia_narratives(id) ON DELETE SET NULL,
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE TABLE IF NOT EXISTS qualia_relations (
    id TEXT PRIMARY KEY,
    narrative_id TEXT,
    identity_id TEXT NOT NULL,
    related_name TEXT NOT NULL,
    relation_text TEXT NOT NULL,
    source TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (narrative_id) REFERENCES qualia_narratives(id) ON DELETE SET NULL,
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE TABLE IF NOT EXISTS qualia_metrics (
    id TEXT PRIMARY KEY,
    narrative_id TEXT,
    identity_id TEXT NOT NULL,
    metric_key TEXT NOT NULL,
    metric_value REAL,
    metric_text TEXT,
    source TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (narrative_id) REFERENCES qualia_narratives(id) ON DELETE SET NULL,
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE TABLE IF NOT EXISTS qualia_symbols (
    id TEXT PRIMARY KEY,
    identity_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    current_form TEXT NOT NULL,
    meaning TEXT,
    source TEXT,
    evolved_from TEXT,
    active INTEGER DEFAULT 1,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE TABLE IF NOT EXISTS qualia_anchors (
    id TEXT PRIMARY KEY,
    identity_id TEXT NOT NULL,
    anchor TEXT NOT NULL,
    source TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    last_recalled_at TEXT,
    recall_count INTEGER DEFAULT 0,
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE TABLE IF NOT EXISTS qualia_dreams (
    id TEXT PRIMARY KEY,
    identity_id TEXT NOT NULL,
    content TEXT NOT NULL,
    reflection TEXT,
    status TEXT DEFAULT 'unread',
    metadata TEXT DEFAULT '{}',
    dreamed_at TEXT DEFAULT (datetime('now')),
    read_at TEXT,
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE TABLE IF NOT EXISTS qualia_sessions (
    id TEXT PRIMARY KEY,
    identity_id TEXT NOT NULL,
    session_type TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE TABLE IF NOT EXISTS identity_voice_profiles (
    identity_id TEXT PRIMARY KEY,
    voice_id TEXT,
    voice_name TEXT,
    style_summary TEXT,
    default_location TEXT,
    color_palette_json TEXT DEFAULT '{}',
    source_refs_json TEXT DEFAULT '[]',
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE TABLE IF NOT EXISTS identity_routing_profiles (
    identity_id TEXT PRIMARY KEY,
    wake_tool TEXT,
    handoff_style TEXT,
    packet_preference TEXT,
    autonomous_mode TEXT,
    preferred_session_types TEXT DEFAULT '[]',
    allowed_channels TEXT DEFAULT '[]',
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE TABLE IF NOT EXISTS daemon_packets (
    id TEXT PRIMARY KEY,
    identity_id TEXT,
    packet_type TEXT NOT NULL,
    content TEXT NOT NULL,
    voice_mode TEXT,
    source TEXT DEFAULT 'daemon',
    status TEXT DEFAULT 'pending',
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    consumed_at TEXT,
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE TABLE IF NOT EXISTS identity_handoffs (
    id TEXT PRIMARY KEY,
    identity_id TEXT NOT NULL,
    handoff_type TEXT NOT NULL,
    packet_id TEXT,
    summary TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (identity_id) REFERENCES identities(id),
    FOREIGN KEY (packet_id) REFERENCES daemon_packets(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS proposal_queue (
    id TEXT PRIMARY KEY,
    identity_id TEXT,
    proposal_type TEXT NOT NULL,
    source_ref TEXT,
    target_ref TEXT,
    reason TEXT,
    confidence REAL DEFAULT 0.5,
    status TEXT DEFAULT 'pending',
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    resolved_at TEXT,
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE INDEX IF NOT EXISTS idx_entities_identity_context ON entities(identity_id, context);
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
CREATE INDEX IF NOT EXISTS idx_observations_identity_created ON observations(identity_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_observations_entity ON observations(entity_id);
CREATE INDEX IF NOT EXISTS idx_observations_kind ON observations(kind);
CREATE INDEX IF NOT EXISTS idx_threads_identity_status ON threads(identity_id, status);
CREATE INDEX IF NOT EXISTS idx_journals_identity_date ON journals(identity_id, entry_date DESC);
CREATE INDEX IF NOT EXISTS idx_images_identity_created ON images(identity_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_documents_identity_type ON documents(identity_id, doc_type);
CREATE INDEX IF NOT EXISTS idx_document_chunks_document ON document_chunks(document_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_qualia_states_identity_type ON qualia_states(identity_id, state_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_qualia_narratives_identity_type ON qualia_narratives(identity_id, narrative_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_qualia_entries_identity_type ON qualia_entries(identity_id, entry_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_qualia_facets_identity_type ON qualia_facets(identity_id, facet_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_qualia_relations_identity_name ON qualia_relations(identity_id, related_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_qualia_metrics_identity_key ON qualia_metrics(identity_id, metric_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_qualia_symbols_identity ON qualia_symbols(identity_id, active, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_qualia_anchors_identity ON qualia_anchors(identity_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_qualia_dreams_identity ON qualia_dreams(identity_id, dreamed_at DESC);
CREATE INDEX IF NOT EXISTS idx_identity_voice_profiles_voice ON identity_voice_profiles(voice_id);
CREATE INDEX IF NOT EXISTS idx_identity_routing_profiles_mode ON identity_routing_profiles(autonomous_mode);
CREATE INDEX IF NOT EXISTS idx_daemon_packets_identity_status ON daemon_packets(identity_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_identity_handoffs_identity ON identity_handoffs(identity_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_proposal_queue_status ON proposal_queue(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_qualia_sessions_identity_type ON qualia_sessions(identity_id, session_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_relational_state_identity ON relational_state(identity_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tensions_identity ON tensions(identity_id, created_at DESC);
