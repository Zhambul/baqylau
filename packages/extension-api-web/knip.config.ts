import { knipConfig } from '@baqylau/dev-tools/knip';

export default knipConfig({
  entry: ['src/index.ts', 'src/host/index.ts', 'src/**/*.test.ts'],
  project: ['src/**/*.ts', 'scripts/*.mjs'],
});
