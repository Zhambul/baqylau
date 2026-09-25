import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { HttpFailure } from '../api/client';
import * as api from '../api/extensions';
import {
  EXTENSION_DIGEST,
  EXTENSION_OWNER,
  extensionCatalog,
  extensionOperation,
  extensionPlan,
  extensionRuntime,
} from '../test/extension-fixture';
import { ExtensionManagement } from './management.svelte';

vi.mock('../api/extensions', () => ({
  changeExtension: vi.fn(),
  previewExtension: vi.fn(),
  readExtensionCatalog: vi.fn(),
  readExtensionHealth: vi.fn(),
  readExtensionOperation: vi.fn(),
  readExtensionRuntime: vi.fn(),
  readRecentOperations: vi.fn(),
  rescanExtensions: vi.fn(),
}));

describe('extension management state', () => {
  let view: ExtensionManagement;

  beforeEach(() => {
    vi.resetAllMocks();
    vi.useFakeTimers();
    vi.mocked(api.readExtensionCatalog).mockResolvedValue(extensionCatalog());
    vi.mocked(api.readExtensionRuntime).mockResolvedValue(extensionRuntime());
    vi.mocked(api.previewExtension).mockResolvedValue(extensionPlan());
    vi.mocked(api.changeExtension).mockResolvedValue(extensionOperation());
    vi.mocked(api.readExtensionOperation).mockResolvedValue(
      extensionOperation(),
    );
    vi.mocked(api.rescanExtensions).mockResolvedValue(extensionCatalog());
    vi.mocked(api.readRecentOperations).mockResolvedValue([]);
    vi.mocked(api.readExtensionHealth).mockResolvedValue({
      failure_limit: 5,
      extensions: [],
    });
    view = new ExtensionManagement();
  });

  afterEach(() => {
    view.close();
    vi.useRealTimers();
  });

  it('loads without an idle polling timer', async () => {
    await view.refresh();
    expect(view.canWrite).toBe(true);
    expect(view.catalog?.revision).toBe(3);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('gives each extension only its own recent operations', async () => {
    const own = extensionOperation();
    const other = {
      ...extensionOperation(),
      operation_id: 'other',
      extension_id: 'test.other',
    };
    vi.mocked(api.readRecentOperations).mockResolvedValue([own, other]);
    await view.refresh();
    expect(view.operationsFor(EXTENSION_OWNER)).toEqual([own]);
    expect(view.operationsFor(null)).toEqual([]);
  });

  it('gives the stored health of one extension', async () => {
    const failing = {
      extension_id: EXTENSION_OWNER,
      state: 'failing' as const,
      consecutive_failures: 2,
      last_failure_where: 'extension projection',
      last_failure_at: 1,
      last_success_at: null,
    };
    vi.mocked(api.readExtensionHealth).mockResolvedValue({
      failure_limit: 5,
      extensions: [failing],
    });
    await view.refresh();
    expect(view.healthOf(EXTENSION_OWNER)).toEqual(failing);
    expect(view.healthOf('test.other')).toBeNull();
  });

  it('keeps mutations closed after load failure until refresh succeeds', async () => {
    vi.mocked(api.readExtensionRuntime).mockRejectedValueOnce(
      new Error('Manager unavailable'),
    );
    await view.refresh();
    expect(view.canWrite).toBe(false);
    expect(view.error).toBe('Manager unavailable');
    await view.refresh();
    expect(view.canWrite).toBe(true);
  });

  it.each(['read-only', 'preparing'] as const)(
    'prevents writes while %s',
    async (reason) => {
      vi.mocked(api.readExtensionRuntime).mockResolvedValue({
        ...extensionRuntime(),
        read_only: reason === 'read-only',
        phase: reason === 'preparing' ? 'preparing' : 'running',
      });
      await view.refresh();
      await view.preview(EXTENSION_OWNER, 'enable', EXTENSION_DIGEST);
      await view.confirm();
      await view.rescan();
      expect(api.previewExtension).not.toHaveBeenCalled();
      expect(api.changeExtension).not.toHaveBeenCalled();
      expect(api.rescanExtensions).not.toHaveBeenCalled();
    },
  );

  it('requires preview and carries exact revisions and dependent confirmation', async () => {
    vi.mocked(api.previewExtension).mockResolvedValue({
      ...extensionPlan(),
      request: {
        ...extensionPlan().request,
        action: 'disable',
        package_digest: null,
      },
      affected_extensions: [EXTENSION_OWNER, 'test.child'],
    });
    await view.refresh();
    await view.preview(EXTENSION_OWNER, 'disable', null);
    expect(api.changeExtension).not.toHaveBeenCalled();
    expect(api.previewExtension).toHaveBeenCalledWith(
      EXTENSION_OWNER,
      {
        action: 'disable',
        package_digest: null,
        expected_revision: 4,
        expected_catalog_revision: 3,
      },
      expect.any(AbortSignal),
    );
    const confirmed = view.confirmation?.request;
    expect(confirmed?.request_id).toEqual(expect.any(String));
    await view.confirm();
    expect(api.changeExtension).toHaveBeenCalledWith(
      EXTENSION_OWNER,
      {
        ...extensionPlan().request,
        action: 'disable',
        package_digest: null,
        request_id: confirmed?.request_id,
        confirmed_dependents: ['test.child'],
      },
      expect.any(AbortSignal),
    );
    expect(view.operation?.status).toBe('preparing');
  });

  it('retries a lost reply with the same request body and ID', async () => {
    vi.mocked(api.changeExtension).mockRejectedValueOnce(
      new Error('Reply lost'),
    );
    await view.refresh();
    await view.preview(EXTENSION_OWNER, 'enable', EXTENSION_DIGEST);
    await view.confirm();
    expect(view.confirmation).not.toBeNull();
    await view.confirm();
    expect(vi.mocked(api.changeExtension).mock.calls[0]?.[1]).toEqual(
      vi.mocked(api.changeExtension).mock.calls[1]?.[1],
    );
    expect(view.confirmation).toBeNull();
  });

  it('requires a new review after a revision conflict', async () => {
    vi.mocked(api.changeExtension).mockRejectedValueOnce(
      new HttpFailure(409, 'Stale revision'),
    );
    await view.refresh();
    await view.preview(EXTENSION_OWNER, 'enable', EXTENSION_DIGEST);
    await view.confirm();
    expect(view.confirmation).toBeNull();
    expect(view.canWrite).toBe(false);
    expect(view.error).toContain('Refresh and review');
    await view.refresh();
    expect(view.canWrite).toBe(true);
  });

  it('recovers a pending operation from another browser and stops polling at completion', async () => {
    vi.mocked(api.readExtensionRuntime).mockResolvedValueOnce({
      ...extensionRuntime(),
      pending_operation: 'operation-1',
      phase: 'preparing',
    });
    await view.refresh();
    expect(view.canWrite).toBe(false);
    expect(view.operation?.status).toBe('preparing');
    vi.mocked(api.readExtensionOperation).mockResolvedValue({
      ...extensionOperation(),
      status: 'succeeded',
    });
    await vi.advanceTimersByTimeAsync(1_000);
    expect(view.operation?.status).toBe('succeeded');
    expect(view.canWrite).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('does not publish a late load or start overlapping requests after navigation', async () => {
    const pending = view.refresh();
    const overlapping = view.refresh();
    view.close();
    await Promise.all([pending, overlapping]);
    expect(api.readExtensionRuntime).toHaveBeenCalledOnce();
    expect(view.runtime).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('rescans the observed catalog revision and cancels a pending preview', async () => {
    await view.refresh();
    await view.preview(EXTENSION_OWNER, 'enable', EXTENSION_DIGEST);
    view.cancel();
    expect(view.confirmation).toBeNull();
    await view.rescan();
    expect(api.rescanExtensions).toHaveBeenCalledWith(
      3,
      expect.any(AbortSignal),
    );
    expect(view.fresh).toBe(true);
  });
});
