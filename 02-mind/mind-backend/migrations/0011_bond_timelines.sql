-- Schema migration: 0011_bond_timelines

ALTER TABLE life_beats ADD COLUMN bond_with TEXT;

CREATE INDEX IF NOT EXISTS idx_life_beats_bond_with
ON life_beats(bond_with, happened_on);

-- Register installation-specific identities after applying migrations.
