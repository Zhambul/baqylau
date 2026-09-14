// Copyright (c) 2026 Zhambyl Yermagambet
// Attach only the session shown in this terminal to its Baqylau window.
import { terminalContract } from "./terminal_contract.mjs";

export default {
  id: "baqylau.terminal",
  setup(context) {
    const windowID = process.env.BAQYLAU_PTY_WINDOW_ID ?? process.env.KITTY_WINDOW_ID;
    if (!windowID) return;
    const terminal = context.client.rpc(terminalContract);
    const announce = (sessionID) => {
      if (!sessionID) return;
      const location = context.location ?? context.data.location.default();
      return terminal.attach({ sessionID, windowID, processID: process.pid }, { location }).catch((error) => {
        console.error("Baqylau terminal attachment failed", error.message);
      });
    };
    const removeSlot = context.ui.slot({
      append: "session.composer.top",
      render: ({ sessionID }) => {
        announce(sessionID);
        return null;
      },
    });
    // A server plugin can reload while the terminal stays on the same page.
    // The next turn supplies the terminal identity again.
    const removeStart = context.data.on("session.execution.started", (event) => {
      const route = context.ui.router.current();
      if (route.type === "session" && route.sessionID === event.data.sessionID) announce(route.sessionID);
    });
    return () => {
      removeStart();
      removeSlot();
    };
  },
};
