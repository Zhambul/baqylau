import * as svelte from 'prettier-plugin-svelte';

/** @type {import('prettier').Config} */
export default {
  plugins: [svelte],
  singleQuote: true,
  svelteSortOrder: 'options-scripts-markup-styles',
  trailingComma: 'all',
};
