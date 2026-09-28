import { knipConfig } from '@baqylau/dev-tools/knip';

export default knipConfig({
  entry: ['src/main.ts', 'src/**/*.test.ts'],
  project: ['src/**/*.{ts,svelte}'],
});
