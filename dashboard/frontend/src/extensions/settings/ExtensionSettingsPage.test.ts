import { render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import * as settingsApi from '../../api/extension-settings';
import * as extensionsApi from '../../api/extensions';

import ExtensionSettingsPage from './ExtensionSettingsPage.svelte';

vi.mock('../../api/extension-settings', async (original) => ({
  ...(await original<typeof settingsApi>()),
  readExtensionSettings: vi.fn(),
  saveExtensionSettings: vi.fn(),
  readExtensionSecrets: vi.fn(),
}));
vi.mock('../../api/extensions', () => ({ readExtensionOperation: vi.fn() }));

const OWNER = 'test.settings';
const SCHEMA_REF = {
  owner: OWNER,
  name: 'settings',
  version: 1,
  digest: 'b'.repeat(64),
};
const SCHEMA = {
  type: 'object',
  properties: { label: { type: 'string', title: 'Label' } },
  required: ['label'],
};

function reply(
  override: string | null,
  revision = 4,
): settingsApi.ExtensionSettings {
  const effective = override ?? '{"label":"Default"}';
  return {
    read_only: false,
    settings: {
      extension_info: {
        extension_id: OWNER,
        package_version: '1.0.0',
        api_version: '1',
        package_digest: 'c'.repeat(64),
      },
      scope: { kind: 'installation' },
      lifecycle_revision: 7,
      catalog_revision: 2,
      settings_revision: revision,
      selected_from_committed: true,
      pending_operation: null,
      definition: {
        defaults: { schema_ref: SCHEMA_REF, json_text: '{"label":"Default"}' },
        scopes: ['installation', 'workspace'],
        secret_references: [],
      },
      schemas: [{ reference: SCHEMA_REF, json_text: JSON.stringify(SCHEMA) }],
      override:
        override === null
          ? null
          : { schema_ref: SCHEMA_REF, json_text: override },
      effective: { schema_ref: SCHEMA_REF, json_text: effective },
    },
  };
}

function operation(
  status: settingsApi.ExtensionOperation['status'],
  failure: settingsApi.ExtensionOperation['failure'] = null,
): settingsApi.ExtensionOperation {
  return {
    operation_id: 'operation-1',
    kind: 'settings',
    extension_id: OWNER,
    request_id: 'request-1',
    accepted_revision: 8,
    runtime_revision: 'runtime-2',
    status,
    created_at: 1,
    updated_at: 2,
    failure,
  };
}

function renderPage(): void {
  render(ExtensionSettingsPage, {
    route: { kind: 'extension-settings', extensionId: OWNER },
  });
}

describe('extension settings page', () => {
  beforeEach(() => {
    vi.mocked(settingsApi.readExtensionSecrets).mockResolvedValue({
      secrets: [],
      read_only: false,
    });
    vi.mocked(extensionsApi.readExtensionOperation).mockResolvedValue(
      operation('succeeded'),
    );
  });

  it('saves a complete document at the read revision and reports the effect', async () => {
    vi.mocked(settingsApi.readExtensionSettings)
      .mockResolvedValueOnce(reply(null))
      .mockResolvedValue(reply('{"label":"Mine"}', 5));
    vi.mocked(settingsApi.saveExtensionSettings).mockResolvedValue(
      operation('succeeded'),
    );
    renderPage();

    const label = await screen.findByLabelText(/Label/);
    expect(screen.getByText(/uses the inherited values/)).toBeInTheDocument();
    await userEvent.clear(label);
    await userEvent.type(label, 'Mine');
    await userEvent.click(screen.getByRole('button', { name: 'Submit' }));

    expect(settingsApi.saveExtensionSettings).toHaveBeenCalledWith(
      OWNER,
      { kind: 'installation' },
      { schema_ref: SCHEMA_REF, json_text: '{"label":"Mine"}' },
      4,
      expect.any(AbortSignal),
    );
    expect(
      await screen.findByText(/Recorded history does not change/),
    ).toBeInTheDocument();
    expect(screen.getByText(/has its own values/)).toBeInTheDocument();
  });

  it('resets the override with an explicit null', async () => {
    vi.mocked(settingsApi.readExtensionSettings).mockResolvedValue(
      reply('{"label":"Mine"}'),
    );
    vi.mocked(settingsApi.saveExtensionSettings).mockResolvedValue(
      operation('succeeded'),
    );
    renderPage();

    await userEvent.click(
      await screen.findByRole('button', { name: 'Reset to inherited values' }),
    );

    expect(settingsApi.saveExtensionSettings).toHaveBeenCalledWith(
      OWNER,
      { kind: 'installation' },
      null,
      4,
      expect.any(AbortSignal),
    );
  });

  it('asks for a refresh when another write came first', async () => {
    vi.mocked(settingsApi.readExtensionSettings).mockResolvedValue(reply(null));
    vi.mocked(settingsApi.saveExtensionSettings).mockRejectedValue(
      new settingsApi.StaleSettingsError(),
    );
    renderPage();

    await screen.findByLabelText(/Label/);
    await userEvent.click(screen.getByRole('button', { name: 'Submit' }));

    expect(
      await screen.findByText(/changed after they were read/),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeInTheDocument();
  });

  it('keeps the previous values when the change fails', async () => {
    vi.mocked(settingsApi.readExtensionSettings).mockResolvedValue(reply(null));
    vi.mocked(settingsApi.saveExtensionSettings).mockResolvedValue(
      operation('failed', {
        code: 'preparation_failed',
        detail: 'migration failed',
      }),
    );
    renderPage();

    await screen.findByLabelText(/Label/);
    await userEvent.click(screen.getByRole('button', { name: 'Submit' }));

    expect(await screen.findByText(/migration failed/)).toBeInTheDocument();
    expect(
      screen.getByText(/previous settings stay active/),
    ).toBeInTheDocument();
  });
});
