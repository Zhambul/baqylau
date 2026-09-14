// Copyright (c) 2026 Zhambyl Yermagambet
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import plugin from "../../harness/impl/opencode2/native.mjs";
import { shellCapture } from "../../harness/impl/opencode2/native_shells.mjs";
import { contextCapture } from "../../harness/impl/opencode2/native_context.mjs";
import { registerAttachments } from "../../harness/impl/opencode2/native_attachments.mjs";
import { registerUsage } from "../../harness/impl/opencode2/native_usage.mjs";

test("model discovery keeps all enabled Go models and native variants", async () => {
  const models = [
    { id: "high-only", name: "High only", providerID: "opencode-go", enabled: true, variants: [{ id: "high" }] },
    { id: "no-effort", name: "No effort", providerID: "opencode-go", enabled: true, variants: [] },
    { id: "disabled", providerID: "opencode-go", enabled: false },
    { id: "other-provider", providerID: "other", enabled: true },
  ];
  let handlers;
  await registerUsage({
    rpc: { register: async (_contract, methods) => { handlers = methods; } },
    catalog: { model: { list: async () => ({ data: models }) } },
  });
  assert.deepEqual(await handlers.models(), { models: [
    { id: "high-only", name: "High only", variants: [{ id: "high" }] },
    { id: "no-effort", name: "No effort", variants: [] },
  ] });
});

test("text admission keeps the prompt and sends file content once", async (context) => {
  const directory = await mkdtemp(join(tmpdir(), "baqylau-attachment-"));
  context.after(() => rm(directory, { recursive: true }));
  const path = join(directory, "context.txt");
  await writeFile(path, "The calibration code is 731.");
  const hooks = new Map();
  const attachments = await registerAttachments({session: {
    hook: async (name, callback) => { hooks.set(name, callback); return {dispose: async () => {}}; },
  }});
  context.after(() => attachments.dispose());
  const text = "Read the attached file.";
  const input = {text, files: [{uri: new URL(`file://${path}`).href, name: "context.txt", mediaType: "text/plain"}]};
  const event = {prompt: {text: `/baqylau-attach ${Buffer.from(JSON.stringify(input)).toString("base64")}`}};
  const repeated = structuredClone(event);
  await hooks.get("prompt")(event);
  await hooks.get("prompt")(repeated);
  assert.ok(event.prompt.text.includes("The calibration code is 731."));
  assert.equal(attachments.originalText(event.prompt.text), text);
  assert.equal(attachments.originalText(repeated.prompt.text), text);
  assert.equal(event.prompt.files[0].name, "context.txt");
  const renderedFile = "\n\nAttached file: context.txt\n\nThe calibration code is 731.";
  const messages = [{role: "user", content: [
    {type: "text", text: event.prompt.text},
    {type: "text", text: renderedFile, metadata: {attachment: {name: "context.txt"}}},
  ]}];
  hooks.get("context")({messages});
  assert.equal(messages[0].content.length, 1);
  assert.equal(messages[0].content[0].text.match(/The calibration code is 731\./g).length, 1);
});

test("slow hook delivery does not delay event capture", async (context) => {
  const directory = await mkdtemp(join(tmpdir(), "baqylau-native-test-"));
  context.after(() => rm(directory, { recursive: true }));
  const records = (await readFile(new URL("../e2e/fixtures/audit_opencode2_greeting.jsonl", import.meta.url), "utf8"))
    .trim().replaceAll("session-one", "ses_nativeE2E").split("\n").map(JSON.parse);
  const replies = [];
  context.mock.method(globalThis, "fetch", () => new Promise((resolve) => replies.push(resolve)));
  const consumed = Promise.withResolvers();
  const close = await plugin.setup({
    location: records[0].session.location,
    rpc: {
      register: async (contract, handlers) => {
        if (contract.id === "baqylau.terminal") {
          await handlers.attach({ sessionID: "ses_nativeE2E", windowID: "731", processID: 732 });
        }
        return { dispose: async () => {} };
      },
    },
    options: { logDirectory: directory, endpoint: "http://127.0.0.1:1/test" },
    session: { hook: async () => ({ dispose: async () => {} }), get: async () => records[0].session },
    catalog: { model: { list: async () => ({ data: [] }) } },
    event: {
      async *subscribe() {
        for (const record of records) yield record.event;
        consumed.resolve();
      },
    },
  });
  await consumed.promise;
  const saved = (await readFile(join(directory, "ses_nativeE2E.jsonl"), "utf8"))
    .trim().split("\n").map(JSON.parse);
  assert.equal(saved.length, records.length + 1);
  assert.equal(saved.find((record) => record.message)?.message, "Hi");
  assert.equal(saved.at(-1).event.type, "session.execution.succeeded");
  assert.equal(replies.length, 3);
  assert.equal(globalThis.fetch.mock.calls[0].arguments[1].headers["X-Baqylau-Client-Process"], "732");
  assert.equal(globalThis.fetch.mock.calls[0].arguments[1].headers["X-Baqylau-Terminal-Window"], "731");
  for (const reply of replies) reply(new Response(""));
  await close();
});

