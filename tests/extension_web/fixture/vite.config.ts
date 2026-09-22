import { fileURLToPath } from 'node:url';

import { svelte } from '@sveltejs/vite-plugin-svelte';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [svelte()],
  resolve: { conditions: ['browser'] },
  test: { include: ['src/**/*.test.ts'], environment: 'jsdom' },
  define: {
    __FIXTURE_LABEL__: JSON.stringify(
      process.env.BAQYLAU_FIXTURE_LABEL ?? 'first',
    ),
  },
  build: {
    target: 'es2022',
    lib: {
      entry: fileURLToPath(new URL('src/index.svelte.ts', import.meta.url)),
      formats: ['es'],
      fileName: 'extension',
      cssFileName: 'extension',
    },
  },
});
