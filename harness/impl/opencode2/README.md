# OpenCode2 plugin

OpenCode2 is found through the same plugin directory contract as the other
harnesses. The common engine has no OpenCode2 branch.

## Event flow

The server plugin records native events. The CLI plugin attaches the session
shown in its terminal through a native RPC. It supplies the terminal window
and the CLI process ID. A shared server's process ID is not a terminal
identity. Attachment saves a record before it sends the registration notice.
The server uses that attachment for later start and end notices.

The native server plugin saves events and their message and tool joins as JSONL.
Child events share their root session's log and assignment turn. Each record
keeps the native session, root identity, and parent identity. Thus a new
translator can recover actor ownership without old memory. Native answer
metadata supplies the recorded question answer; a successful key write alone
does not resolve it. The existing file-change notifications wake the Python
reader. There is no polling timer. Each stored record can be translated after a
restart.

Native shell calls use `background: true`. The tool can return while its shell
is still running. The plugin joins `shell.created`, the tool result, and
`shell.exited` by the native shell ID. It saves the original call, actor, and
turn beside the exit record. The common output-file reader supplies output
updates. A native exit ends the output stream; no process polling is added.
An exit that arrives before the tool result waits for that result in the join.
If a shell notice later wakes a child, its reply does not replace the result
of an assignment that has already finished.

Native file tools are `read`, `write`, and `edit`. File results keep their input
path after success or failure. New writes report creation in the result text;
their saved input supplies the content and line count. Edits supply a diff and
line counts in `metadata.files`. Translation does not read the workspace file,
which can already have changed when the event is processed.

The native catalog is `context.catalog.model.list({}).data`. Its model entries
supply the context limit. Each completed step stores its own model reference
and limit; context use is not summed across earlier steps. Model limits are
cached inside the native process and no timer refreshes them. Missing limits
are not replaced with a guessed value. Native root title changes update the
session title; child title changes update only the child actor.

A child session keeps the turn that asked for its work. That turn is not read
from the root session, because a background child outlives it: the root ends its
turn at once, and by the time the child finishes, the root already runs the turn
that answers the completion notice. A notice is not a prompt; it is delivered in
its own native turn, and the record starts a turn for it without a user message.

The plugin sends a registration notice on terminal attachment and at execution
start and end, after the log write. HTTP delivery has a one-second timeout and
runs separately from event capture. No HTTP request is sent for each text
delta. OpenCode2 creates a fresh session when its first message is sent, so a
dashboard launch must carry a first message.

Read the installed native API contract with
`opencode2 api --standalone GET /openapi.json`.

## Install for normal terminal launches

Run this command from the Baqylau repository:

```sh
.venv/bin/python -m harness.impl.opencode2.install
```

It links this package into `~/.config/opencode/plugins/baqylau`. It preserves
the user's configuration document and refuses to replace another file. It
also supports `OPENCODE_CONFIG_DIR` and `XDG_CONFIG_HOME`.

The package has both server and CLI entries. OpenCode2 discovers both entries
for a normal `opencode2` command. Its existing shared server reloads the
watched configuration directory. Event logs use
`~/.local/share/baqylau/opencode2`, and notices go to dashboard port 8377.
`BAQYLAU_OPENCODE_LOG_DIR` and `BAQYLAU_DASHBOARD_PORT` select an isolated
instance. Dashboard launches supply both the environment and plugin options,
so global plugin discovery also uses the selected event directory.

## Catalog and account limits

The catalog reads enabled Go models and their effort values from the native
model catalog. It caches the result for 60 seconds. The new-session form reads
these choices with the native compact and effort commands.

The usage reader starts a private native API process and calls the plugin's
usage RPC. The native credential interface resolves the active OpenCode Go
key, which stays in that process. The RPC calls
`https://opencode.ai/zen/go/v1/usage` and returns the reported five-hour,
weekly, and monthly percentages and reset times. The dashboard caches a
successful read for 60 seconds. A failed read produces an error row and is
retried after five seconds. Token use for a session remains separate from
these account limits.

## Launch

The launcher uses `--standalone` and `OPENCODE_CONFIG_CONTENT`. This keeps the
process separate from the user's shared server, while it uses the signed-in
native profile. The shared E2E runtime stores event logs in its test directory.

The launcher waits for the initial draft and selected model before it sends Enter: `--prompt`
alone does not submit it. A successful key write is not enough. The launcher
waits for the native start screen to close and retries only while the same
draft remains on that screen. This wait has a fixed time limit. The check uses
the start-page controls and the visible end of the draft. Long prompts can move
the logo and the start of the draft outside the screen.