test("a child keeps the turn that asked for its work", async (context) => {
  const directory = await mkdtemp(join(tmpdir(), "baqylau-native-child-"));
  context.after(() => rm(directory, { recursive: true }));
  const sessions = {
    ses_parent: { id: "ses_parent", location: { directory: "/projects/one" } },
    ses_child: { id: "ses_child", parentID: "ses_parent", location: { directory: "/projects/one" } },
  };
  const events = [
    { id: "evt_parent_turn", type: "session.execution.started", data: { sessionID: "ses_parent" } },
    { id: "evt_child_turn", type: "session.execution.started", data: { sessionID: "ses_child" } },
    { id: "evt_parent_end", type: "session.execution.succeeded", data: { sessionID: "ses_parent" } },
    { id: "evt_notice_turn", type: "session.execution.started", data: { sessionID: "ses_parent" } },
    { id: "evt_child_step", type: "session.step.ended", data: { sessionID: "ses_child" } },
  ];
  context.mock.method(globalThis, "fetch", async () => new Response(""));
  const consumed = Promise.withResolvers();
  const close = await plugin.setup({
    location: { directory: "/projects/one" },
    rpc: { register: async () => ({ dispose: async () => {} }) },
    options: { logDirectory: directory, endpoint: "http://127.0.0.1:1/test" },
    session: { hook: async () => ({ dispose: async () => {} }), get: async ({ sessionID }) => sessions[sessionID] },
    catalog: { model: { list: async () => ({ data: [] }) } },
    event: {
      async *subscribe() {
        for (const event of events) yield event;
        consumed.resolve();
      },
    },
  });
  await consumed.promise;
  const saved = (await readFile(join(directory, "ses_parent.jsonl"), "utf8"))
    .trim().split("\n").map(JSON.parse);
  // The child outlives the turn that asked for its work. The notification turn
  // that the parent starts after it must not take the work of that child.
  assert.deepEqual(saved.map((record) => [record.event.id, record.turn_id]), [
    ["evt_parent_turn", "evt_parent_turn"],
    ["evt_child_turn", "evt_parent_turn"],
    ["evt_parent_end", "evt_parent_turn"],
    ["evt_notice_turn", "evt_notice_turn"],
    ["evt_child_step", "evt_parent_turn"],
  ]);
  await close();
});

test("only a notice that a turn began for is kept", async (context) => {
  const directory = await mkdtemp(join(tmpdir(), "baqylau-native-notice-"));
  context.after(() => rm(directory, { recursive: true }));
  const item = { type: "synthetic", payload: { text: "<subagent>done</subagent>" } };
  const events = [
    { id: "evt_wake_queued", created: 100, type: "session.inbox.enqueued", data: { sessionID: "ses_parent", inboxID: "box_wake", item } },
    { id: "evt_wake_turn", created: 200, type: "session.execution.started", data: { sessionID: "ses_parent" } },
    { id: "evt_wake_given", created: 210, type: "session.inbox.delivered", data: { sessionID: "ses_parent", inboxID: "box_wake" } },
    { id: "evt_join_queued", created: 300, type: "session.inbox.enqueued", data: { sessionID: "ses_parent", inboxID: "box_join", item } },
    { id: "evt_join_given", created: 310, type: "session.inbox.delivered", data: { sessionID: "ses_parent", inboxID: "box_join" } },
  ];
  context.mock.method(globalThis, "fetch", async () => new Response(""));
  const consumed = Promise.withResolvers();
  const close = await plugin.setup({
    location: { directory: "/projects/one" },
    rpc: { register: async () => ({ dispose: async () => {} }) },
    options: { logDirectory: directory, endpoint: "http://127.0.0.1:1/test" },
    session: { hook: async () => ({ dispose: async () => {} }), get: async () => ({ id: "ses_parent", location: { directory: "/projects/one" } }) },
    catalog: { model: { list: async () => ({ data: [] }) } },
    event: {
      async *subscribe() {
        for (const event of events) yield event;
        consumed.resolve();
      },
    },
  });
  await consumed.promise;
  const saved = (await readFile(join(directory, "ses_parent.jsonl"), "utf8"))
    .trim().split("\n").map(JSON.parse);
  // The second notice arrives in the turn that the first one began. That turn
  // answers it without a turn of its own.
  assert.deepEqual(saved.map((record) => [record.event.id, record.notice]), [
    ["evt_wake_queued", null],
    ["evt_wake_turn", null],
    ["evt_wake_given", "<subagent>done</subagent>"],
    ["evt_join_queued", null],
    ["evt_join_given", null],
  ]);
  await close();
});

