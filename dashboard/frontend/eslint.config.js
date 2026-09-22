import { eslintConfig } from '@baqylau/dev-tools/eslint';

export default eslintConfig({
  root: import.meta.dirname,
  ignores: [
    'coverage/**',
    'eslint.config.js',
    'playwright-report/**',
    'svelte.config.js',
    'src/api/generated/**',
    'test-results/**',
  ],
});
