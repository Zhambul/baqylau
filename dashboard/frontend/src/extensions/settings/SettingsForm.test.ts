import { render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import SettingsForm from './SettingsForm.svelte';

const schema = {
  type: 'object',
  properties: {
    label: { type: 'string', title: 'Label' },
    limit: { type: 'integer', title: 'Limit', minimum: 1 },
    level: { type: 'string', title: 'Level', enum: ['low', 'high'] },
    enabled: { type: 'boolean', title: 'Enabled' },
    tags: { type: 'array', title: 'Tags', items: { type: 'string' } },
  },
  required: ['label', 'limit'],
};
const value = {
  label: 'First',
  limit: 3,
  level: 'low',
  enabled: true,
  tags: ['one'],
};

describe('settings form', () => {
  it('shows each supported field kind and saves the edited values', async () => {
    const onsave = vi.fn();
    render(SettingsForm, { schema, value, disabled: false, onsave });

    const label = screen.getByLabelText(/Label/);
    await userEvent.clear(label);
    await userEvent.type(label, 'Second');
    expect(screen.getByLabelText(/Limit/)).toHaveValue(3);
    expect(screen.getByLabelText(/Level/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Enabled/)).toBeChecked();
    expect(screen.getByDisplayValue('one')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Submit' }));

    expect(onsave).toHaveBeenCalledWith({ ...value, label: 'Second' });
  });

  it('does not save a value that the schema refuses', async () => {
    const onsave = vi.fn();
    render(SettingsForm, { schema, value, disabled: false, onsave });

    const limit = screen.getByLabelText(/Limit/);
    await userEvent.clear(limit);
    await userEvent.type(limit, '0');
    await userEvent.click(screen.getByRole('button', { name: 'Submit' }));

    expect(onsave).not.toHaveBeenCalled();
  });
});
