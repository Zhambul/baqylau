import type { ExtensionViewSnapshot, ThemeValues } from '../view.js';
import { immutableSnapshot } from './context.js';
import type { ViewSession } from './types.js';

export function createSession(
  target: HTMLElement,
  source: ExtensionViewSnapshot,
): ViewSession {
  const initial = immutableSnapshot(source);
  const root = target.ownerDocument.createElement('div');
  root.dataset.extensionView = initial.viewId;
  const shadow = root.attachShadow({ mode: 'open' });
  const mountTarget = target.ownerDocument.createElement('div');
  shadow.append(mountTarget);
  target.append(root);
  setTheme(mountTarget, initial.theme);
  return {
    root,
    shadow,
    target: mountTarget,
    initial,
    controller: new AbortController(),
    task: Promise.resolve(),
    mounted: null,
  };
}

export function setTheme(target: HTMLElement, theme: ThemeValues): void {
  target.style.backgroundColor = theme.background;
  target.style.color = theme.foreground;
  target.style.fontFamily = theme.fontFamily;
  target.style.fontSize = theme.fontSize;
  target.style.setProperty('--baqylau-muted', theme.muted);
  target.style.setProperty('--baqylau-accent', theme.accent);
  target.dataset.theme = theme.mode;
}

export async function disposeMounted(session: ViewSession): Promise<void> {
  const mounted = session.mounted;
  session.mounted = null;
  await mounted?.dispose();
}
