import { eslintConfig } from '@baqylau/dev-tools/eslint';

export default eslintConfig({
  root: import.meta.dirname,
  ignores: ['dist/**', 'coverage/**', 'src/generated/**', 'eslint.config.js'],
  nodeFiles: ['vite.config.ts', 'scripts/*.mjs'],
});
