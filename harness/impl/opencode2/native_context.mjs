// Copyright (c) 2026 Zhambyl Yermagambet
// Save each step's model limit while the native catalog is available.
export function contextCapture(context) {
  const limits = new Map();
  const steps = new Map();
  return async (event) => {
    if (event.type === "session.step.started") {
      const model = event.data.model;
      const key = `${model.providerID}/${model.id}`;
      if (!limits.has(key)) {
        const catalog = await context.catalog.model.list({});
        for (const entry of catalog.data) limits.set(`${entry.providerID}/${entry.id}`, entry.limit.context);
      }
      steps.set(event.data.assistantMessageID, {
        sessionID: event.data.sessionID,
        context: { model, window_tokens: limits.get(key) ?? null },
      });
    }
    if (event.type === "session.step.ended") {
      const saved = steps.get(event.data.assistantMessageID)?.context ?? null;
      steps.delete(event.data.assistantMessageID);
      return saved;
    }
    if (["session.execution.succeeded", "session.execution.failed", "session.execution.interrupted"].includes(event.type)) {
      for (const [key, step] of steps) if (step.sessionID === event.data.sessionID) steps.delete(key);
    }
    return null;
  };
}
