import type {
  HarnessCatalog,
  HarnessDescription,
  SessionCapabilities,
} from '../../harnesses/model';
import type { components } from '../generated/schema';

type Schemas = components['schemas'];

function capabilities(controlNames: readonly string[]): SessionCapabilities {
  const controls = new Set(controlNames);
  return {
    send: controls.has('send_text'),
    interrupt: controls.has('interrupt'),
    background: controls.has('background'),
    close: controls.has('close_session'),
    rename: controls.has('rename_session'),
    autoname: controls.has('auto_name_session'),
    rewind: controls.has('apply_rewind'),
    compact: controls.has('compact'),
    model: controls.has('select_model'),
    effort: controls.has('select_effort'),
    answer: controls.has('answer_question'),
    plan: controls.has('decide_plan'),
  };
}

export function translateHarness(
  wire: Schemas['HarnessDescriptionResponse'],
): HarnessDescription {
  return {
    name: wire.name,
    displayName: wire.display_name,
    launchable: wire.launchable,
    defaultForLaunch: wire.default_for_launch,
    supportsAttachments: wire.supports_attachments,
    supportsAccounts: wire.supports_accounts,
    supportsTerminalInput: wire.supports_terminal_input,
    requiresInitialMessage: wire.requires_initial_message,
    capabilities: capabilities(wire.control_names),
  };
}

export function translateCatalog(
  wire: Schemas['HarnessCatalogResponse'],
): HarnessCatalog {
  return {
    commands: wire.commands.map((command) => ({
      command: command.command,
      description: command.description,
      minimumPromptCount: command.minimum_prompt_count,
    })),
    models: wire.models.map((model) => ({
      modelId: model.model_id,
      displayName: model.display_name,
      default: model.default,
      efforts: model.efforts.map((effort) => ({
        value: effort.value,
        displayName: effort.display_name,
        default: effort.default,
      })),
    })),
    rewindModes: wire.rewind_modes.map((mode) => ({
      value: mode.value,
      displayName: mode.display_name,
    })),
  };
}