The plugin supports both Kitty and PTY window identities. The selected model
and effort are passed through native model configuration as
`provider/model#effort`. A model with no effort setting uses `provider/model`.

## Models

All enabled OpenCode Go models are available. Baqylau does not read a provider
API token to discover them. `opencode2 models` lists them under `opencode-go/`.

Behaviour is not the same on every model:

* Effort values are model-dependent. The launcher refuses a value the selected
  model does not have, with "Variant unavailable". The DeepSeek Flash models
  take low, high and max; `deepseek-v4-pro` takes only high and max; the
  `gpt-5.6` models also take medium. Baqylau uses low as the default when it
  is available, then the first native value. Models with no effort values have
  no effort selector. Model changes keep a supported effort or select the new
  model's default. Native display names are used to confirm the selection.
* File tools are model-dependent. The installed context hook gives the native
  `patch` tool only to a model whose native ID contains `gpt-`, without `oss`
  or `gpt-4`, and REMOVES `write` and `edit` from that model. Every other model
  gets `write` and `edit` and no `patch`. The file delete and move rows
  therefore run on `opencode-go/gpt-5.6-luna`.
* Image input is model-dependent. Only a vision model reads an image.
* The subagent, shell, question, skill and web tools are the same on every
  model.

Some subscription models collect data to improve their own quality. The
installed CLI refuses those until the workspace owner opts in on the OpenCode
workspace page. Both Muse Spark Contributor models are in that group.

## Features that only OpenCode2 has

* A native local text file read event. Codex has none.
* Questions with more than one selected choice. Codex supports one choice.
* Readable model reasoning. Claude Code does not expose it.
* Native file delete and move tools. Claude Code has neither.

The `patch` tool gives no path of its own. It names each file that it wrote in
its result, with a status of `added`, `deleted` or `modified`, a diff, and the
line counts. A move keeps the source path in the `Index:` line of that diff and
names only the target file, so a diff that names another file is read as a move.
See Models for which model gets `patch`.

## Integration gaps and native limits

Each limit has a `# Harness limit:` comment on its scenario. The feature files
are the record; this list is only a summary.

* Baqylau has no OpenCode2 plan decision control.
* The native revert API has conversation and combined modes, but no file-only mode.
* No native task-list tools in 0.0.0-beta-19242. This was checked with the
  native tool registry through `context.tool.transform`, not only a model reply.

The dashboard can move an active foreground shell to the background through
the native API. It rejects idle requests and confirms a new native background
event before it reports success. The existing output reader follows the same
shell until it exits. The shared scenario checks the transition, later output,
and final state.

## Rewind

The dashboard can restore a named prompt with conversation or combined mode.
The control checks the message ID and prompt against the session's native log.
It calls the native revert API and checks the returned boundary before it
restores the editable draft. The next prompt commits the native boundary.
Dashboard launches enable native snapshots for file restoration.

The shared scenarios check prompt revision, file restoration, and retained
conversation memory. Conversation-only rewind preserves later file changes.
A compact multiline paste is expanded with the native repeat-paste action,
then the composer reader checks the full text before the control reports success.

## Permission answers

Native permission requests appear as pending questions. Dashboard answers call
the native API with the exact session and request IDs. The event log records
the server process ID. The control checks that the same server is still alive
and has one local listening address. A private server supplies its password
through its process environment. A shared server uses its native service
profile, after the control checks that the profile names the same address.
Passwords are not saved in the event log.

The control reports success only after a new native reply event confirms the
requested decision. The shared live scenarios check Allow once, Always allow,
and Reject. Reject aborts the native turn. A real Kitty scenario also checks
the shared-server path.

## Native Plan agent

The installed `opencode.plan` plugin makes the Plan agent read-only outside
its plan directory. It tells the model to discuss the plan in the conversation
and remain in Plan mode until the user switches agents. It does not supply a
plan approval dialog or an exit-plan tool. A dashboard approval action would
therefore be an additional workflow, rather than a native dialog control.
The draft, model, and effort controls read both the Build and Plan footers.
The real-terminal scenario checks these controls after a native agent switch.

## Attachments and drafts

The native prompt hook admits attachments before inbox admission. It sends
file bytes as inline data, with the original display name. An encoded message
carries only the prompt and local paths through the terminal. The hook decodes
it before OpenCode2 records the prompt or sends it to the model. File bytes do
not pass through the terminal. Text files are also placed in the admitted
prompt under `Content of <name>`, with an explicit end marker. The context
hook removes the duplicate native text part. This makes the content available
to models that did not use the separate native attachment part. Images keep
their native file representation. The event capture keeps the original prompt
text, so send confirmation and dashboard history do not include the added block.
Native event capture keeps the delivered file names with
the prompt, so the dashboard can show them. The shared tests cover first and
later turns, subagents, multiple files, a file without text, and image content.

