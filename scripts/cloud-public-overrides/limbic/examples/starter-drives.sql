-- Fictional demonstration, not a psychological profile or personal history.
-- Edit the labels and values for your installation. Re-running never overwrites.
INSERT OR IGNORE INTO identities (id, display_name)
VALUES ('avery', 'Avery'), ('rowan', 'Rowan');

INSERT OR IGNORE INTO drives
  (identity_id, drive, panksepp_system, display_name, baseline, floor, ceiling,
   half_life_hours, env_sensitivity, body_feel, action_bias, regulation_note)
SELECT i.id, d.drive, d.drive, d.label, d.baseline, 0.0, 1.0, 8.0, '{}',
       '[{"min":0,"label":"quiet"},{"min":0.5,"label":"noticeable"},{"min":0.8,"label":"strong"}]',
       '[{"min":0,"tendencies":[]},{"min":0.5,"tendencies":["consider a suitable activity"]}]',
       'Advisory only; values, judgment, consent, and boundaries govern behavior.'
FROM identities AS i
CROSS JOIN (
  SELECT 'seeking' AS drive, 'Curiosity' AS label, 0.2 AS baseline
  UNION ALL SELECT 'care', 'Care', 0.25
  UNION ALL SELECT 'play', 'Play', 0.2
  UNION ALL SELECT 'fear', 'Caution', 0.1
  UNION ALL SELECT 'panic', 'Connection need', 0.1
  UNION ALL SELECT 'guard', 'Protection', 0.1
) AS d
WHERE i.id IN ('avery', 'rowan');

INSERT OR IGNORE INTO recipes
  (recipe, display_name, feel, conditions, tints, blend_note)
VALUES
  ('curious-example', 'Curious', 'Interest is active while caution remains manageable.',
   '{"drives":[{"drive":"seeking","test":"min","value":0.35},{"drive":"fear","test":"max","value":0.5}]}',
   '{}', 'Fictional example of a multi-drive recipe.');
