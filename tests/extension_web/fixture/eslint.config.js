import { eslintConfig } from '@baqylau/dev-tools/eslint';

export default eslintConfig({
  root: import.meta.dirname,
  ignores: [
    'dist/**',
    'host-dist/**',
    'test-results/**',
    'playwright-report/**',
    'eslint.config.js',
    'svelte.config.js',
  ],
  nodeFiles: ['vite*.config.ts', 'playwright.config.ts', 'tests/**/*.ts'],
});
