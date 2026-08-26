import { render, screen, waitFor } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { readSessionList } from '../api/session-data';
import { translateSessionSnapshot } from '../api/translators/session-data';
import type { ActorStatus, SessionSnapshot } from '../sessions/model';
import { wireActor, wireSession, wireSnapshot } from '../test/session-fixture';
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

vi.mock('../api/session-data', async () => {
  const actual = await vi.importActual<typeof import('../api/session-data')>(
    '../api/session-data',
  );
  return { ...actual, readSessionList: vi.fn() };
});

function session(
  id: string,
  title: string,
  status: ActorStatus,
): SessionSnapshot {
  return translateSessionSnapshot({
    ...wireSnapshot(id),
    session: { ...wireSession(id), title },
    actors: [{ ...wireActor(id), status }],
  });
}

describe('application shell', () => {
  beforeEach(() => {
    window.location.hash = '#/';
    vi.mocked(readSessionList).mockResolvedValue({ cursor: 42, sessions: [] });
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

  it('keeps live session pills in session-list order', async () => {
    vi.mocked(readSessionList).mockResolvedValue({
      cursor: 42,
      sessions: [
        session('session-working', 'First session', 'working'),
        session('session-asking', 'Second session', 'awaiting_attention'),
      ],
    });
    render(App);

    await waitFor(() => {
      expect(
        [...document.querySelectorAll('.attn-pill')].map((pill) =>
          pill.textContent.trim(),
        ),
      ).toEqual(['First session', 'Second session']);
    });
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
