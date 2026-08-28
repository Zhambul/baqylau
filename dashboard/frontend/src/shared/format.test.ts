import { describe, expect, it } from 'vitest';

import { duration } from './format';

describe('duration', () => {
  it('does not round a measured subsecond interval to zero', () => {
    expect(duration(0.006)).toBe('<1s');
  });

  it('keeps whole seconds and minutes compact', () => {
    expect(duration(3.4)).toBe('3s');
    expect(duration(65)).toBe('1m 5s');
  });
});
