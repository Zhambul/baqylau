import { createServer } from 'node:http';

import type { Asset } from './assets.js';

export async function serve(assets: Map<string, Asset>) {
  const server = createServer((request, response) => {
    const path = new URL(request.url ?? '/', 'http://localhost').pathname;
    const asset = assets.get(path === '/' ? '/index.html' : path);
    if (!asset) {
      response.writeHead(404);
      response.end();
      return;
    }
    response.writeHead(200, { 'content-type': asset.contentType });
    response.end(asset.body);
  });
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const address = server.address();
  if (!address || typeof address === 'string')
    throw new Error('Test server has no TCP address.');
  return {
    url: `http://127.0.0.1:${String(address.port)}`,
    close: () =>
      new Promise<void>((resolve, reject) => {
        server.close((error) => {
          if (error) reject(error);
          else resolve();
        });
      }),
  };
}
