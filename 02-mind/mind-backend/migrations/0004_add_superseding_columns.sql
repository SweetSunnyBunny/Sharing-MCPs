-- Schema migration: 0004_add_superseding_columns

ALTER TABLE observations ADD COLUMN superseded_by INTEGER REFERENCES observations(id);
ALTER TABLE observations ADD COLUMN supersedes INTEGER REFERENCES observations(id);
