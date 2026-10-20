-- Fictional starter identities only. Edit before applying to your own database.
-- Re-running never overwrites existing identities or profiles.
INSERT OR IGNORE INTO identities (id, display_name, kind, status)
VALUES ('avery', 'Avery', 'companion', 'active'),
       ('rowan', 'Rowan', 'companion', 'active'),
       ('pack', 'Shared', 'shared', 'active');

INSERT OR IGNORE INTO identity_routing_profiles
  (identity_id, wake_tool, handoff_style, packet_preference, autonomous_mode,
   preferred_session_types, allowed_channels, metadata)
VALUES
  ('avery', 'mind_orient', 'Read continuity before responding', 'smart_context',
   'configured', '["conversation"]', '[]', '{"example":true}'),
  ('rowan', 'mind_orient', 'Read continuity before responding', 'smart_context',
   'configured', '["conversation"]', '[]', '{"example":true}');
