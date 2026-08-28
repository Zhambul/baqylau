import { describe, expect, it } from 'vitest';

import { autoGrow } from './auto-grow';

describe('autoGrow', () => {
  it('keeps the full textarea content visible', () => {
    const textarea = document.createElement('textarea');
    let scrollHeight = 48;
    Object.defineProperty(textarea, 'scrollHeight', {
      get: () => scrollHeight,
    });

    const action = autoGrow(textarea, 'first line');
    expect(textarea.style.height).toBe('48px');

    scrollHeight = 144;
    textarea.dispatchEvent(new Event('input'));
    expect(textarea.style.height).toBe('144px');

    scrollHeight = 24;
    action?.update?.('short');
    expect(textarea.style.height).toBe('24px');
    action?.destroy?.();
  });
});
