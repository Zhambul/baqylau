-- Schema 29, before raw extension observations were added.
CREATE TABLE extension_runtime_resolutions(
    runtime_revision TEXT PRIMARY KEY,
    resolution TEXT NOT NULL,
    FOREIGN KEY(runtime_revision) REFERENCES extension_runtime_revisions(runtime_revision)
);
