import {
  changeExtension,
  previewExtension,
  readExtensionCatalog,
  readExtensionHealth,
  readExtensionOperation,
  readExtensionRuntime,
  readRecentOperations,
  rescanExtensions,
} from '../api/extensions';
import type {
  ExtensionAction,
  ExtensionCatalog,
  ExtensionChangeRequest,
  ExtensionHealthReport,
  ExtensionOperation,
  ExtensionPlan,
  ExtensionRuntime,
} from '../api/extensions';
import { HttpFailure } from '../api/client';
import { newRequestId } from '../shared/browser/identity';

const PROGRESS_INTERVAL_MS = 1_000;

type Confirmation = {
  readonly plan: ExtensionPlan;
  readonly request: ExtensionChangeRequest;
};

export class ExtensionManagement {
  catalog = $state<ExtensionCatalog | null>(null);
  runtime = $state<ExtensionRuntime | null>(null);
  operation = $state<ExtensionOperation | null>(null);
  operations = $state<readonly ExtensionOperation[]>([]);
  health = $state<ExtensionHealthReport | null>(null);
  confirmation = $state<Confirmation | null>(null);
  busy = $state(false);
  error = $state<string | null>(null);
  fresh = $state(false);

  readonly canWrite = $derived(
    this.fresh &&
      !this.busy &&
      this.runtime?.read_only === false &&
      this.runtime.phase === 'running' &&
      this.runtime.pending_operation === null,
  );

  private readonly controller = new AbortController();
  private timer: ReturnType<typeof setTimeout> | null = null;

  /** The recent operations of one extension, newest first. */
  operationsFor(owner: string | null): readonly ExtensionOperation[] {
    return this.operations.filter(
      (operation) => owner !== null && operation.extension_id === owner,
    );
  }

  /** The stored health of one extension, or null when it has no failures. */
  healthOf(owner: string | null) {
    return (
      this.health?.extensions.find((entry) => entry.extension_id === owner) ??
      null
    );
  }

  refresh(): Promise<void> {
    return this.run(async () => {
      this.confirmation = null;
      await this.read();
    });
  }

  preview(
    owner: string,
    action: ExtensionAction,
    digest: string | null,
  ): Promise<void> {
    const runtime = this.runtime;
    const catalog = this.catalog;
    if (!this.canWrite || runtime === null || catalog === null)
      return Promise.resolve();
    return this.run(async () => {
      const plan = await previewExtension(
        owner,
        {
          action,
          package_digest: action === 'disable' ? null : digest,
          expected_revision: runtime.revision,
          expected_catalog_revision: catalog.revision,
        },
        this.controller.signal,
      );
      if (this.controller.signal.aborted) return;
      this.confirmation = {
        plan,
        request: {
          ...plan.request,
          request_id: newRequestId(),
          confirmed_dependents: plan.affected_extensions.filter(
            (target) => target !== plan.extension_id,
          ),
        },
      };
    });
  }

  confirm(): Promise<void> {
    const selection = this.confirmation;
    if (!this.canWrite || selection === null) return Promise.resolve();
    return this.run(async () => {
      const operation = await changeExtension(
        selection.plan.extension_id,
        selection.request,
        this.controller.signal,
      );
      if (this.controller.signal.aborted) return;
      this.confirmation = null;
      this.operation = operation;
      await this.read();
    });
  }

  cancel(): void {
    if (!this.busy) this.confirmation = null;
  }

  rescan(): Promise<void> {
    const catalog = this.catalog;
    if (!this.canWrite || catalog === null) return Promise.resolve();
    return this.run(async () => {
      this.confirmation = null;
      await rescanExtensions(catalog.revision, this.controller.signal);
      await this.read();
    });
  }

  close(): void {
    this.controller.abort();
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }

  private async read(): Promise<void> {
    if (this.isClosed()) return;
    this.fresh = false;
    const signal = this.controller.signal;
    const [catalog, runtime, operations, health] = await Promise.all([
      readExtensionCatalog(signal),
      readExtensionRuntime(signal),
      readRecentOperations(signal),
      readExtensionHealth(signal),
    ]);
    const operationId =
      runtime.pending_operation ??
      (this.operation?.status === 'preparing'
        ? this.operation.operation_id
        : null);
    const operation =
      operationId === null
        ? this.operation
        : await readExtensionOperation(operationId, signal);
    if (signal.aborted) return;
    this.catalog = catalog;
    this.runtime = runtime;
    this.operations = operations;
    this.health = health;
    this.operation = operation;
    this.fresh = true;
  }

  private async run(action: () => Promise<void>): Promise<void> {
    if (this.busy || this.isClosed()) return;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    this.busy = true;
    this.error = null;
    try {
      await action();
    } catch (error) {
      if (this.isClosed()) return;
      this.error =
        error instanceof Error
          ? error.message
          : 'The extension request failed.';
      if (error instanceof HttpFailure && error.status === 409) {
        this.fresh = false;
        this.confirmation = null;
        this.error =
          'The selected state changed. Refresh and review the action again.';
      }
    } finally {
      if (!this.isClosed()) {
        this.busy = false;
        this.scheduleProgress();
      }
    }
  }

  private isClosed(): boolean {
    return this.controller.signal.aborted;
  }

  private scheduleProgress(): void {
    if (this.confirmation !== null) return;
    if (
      (this.runtime?.pending_operation !== null &&
        this.runtime?.pending_operation !== undefined) ||
      this.operation?.status === 'preparing' ||
      this.runtime?.cleanup_pending === true ||
      this.runtime?.phase === 'preparing' ||
      this.runtime?.phase === 'awaiting_boundary'
    ) {
      this.timer = setTimeout(() => {
        void this.refresh();
      }, PROGRESS_INTERVAL_MS);
    }
  }
}
