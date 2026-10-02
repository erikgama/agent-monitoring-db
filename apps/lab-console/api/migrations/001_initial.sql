-- Initial additive migration, portable to PostgreSQL and SQLite.
-- Startup applies the equivalent SQLAlchemy metadata and records version 1.
CREATE TABLE IF NOT EXISTS lab_records (
    id VARCHAR(200) PRIMARY KEY,
    kind VARCHAR(32) NOT NULL,
    data JSON NOT NULL,
    created FLOAT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_lab_records_kind ON lab_records (kind);