The composer reader reads the draft above the native model footer. Sending a
dashboard draft clears the terminal draft before it inserts the new text.
The shared Kitty test checks that rename preserves the draft and send does
not duplicate it.

Model selection uses the native model list and completes the effort selection
when that dialog opens. It checks the model footer before it reports success.
The shared control test sends another turn after the change and checks the
reported model again.

## Subagents

The native `subagent` tool WAITS for its result unless the call sets
`background: true`. This is the opposite of the other two harnesses: the Claude
Code `Agent` tool runs in the background unless the call sets
`run_in_background: false`, and Codex `spawn_agent` always returns at once and
has a separate `wait_agent` tool that blocks. All three therefore support both,
and the E2E suite covers both in `subagent_foreground.feature` and
`subagent_background.feature`.

Which one the model uses is the model's choice: `background` is a tool argument,
not a launch setting, so the suite asks for it in the prompt.

## The first text of a session

A NEW session keeps the first text in its DRAFT. The start page holds it until
someone sends it, so the launcher sends it there. A RESUMED session sends that
text ITSELF and draws no start page at all, so the launcher gives it the text on
the command line and sends nothing.

## Queued prompts

OpenCode2 puts every prompt in the INBOX of its session. An idle session takes
the prompt at once and starts a turn. A session that runs keeps the prompt in
the inbox until its turn ends. The send control reads the session event log for
the delivery of the text it sent: a prompt that is not taken is reported as a
QUEUED prompt, which is what the dashboard shows and what the queue of the
session holds until the prompt starts.

## A turn with more than one answer

A background child announces itself in the INBOX of its root. If the root still
runs the turn that asked for the work, the native side gives it that
announcement inside that turn: the root answers again, and its ONE native
execution then holds several complete answers. The record keeps that as one
turn, because one native execution is one turn.

Baqylau expects one final answer for one turn, so a scenario that needs a
counted answer keeps its OpenCode2 subagents in the foreground. The dedicated
background scenarios do not count answers of the lead.

## A Kitty window

OpenCode2 can use a shared server or start a child server. The CLI plugin
supplies the process ID of the terminal program in both cases.

* A window is attached to its session only when it hosts the CLI process.
  The Kitty plugin reports the whole process tree
  of each window, as the PTY plugin does.
* A prompt goes to the terminal as ONE paste. Kitty closes its own
  `--bracketed-paste` option with a SECOND, empty paste, and OpenCode2 answers a
  paste that carries nothing by reading the CLIPBOARD of the person into the
  prompt. Baqylau writes the marks of the paste itself, so both terminals send
  the same bytes.

## Tests

Live coverage uses rows in the existing feature Examples tables and the normal
dashboard launcher. There is no separate OpenCode2 live test runner, private
application graph, or weaker greeting assertion.

Run the shared greeting and command rows with:

```sh
.venv/bin/python -m pytest tests/e2e/test_scenarios.py -q -x -k 'opencode2 and (harness_answers or command_the_model_runs) and lead'
```

Run the main OpenCode2 scenario rows with `make test-drift E2E="-k opencode2"`.
Real-terminal journeys and browser cases have separate shared runners; this
command does not verify them.

The shared catalog and account-limit scenarios include OpenCode2. The
real-terminal journeys install the plugin in an isolated native configuration
directory and run the normal CLI with a shared service. That service has its
own port and state directory. Cleanup uses the same configuration and state
paths, so it cannot stop the user's service.

Test skills use the native `.agents/skills` location. The native `skill` tool
takes an `id`, with no argument field; its called and completed records supply
the skill lifecycle and result. Each call has a separate identity on its worker.

E2E runtime settings select a native web search provider with
`websearch.provider: random`. The launcher passes an explicit runtime settings
file through `OPENCODE_CONFIG`; normal launches do not set a search provider or
change the user's consent.

Interruption sends Escape only to the owned terminal and checks new native
records before it reports success. An old interruption record cannot confirm a
new request. A failed shell tool with native error type `aborted` records a
cancelled command.

Rename opens the native dialog, replaces its input, and waits for a new root
title event. It does not submit a model prompt or change the composer draft. An
old matching title cannot confirm a new request. If the dialog does not open,
the handler sends no text.

The delivery regression test retains a captured greeting to check a specific
ordering fault: an end notice can arrive before the event log is read. Native
plugin tests also check event capture while HTTP delivery is delayed.
