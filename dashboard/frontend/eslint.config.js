import { eslintConfig } from '@baqylau/dev-tools/eslint';

export default [
  ...eslintConfig({
    root: import.meta.dirname,
    ignores: [
      'coverage/**',
      'eslint.config.js',
      'playwright-report/**',
      'svelte.config.js',
      'src/api/generated/**',
      'test-results/**',
    ],
  }),
  {
    // Feature packages (Git, adapters, and others) load at run time through
    // the public SDK. The host never imports their code.
    files: ['src/**/*.ts', 'src/**/*.svelte'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: [
                '@baqylau/*',
                '!@baqylau/extension-api',
                '!@baqylau/extension-api/*',
              ],
              message:
                'The host uses only the public extension SDK; feature packages load at run time.',
            },
            {
              group: ['**/packages/**'],
              message: 'The host does not import repository packages by path.',
            },
          ],
        },
      ],
    },
  },
];
