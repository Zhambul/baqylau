// Copyright (c) 2026 Zhambyl Yermagambet
// Keep native events in a local log before Baqylau reads them.
import { appendFile, mkdir } from "node:fs/promises";
import { join } from "node:path";
import { homedir } from "node:os";
import { delivery } from "./delivery.mjs";
import { shellCapture } from "./native_shells.mjs";
import { contextCapture } from "./native_context.mjs";
import { registerUsage } from "./native_usage.mjs";
import { registerTerminal } from "./native_terminal.mjs";
import { registerAttachments } from "./native_attachments.mjs";

export default {
  id: "baqylau",
  async setup(context) {
    const usage = await registerUsage(context);
    const attachments = await registerAttachments(context);
    const dataDirectory = process.env.BAQYLAU_DATA_DIR ?? join(homedir(), ".local/share/baqylau");
    const directory = context.options.logDirectory ?? process.env.BAQYLAU_OPENCODE_LOG_DIR ?? join(dataDirectory, "opencode2");
    await mkdir(directory, { recursive: true, mode: 0o700 });
    const abort = new AbortController();
    const port = process.env.BAQYLAU_DASHBOARD_PORT ?? "8377";
    const notification = delivery(context.options.endpoint ?? `http://127.0.0.1:${port}/api/harnesses/opencode2/hooks`);
    const sessions = new Map();
    const turns = new Map();
    const owners = new Map();
    const inbox = new Map();
    const messages = new Map();
    const results = new Map();
    const tools = new Map();
    const shells = shellCapture();
    const stepContext = contextCapture(context);
    const save = (record) => appendFile(join(directory, `${record.root_id}.jsonl`), `${JSON.stringify({ ...record, server_process_id: process.pid })}\n`, { mode: 0o600 });
    const terminal = await registerTerminal(context, save, notification);
    const consume = async () => {
      for await (const event of context.event.subscribe({ signal: abort.signal })) {
        const completed = shells.observe(event);
        if (completed) await save(completed);
        const sessionID = event.data?.sessionID;
        if (!sessionID || !/^ses_[a-zA-Z0-9]+$/.test(sessionID)) continue;
        if (!sessions.has(sessionID)) {
          const known = await context.session.get({ sessionID });
          sessions.set(sessionID, known);
          // A child that has no turn of its own belongs to the turn that asked
          // for its work. A background child outlives that turn, so the turn
          // running in the root cannot answer for it: by then the root already
          // runs the notification turn.
          if (known.parentID) owners.set(sessionID, turns.get(known.parentID)?.id ?? owners.get(known.parentID) ?? null);
        }
        let root = sessions.get(sessionID);
        while (root.parentID) {
          if (!sessions.has(root.parentID)) sessions.set(root.parentID, await context.session.get({ sessionID: root.parentID }));
          root = sessions.get(root.parentID);
        }
        // A shared server loads one plugin instance for each location. Its
        // event stream includes other locations, which have their own writer.
        if (root.location.directory !== context.location.directory) continue;
        if (event.type === "session.execution.started") turns.set(sessionID, { id: event.id, at: event.created });
        const toolKey = `${sessionID}:${event.data.id}`;
        if (event.type === "session.tool.input.started") tools.set(toolKey, { name: event.data.name });
        if (event.type === "session.tool.called" && tools.has(toolKey)) tools.get(toolKey).input = event.data.input;
        if (event.type === "session.inbox.enqueued") inbox.set(event.data.inboxID, {
          item: event.data.item,
          at: event.created,
          prompt: attachments.originalText(event.data.item?.payload?.text),
        });
        if (event.type === "session.text.ended") {
          const previous = messages.get(event.data.assistantMessageID) ?? "";
          messages.set(event.data.assistantMessageID, previous + event.data.text);
        }
        if (event.type === "session.step.ended") results.set(sessionID, messages.get(event.data.assistantMessageID) ?? null);
        // A native notice wakes the root without a prompt. It is kept apart from
        // the text a person sends, and only a notice that a turn BEGAN FOR is
        // kept at all: a finished background child announces itself in a turn
        // that still runs too, and that notice starts nothing.
        const delivered = event.type === "session.inbox.delivered" ? inbox.get(event.data.inboxID) : undefined;
        const woke = delivered !== undefined && (turns.get(sessionID)?.at ?? 0) > delivered.at;
        const record = {
          event,
          session: sessions.get(sessionID),
          root_id: root.id,
          // The work of a child belongs to the turn that asked for it, so a
          // child keeps the turn of its parent and not a turn of its own.
          turn_id: owners.get(sessionID) ?? turns.get(root.id)?.id ?? null,
          prompt: delivered?.item?.type === "user" ? delivered.prompt ?? null : null,
          attachment_names: (delivered?.item?.payload?.files ?? []).map((file) => file.name ?? "attachment"),
          notice: woke && delivered.item?.type !== "user" ? delivered.item?.payload?.text ?? null : null,
          message: event.type === "session.step.ended" || event.type.startsWith("session.execution.") ? results.get(sessionID) ?? null : null,
          tool: tools.get(toolKey) ?? null,
          context: await stepContext(event),
        };
        const earlyCompletion = shells.bind(record);
        await save(record);
        if (earlyCompletion) await save(earlyCompletion);
        if (sessionID === root.id && (event.type === "session.execution.started" ||
            ["session.execution.succeeded", "session.execution.failed", "session.execution.interrupted"].includes(event.type))) {
          terminal.send(record);
        }
        if (event.type === "session.inbox.delivered") inbox.delete(event.data.inboxID);
        if (event.type === "session.step.ended") messages.delete(event.data.assistantMessageID);
        if (["session.tool.success", "session.tool.failed"].includes(event.type)) tools.delete(toolKey);
        if (["session.execution.succeeded", "session.execution.failed", "session.execution.interrupted"].includes(event.type)) {
          sessions.delete(sessionID);
          turns.delete(sessionID);
          owners.delete(sessionID);
          results.delete(sessionID);
        }
      }
    };
    const task = consume();
    task.catch((error) => {
      if (!abort.signal.aborted) console.error("Baqylau event log failed", error);
    });
    return async () => {
      abort.abort();
      await task.catch(() => {});
      await notification.close();
      await usage.dispose();
      await attachments.dispose();
      await terminal.close();
    };
  },
};
