import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';

import type { ExtensionRuntime } from '../api/extensions';

import ShutdownEvidence from './ShutdownEvidence.svelte';

const record: NonNullable<ExtensionRuntime['last_shutdown']> = {
  record_id: 'shutdown-one',
  manager_id: 'manager-one',
  recorded_at: 1000,
  runtimes: [
    {
      runtime_revision: 'runtime-one',
      resources_closed: true,
      issues: [
        {
          runtime_revision: 'runtime-one',
          extension_id: 'test.sample',
          reason: 'unresolved_jobs',
          pending_job_ids: ['external-write'],
        },
      ],
    },
  ],
};

describe('shutdown evidence', () => {
  it('keeps unresolved jobs separate from physical resource closure', () => {
    render(ShutdownEvidence, { record });
    expect(screen.getByText('Worker resources: closed')).toBeInTheDocument();
    expect(
      screen.getByText('Unresolved jobs: external-write'),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/does not mean that an external job completed/),
    ).toBeInTheDocument();
  });

  it('does not report resource closure after an unsuccessful close', () => {
    render(ShutdownEvidence, {
      record: {
        ...record,
        runtimes: [
          {
            runtime_revision: 'runtime-one',
            resources_closed: false,
            issues: [],
          },
        ],
      },
    });
    expect(
      screen.getByText('Worker resources: closure not confirmed'),
    ).toBeInTheDocument();
  });
});
