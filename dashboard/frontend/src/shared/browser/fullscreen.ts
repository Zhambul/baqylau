type WebKitDocument = Document & {
  readonly webkitFullscreenElement?: Element | null;
  webkitExitFullscreen?: () => Promise<void> | void;
};

type WebKitElement = HTMLElement & {
  webkitRequestFullscreen?: () => Promise<void> | void;
};

export function fullscreenAvailable(
  documentValue: Document = document,
): boolean {
  const root = documentValue.documentElement as WebKitElement;
  return (
    // Old WebKit can omit this method even though the DOM types require it.
    // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition
    root.requestFullscreen !== undefined ||
    root.webkitRequestFullscreen !== undefined
  );
}

export function fullscreenActive(documentValue: Document = document): boolean {
  const webkitDocument = documentValue as WebKitDocument;
  return Boolean(
    documentValue.fullscreenElement ?? webkitDocument.webkitFullscreenElement,
  );
}

export async function toggleFullscreen(
  documentValue: Document = document,
): Promise<void> {
  const webkitDocument = documentValue as WebKitDocument;
  const root = documentValue.documentElement as WebKitElement;
  if (fullscreenActive(documentValue)) {
    // Old WebKit can omit this method even though the DOM types require it.
    // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition
    if (documentValue.exitFullscreen !== undefined) {
      await documentValue.exitFullscreen();
      return;
    }
    await webkitDocument.webkitExitFullscreen?.();
    return;
  }
  // Old WebKit can omit this method even though the DOM types require it.
  // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition
  if (root.requestFullscreen !== undefined) {
    await root.requestFullscreen();
    return;
  }
  await root.webkitRequestFullscreen?.();
}
