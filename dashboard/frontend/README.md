# Dashboard frontend

This directory contains the sole browser application. It uses Svelte 5 runes,
strict TypeScript, Vite, Vitest, Testing Library, and Playwright. The existing
global stylesheet remains the design source of truth and is imported by
`src/main.ts`.

FastAPI owns `dashboard/static/index.html`. Vite does not build an HTML file. A
production build writes content-hashed files and a manifest to
`dashboard/static/build`; `dashboard.frontend_build` validates and stamps that
output. Normal daemon startup rejects missing or stale output.

Run the repository targets from the repository root:

```sh
make build-frontend
make test-frontend
make test-browser
make lint-frontend
```

The source boundaries are deliberate:

- `src/api` owns generated wire types, HTTP/SSE clients, and translators.
- Feature directories own view models and Svelte components.
- `src/shared/browser` owns long-lived browser resources and identities.
- `src/entries/TrustedHtml.svelte` is the only raw-HTML leaf. It accepts only
  the branded escaped value produced by `src/entries/markup.ts`.
- Large server snapshots stay in route-scoped state. High-frequency input
  remains local to its component.

Use `npm run generate:api` only while an API daemon is available on the command's
documented port. Review the generated schema diff before committing it.
