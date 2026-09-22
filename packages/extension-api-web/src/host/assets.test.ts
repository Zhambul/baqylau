import { describe, expect, it } from 'vitest';

import { BUNDLE, DIGEST, ORIGIN } from '../test/fixtures.js';
import { assetUrls, loadStyle } from './assets.js';

describe('package asset paths', () => {
  it('accepts only URLs in the selected digest directory', () => {
    const style = `${ORIGIN}/extensions/test.views/${DIGEST}/extension.css`;
    expect(assetUrls({ ...BUNDLE, styleUrls: [style] }, ORIGIN)).toEqual({
      moduleUrl: BUNDLE.moduleUrl,
      styleUrls: [style],
    });
  });

  it.each([
    `${ORIGIN.replace('://', '://name:secret@')}/extensions/test.views/${DIGEST}/extension.js`,
    'https://example.com/extension.js',
    `${ORIGIN}/extensions/other/${DIGEST}/extension.js`,
    `${ORIGIN}/extensions/test.views/${'b'.repeat(64)}/extension.js`,
    `${ORIGIN}/extensions/test.views/${DIGEST}/%2e%2e/private.js`,
    `${ORIGIN}/extensions/test.views/${DIGEST}/file%252fprivate.js`,
    `${ORIGIN}/extensions/test.views/${DIGEST}/file%5cprivate.js`,
    `${BUNDLE.moduleUrl}?mutable=true`,
    `${BUNDLE.moduleUrl}#part`,
  ])('rejects an invalid package URL: %s', (moduleUrl) => {
    expect(() => assetUrls({ ...BUNDLE, moduleUrl }, ORIGIN)).toThrow();
  });

  it.each([
    'test_views',
    'Test.views',
    'test..views',
    '-test',
    'a'.repeat(101),
  ])('uses the Python SDK owner syntax: %s', (extensionId) => {
    const moduleUrl = `${ORIGIN}/extensions/${extensionId}/${DIGEST}/extension.js`;
    expect(() =>
      assetUrls({ ...BUNDLE, extensionId, moduleUrl }, ORIGIN),
    ).toThrow('Extension bundle identity is invalid.');
  });

  it('releases a pending stylesheet when its view is removed', async () => {
    const shadow = document.createElement('div').attachShadow({ mode: 'open' });
    const controller = new AbortController();
    const pending = loadStyle(`${ORIGIN}/style.css`, shadow, controller.signal);
    expect(shadow.querySelector('link')).not.toBeNull();
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    expect(shadow.childElementCount).toBe(0);
  });

  it('reports a stylesheet error and removes its failed element', async () => {
    const shadow = document.createElement('div').attachShadow({ mode: 'open' });
    const pending = loadStyle(
      `${ORIGIN}/style.css`,
      shadow,
      new AbortController().signal,
    );
    shadow.querySelector('link')?.dispatchEvent(new Event('error'));
    await expect(pending).rejects.toThrow('Extension style failed to load.');
    expect(shadow.childElementCount).toBe(0);
  });
});
