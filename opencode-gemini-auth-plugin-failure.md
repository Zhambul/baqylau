# Investigation report: `opencode-gemini-auth` server plugin failure

## Summary

The OpenCode server cannot load the `opencode-gemini-auth` plugin. The plugin uses the old plugin format. OpenCode 1.18.30 needs the new server plugin format. The new format needs a `default` export with an `id` and an `effect` or `setup` function. The plugin has no `default` export. The loader rejects the module. The cause is a plugin API version mismatch. It is not a credentials problem.

**Reference:** `err_7ac2d9db` — found in the server log. The log is accessible from this session.

---

## Log entry found

**File:** `/Users/z.yermagambet/.local/share/opencode/log/opencode.log`
**Line:** `113829`

```
timestamp=2026-09-13T00:41:09.417Z level=WARN run=a4ad8a3f
message="failed to load plugin"
target=opencode-gemini-auth@latest ref=err_7ac2d9db
cause="Cause([Fail(PluginModule.LoadError:
  Plugin must export a default definition with an id and an effect or setup function.
  (cause: SchemaError(Missing key at [\"default\"])))])"
role=server
```

The line before it shows the real entrypoint:

```
timestamp=2026-09-13T00:41:09.416Z level=INFO run=a4ad8a3f
msg="loading plugin" id=opencode-gemini-auth@latest
entrypoint=file:///Users/z.yermagambet/.cache/opencode/node_modules/opencode-gemini-auth/index.ts
http.span=142 role=server
```

The original cause is `SchemaError(Missing key at ["default"])`. The plugin module has no `default` export.

---

## Configuration

| Item | Value |
| --- | --- |
| Global config | `~/.config/opencode/opencode.json` |
| Plugin entry | `"plugin": ["opencode-gemini-auth@latest"]` |
| Project config in `baqylau` | none |
| Effect | The global plugin list applies to this project. |
| OpenCode binary | `/opt/homebrew/bin/opencode` |
| OpenCode version | `1.18.30` (Homebrew, `anomalyco/tap/opencode`) |
| Loader entrypoint | `~/.cache/opencode/node_modules/opencode-gemini-auth/index.ts` |

The project has no own `opencode.json`. The global config supplies the plugin list. So the failure also occurs in other projects that use the same global config.

---

## Root cause chain

1. The global config enables `opencode-gemini-auth@latest`.
2. OpenCode 1.18.30 loads plugins with the new server plugin loader.
3. The loader schema is:
   `Struct({ default: Union([{ id, effect }, { id, setup }]) })`.
4. A plugin module must have a `default` export. The default must have an `id` and an `effect` function or a `setup` function.
5. The cached plugin module has no `default` export.
6. The schema decode fails with `Missing key at ["default"]`.
7. OpenCode skips the plugin and writes the warning.

---

## Plugin version evidence

| Location | Version | `main` | Default export |
| --- | --- | --- | --- |
| `~/.cache/opencode/node_modules/opencode-gemini-auth` | `1.4.9` | `index.ts` (implicit) | no |
| `~/.cache/opencode/packages/opencode-gemini-auth@latest/node_modules/...` | `1.4.12` | `./index.ts` | no |
| npm registry `latest` | `1.4.15` | `./dist/index.js` | no |
| GitHub `main` | `1.4.16` | `./dist/index.js` | no |

All four versions export only named functions:

```ts
export {
  GeminiCLIOAuthPlugin,
  GoogleOAuthPlugin,
} from "./src/plugin";
```

None of them export a `default`. So a version bump does not fix the error.

---

## The new plugin format

The OpenCode 1.18.30 loader contains this schema:

```js
Struct({
  default: Union([
    Struct({ id: String, effect: declare((o) => typeof o === "function") }),
    Struct({ id: String, setup:  declare((o) => typeof o === "function") }),
  ]),
})
```

The loader then imports the entrypoint, reads `.default`, and adapts `setup` to `effect`:

```js
let module = await import(entrypoint);
let plugin = (await decodeUnknownEffect(schema)(module)).default;
let effectPlugin = "effect" in plugin ? plugin : PluginPromise.fromPromise(plugin);
```

The plugin has no `.default`. The decode fails.

---

## Why it is not a credentials problem

- The error occurs during module load, before any auth code runs.
- The failure is a schema decode error. It names a missing `default` key.
- The plugin never registers its OAuth provider or auth hooks.
- The `google` provider credential is not the cause. Its value is not shown here.

A credential problem would show a different error, for example an auth denial or a token error.

---

## Secondary issue

The plugin cache is inconsistent, and the update check fails:

```
level=WARN message="failed to check plugin update"
target=opencode-gemini-auth@latest
cause="Cause([Fail(NpmInstallFailedError (cause: Error: Package is not installed: open...
```

The cache holds version `1.4.9` in `~/.cache/opencode/node_modules` and version `1.4.12` in the `@latest` package folder. A cache refresh may install `1.4.15`. But `1.4.15` also has no `default` export. A cache refresh alone does not fix the load error.

---

## History and timing

- The failure is old. The log shows it from `2026-08-05`, before the OpenCode upgrade.
- On `2026-08-16`, Homebrew upgraded OpenCode from `1.17.3` to `1.18.18`.
- The error text changed with the upgrade:
  - Old: `SchemaError(Missing key at ["default"])`
  - New: `PluginModule.LoadError: Plugin must export a default definition with an id and an effect or setup function.`
- Both messages mean the same thing: the module has no `default` export.
- OpenCode `1.17.3` already required the default export. So a downgrade does not clearly help.

Upstream status: issue/PR #84, "feat: add native OpenCode V2 plugin support", is open and not merged. The V2 `PluginContext` has no `auth` domain. So a local wrapper is not a small change.

---

## Recommended fix

The published plugin cannot load on OpenCode 1.18.30. Choose one option.

### Option A — Remove the plugin (recommended, if you do not use Gemini)

Remove this entry from `~/.config/opencode/opencode.json`:

```json
"opencode-gemini-auth@latest"
```

Then restart OpenCode. The warning stops. This is the smallest and most correct fix.

### Option B — Use the native Google provider

If you need Gemini now, use the native OpenCode Google provider with an API key. The upstream V2 port covers only organization Code Assist accounts. Google ended direct consumer OAuth. The port is not released.

### Option C — Wait for upstream

Keep the config as is. Accept the startup warning until the upstream V2 port is released.

### Do not do

- Do not expect a version bump to `1.4.15` or `1.4.16` to help.
- Do not expect a cache refresh to help.
- Do not expect a downgrade to `1.17.3` to help. The log shows the same failure there.

---

## Verification steps after a fix

1. Apply the chosen fix.
2. Restart the OpenCode server.
3. Check the log for the target:

```sh
grep "opencode-gemini-auth" ~/.local/share/opencode/log/opencode.log | tail
```

4. Confirm there is no new line with `message="failed to load plugin"` and `target=opencode-gemini-auth@latest`.

---

## Notes

- No credentials are exposed in this report.
- I did not change the global config. The change affects all OpenCode sessions.
- The config file `~/.config/opencode/opencode.json` is the single source of the failure in this project.
