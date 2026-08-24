import eslint from '@eslint/js';
import svelte from 'eslint-plugin-svelte';
import globals from 'globals';
import typescript from 'typescript-eslint';

export default typescript.config(
  {
    ignores: [
      'coverage/**',
      'eslint.config.js',
      'playwright-report/**',
      'svelte.config.js',
      'src/api/generated/**',
      'test-results/**',
    ],
  },
  eslint.configs.recommended,
  ...typescript.configs.strictTypeChecked,
  ...typescript.configs.stylisticTypeChecked,
  ...svelte.configs.recommended,
  {
    languageOptions: {
      globals: globals.browser,
      parserOptions: {
        extraFileExtensions: ['.svelte'],
        parser: typescript.parser,
        project: ['./tsconfig.json', './tsconfig.node.json'],
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      '@typescript-eslint/consistent-type-definitions': ['error', 'type'],
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/no-non-null-assertion': 'error',
      '@typescript-eslint/no-unnecessary-type-assertion': 'error',
      '@typescript-eslint/no-unsafe-type-assertion': 'error',
    },
  },
  {
    files: ['vite.config.ts', 'playwright.config.ts', 'tests/**/*.ts'],
    languageOptions: {
      globals: globals.node,
    },
  },
);
