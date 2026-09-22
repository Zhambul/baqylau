-- The catalog DDL from the schema-27 worktree before lifecycle storage.
-- Append to main-schema-26.sql to create an independent previous database.
CREATE TABLE IF NOT EXISTS extension_catalog_head(
    id INTEGER PRIMARY KEY CHECK(id = 1),
    revision INTEGER NOT NULL CHECK(revision >= 0)
);
CREATE TABLE IF NOT EXISTS extension_packages(
    source_path TEXT PRIMARY KEY,
    resolved_path TEXT,
    package_digest TEXT,
    extension_id TEXT,
    manifest TEXT,
    issue_code TEXT,
    issue_detail TEXT,
    CHECK((issue_code IS NULL) = (issue_detail IS NULL)),
    CHECK(issue_code IS NOT NULL OR (
        resolved_path IS NOT NULL AND package_digest IS NOT NULL AND manifest IS NOT NULL
    ))
);
CREATE TABLE IF NOT EXISTS extension_catalog_errors(
    root_path TEXT PRIMARY KEY,
    issue_code TEXT NOT NULL,
    issue_detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS extension_package_manifests(
    package_digest TEXT PRIMARY KEY,
    manifest TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS index_extension_packages_owner ON extension_packages(extension_id);
