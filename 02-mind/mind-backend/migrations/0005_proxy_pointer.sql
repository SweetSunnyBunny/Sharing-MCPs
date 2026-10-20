-- Schema migration: 0005_proxy_pointer

CREATE TABLE IF NOT EXISTS document_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    node_id TEXT NOT NULL,
    parent_node_id INTEGER,
    title TEXT NOT NULL,
    breadcrumb TEXT,
    depth INTEGER DEFAULT 0,
    node_kind TEXT DEFAULT 'section',
    body TEXT,
    line_start INTEGER,
    line_end INTEGER,
    figures_json TEXT DEFAULT '[]',
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(document_id, node_id),
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
    FOREIGN KEY (parent_node_id) REFERENCES document_nodes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_document_nodes_document ON document_nodes(document_id, node_id);
CREATE INDEX IF NOT EXISTS idx_document_nodes_parent ON document_nodes(parent_node_id);
CREATE INDEX IF NOT EXISTS idx_document_nodes_kind ON document_nodes(node_kind);

-- ============ Chunk → Node Pointer ============
-- A chunk is no longer a standalone retrieval unit; it points back to the
-- full unbroken section body stored on document_nodes. Vector hits resolve
-- to the owning node and the synthesizer reads the whole node body.

ALTER TABLE document_chunks ADD COLUMN node_id INTEGER REFERENCES document_nodes(id);
CREATE INDEX IF NOT EXISTS idx_document_chunks_node ON document_chunks(node_id);

-- ============ Image → Node Membership ============
-- Figures live inside the section that contains them. At retrieval time,
-- selecting images for a response means scanning the figures_json of the
-- top-k retrieved nodes — no multimodal embedding needed.

ALTER TABLE images ADD COLUMN document_node_id INTEGER REFERENCES document_nodes(id);
CREATE INDEX IF NOT EXISTS idx_images_document_node ON images(document_node_id);
