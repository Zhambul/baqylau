import { coverageThresholds } from '@baqylau/dev-tools/coverage';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['src/**/*.test.ts'],
    environment: 'jsdom',
    coverage: {
      provider: 'v8',
      include: ['src/host/**/*.ts'],
      exclude: ['src/**/*.test.ts'],
      thresholds: coverageThresholds,
    },
  },
});
