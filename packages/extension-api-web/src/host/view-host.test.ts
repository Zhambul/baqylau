import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ExtensionViewContext, ExtensionWebModule } from '../index.js';
import { BUNDLE, fixture, mountedView, SNAPSHOT } from '../test/fixtures.js';

afterEach(() => {
  document.body.replaceChildren();
});

describe('extension view host', () => {
  it('mounts only inside a package-owned shadow tree', async () => {
    const mounted = mountedView();
    const mount = vi
      .fn<ExtensionWebModule['mount']>()
      .mockImplementation((target, context) => {
        const paragraph = document.createElement('p');
        paragraph.textContent = context.viewId;
        target.append(paragraph);
        return mounted;
      });
    const test = fixture({ mount });
    await test.host.show(BUNDLE, SNAPSHOT);
    const root = test.target.firstElementChild;
    expect(root?.shadowRoot?.textContent).toBe(SNAPSHOT.viewId);
    expect(test.target.querySelector('p')).toBeNull();
    expect(mount).toHaveBeenCalledOnce();
    await test.host.clear();
    expect(test.target.childElementCount).toBe(0);
    expect(test.signals.every((signal) => signal.aborted)).toBe(true);
    expect(mounted.dispose).toHaveBeenCalledOnce();
    expect(test.errors).not.toHaveBeenCalled();
  });

  it('updates settings and theme through a new immutable context', async () => {
    const mounted = mountedView();
    const test = fixture({ mount: () => mounted });
    await test.host.show(BUNDLE, SNAPSHOT);
    const next = {
      ...SNAPSHOT,
      settingsRevision: 1,
      theme: { ...SNAPSHOT.theme, fontSize: '16px' },
    };
    await test.host.update(next);
    const received: ExtensionViewContext | undefined =
      mounted.update.mock.calls[0]?.[0];
    expect(received?.settingsRevision).toBe(1);
    expect(received?.theme.fontSize).toBe('16px');
    expect(received?.signal.aborted).toBe(false);
    await test.host.clear();
  });

  it('contains a mount failure without replacing the host page', async () => {
    const sibling = document.createElement('button');
    document.body.append(sibling);
    const test = fixture({
      mount: () => {
        throw new Error('private failure details');
      },
    });
    await test.host.show(BUNDLE, SNAPSHOT);
    expect(sibling.isConnected).toBe(true);
    expect(test.target.firstElementChild?.shadowRoot?.textContent).toBe(
      'Extension view is not available.',
    );
    expect(test.errors).toHaveBeenCalledOnce();
    await test.host.clear();
  });

  it('rejects a changed runtime during update', async () => {
    const mounted = mountedView();
    const test = fixture({ mount: () => mounted });
    await test.host.show(BUNDLE, SNAPSHOT);
    await test.host.update({ ...SNAPSHOT, runtimeRevision: 'runtime-2' });
    expect(mounted.update).not.toHaveBeenCalled();
    expect(mounted.dispose).toHaveBeenCalledOnce();
    expect(test.errors).toHaveBeenCalledOnce();
    await test.host.clear();
  });

  it('contains disposal failure and permits a replacement view', async () => {
    const mounted = mountedView();
    mounted.dispose.mockRejectedValue(new Error('dispose failed'));
    const test = fixture({ mount: () => mounted });
    await test.host.show(BUNDLE, SNAPSHOT);
    await test.host.clear();
    expect(test.errors).toHaveBeenCalledOnce();
    expect(test.target.childElementCount).toBe(0);
    test.importer.mockResolvedValue({ mount: () => mountedView() });
    await test.host.show(BUNDLE, SNAPSHOT);
    expect(test.target.childElementCount).toBe(1);
    await test.host.clear();
  });
});
