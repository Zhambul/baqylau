import { render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import FeedItem from './FeedItem.svelte';
import type { VisibleEntryPresentation } from './presentation';

const EDIT: VisibleEntryPresentation = {
  key: 'edit-one',
  group: 'files',
  summaryKind: 'file_edit',
  conversationKind: '',
  state: 'succeeded',
  linesAdded: 1,
  linesRemoved: 1,
  final: false,
  occurredAt: 1,
  turnId: 'turn-one',
  assignmentId: null,
  messageId: null,
  kind: 'file',
  action: 'updated',
  path: 'dashboard/frontend/src/app/App.svelte',
  body: {
    kind: 'diff',
    text: '@@ -1 +1 @@\n-const oldValue = false;\n+const newValue = true;',
    path: 'dashboard/frontend/src/app/App.svelte',
  },
};

function browser(state: 'succeeded' | 'failed'): VisibleEntryPresentation {
  return {
    ...EDIT,
    key: `browser-${state}`,
    group: 'commands',
    summaryKind: 'tool',
    state,
    kind: 'block',
    header: { kind: 'chip', chipKind: 'tool', label: 'Browser' },
    summary: 'Read browser tabs',
    body: {
      kind: 'content',
      content: {
        text: 'Tab Context:\n- Available tabs',
        mediaType: 'text/plain',
      },
    },
    note: false,
    quiet: true,
    finishedAt: 1,
    exitCode: null,
  };
}

describe('feed item', () => {
  it('shows tool outcome dots for success and failure', () => {
    const properties = {
      extraClass: '',
      defaultOpen: false,
      rewindModes: [],
      rewindOpen: false,
      onOpenRewind: undefined,
      onCancelRewind: undefined,
      onRewind: undefined,
    };
    const succeeded = render(FeedItem, {
      ...properties,
      presentation: browser('succeeded'),
    });
    const failed = render(FeedItem, {
      ...properties,
      presentation: browser('failed'),
    });

    expect(succeeded.container.querySelector('.blk')).toHaveAttribute(
      'data-out',
      'ok',
    );
    expect(succeeded.container.querySelector('.anmark')).toHaveAttribute(
      'aria-label',
      'succeeded',
    );
    expect(failed.container.querySelector('.blk')).toHaveAttribute(
      'data-out',
      'bad',
    );
    expect(failed.container.querySelector('.anmark')).toHaveAttribute(
      'aria-label',
      'failed',
    );
  });

  it('expands an Edit operation when its header is clicked', async () => {
    const user = userEvent.setup();
    const { container } = render(FeedItem, {
      presentation: EDIT,
      extraClass: '',
      defaultOpen: false,
      rewindModes: [],
      rewindOpen: false,
      onOpenRewind: undefined,
      onCancelRewind: undefined,
      onRewind: undefined,
    });
    const block = container.querySelector('.blk');

    expect(block).toHaveAttribute('data-open', '0');
    expect(screen.queryByText('newValue', { exact: false })).toBeNull();
    const header = container.querySelector('.bhead');
    expect(header).toHaveAttribute('role', 'button');
    if (!(header instanceof HTMLElement)) throw new Error('header is missing');
    await user.click(header);
    expect(block).toHaveAttribute('data-open', '1');
    expect(screen.getByText('newValue', { exact: false })).toBeVisible();
    expect(container.querySelector('.removed')).toHaveAttribute(
      'aria-label',
      'removed line 1',
    );
    expect(container.querySelector('.added')).toHaveAttribute(
      'aria-label',
      'added line 1',
    );
    expect(container.querySelector('.removed .dm')).toHaveTextContent('−');
    expect(container.querySelector('.added .dm')).toHaveTextContent('+');
    expect(container.querySelector('.added .token.keyword')).toHaveTextContent(
      'const',
    );
  });

  it('renders a closed body before it copies the body text', async () => {
    const user = userEvent.setup();
    const { container } = render(FeedItem, {
      presentation: EDIT,
      extraClass: '',
      defaultOpen: false,
      rewindModes: [],
      rewindOpen: false,
      onOpenRewind: undefined,
      onCancelRewind: undefined,
      onRewind: undefined,
    });
    const block = container.querySelector('.blk');

    expect(block).toHaveAttribute('data-open', '0');
    expect(container.querySelector('.bbody')).toBeNull();
    await user.click(screen.getByRole('button', { name: '⧉copy' }));

    expect(block).toHaveAttribute('data-open', '1');
    expect(container.querySelector('.bbody')).not.toBeNull();
  });
});
