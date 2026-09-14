// Copyright (c) 2026 Zhambyl Yermagambet
// Keep the native shell identity linked to its original tool call and turn.
export function shellCapture() {
  const shells = new Map();
  const finish = (entry) => {
    if (!entry?.exit || !entry.record) return null;
    if (!["session.tool.success", "session.tool.failed"].includes(entry.record.event.type)) return null;
    shells.delete(entry.info.id);
    if (!entry.record.tool?.input?.background && entry.record.event.data.metadata?.status !== "running") return null;
    return {
      ...entry.record,
      event: entry.exit,
      shell: { ...entry.record.shell, status: entry.exit.data.status, exit: entry.exit.data.exit },
    };
  };
  return {
    observe(event) {
      if (event.type === "shell.created") shells.set(event.data.info.id, { info: event.data.info });
      if (event.type !== "shell.exited") return null;
      const entry = shells.get(event.data.id);
      if (!entry) return null;
      entry.exit = event;
      return finish(entry);
    },
    bind(record) {
      const entry = shells.get(record.event.data.metadata?.shellID);
      if (!entry) return null;
      record.shell = { ...entry.info, call_id: record.event.data.id };
      entry.record = record;
      return finish(entry);
    },
  };
}
