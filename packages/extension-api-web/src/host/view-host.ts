import type { ExtensionViewContext, ExtensionViewSnapshot } from '../view.js';
import { assetUrls, importModule, loadStyle } from './assets.js';
import { immutableSnapshot, viewContext } from './context.js';
import { createSession, disposeMounted, setTheme } from './session.js';
import type { ExtensionBundle, ViewHostOptions, ViewSession } from './types.js';
import { requireModule, requireMounted, scopeKey } from './validation.js';

/** Load package-owned views without passing a component across runtimes. */
export class ExtensionViewHost {
  private current: ViewSession | null = null;

  constructor(private readonly options: ViewHostOptions) {}

  show(
    bundle: ExtensionBundle,
    snapshot: ExtensionViewSnapshot,
  ): Promise<void> {
    void this.clear();
    const session = createSession(this.options.target, snapshot);
    this.current = session;
    return this.enqueue(session, async () => {
      if (bundle.extensionId !== session.initial.extensionId) {
        throw new Error('Extension view belongs to another package.');
      }
      const urls = assetUrls(bundle, this.options.origin);
      const modulePromise = (this.options.importModule ?? importModule)(
        urls.moduleUrl,
      );
      const stylePromise = Promise.all(
        urls.styleUrls.map((url) =>
          loadStyle(url, session.shadow, session.controller.signal),
        ),
      );
      const [candidate] = await Promise.all([modulePromise, stylePromise]);
      if (!this.active(session)) return;
      const module = requireModule(candidate);
      const mounted: unknown = await module.mount(
        session.target,
        this.context(session, session.initial),
      );
      session.mounted = requireMounted(mounted);
      if (!this.active(session)) await disposeMounted(session);
    });
  }

  update(snapshot: ExtensionViewSnapshot): Promise<void> {
    const session = this.current;
    if (!session) return Promise.resolve();
    const next = immutableSnapshot(snapshot);
    return this.enqueue(session, async () => {
      const initial = session.initial;
      if (
        next.extensionId !== initial.extensionId ||
        next.viewId !== initial.viewId ||
        next.runtimeRevision !== initial.runtimeRevision ||
        scopeKey(next.scope) !== scopeKey(initial.scope)
      ) {
        throw new Error('Changed view identity needs a new mount.');
      }
      setTheme(session.target, next.theme);
      await session.mounted?.update(this.context(session, next));
    });
  }

  clear(): Promise<void> {
    const session = this.current;
    this.current = null;
    if (!session) return Promise.resolve();
    session.controller.abort();
    session.root.remove();
    session.task = session.task
      .then(() => disposeMounted(session))
      .catch((error: unknown) => {
        this.options.reportFailure(error);
      });
    return session.task;
  }

  private active(session: ViewSession): boolean {
    return this.current === session && !session.controller.signal.aborted;
  }

  private context(
    session: ViewSession,
    snapshot: ExtensionViewSnapshot,
  ): ExtensionViewContext {
    return viewContext(
      snapshot,
      session.controller.signal,
      this.options.createClient,
    );
  }

  private enqueue(
    session: ViewSession,
    operation: () => Promise<void>,
  ): Promise<void> {
    session.task = session.task
      .then(async () => {
        if (this.active(session)) await operation();
      })
      .catch(async (error: unknown) => {
        if (this.active(session)) {
          session.controller.abort();
          session.target.replaceChildren();
          const fallback = session.target.ownerDocument.createElement('p');
          fallback.setAttribute('role', 'alert');
          fallback.textContent = 'Extension view is not available.';
          session.target.append(fallback);
          this.options.reportFailure(error);
        } else if (!(
          error instanceof DOMException && error.name === 'AbortError'
        )) {
          this.options.reportFailure(error);
        }
        await disposeMounted(session).catch((disposeError: unknown) => {
          this.options.reportFailure(disposeError);
        });
      });
    return session.task;
  }
}
