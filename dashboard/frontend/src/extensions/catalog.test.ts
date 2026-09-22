import { describe, expect, it } from 'vitest';

import {
  enabledRuntime,
  extensionCatalog,
  extensionRuntime,
} from '../test/extension-fixture';
import { extensionRows } from './catalog';

describe('extension catalog presentation', () => {
  it('does not report new source bytes as the active version', () => {
    const catalog = extensionCatalog();
    catalog.entries = catalog.entries.map((entry) => ({
      ...entry,
      package_version: '2.0.0',
    }));
    expect(extensionRows(catalog, enabledRuntime())[0]).toMatchObject({
      activeVersion: '1.0.0',
      committedVersion: '1.0.0',
      actualState: 'enabled',
      requested: true,
      source: { package_version: '2.0.0' },
    });
  });

  it('retains active and requested owners after source removal', () => {
    const runtime = enabledRuntime();
    runtime.requested.push({ extension_id: 'test.missing', enabled: true });
    const rows = extensionRows(
      { revision: 4, entries: [], root_issues: [] },
      runtime,
    );
    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatchObject({
      owner: 'test.base',
      source: null,
      actualState: 'enabled',
    });
    expect(rows[1]).toMatchObject({
      owner: 'test.missing',
      source: null,
      requested: true,
      activeVersion: null,
    });
  });

  it('separates unavailable runtime from disabled state', () => {
    const runtime = extensionRuntime();
    expect(extensionRows(extensionCatalog(), runtime)[0]?.actualState).toBe(
      'disabled',
    );
    runtime.directory = null;
    expect(extensionRows(extensionCatalog(), runtime)[0]?.actualState).toBe(
      'unavailable',
    );
  });

  it('keeps invalid sources visible', () => {
    const catalog = extensionCatalog();
    catalog.entries.push({
      ...catalog.entries[0],
      extension_id: null,
      name: null,
      source_path: '/private/packages/broken',
      resolved_path: null,
      package_version: null,
      package_digest: null,
      capabilities: [],
      issue: { code: 'invalid_manifest', detail: 'Invalid declaration' },
    });
    expect(extensionRows(catalog, extensionRuntime())[1]).toMatchObject({
      owner: null,
      name: 'Invalid package',
      source: { issue: { code: 'invalid_manifest' } },
    });
  });
});
