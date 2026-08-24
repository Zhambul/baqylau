import { render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from './App.svelte';

vi.mock('../api/application', async () => {
  const actual =
    await vi.importActual<typeof import('../api/application')>(
      '../api/application',
    );
  return {
    ...actual,
    setGlobalNotifications: vi.fn().mockResolvedValue(undefined),
  };
});

describe('application shell', () => {
  beforeEach(() => {
    window.location.hash = '#/';
    vi.stubGlobal(
      'fetch',
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            void resolve;
          }),
      ),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('keeps the load-bearing shell controls and order', () => {
    render(App);

    expect(screen.getByRole('link', { name: 'baqylau' })).toHaveAttribute(
      'href',
      '#/',
    );
    expect(screen.getByRole('button', { name: '▦ stats' })).toBeVisible();
    expect(screen.getByRole('button', { name: '◉ alerts' })).toBeVisible();
    expect(screen.getByRole('button', { name: '+ session' })).toBeVisible();
    expect(document.querySelector('#accounts')).not.toBeNull();
    expect(document.querySelector('#attn')).not.toBeNull();
    expect(document.querySelector('#modal')).not.toBeNull();
    expect(document.querySelector('#toasts')).not.toBeNull();
  });

  it('navigates to stats and updates the view', async () => {
    const user = userEvent.setup();
    render(App);

    await user.click(screen.getByRole('button', { name: '▦ stats' }));

    expect(window.location.hash).toBe('#/stats');
    expect(screen.getByText('loading stats…')).toBeVisible();
  });

  it('toggles the global alerts presentation', async () => {
    const user = userEvent.setup();
    render(App);

    await user.click(screen.getByRole('button', { name: '◉ alerts' }));

    expect(screen.getByRole('button', { name: '○ alerts off' })).toHaveClass(
      'off',
    );
  });
});
