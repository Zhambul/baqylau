// Copyright (c) 2026 Zhambyl Yermagambet
// Admit files before the native inbox records the user prompt.
import { readFile } from "node:fs/promises";

const PREFIX = "/baqylau-attach ";
const DESCRIPTION = "The file content is included with this message. Use the attached content directly.";

export async function registerAttachments(context) {
  const originals = new Map();
  const admission = await context.session.hook("prompt", async (event) => {
    if (!event.prompt.text.startsWith(PREFIX)) return;
    const encoded = event.prompt.text.slice(PREFIX.length).trim();
    const message = JSON.parse(Buffer.from(encoded, "base64").toString("utf8"));
    const files = await Promise.all(message.files.map(load));
    const rendered = message.text + files.map((file) => file.text).join("");
    const pending = originals.get(rendered) ?? [];
    pending.push(message.text);
    originals.set(rendered, pending);
    event.prompt.text = rendered;
    event.prompt.files = files.map((file) => file.native);
  });
  const content = await context.session.hook("context", (event) => {
    for (const message of event.messages) {
      if (message.role !== "user" || !Array.isArray(message.content)) continue;
      const first = message.content[0];
      if (first?.type !== "text") continue;
      message.content = message.content.filter((part, index) => index === 0 || !included(first.text, part));
    }
  });
  return {
    originalText(text) {
      const pending = originals.get(text);
      const original = pending?.shift() ?? text;
      if (!pending?.length) originals.delete(text);
      return original;
    },
    async dispose() {
      await admission.dispose();
      await content.dispose();
      originals.clear();
    },
  };
}

async function load(file) {
  const bytes = await readFile(new URL(file.uri));
  const mediaType = file.mediaType ?? "application/octet-stream";
  const text = mediaType.startsWith("text/") || mediaType === "application/json"
    ? `\n\nContent of ${file.name}:\n${bytes.toString("utf8")}\n\nEnd of attached file: ${file.name}`
    : "";
  return {
    text,
    native: { name: file.name, description: DESCRIPTION, uri: `data:${mediaType};base64,${bytes.toString("base64")}` },
  };
}

function included(text, part) {
  const attachment = part.metadata?.attachment;
  if (part.type !== "text" || !attachment?.name) return false;
  const description = attachment.description ? `\nDescription: ${attachment.description}` : "";
  const prefix = `\n\nAttached file: ${attachment.name}${description}\n\n`;
  return part.text.startsWith(prefix) && text.includes(`Content of ${attachment.name}:\n${part.text.slice(prefix.length)}`);
}
