import { createHash } from 'node:crypto';
import { readFile, readdir } from 'node:fs/promises';
import { join } from 'node:path';

export type Asset = { path: string; body: Buffer; contentType: string };

export async function readAssets(root: string, prefix = ''): Promise<Asset[]> {
  const entries = await readdir(join(root, prefix), { withFileTypes: true });
  const groups = await Promise.all(
    entries.map(async (entry) => {
      const path = prefix ? `${prefix}/${entry.name}` : entry.name;
      if (entry.isDirectory()) return readAssets(root, path);
      const body = await readFile(join(root, path));
      const contentType = path.endsWith('.js')
        ? 'text/javascript'
        : path.endsWith('.css')
          ? 'text/css'
          : 'text/html';
      return [{ path, body, contentType }];
    }),
  );
  return groups
    .flat()
    .sort((left, right) => left.path.localeCompare(right.path));
}

export function digest(assets: Asset[]): string {
  const digest = createHash('sha256');
  for (const asset of assets) {
    digest.update(asset.path);
    digest.update('\0');
    digest.update(asset.body);
    digest.update('\0');
  }
  return digest.digest('hex');
}
