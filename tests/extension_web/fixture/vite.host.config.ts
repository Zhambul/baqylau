import { fileURLToPath } from 'node:url';

import { defineConfig } from 'vite';

export default defineConfig({
  root: fileURLToPath(new URL('host', import.meta.url)),
  build: { target: 'es2022', outDir: '../host-dist', emptyOutDir: true },
});
