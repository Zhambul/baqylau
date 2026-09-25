import { eslintConfig } from '@baqylau/dev-tools/eslint';

export default eslintConfig({
  root: import.meta.dirname,
  nodeFiles: ['vite.config.ts', 'knip.config.ts'],
});
