import { render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { dismissGoal } from '../../api/session-preferences';
import { translateSession } from '../../api/translators/session-data';
import { wireSession } from '../../test/session-fixture';
import type { Session } from '../model';
import GoalTasks from './GoalTasks.svelte';

vi.mock('../../api/session-preferences', () => ({ dismissGoal: vi.fn() }));

function completedGoal(objective = 'Ship it'): Session {
  return {
    ...translateSession(wireSession('session-one')),
    goal: { objective, state: 'completed', completed: true, reason: null },
  };
}

async function confirmDismissal(): Promise<void> {
  await userEvent.click(
    screen.getByRole('button', { name: 'Dismiss completed goal' }),
  );
  expect(screen.getByText('Ship it')).toBeInTheDocument();
  await userEvent.click(
    screen.getByRole('button', { name: 'Confirm dismiss completed goal' }),
  );
}

describe('completed goal dismissal', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(dismissGoal).mockResolvedValue();
  });

  it('uses the backend state instead of browser storage', async () => {
    const view = render(GoalTasks, { session: completedGoal() });
    await confirmDismissal();
    expect(dismissGoal).toHaveBeenCalledWith('session-one', 'Ship it');
    expect(screen.getByText('Ship it')).toBeInTheDocument();
    await view.rerender({ session: completedGoal(), hidden: true });
    expect(screen.queryByText('Ship it')).not.toBeInTheDocument();
    expect(screen.getByText('Rewrite')).toBeInTheDocument();
  });

  it('shows a resumed goal before the preference update arrives', () => {
    render(GoalTasks, {
      session: translateSession(wireSession()),
      hidden: true,
    });
    expect(screen.getByText('Ship it')).toBeInTheDocument();
  });

  it('keeps the goal visible after a failed save and permits a retry', async () => {
    vi.mocked(dismissGoal).mockRejectedValueOnce(new Error('offline'));
    render(GoalTasks, { session: completedGoal() });
    await confirmDismissal();
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Could not hide the goal',
    );
    await confirmDismissal();
    expect(dismissGoal).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('disables the button while the save is pending', async () => {
    let finish = (): void => {
      throw new Error('The save has not started');
    };
    vi.mocked(dismissGoal).mockReturnValue(
      new Promise<void>((resolve) => {
        finish = resolve;
      }),
    );
    render(GoalTasks, { session: completedGoal() });
    await confirmDismissal();
    expect(
      screen.getByRole('button', { name: 'Dismiss completed goal' }),
    ).toBeDisabled();
    finish();
  });

  it('cancels confirmation with Escape', async () => {
    render(GoalTasks, { session: completedGoal() });
    await userEvent.click(
      screen.getByRole('button', { name: 'Dismiss completed goal' }),
    );
    await userEvent.keyboard('{Escape}');
    expect(
      screen.getByRole('button', { name: 'Dismiss completed goal' }),
    ).toHaveTextContent('✕');
    expect(dismissGoal).not.toHaveBeenCalled();
  });

  it('does not carry confirmation to a changed goal', async () => {
    const view = render(GoalTasks, { session: completedGoal() });
    await userEvent.click(
      screen.getByRole('button', { name: 'Dismiss completed goal' }),
    );
    await view.rerender({ session: completedGoal('Next goal') });
    expect(
      screen.getByRole('button', { name: 'Dismiss completed goal' }),
    ).toHaveTextContent('✕');
  });
});
