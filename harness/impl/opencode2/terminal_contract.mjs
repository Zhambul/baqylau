// Copyright (c) 2026 Zhambyl Yermagambet
// The terminal client supplies its own process and window identity.
export const terminalContract = {
  id: "baqylau.terminal",
  methods: {
    attach: {
      input: {
        type: "object",
        properties: {
          sessionID: { type: "string" },
          windowID: { type: "string", minLength: 1 },
          processID: { type: "integer", minimum: 1 },
        },
        required: ["sessionID", "windowID", "processID"],
        additionalProperties: false,
      },
      output: { type: "object", additionalProperties: false },
      errors: {},
    },
  },
  events: {},
};
