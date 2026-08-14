# Contract blocker resolution

Status: pending. This gate runs after the first vertical slice and before the
canonical read model. Implementors receive this task only; they are not told
its plan position or phase name.

## Objective

Make the binding resolutions in §43.4 executable and remove the contradictions
that would otherwise make later API, storage, and parity work ambiguous.

## Required work

- Read the complete v4 design and `rewrite/blockers.md`.
- Update the generated schema and migration artifacts so `operations` has
  nullable `native_operation_key` plus the partial unique index on
  `(agent_session_id, kind, native_operation_key)`. Add a migration and
  duplicate/concurrent-insert tests.
- Update framed-stream constants and fixtures to use a 104-byte header and
  reject 84-byte version-1 headers.
- Add `GET /api/v1/limits` and closed `LimitsDTO` to the endpoint manifest,
  OpenAPI output, storage matrix, server route, and contract tests. Remove any
  temporary contradiction exclusion.
- Regenerate the schema digest, endpoint inventory, README/rendered reports,
  and all generated verification artifacts from their authoritative sources.
- Publish the normative OpenCode plugin manifest and fixtures, including the
  observational/delegating classification and environment allowlist from
  §43.4. Keep unsupported delegating hooks fail-closed.
- Publish the five Claude observational families and their fixture mappings.
- Preserve peer-UID local authentication, no-spool behavior, and the explicit
  unresolved legacy parity fixture. Do not invent parity output.
- Synchronize `rewrite/index.md`, `rewrite/blockers.md`, and all task statuses.

## Tests and evidence

Write unit and end-to-end tests first for every changed contract. Run Black and
`black --check`, Ruff, `pylint --enable=all`, `mypy --strict`, the complete
Python suite, `rustfmt --check`, Clippy with `-D warnings`, and generated
artifact checks. Record migration/rollback evidence, exact commands, and any
remaining uncertainty in `rewrite/blockers.md`.

## Completion gate

No 84-byte stream header is accepted; database uniqueness holds under retry;
the endpoint set is exactly 114 including `/api/v1/limits`; generated schema
and documentation digests agree; OpenCode and Claude manifests have fixtures;
and all findings from two independent reviews plus manual Claude/Codex/OpenCode
tests are fixed or explicitly recorded as non-blocking.
