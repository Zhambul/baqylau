import eslint from '@eslint/js';
import svelte from 'eslint-plugin-svelte';
import globals from 'globals';
import typescript from 'typescript-eslint';

/**
 * Use the same typed rules for the dashboard and external packages.
 * @param {{ root: string; projects?: string[]; ignores?: string[]; nodeFiles?: string[] }} options
 * @returns {ReturnType<typeof typescript.config>}
 */
export function eslintConfig({
  root,
  projects = ['./tsconfig.json', './tsconfig.node.json'],
  ignores = [],
  nodeFiles = ['vite.config.ts', 'playwright.config.ts', 'tests/**/*.ts'],
}) {
  return typescript.config(
    { ignores },
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
          project: projects,
          tsconfigRootDir: root,
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
    { files: nodeFiles, languageOptions: { globals: globals.node } },
  );
}
