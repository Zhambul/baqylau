import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const python = process.env.BAQYLAU_SDK_PYTHON ?? 'python3';
const source = execFileSync(
  python,
  [fileURLToPath(new URL('./export_models.py', import.meta.url))],
  { encoding: 'utf8' },
);
const output = new URL('../src/generated/wire.ts', import.meta.url);
const checkArguments = process.argv.includes('--check') ? ['--check'] : [];
execFileSync(
  'openapi-typescript',
  ['--immutable', '--output', fileURLToPath(output), ...checkArguments],
  {
    input: source,
    stdio: ['pipe', 'inherit', 'inherit'],
  },
);
