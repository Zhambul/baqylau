// Copyright (c) 2026 Zhambyl Yermagambet
// The host loads this module by its digest and mounts it in the workspace page.
export function mount(target, context) {
  const line = target.ownerDocument.createElement('p');
  line.textContent = `Hello from ${context.extensionId}`;
  target.append(line);
  return {
    update() {},
    dispose() {
      line.remove();
    },
  };
}
