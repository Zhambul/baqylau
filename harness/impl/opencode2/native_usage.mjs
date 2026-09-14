// Copyright (c) 2026 Zhambyl Yermagambet
// Read account limits with the credential owned by the native server.
const usageContract = {
  id: "baqylau",
  methods: {
    usage: {
      input: { type: "object", additionalProperties: false },
      output: { type: "object" },
      errors: {},
    },
    models: {
      input: { type: "object", additionalProperties: false },
      output: { type: "object" },
      errors: {},
    },
  },
  events: {},
};

export async function registerUsage(context) {
  return context.rpc.register(usageContract, {
    models: async () => {
      const catalog = await context.catalog.model.list({});
      return { models: catalog.data
        .filter((model) => model.providerID === "opencode-go" && model.enabled)
        .map(({ id, name, variants }) => ({ id, name, variants: variants.map(({ id }) => ({ id })) })) };
    },
    usage: async () => {
      const connection = await context.integration.connection.active("opencode-go");
      const credential = connection ? await context.integration.connection.resolve(connection) : undefined;
      if (!credential?.key) return { status: 401 };
      const response = await fetch("https://opencode.ai/zen/go/v1/usage", {
        headers: { Authorization: `Bearer ${credential.key}` },
        signal: AbortSignal.timeout(10000),
      });
      if (!response.ok) return { status: response.status };
      const result = await response.json();
      return { status: response.status, usage: result.usage };
    },
  });
}
