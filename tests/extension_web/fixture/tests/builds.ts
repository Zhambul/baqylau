import { execFileSync } from 'node:child_process';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { digest, readAssets } from './assets.js';
import type { Asset } from './assets.js';

const PACKAGE_ROOT = fileURLToPath(new URL('../', import.meta.url));

export async function buildPackages(root: string) {
  const hostDirectory = join(root, 'host');
  build('build:host', hostDirectory, 'host');
  const hostAssets = await readAssets(hostDirectory);
  const hostDigest = digest(hostAssets);
  const assets = new Map(hostAssets.map((asset) => [`/${asset.path}`, asset]));
  const first = await buildFeature(root, 'first', assets);
  const second = await buildFeature(root, 'second', assets);
  return { assets, first, second, hostDirectory, hostDigest };
}

function build(script: string, output: string, label: string): void {
  execFileSync('npm', ['run', script, '--', '--outDir', output], {
    cwd: PACKAGE_ROOT,
    env: { ...process.env, BAQYLAU_FIXTURE_LABEL: label },
    encoding: 'utf8',
  });
}

async function buildFeature(
  root: string,
  label: string,
  catalog: Map<string, Asset>,
): Promise<string> {
  const directory = join(root, label);
  build('build', directory, label);
  const assets = await readAssets(directory);
  const revision = digest(assets);
  for (const asset of assets)
    catalog.set(`/extensions/test.views/${revision}/${asset.path}`, asset);
  return revision;
}
