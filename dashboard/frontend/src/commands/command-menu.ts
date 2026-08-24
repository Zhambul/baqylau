import type { HarnessCatalog } from '../harnesses/model';

export type CommandOption = HarnessCatalog['commands'][number];

export const COMMAND_MENU_LIMIT = 30;

export function commandToken(value: string): string | null {
  if (!value.startsWith('/')) return null;
  const token = value.slice(1);
  return /\s/.test(token) ? null : token;
}

export function matchingCommands(
  commands: readonly CommandOption[],
  token: string,
): readonly CommandOption[] {
  const query = token.toLowerCase();
  const prefixes: CommandOption[] = [];
  const contains: CommandOption[] = [];
  for (const command of commands) {
    const index = command.command.toLowerCase().indexOf(query);
    if (index === 0) prefixes.push(command);
    else if (index > 0) contains.push(command);
  }
  return [...prefixes, ...contains].slice(0, COMMAND_MENU_LIMIT);
}

export function highlightedCommand(
  value: string,
  commands: readonly CommandOption[],
): string | null {
  const match = /^\/(\S+)(?=\s|$)/.exec(value);
  const name = match?.[1];
  if (name === undefined) return null;
  return commands.some((command) => command.command === name) ? name : null;
}
