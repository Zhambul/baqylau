import type { ExtensionBundle } from './types.js';

export function assetUrls(
  bundle: ExtensionBundle,
  origin: string,
): {
  moduleUrl: string;
  styleUrls: string[];
} {
  if (
    !/^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$/.test(bundle.extensionId) ||
    bundle.extensionId.length > 100 ||
    !/^[a-f0-9]{64}$/.test(bundle.packageDigest)
  ) {
    throw new Error('Extension bundle identity is invalid.');
  }
  const prefix = `/extensions/${bundle.extensionId}/${bundle.packageDigest}/`;
  const validate = (source: string): string => {
    const url = new URL(source, origin);
    if (
      url.origin !== origin ||
      url.username ||
      url.password ||
      !url.pathname.startsWith(prefix) ||
      url.search ||
      url.hash
    ) {
      throw new Error('Extension asset is outside its installed package.');
    }
    const path = decodeURIComponent(url.pathname.slice(prefix.length));
    if (
      path.includes('\\') ||
      path.includes('%') ||
      path.split('/').some((part) => !part || part === '.' || part === '..')
    ) {
      throw new Error('Extension asset path is invalid.');
    }
    return url.href;
  };
  return {
    moduleUrl: validate(bundle.moduleUrl),
    styleUrls: bundle.styleUrls.map(validate),
  };
}

export async function importModule(url: string): Promise<unknown> {
  return import(/* @vite-ignore */ url);
}

export function loadStyle(
  url: string,
  shadow: ShadowRoot,
  signal: AbortSignal,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const link = shadow.ownerDocument.createElement('link');
    link.rel = 'stylesheet';
    link.href = url;
    const cleanup = (): void => {
      link.removeEventListener('load', loaded);
      link.removeEventListener('error', failed);
      signal.removeEventListener('abort', aborted);
    };
    const loaded = (): void => {
      cleanup();
      resolve();
    };
    const failed = (): void => {
      cleanup();
      link.remove();
      reject(new Error('Extension style failed to load.'));
    };
    const aborted = (): void => {
      cleanup();
      link.remove();
      reject(new DOMException('Extension view was removed.', 'AbortError'));
    };
    if (signal.aborted) {
      aborted();
      return;
    }
    link.addEventListener('load', loaded, { once: true });
    link.addEventListener('error', failed, { once: true });
    signal.addEventListener('abort', aborted, { once: true });
    shadow.append(link);
  });
}