for (const exitFirst of [false, true]) {
  test(`background exit keeps its original turn; exit first: ${exitFirst}`, () => {
    const capture = shellCapture();
    const record = {
      root_id: "ses_parent", turn_id: "turn_original", session: { id: "ses_child", parentID: "ses_parent" },
      tool: { name: "shell", input: { command: "sleep 30; echo done", background: true } },
      event: { type: "session.tool.success", data: { id: "call_original", metadata: { shellID: "sh_one", status: "running" } } },
    };
    const exit = { id: "event_exit", type: "shell.exited", data: { id: "sh_one", exit: 0, status: "exited" } };
    capture.observe({ type: "shell.created", data: { info: { id: "sh_one", status: "running", file: "/tmp/output" } } });
    capture.bind({ ...record, event: { ...record.event, type: "session.tool.progress" } });
    const first = exitFirst ? capture.observe(exit) : capture.bind(record);
    const completed = exitFirst ? capture.bind(record) : capture.observe(exit);
    assert.equal(first, null);
    assert.equal(completed.root_id, "ses_parent");
    assert.equal(completed.session.id, "ses_child");
    assert.equal(completed.turn_id, "turn_original");
    assert.equal(completed.shell.call_id, "call_original");
    assert.equal(completed.shell.exit, 0);
    assert.equal(capture.observe(exit), null);
  });
}

test("step context keeps each actor's model and reuses its catalog", async () => {
  let reads = 0;
  const capture = contextCapture({ catalog: { model: { list: async () => {
    reads += 1;
    return { data: [
      { providerID: "provider", id: "large", limit: { context: 1000000 } },
      { providerID: "provider", id: "small", limit: { context: 100000 } },
    ] };
  } } } });
  await capture({ type: "session.step.started", data: { sessionID: "lead", assistantMessageID: "one", model: { providerID: "provider", id: "large" } } });
  await capture({ type: "session.step.started", data: { sessionID: "child", assistantMessageID: "two", model: { providerID: "provider", id: "small" } } });
  const child = await capture({ type: "session.step.ended", data: { assistantMessageID: "two" } });
  const lead = await capture({ type: "session.step.ended", data: { assistantMessageID: "one" } });
  assert.equal(child.window_tokens, 100000);
  assert.equal(lead.window_tokens, 1000000);
  assert.equal(child.model.id, "small");
  assert.equal(lead.model.id, "large");
  assert.equal(reads, 1);
});

test("each location records only its own root sessions", async (context) => {
  const directory = await mkdtemp(join(tmpdir(), "baqylau-native-location-"));
  context.after(() => rm(directory, { recursive: true }));
  const consumed = Promise.withResolvers();
  const close = await plugin.setup({
    location: { directory: "/projects/one" },
    rpc: { register: async () => ({ dispose: async () => {} }) },
    options: { logDirectory: directory },
    session: { hook: async () => ({ dispose: async () => {} }), get: async () => ({ id: "ses_other", location: { directory: "/projects/two" } }) },
    catalog: { model: { list: async () => ({ data: [] }) } },
    event: {
      async *subscribe() {
        yield { id: "evt_other", type: "session.execution.started", data: { sessionID: "ses_other" } };
        consumed.resolve();
      },
    },
  });
  await consumed.promise;
  await assert.rejects(readFile(join(directory, "ses_other.jsonl")), { code: "ENOENT" });
  await close();
});
