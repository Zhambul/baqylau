import type {
  ExtensionCatalog,
  ExtensionOperation,
  ExtensionPackage,
  ExtensionPlan,
  ExtensionRuntime,
} from '../api/extensions';

export const EXTENSION_OWNER = 'test.base';
export const EXTENSION_DIGEST = 'a'.repeat(64);

function extensionPackage(): ExtensionPackage {
  return {
    extension_id: EXTENSION_OWNER,
    name: 'Base extension',
    package_version: '1.0.0',
    package_digest: EXTENSION_DIGEST,
    source_path: '/private/packages/base',
    resolved_path: '/private/packages/base',
    capabilities: ['lifecycle'],
    issue: null,
  };
}

export function extensionCatalog(): ExtensionCatalog {
  return { revision: 3, entries: [extensionPackage()], root_issues: [] };
}

export function extensionRuntime(): ExtensionRuntime {
  return {
    revision: 4,
    registry_revision: 2,
    phase: 'running',
    active_runtime: 'runtime-1',
    directory: {
      catalog_revision: 3,
      runtime_revision: 'runtime-1',
      entries: [],
    },
    committed_runtime: 'runtime-1',
    committed_packages: [],
    requested: [],
    pending_operation: null,
    cleanup: [],
    cleanup_pending: false,
    read_only: false,
  };
}

export function enabledRuntime(): ExtensionRuntime {
  const info = {
    extension_id: EXTENSION_OWNER,
    package_version: '1.0.0',
    package_digest: EXTENSION_DIGEST,
    api_version: '0.1.0a1',
  };
  return {
    ...extensionRuntime(),
    directory: {
      catalog_revision: 3,
      runtime_revision: 'runtime-1',
      entries: [{ extension_info: info, state: 'enabled' }],
    },
    committed_packages: [{ extension_info: info, settings_revision: 0 }],
    requested: [
      {
        extension_id: EXTENSION_OWNER,
        enabled: true,
        package_digest: EXTENSION_DIGEST,
      },
    ],
  };
}

export function extensionOperation(): ExtensionOperation {
  return {
    operation_id: 'operation-1',
    kind: 'enable',
    extension_id: EXTENSION_OWNER,
    request_id: 'request-1',
    accepted_revision: 5,
    runtime_revision: 'runtime-2',
    status: 'preparing',
    created_at: 1000,
    updated_at: 1000,
    failure: null,
  };
}

export function extensionPlan(): ExtensionPlan {
  return {
    extension_id: EXTENSION_OWNER,
    affected_extensions: [EXTENSION_OWNER],
    request: {
      action: 'enable',
      package_digest: EXTENSION_DIGEST,
      expected_revision: 4,
      expected_catalog_revision: 3,
    },
  };
}
