import { describe, expect, it } from 'vitest';

import type { components } from '../generated/schema';
import { translateHarness } from './harnesses';

type WireHarness = components['schemas']['HarnessDescriptionResponse'];

function wireHarness(controlNames: string[]): WireHarness {
  return {
    name: 'test-harness',
    display_name: 'Test harness',
    launchable: true,
    default_for_launch: false,
    supports_attachments: true,
    control_names: controlNames,
    supports_accounts: false,
    supports_terminal_input: true,
    requires_initial_message: false,
  };
}

describe('harness translator', () => {
  it('denies every action when the server declares no controls', () => {
    expect(translateHarness(wireHarness([])).capabilities).toEqual({
      send: false,
      interrupt: false,
      background: false,
      close: false,
      rename: false,
      autoname: false,
      rewind: false,
      compact: false,
      model: false,
      effort: false,
      answer: false,
      plan: false,
    });
  });

  it('maps each server control to only its matching action', () => {
    expect(
      translateHarness(
        wireHarness([
          'send_text',
          'interrupt',
          'background',
          'close_session',
          'rename_session',
          'auto_name_session',
          'apply_rewind',
          'compact',
          'select_model',
          'select_effort',
          'answer_question',
          'decide_plan',
        ]),
      ).capabilities,
    ).toEqual({
      send: true,
      interrupt: true,
      background: true,
      close: true,
      rename: true,
      autoname: true,
      rewind: true,
      compact: true,
      model: true,
      effort: true,
      answer: true,
      plan: true,
    });
  });
});
