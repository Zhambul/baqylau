import { testProfile } from '@baqylau/dev-tools/vitest';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: testProfile({
    include: ['src/**/*.test.ts'],
    coverageInclude: ['src/host/**/*.ts'],
    coverageExclude: ['src/**/*.test.ts'],
  }),
});
