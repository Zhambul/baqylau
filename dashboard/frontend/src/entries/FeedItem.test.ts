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
  body: { kind: 'diff', text: '@@ -1 +1 @@\n-old\n+new' },
};

describe('feed item', () => {
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
    const header = container.querySelector('.bhead');
    expect(header).toHaveAttribute('role', 'button');
    if (!(header instanceof HTMLElement)) throw new Error('header is missing');
    await user.click(header);
    expect(block).toHaveAttribute('data-open', '1');
    expect(screen.getByText('new')).toBeVisible();
  });
});
