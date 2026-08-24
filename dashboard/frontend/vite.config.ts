import { fileURLToPath, URL } from 'node:url';

import { svelte } from '@sveltejs/vite-plugin-svelte';
import { defineConfig } from 'vitest/config';

const frontendRoot = fileURLToPath(new URL('.', import.meta.url));

export default defineConfig({
  root: frontendRoot,
  plugins: [svelte()],
  resolve: {
    conditions: ['browser'],
  },
  build: {
    emptyOutDir: true,
    manifest: true,
    outDir: '../static/build',
    rolldownOptions: {
      input: fileURLToPath(new URL('src/main.ts', import.meta.url)),
    },
    target: 'es2022',
  },
  server: {
    cors: {
      origin: /^http:\/\/(?:127\.0\.0\.1|localhost):\d+$/,
    },
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
  },
  test: {
    include: ['src/**/*.test.ts'],
    coverage: {
      exclude: ['src/main.ts', 'src/test/**'],
      include: [
        'src/app/route.ts',
        'src/application/usage-layout.ts',
        'src/attachments/attachment-tray.svelte.ts',
        'src/commands/command-menu.ts',
        'src/dictation/dictation-controller.svelte.ts',
        'src/entries/feed-model.ts',
        'src/entries/markup.ts',
        'src/sessions/agent-presentation.ts',
        'src/sessions/global-reducer.ts',
        'src/sessions/grouping.ts',
        'src/sessions/optimistic-prompts.ts',
        'src/sessions/session-reducer.ts',
        'src/sessions/shell-fold.ts',
        'src/shared/browser/keyboard.ts',
        'src/shared/browser/presence.ts',
      ],
      provider: 'v8',
      reporter: ['text', 'html'],
      thresholds: {
        branches: 55,
        functions: 75,
        lines: 75,
        statements: 70,
      },
    },
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
  },
});
