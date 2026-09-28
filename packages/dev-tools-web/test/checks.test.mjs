// Prove that each shared rule fails on a deliberate violation, and that the
// dashboard keeps the shared rules. Each case copies the clean sample into
// `.cases/`, where it resolves the policy from this package's node_modules,
// adds one violation, and runs the tool that must refuse it.

import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { cpSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { after, describe, it } from 'node:test';
import { fileURLToPath } from 'node:url';

import { pinMismatches, toolPins } from '../pins.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const cases = join(here, '.cases');
const bin = join(here, 'node_modules', '.bin');
const dashboard = join(here, '..', '..', '..', 'dashboard', 'frontend');

const tools = {
  eslint: ['eslint', '.', '--max-warnings', '0'],
  svelteCheck: [
    'svelte-check',
    '--tsconfig',
    'tsconfig.json',
    '--fail-on-warnings',
  ],
  knip: ['knip'],
  prettier: ['prettier', '--check', '.'],
  coverage: ['vitest', 'run', '--coverage'],
};

function copyOf(name, files = {}) {
  const directory = join(cases, name);
  rmSync(directory, { recursive: true, force: true });
  cpSync(join(here, 'sample'), directory, { recursive: true });
  for (const [path, text] of Object.entries(files)) {
    mkdirSync(dirname(join(directory, path)), { recursive: true });
    writeFileSync(join(directory, path), text);
  }
  return directory;
}

function run(directory, [tool, ...args]) {
  return spawnSync(join(bin, tool), args, { cwd: directory, encoding: 'utf8' });
}

function output(result) {
  return `${result.stdout}${result.stderr}`;
}

after(() => {
  rmSync(cases, { recursive: true, force: true });
});

describe('the clean sample', () => {
  const directory = copyOf('clean');
  for (const [name, command] of Object.entries(tools)) {
    it(`passes ${name}`, () => {
      const result = run(directory, command);
      assert.equal(result.status, 0, output(result));
    });
  }
});

describe('a deliberate violation', () => {
  it('fails ESLint for an explicit any', () => {
    const directory = copyOf('any', {
      'src/total.ts':
        'export function total(values: any): number {\n  return Number(values);\n}\n',
    });
    const result = run(directory, tools.eslint);
    assert.notEqual(result.status, 0);
    assert.match(output(result), /no-explicit-any/);
  });

  it('fails svelte-check for a prop of the wrong type', () => {
    const app =
      '<script lang="ts">\n  import Count from \'./Count.svelte\';\n</script>\n\n<Count count="three" />\n';
    const directory = copyOf('props', { 'src/App.svelte': app });
    const result = run(directory, tools.svelteCheck);
    assert.notEqual(result.status, 0, output(result));
  });

  it('fails Knip for an unused export', () => {
    const directory = copyOf('export', {
      'src/extra.ts': 'export const unused = 1;\n',
    });
    const result = run(directory, tools.knip);
    assert.notEqual(result.status, 0);
    assert.match(output(result), /extra\.ts/);
  });

  it('fails Prettier for bad formatting', () => {
    const directory = copyOf('format', {
      'src/total.ts':
        'export function total(values:readonly number[]):number{return values.reduce((sum,value)=>sum+value,0)}\n',
    });
    const result = run(directory, tools.prettier);
    assert.notEqual(result.status, 0);
  });

  it('fails coverage below the shared thresholds', () => {
    const untested = [
      'export function total(values: readonly number[]): number {',
      '  return values.reduce((sum, value) => sum + value, 0);',
      '}',
      '',
      'export function largest(values: readonly number[]): number {',
      '  let found = -Infinity;',
      '  for (const value of values) {',
      '    if (value > found) found = value;',
      '  }',
      '  return found;',
      '}',
      '',
      'export function smallest(values: readonly number[]): number {',
      '  let found = Infinity;',
      '  for (const value of values) {',
      '    if (value < found) found = value;',
      '  }',
      '  return found;',
      '}',
      '',
    ].join('\n');
    const directory = copyOf('coverage', { 'src/total.ts': untested });
    const result = run(directory, tools.coverage);
    assert.notEqual(result.status, 0);
    assert.match(output(result), /threshold/i);
  });
});

describe('parity with the dashboard', () => {
  // The dashboard may add host rules, such as its import boundary; it must keep every shared rule.
  it('keeps every shared ESLint rule in the dashboard', () => {
    const shared = JSON.parse(
      run(copyOf('parity'), ['eslint', '--print-config', 'src/total.ts'])
        .stdout,
    );
    const host = spawnSync(
      join(dashboard, 'node_modules', '.bin', 'eslint'),
      ['--print-config', 'src/main.ts'],
      {
        cwd: dashboard,
        encoding: 'utf8',
      },
    );
    const hostRules = JSON.parse(host.stdout).rules;
    for (const [rule, setting] of Object.entries(shared.rules)) {
      assert.deepEqual(hostRules[rule], setting, rule);
    }
  });

  it('keeps every shared TypeScript option in the dashboard', () => {
    const shared = JSON.parse(
      run(copyOf('parity'), ['tsc', '--showConfig', '-p', 'tsconfig.json'])
        .stdout,
    );
    const host = spawnSync(
      join(dashboard, 'node_modules', '.bin', 'tsc'),
      ['--showConfig', '-p', 'tsconfig.json'],
      {
        cwd: dashboard,
        encoding: 'utf8',
      },
    );
    const hostOptions = JSON.parse(host.stdout).compilerOptions;
    for (const [option, value] of Object.entries(shared.compilerOptions)) {
      assert.deepEqual(hostOptions[option], value, option);
    }
  });
});

describe('the tool pins', () => {
  it('accepts the pinned versions and names another version', () => {
    assert.deepEqual(pinMismatches({ ...toolPins, react: '19.0.0' }), []);
    assert.deepEqual(pinMismatches({ knip: '6.0.0' }), [
      `knip: declared 6.0.0; the policy pins ${toolPins.knip}`,
    ]);
  });
});

describe('the manifest entries', () => {
  const view = {
    'src/view.ts':
      'export function mount(): void {\n  document.title = "view";\n}\n',
  };
  const manifest = JSON.stringify({
    contributions: { web: [{ module: 'src/view.ts' }] },
  });
  const declaring = [
    "import { knipConfig } from '@baqylau/dev-tools/knip';",
    '',
    'export default knipConfig({',
    "  entry: ['src/main.ts', 'src/**/*.test.ts'],",
    "  project: ['src/**/*.{ts,svelte}'],",
    '  root: import.meta.dirname,',
    "  manifest: 'extension.json',",
    '});',
    '',
  ].join('\n');

  it('gives Knip the web view module that the manifest declares', () => {
    const directory = copyOf('manifest', {
      ...view,
      'extension.json': manifest,
      'knip.config.ts': declaring,
    });
    const result = run(directory, tools.knip);
    assert.equal(result.status, 0, output(result));
  });

  it('reports the same module as unused without the manifest', () => {
    const directory = copyOf('no-manifest', {
      ...view,
      'extension.json': manifest,
    });
    const result = run(directory, tools.knip);
    assert.notEqual(result.status, 0);
    assert.match(output(result), /view\.ts/);
  });
});
