/**
 * Name the exact version of each frontend quality tool. A package that uses
 * one of these tools declares this version; the pin check refuses another.
 */
export const toolPins = Object.freeze({
  '@playwright/test': '1.62.1',
  '@vitest/coverage-v8': '4.1.11',
  eslint: '10.9.0',
  jsdom: '30.0.1',
  knip: '6.32.2',
  prettier: '3.9.6',
  svelte: '5.56.10',
  'svelte-check': '4.7.6',
  typescript: '5.9.3',
  vitest: '4.1.11',
});

/**
 * Find each declared tool whose version is not the pinned version.
 * @param {Record<string, string>} declared the package's dependencies and devDependencies
 * @returns {string[]} one line for each mismatch
 */
export function pinMismatches(declared) {
  return Object.entries(toolPins).flatMap(([tool, pinned]) => {
    const version = declared[tool];
    return version === undefined || version === pinned
      ? []
      : [`${tool}: declared ${version}; the policy pins ${pinned}`];
  });
}
