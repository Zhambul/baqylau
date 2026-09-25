import { expect, it } from 'vitest';

import { total } from './total';

it('adds the values', () => {
  expect(total([1, 2, 3])).toBe(6);
});
