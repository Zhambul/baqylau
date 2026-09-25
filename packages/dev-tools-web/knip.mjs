import { readFileSync } from 'node:fs';
import { join } from 'node:path';

/**
 * Name the web view modules that an extension manifest declares as package
 * source. A module in a build output (`dist/`) is not source: the package
 * names its own source entries for it.
 * @param {string} root the package root
 * @param {string} manifest the manifest path under the root
 * @returns {string[]} the declared source modules
 */
export function manifestEntries(root, manifest) {
  /** @type {{ contributions?: { web?: { module: string }[] } }} */
  const declared = JSON.parse(readFileSync(join(root, manifest), 'utf8'));
  const modules = (declared.contributions?.web ?? []).map(
    (view) => view.module,
  );
  return modules.filter((module) => !module.split('/').includes('dist'));
}

/**
 * Find unused files, exports, and dependencies with the same rules in every package.
 * Each package names its own entry points and source files. A package with an
 * extension manifest also gets the web view modules that the manifest declares.
 * @param {{ entry: string[]; project: string[]; ignore?: string[]; ignoreBinaries?: string[]; root?: string; manifest?: string }} options
 * @returns {{ entry: string[]; project: string[]; ignore: string[]; ignoreBinaries: string[] }}
 */
export function knipConfig({
  entry,
  project,
  ignore = [],
  ignoreBinaries = [],
  root,
  manifest,
}) {
  const declared =
    root === undefined || manifest === undefined
      ? []
      : manifestEntries(root, manifest);
  return { entry: [...entry, ...declared], project, ignore, ignoreBinaries };
}
