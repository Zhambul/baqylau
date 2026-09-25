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

it('freezes the feed entry subject of a replacement view', () => {
  const subject = { entryId: 'entry-1', kind: 'shell_started' };
  const snapshot = immutableSnapshot({ ...SNAPSHOT, subject });
  expect(snapshot.subject).toEqual(subject);
  expect(snapshot.subject).not.toBe(subject);
  expect(Reflect.set(snapshot.subject ?? {}, 'entryId', 'entry-2')).toBe(false);
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
    readSettings() {
      return Promise.resolve({
        settingsRevision: 2,
        override: null,
        effective: {
          schema_ref: {
            owner: 'test.view',
            name: 'settings',
            version: 1,
            digest: 'a'.repeat(64),
          },
          json_text: '{}',
        },
      });
    },
    saveSettings(_document: unknown, expected: number) {
      this.calls += expected;
      return Promise.resolve({ operationId: 'operation-1' });
    },
    query(queryId: string) {
      this.calls += queryId.length;
      return Promise.reject(new Error('No queries.'));
    },
    watchChanges(listener: () => void) {
      listener();
      return () => undefined;
    },
    runCommand(commandId: string) {
      this.calls += commandId.length;
      return Promise.resolve({
        jobId: 'job-1',
        state: 'accepted' as const,
        document: null,
        diagnostic: null,
      });
    },
    readJob(jobId: string) {
      return Promise.resolve({
        jobId,
        state: 'running' as const,
        document: null,
        diagnostic: null,
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
  expect((await context.api.readSettings()).settingsRevision).toBe(2);
  await context.api.saveSettings(null, 2);
  expect(client.calls).toBe(3);
  await expect(context.api.query('q', '{}')).rejects.toThrow('No queries.');
  expect(client.calls).toBe(4);
  let changed = 0;
  context.api.watchChanges(() => {
    changed += 1;
  })();
  expect(changed).toBe(1);
  expect((await context.api.runCommand('c', 'key-1', '{}', null)).jobId).toBe(
    'job-1',
  );
  expect(client.calls).toBe(5);
  expect((await context.api.readJob('job-1')).state).toBe('running');
  expect(Object.isFrozen(context.api)).toBe(true);
});
