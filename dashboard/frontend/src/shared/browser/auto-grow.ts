import type { Action } from 'svelte/action';

function resizeTextArea(textarea: HTMLTextAreaElement): void {
  textarea.style.height = 'auto';
  textarea.style.height = `${String(textarea.scrollHeight)}px`;
}

export const autoGrow: Action<HTMLTextAreaElement, string> = (textarea) => {
  function resize(): void {
    resizeTextArea(textarea);
  }

  let width = textarea.clientWidth;
  const observer =
    typeof ResizeObserver === 'function'
      ? new ResizeObserver(() => {
          const nextWidth = textarea.clientWidth;
          if (nextWidth === width) return;
          width = nextWidth;
          resize();
        })
      : null;

  textarea.addEventListener('input', resize);
  observer?.observe(textarea);
  resize();

  return {
    update: resize,
    destroy(): void {
      textarea.removeEventListener('input', resize);
      observer?.disconnect();
    },
  };
};
