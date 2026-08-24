import { describe, expect, it } from 'vitest';

import type { CommandOption } from './command-menu';
import {
  COMMAND_MENU_LIMIT,
  commandToken,
  highlightedCommand,
  matchingCommands,
} from './command-menu';

function command(name: string): CommandOption {
  return {
    command: name,
    description: `${name} description`,
    minimumPromptCount: 0,
  };
}

describe('commandToken', () => {
  it('accepts only the leading command token before arguments', () => {
    expect(commandToken('/')).toBe('');
    expect(commandToken('/commit')).toBe('commit');
    expect(commandToken(' /commit')).toBeNull();
    expect(commandToken('/commit now')).toBeNull();
  });
});

describe('matchingCommands', () => {
  it('ranks prefixes before contains and keeps server order in each group', () => {
    const commands = [
      command('gh:commit'),
      command('commit'),
      command('audit-commit'),
      command('commit-push'),
    ];

    expect(
      matchingCommands(commands, 'commit').map((row) => row.command),
    ).toEqual(['commit', 'commit-push', 'gh:commit', 'audit-commit']);
  });

  it('matches case-insensitively and caps the visible catalog', () => {
    const commands = Array.from({ length: 40 }, (_, index) =>
      command(`Command-${String(index)}`),
    );

    expect(matchingCommands(commands, 'command')).toHaveLength(
      COMMAND_MENU_LIMIT,
    );
  });
});

describe('highlightedCommand', () => {
  it('recognizes only complete command names from the current catalog', () => {
    const commands = [command('compact')];

    expect(highlightedCommand('/compact details', commands)).toBe('compact');
    expect(highlightedCommand('/comp', commands)).toBeNull();
    expect(highlightedCommand('say /compact', commands)).toBeNull();
  });
});
