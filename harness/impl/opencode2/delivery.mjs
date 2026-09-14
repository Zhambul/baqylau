// Copyright (c) 2026 Zhambyl Yermagambet
// Notify Baqylau after the log write. Do not block native event capture.
export function delivery(endpoint) {
  const pending = new Set();
  return {
    send(record, terminal) {
      if (!endpoint) return;
      const headers = {
        "Content-Type": "application/json",
        "X-Baqylau-Client-Process": String(terminal.processID),
      };
      const windowID = terminal.windowID;
      if (windowID) {
        headers["X-Baqylau-Terminal-Window"] = windowID;
      }
      const task = fetch(endpoint, {
        method: "POST",
        headers,
        body: JSON.stringify(record),
        signal: AbortSignal.timeout(1000),
      }).then(async (response) => {
        await response.arrayBuffer();
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
      }).catch((error) => {
        console.error("Baqylau event delivery failed; event log kept", error.message);
      }).finally(() => pending.delete(task));
      pending.add(task);
    },
    async close() {
      await Promise.all(pending);
    },
  };
}
