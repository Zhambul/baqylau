import type {
  ExtensionCatalog,
  ExtensionPackage,
  ExtensionRuntime,
} from '../api/extensions';

export type ExtensionRow = {
  readonly key: string;
  readonly owner: string | null;
  readonly name: string;
  readonly source: ExtensionPackage | null;
  readonly activeVersion: string | null;
  readonly activeDigest: string | null;
  readonly committedVersion: string | null;
  readonly actualState: string;
  readonly requested: boolean;
};

function row(
  owner: string | null,
  source: ExtensionPackage | null,
  runtime: ExtensionRuntime,
): ExtensionRow {
  const active = runtime.directory?.entries.find(
    (entry) => entry.extension_info.extension_id === owner,
  );
  const committed = runtime.committed_packages.find(
    (entry) => entry.extension_info.extension_id === owner,
  );
  const requested =
    runtime.requested.find((entry) => entry.extension_id === owner)?.enabled ??
    false;
  return {
    key: source?.source_path ?? `retained:${owner ?? ''}`,
    owner,
    name: source?.name ?? owner ?? 'Invalid package',
    source,
    activeVersion:
      active?.state === 'enabled'
        ? active.extension_info.package_version
        : null,
    activeDigest:
      active?.state === 'enabled' ? active.extension_info.package_digest : null,
    committedVersion: committed?.extension_info.package_version ?? null,
    actualState:
      runtime.directory === null
        ? 'unavailable'
        : (active?.state ?? 'disabled'),
    requested,
  };
}

export function extensionRows(
  catalog: ExtensionCatalog,
  runtime: ExtensionRuntime,
): readonly ExtensionRow[] {
  const rows = catalog.entries.map((entry) =>
    row(entry.extension_id, entry, runtime),
  );
  const listed = new Set(catalog.entries.map((entry) => entry.extension_id));
  const retained = new Set([
    ...runtime.committed_packages.map(
      (entry) => entry.extension_info.extension_id,
    ),
    ...runtime.requested.map((entry) => entry.extension_id),
    ...(runtime.directory?.entries.map(
      (entry) => entry.extension_info.extension_id,
    ) ?? []),
  ]);
  for (const owner of retained) {
    if (!listed.has(owner)) rows.push(row(owner, null, runtime));
  }
  return rows.sort((left, right) => left.key.localeCompare(right.key));
}
