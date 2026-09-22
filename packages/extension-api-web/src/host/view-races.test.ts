import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ExtensionWebModule, MountedExtensionView } from '../index.js';
import {
  BUNDLE,
  deferred,
  fixture,
  mountedView,
  SNAPSHOT,
} from '../test/fixtures.js';

afterEach(() => {
  document.body.replaceChildren();
});

describe('view lifecycle races', () => {
  it('reports a failed late disposal without restoring a removed view', async () => {
    const mounting = deferred<MountedExtensionView>();
    const entered = deferred<undefined>();
    const mounted = mountedView();
    mounted.dispose.mockRejectedValue(new Error('late cleanup failed'));
    const test = fixture({
      mount: () => {
        entered.resolve(undefined);
        return mounting.promise;
      },
    });
    const showing = test.host.show(BUNDLE, SNAPSHOT);
    await entered.promise;
    const clearing = test.host.clear();
    mounting.resolve(mounted);
    await Promise.all([showing, clearing]);
    expect(test.errors).toHaveBeenCalledOnce();
    expect(test.target.childElementCount).toBe(0);
    expect(mounted.dispose).toHaveBeenCalledOnce();
  });

  it('does not mount when disable occurs during import', async () => {
    const loading = deferred<unknown>();
    const mount = vi
      .fn<ExtensionWebModule['mount']>()
      .mockReturnValue(mountedView());
    const test = fixture({ mount });
    test.importer.mockReturnValue(loading.promise);
    const showing = test.host.show(BUNDLE, SNAPSHOT);
    await vi.waitFor(() => {
      expect(test.importer).toHaveBeenCalledOnce();
    });
    const clearing = test.host.clear();
    expect(test.target.childElementCount).toBe(0);
    loading.resolve({ mount });
    await Promise.all([showing, clearing]);
    expect(mount).not.toHaveBeenCalled();
  });

  it('disposes a late mount once and keeps the new view', async () => {
    const mounting = deferred<MountedExtensionView>();
    const entered = deferred<undefined>();
    const old = mountedView();
    const test = fixture({
      mount: () => {
        entered.resolve(undefined);
        return mounting.promise;
      },
    });
    const showing = test.host.show(BUNDLE, SNAPSHOT);
    await entered.promise;
    const replacement = mountedView();
    test.importer.mockResolvedValue({ mount: () => replacement });
    await test.host.show(BUNDLE, { ...SNAPSHOT, runtimeRevision: 'runtime-2' });
    expect(test.signals[0]?.aborted).toBe(true);
    mounting.resolve(old);
    await showing;
    expect(old.dispose).toHaveBeenCalledOnce();
    expect(replacement.dispose).not.toHaveBeenCalled();
    expect(test.target.childElementCount).toBe(1);
    await test.host.clear();
  });

  it('aborts immediately and serializes disposal after an active update', async () => {
    const updating = deferred<undefined>();
    const entered = deferred<undefined>();
    const mounted = mountedView();
    mounted.update.mockImplementation(() => {
      entered.resolve(undefined);
      return updating.promise;
    });
    const test = fixture({ mount: () => mounted });
    await test.host.show(BUNDLE, SNAPSHOT);
    const pending = test.host.update({ ...SNAPSHOT, settingsRevision: 1 });
    await entered.promise;
    const clearing = test.host.clear();
    expect(test.signals.every((signal) => signal.aborted)).toBe(true);
    expect(test.target.childElementCount).toBe(0);
    expect(mounted.dispose).not.toHaveBeenCalled();
    updating.resolve(undefined);
    await Promise.all([pending, clearing]);
    expect(mounted.dispose).toHaveBeenCalledOnce();
  });
});
