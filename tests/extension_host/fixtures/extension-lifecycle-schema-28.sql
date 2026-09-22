-- Schema 28, before candidate migration resolutions were added.
CREATE TABLE extension_runtime_revisions(
    runtime_revision TEXT PRIMARY KEY,
    selection TEXT NOT NULL,
    committed_at REAL
);
CREATE TABLE extension_lifecycle_operations(
    operation_id TEXT PRIMARY KEY,
    manager_id TEXT NOT NULL,
    runtime_revision TEXT NOT NULL UNIQUE,
    accepted_revision INTEGER NOT NULL CHECK(accepted_revision > 0),
    status TEXT NOT NULL CHECK(status IN ('preparing', 'succeeded', 'failed', 'interrupted')),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL CHECK(updated_at >= created_at),
    proposal TEXT NOT NULL,
    failure TEXT,
    CHECK((status IN ('failed', 'interrupted')) = (failure IS NOT NULL)),
    FOREIGN KEY(runtime_revision) REFERENCES extension_runtime_revisions(runtime_revision)
);
CREATE TABLE extension_lifecycle_head(
    id INTEGER PRIMARY KEY CHECK(id = 1),
    revision INTEGER NOT NULL CHECK(revision >= 0),
    manager_id TEXT NOT NULL,
    committed_runtime TEXT,
    pending_operation TEXT,
    FOREIGN KEY(committed_runtime) REFERENCES extension_runtime_revisions(runtime_revision),
    FOREIGN KEY(pending_operation) REFERENCES extension_lifecycle_operations(operation_id)
);
CREATE TABLE extension_requests(
    extension_id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
    package_digest TEXT,
    CHECK(enabled = 0 OR package_digest IS NOT NULL),
    FOREIGN KEY(package_digest) REFERENCES extension_package_manifests(package_digest)
);
CREATE TABLE extension_settings(
    extension_id TEXT PRIMARY KEY,
    overrides TEXT NOT NULL
);
