import { expect, it } from 'vitest';

import { SNAPSHOT } from '../test/fixtures.js';
import { immutableSnapshot, viewContext } from './context.js';

it('detaches and freezes public data before passing it to a module', () => {
  const snapshot = immutableSnapshot(SNAPSHOT);
  expect(snapshot.scope).not.toBe(SNAPSHOT.scope);
  expect(snapshot.theme).not.toBe(SNAPSHOT.theme);
  expect(Reflect.set(snapshot.scope, 'workspace_id', 'another')).toBe(false);
  expect(Reflect.set(snapshot.theme, 'fontSize', '99px')).toBe(false);
  expect(SNAPSHOT.theme.fontSize).toBe('14px');
});

it('passes an API facade instead of the host client instance', async () => {
  const client = {
    calls: 0,
    listExtensions() {
      this.calls += 1;
      return Promise.resolve({
        catalog_revision: 1,
        runtime_revision: 'runtime-1',
        entries: [],
      });
    },
  };
  const context = viewContext(
    SNAPSHOT,
    new AbortController().signal,
    () => client,
  );
  expect(context.api).not.toBe(client);
  expect('calls' in context.api).toBe(false);
  await context.api.listExtensions({ active_only: true });
  expect(client.calls).toBe(1);
  expect(Object.isFrozen(context.api)).toBe(true);
});
