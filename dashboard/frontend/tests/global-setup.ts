import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const repositoryRoot = fileURLToPath(new URL('../../..', import.meta.url));

export default function buildFrontend(): void {
  execFileSync('make', ['build-frontend'], {
    cwd: repositoryRoot,
    stdio: 'inherit',
  });
}
