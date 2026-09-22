import { execFileSync } from 'node:child_process';
import { cp, mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const artifacts = process.argv[2];
if (!artifacts)
  throw new Error('Supply the directory with the two npm artifacts.');
const workspace = await mkdtemp(join(tmpdir(), 'baqylau-external-view-'));
await cp(
  fileURLToPath(
    new URL('../../../tests/extension_web/fixture', import.meta.url),
  ),
  workspace,
  { recursive: true },
);
const options = { cwd: workspace, stdio: 'inherit' };
execFileSync(
  'npm',
  [
    'pkg',
    'set',
    `dependencies.@baqylau/extension-api=file:${resolve(artifacts, 'baqylau-extension-api-0.1.0-alpha.1.tgz')}`,
    `devDependencies.@baqylau/dev-tools=file:${resolve(artifacts, 'baqylau-dev-tools-0.1.0-alpha.1.tgz')}`,
  ],
  { ...options, stdio: 'inherit' },
);
execFileSync('npm', ['install', '--ignore-scripts'], {
  ...options,
  stdio: 'inherit',
});
console.log(`External view package: ${workspace}`);
if (!process.argv.includes('--prepare-only')) {
  for (const script of [
    'format:check',
    'check',
    'lint',
    'test',
    'test:browser',
  ]) {
    execFileSync('npm', ['run', script], { ...options, stdio: 'inherit' });
  }
}
