# Baqylau frontend tools

This package supplies the common frontend rules. The dashboard, the web SDK,
and external view packages use these exports; package roots and file lists stay
local, and the common rules are not copied into extension source.

| Export                                                  | Use                                                                                                         |
| ------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `@baqylau/dev-tools/eslint`                             | `eslintConfig({ root, projects, ignores, nodeFiles })`                                                      |
| `@baqylau/dev-tools/prettier`                           | the Prettier options (set `"prettier"` in `package.json`)                                                   |
| `@baqylau/dev-tools/tsconfig/browser`, `/tsconfig/node` | the TypeScript options to extend                                                                            |
| `@baqylau/dev-tools/knip`                               | `knipConfig({ entry, project, ignore, ignoreBinaries })` for `knip.config.ts`                               |
| `@baqylau/dev-tools/vitest`                             | `testProfile({ include, coverageInclude, coverageExclude, environment, setupFiles })` for the `test` option |
| `@baqylau/dev-tools/coverage`                           | the shared coverage thresholds                                                                              |
| `@baqylau/dev-tools/pins`                               | the exact version of each quality tool                                                                      |

Tool pins: a package that uses one of the pinned tools declares its exact
version. Add `baqylau-dev-tools-pins` to the package's `lint` script; it refuses
another version. (npm peer dependencies would say the same thing, but npm
10.9 fails to resolve them for a linked or local install of this package.)

`test/` is a private consumer package: `make test-dev-tools-web` proves that
each rule fails on a deliberate violation (an explicit `any`, a Svelte prop of
the wrong type, an unused export, bad formatting, and coverage below the
thresholds), that the clean sample passes, and that the dashboard keeps every
shared ESLint rule and TypeScript option.
