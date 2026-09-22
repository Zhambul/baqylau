import type { ExtensionWebModule, MountedExtensionView } from '../view.js';
import type { ExtensionScope } from '../wire.js';

export function requireModule(candidate: unknown): ExtensionWebModule {
  if (!isModule(candidate)) {
    throw new Error('Extension module must export a mount function.');
  }
  return candidate;
}

function isModule(candidate: unknown): candidate is ExtensionWebModule {
  if (
    typeof candidate !== 'object' ||
    candidate === null ||
    !('mount' in candidate) ||
    typeof candidate.mount !== 'function'
  ) {
    return false;
  }
  return true;
}

export function requireMounted(candidate: unknown): MountedExtensionView {
  if (!isMounted(candidate)) {
    throw new Error(
      'Extension mount must return update and dispose functions.',
    );
  }
  return candidate;
}

function isMounted(candidate: unknown): candidate is MountedExtensionView {
  if (
    typeof candidate !== 'object' ||
    candidate === null ||
    !('update' in candidate) ||
    !('dispose' in candidate) ||
    typeof candidate.update !== 'function' ||
    typeof candidate.dispose !== 'function'
  ) {
    return false;
  }
  return true;
}

export function scopeKey(scope: ExtensionScope): string {
  switch (scope.kind) {
    case 'session':
      return JSON.stringify([
        scope.kind,
        scope.session_id,
        scope.actor_id,
        scope.harness,
      ]);
    case 'workspace':
      return JSON.stringify([scope.kind, scope.workspace_id]);
    case 'repository':
      return JSON.stringify([
        scope.kind,
        scope.repository_id,
        scope.worktree,
        scope.git_directory,
      ]);
    case 'installation':
      return scope.kind;
  }
}
