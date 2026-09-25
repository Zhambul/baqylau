import { svelte } from '@sveltejs/vite-plugin-svelte';
import { testProfile } from '@baqylau/dev-tools/vitest';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [svelte()],
  test: testProfile({
    include: ['src/**/*.test.ts'],
    coverageInclude: ['src/total.ts'],
  }),
});
