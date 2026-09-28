import { coverageThresholds } from './coverage.mjs';

/**
 * Run unit tests and measure coverage with the shared provider, reporters, and thresholds.
 * Each package names its test files and the behavior modules that coverage measures.
 * @param {{ include: string[]; coverageInclude: string[]; coverageExclude?: string[]; environment?: string; setupFiles?: string[] }} options
 */
export function testProfile({
  include,
  coverageInclude,
  coverageExclude = [],
  environment = 'jsdom',
  setupFiles = [],
}) {
  return {
    include,
    environment,
    setupFiles,
    coverage: {
      include: coverageInclude,
      exclude: coverageExclude,
      provider: /** @type {const} */ ('v8'),
      reporter: ['text', 'html'],
      thresholds: coverageThresholds,
    },
  };
}
