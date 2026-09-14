// Copyright (c) 2026 Zhambyl Yermagambet
// Keep terminal attachment separate from the shared server process.
import { randomUUID } from "node:crypto";
import { terminalContract } from "./terminal_contract.mjs";

export async function registerTerminal(context, save, notification) {
  const terminals = new Map();
  const registration = await context.rpc.register(terminalContract, {
    attach: async (terminal) => {
      const session = await context.session.get({ sessionID: terminal.sessionID });
      if (session.parentID || session.location.directory !== context.location.directory) return {};
      terminals.set(session.id, terminal);
      const record = {
        event: {
          id: `baqylau:${randomUUID()}`,
          type: "baqylau.terminal.attached",
          created: Date.now(),
          data: { sessionID: session.id },
        },
        session,
        root_id: session.id,
      };
      await save(record);
      notification.send(record, terminal);
      return {};
    },
  });
  return {
    send(record) {
      const terminal = terminals.get(record.root_id);
      if (terminal) notification.send(record, terminal);
    },
    close: () => registration.dispose(),
  };
}
