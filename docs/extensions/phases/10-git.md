# P10 — Git extension

Status: in_progress

Depends on: P08; P09 for P10-T06

Context: The user wants a Git interface with log, diff, uncommitted changes, a file tree, commit, push, and commit message generation. Repository state must be shared across sessions and available without a session.

Task: Build the Git feature in a separate extension package. Use actual Git commands and the public host services. Keep all product UI and tests in the package.

Outcomes: A Git workspace page and Kitty pane support the requested read and write workflow. Direct Git and adapters operations update the same repository view.

Verification: Real temporary repository E2E covers reads, staging, generated drafts, commit, push, failure, concurrency, and optional adapters cooperation.

Evidence: Not recorded.

## Read first

Read the released SDK instructions, [protocols](../protocols.md), [quality](../quality.md), current `core/git_status.py`, `core/repository.py`, `inference/contract.py`, and the actual Git command documentation for each implemented operation.

### P10-T01 — Define repository identity and read services

Status: done

Owner: Claude Code

Depends on: P08-T05

Context: A repository can have several sessions and linked worktrees. Paths can change or disappear. A Git page must not create a coding session to read data.

Task: Build the external package and define repository and worktree records. Resolve Git directory and worktree identity through the public process service. Add typed queries for branch, status, log, commit details, tree, and diff. Use Git's documented machine-readable forms, null-delimited paths where supported, explicit cwd, and argument arrays.

Outcomes: Stable repository scope and bounded read APIs. Staged, unstaged, and untracked changes are distinct. Empty repositories, detached HEAD, missing worktrees, binary files, and large diffs have explicit responses.

Code areas: External backend Git service, schemas, record projectors, manifest, and fixture repository builders.

Verification: C21 reads a repository with no session. Test two sessions sharing a worktree, two linked worktrees, paths with spaces and newlines, renamed files, empty history, and detached HEAD. Compare API results with Git output from the same repository state.

Evidence: The package is a separate repository, `~/code/personal/baqylau-git` (distribution `baqylau-git`, dependencies `baqylau-extension-api==0.1.0a1` and `pydantic`). It has no host module and uses the shared policy with the extension profile. Git runs only through the public process service (declared program `git`), always with an argument array, an explicit `cwd`, and fixed read options (no optional locks, no color, unquoted paths); diffs also refuse external diff and text conversion programs. A repository is the SHA-256 of Git's common directory, so linked worktrees, subdirectories, and every session in one worktree resolve to the same ID; a worktree also keeps its own Git directory (`identity.py`). Seven typed queries: resolve (installation scope), and branch, status, log page, commit details, tree, and diff (repository scope). Status reads `status --porcelain=v2 -z --branch` and keeps staged, unstaged, and untracked changes apart; history, trees, and file lists use `-z` records. Explicit responses: an empty repository has a branch and no commit, a detached HEAD has a commit and no branch, a missing or plain directory has a reason, a binary change is named and not printed, and a tree over 5,000 entries or a patch over 200,000 characters is cut and marked. A patch past the host's process output bound is empty and marked cut. `scripts/build_manifest.py` writes `extension.json` (7 queries, 13 schemas) from the package's models.

Verification on 2026-09-25: the package's ruff, wemake, types, and deadcode gates pass. Nine unit cases compare results with Git output from the same temporary repository: two linked worktrees and a subdirectory, a plain and a missing directory, an empty repository, detached HEAD, a staged rename with an unstaged edit and untracked paths with spaces and newlines, log pages against `git log`, commit details with a rename, a tree with a binary diff, and both diff bounds. C21: `tests/e2e/test_repository_reads.py` installs the package into a private host through the test kit, resolves a repository, reads HEAD and status with no session, and then reads a removed worktree and gets the missing-worktree result; the installed runner passes it.

Changes in the main repository: the test kit gives packages an offline wheelhouse and lock (`baqylau_extension_testkit.wheelhouse.build_environment`, with `BAQYLAU_SDK_WHEEL`). The shared dead-code policy counts the declared fields of a package's pydantic documents as contract entries, because hosts read them only through JSON (`baqylau_dev/model_fields.py`, `tests/dev_tools/test_document_fields.py`); other unused code still fails. In the main repository, `make lint` passes and 3,589 Python cases pass (Kitty and live cases not run).

### P10-T02 — Build repository views and refresh behavior

Status: in_progress

Owner: Claude Code

Depends on: P10-T01

Context: The requested interface combines commit history, file tree, working changes, and selected diffs. It must stay external to both host frontends.

Task: Add the Git workspace page and Kitty pane. Provide commit selection, changed-file groups, tree navigation, diff display, and paging. Refresh on relevant canonical shell outcomes, repository file notices, and extension command outcomes. Query Git to confirm the resulting state. Coalesce related notices through the host source API.

Outcomes: Both displays show consistent repository data and selection. A command start does not imply successful completion. External changes appear without an idle full-repository scan.

Code areas: External web components, terminal layouts, source subscriptions, query cache, and view state.

Verification: C18, C20, C21, and C28 cover both views, live file edits, commit selection, paging, resize, reconnect, and disable. Two sessions show one shared repository state. Remove a worktree and display a recoverable unavailable state.

Evidence (partial, 2026-09-25):

Host changes that the task needed (none of them is Git specific):

- Kitty data. A pure presenter cannot read live data, and the host never filled `TerminalViewRequest.document`. A terminal view can now name one of its package's queries (`TerminalView.query`); the manifest check requires a declared query that accepts every view scope. For each presentation the host runs the query with `TerminalViewInput` (the pane's focus) under the query's call grant, and gives the ready result to the presenter as its document (`extensions/terminal_documents.py`). A failed query is a failed view. The HTTP query route and the view documents share one checked call path (`extensions/query_calls.py`).
- Scope holds. Only running jobs held a repository scope, so a repository's sources were never planned for a view. An open change stream now holds its scope (`api/extensions/change_frames.py`), and it sends one heartbeat at once, because the server sends the headers only with the first frame.
- Web client. `context.api` gains `query(queryId, argumentsJson, page?)` and `watchChanges(listener)`; the dashboard implements them over the query route and the change stream (`src/api/extension-queries.ts`, `src/api/extension-changes.ts`). Commands come with P10-T03.
- Repository identity. The SDK owns the rule (`baqylau_extension_api.repositories`: the ID is a digest of the shared Git directory, read from one `git rev-parse`), so the host and the package name a repository the same way. The host resolves a directory (`extensions/repository_scopes.py`, `GET /api/extension-web/repository-scope`), the Kitty selector offers the window session's repository, and the dashboard has a repository page route (`#/repo/<directory>/x/<extension>/<view>`) that the session page links to.

Package: a source watches the worktree, its Git directory, and the shared Git directory. After each notice it reads `git status` once, and only a new status fingerprint is an observation; the position counts states, so a return to an earlier state is new. A translator gives one fact for each state, and a projector keeps the latest state in one record, whose change wakes the open views. The workspace query gives HEAD, the change groups, the first history page, and the focused commit or file with its diff; item IDs are commit hashes and short path hashes. The Kitty presenter arranges that document (stable blocks, safe text through the SDK's display rule); the web page (`web/git.js`) shows the same data, loads older history pages, and reads again after each change. A missing worktree is a state in both views, not a failure.

Verification: 17 package unit cases (watch plan and reads, focus by ID, a stale focus, unsafe text, a missing worktree) and 5 E2E cases through the kit runner against a private host: an edit refreshes the state and the Kitty view route (C20); a removed worktree is unavailable in the query and the view, and a new repository at its path is shown again; the page lists changes, shows a file diff and a commit, follows a new file and a removal (C18, C21); older history pages load. Host: SDK view query and repository rule tests, the daemon view-query case, the scope hold, the selector scopes, the repository scope route, the kit change watch, and a route test. Gates on 2026-09-25: `make lint` passes; 3,603 Python cases pass (Kitty and live cases not run); 167 dashboard unit tests and the 32 extension browser cases in Chromium and WebKit pass.

Found and fixed during the work: a dashboard page passed a deep state proxy as the view scope, which the view host cannot clone; a change stream opened only after its first heartbeat; `make release-artifacts` left build trees that the lint gates scanned.

Open: a real Kitty run, and a live case with two sessions in one worktree; both need the user's approval. Two sessions resolve one scope by the shared rule (linked worktree and selector tests).

### P10-T03 — Add file selection and commit

Status: done

Owner: Claude Code

Depends on: P10-T02, P05-T03

Context: Commit must use the user's selected changes and preserve unrelated work. Several sessions or tools can change the same index.

Task: Add explicit stage and unstage actions, an editable commit message, and Commit. Use a per-repository command queue. Record expected HEAD and index state with the selection. Check them before a write. Use the actual Git index and make its shared effect visible. Preserve message drafts after failed actions.

Outcomes: The user can select files and commit the intended staged changes. Concurrent changes produce a clear state conflict. Hook failures return useful results without losing the draft.

Code areas: External command handlers, operation schemas, web commit form, Kitty actions, and repository fixtures.

Verification: Test selected and unselected files, pre-existing staged changes, concurrent index edits, an empty message, a failed hook, and a successful commit. Inspect the resulting commit through Git and the public query API. C22–C24 cover duplicate requests, stale state, and read-only mode.

Evidence: Three write commands (`stage`, `unstage`, `commit-staged`), each declared with `effect: write` and reconciliation. The state that a write expects is a digest of HEAD and every index entry (`index_state.py`); the workspace document gives it with the files, and each write compares it with the real repository first, so a change by another session or tool gives `state_changed` and no write. Stage and unstage use literal paths (`--literal-pathspecs`), so a path such as `*.txt` names one file; unstage works with no commit (`rm --cached`). Commit uses the real index, so files that another tool staged are part of it and the Staged list shows them; it refuses an empty message and an empty index, and runs the repository's hooks; a failing hook is a failed job with Git's message. The host runs command jobs on one serial executor, which is the queue of each repository, and a repeated request key gives the same job. A lost reply is reconciled from the repository without a repeated write: an unchanged state proves that the write did not run; staged or unstaged paths are compared with the worktree or HEAD; a commit is proven when HEAD has the requested message; anything else stays `outcome_unknown`. Cancel reports `outcome_unknown`, because a running Git write is not stopped. Web: `context.api` gains `runCommand` and `readJob` (the dashboard converts the host's job reply into the SDK's `CommandJob`), and the page has Stage and Unstage buttons and a commit form outside the redrawn area, so a refresh keeps the draft; only a commit that succeeded clears it, and a failed one shows why. Kitty: each changed file's entry has a stage or unstage action that expects the shown state (at most the host's 256 actions).

Verification on 2026-09-25: package unit cases stage only the selected file with a staged file already present, commit exactly the index, refuse a stale state, an empty message, and an empty index, keep HEAD after a failing hook, unstage with no commit, reconcile a write that ran, one that did not, and another tool's commit, and check the Kitty actions. E2E through the kit runner (8 cases pass): a repeated commit request is one job and one commit (C22); a stale state is refused and the index stays (C23); on the page, a failing hook keeps the draft and shows the hook's message, then the commit succeeds and clears it. Read-only mode (C24): the host refuses write commands before dispatch (host command tests); a package case would need a host restart in read-only mode, which the kit does not offer. Host: `make lint` passes; 167 dashboard unit tests, 29 web SDK tests, and the 32 extension browser cases pass.

### P10-T04 — Add commit message generation

Status: done

Owner: Claude Code

Depends on: P10-T03

Context: The user wants a Generate message button and an editable result. The host already has an inference service.

Task: Add a command that reads the selected staged diff at a recorded revision and calls the public inference service. Bound large input and report any omitted files. Return a draft message. Detect selection changes while generation runs. Let the user edit the result before Commit.

Outcomes: Generation uses the existing configured model service. It does not automatically commit or push. A stale generated result cannot overwrite a newer user edit.

Code areas: External generation command, prompt and result models, draft state, settings, and model fixture.

Verification: Use a deterministic model fixture to inspect the exact selected diff and resulting draft. Test model failure, cancellation, large input, changed selection, and a user edit during generation. Confirm no Git write occurs until the explicit Commit action.

Evidence: `generate-message` is a read command (`effect: read`, no reconciliation) that calls the host's inference service (`uses_inference`); it is a job and not a query, because a model call can take longer than the query deadline. It checks the expected state, refuses an empty index, and builds a bounded prompt from each staged file's diff (60,000 characters in total; a binary file is one line; files that do not fit are named in the prompt and in the draft's `omitted_paths`). The draft names the state that it was read from and is `stale` when the staged files changed during the model call. A fenced reply loses its fence; no reply is `model_unavailable`, and an empty reply is `empty_reply`. The command never writes to Git. The page applies a draft only when it is not stale and the user did not edit the message while the model wrote it; Discard stops the wait, and the job's later draft is not used. The draft is only text in the form; nothing is committed until the user presses Commit.

Verification on 2026-09-25: package unit cases with a deterministic model fixture (`tests/model_fixture.py`) check the exact staged diff in the prompt and not the unstaged edit, the fence, the unchanged repository, a model failure, a large file that is omitted and named, a file staged during the model call (stale draft), and an empty index (the model is not asked). Browser case: the private host has no model, so the page shows the real failure and keeps the typed message; with the page's job requests answered by a deterministic model in the browser, a user edit wins over the draft, a discarded draft is not used, a stale draft is not used, and a current draft fills the message; the repository has no commit at the end. The kit runner passes 9 cases.

Limit: the host runs all extension jobs on one serial executor, so a long model call delays other extension jobs until it ends.

### P10-T05 — Add push and command reconciliation

Status: done

Owner: Claude Code

Depends on: P10-T03

Context: Push is an external write. A lost worker reply must not trigger an automatic repeated operation with unknown effects.

Task: Add remote and branch selection, a visible Push action, progress, and typed results. Use the normal non-force push path for the first version. Add cancellation reporting and reconciliation for lost commit or push results. Check repository identity and branch again when queued work starts.

Outcomes: Push succeeds or reports authentication, hook, branch, or remote rejection clearly. Unknown results remain explicit until reconciled. User drafts and selections survive failure.

Code areas: External Git command and reconciliation handlers, job result models, web and Kitty actions.

Verification: Use a local bare remote. Test successful push, rejected non-fast-forward push, hook failure, missing upstream, changed branch, canceled request, and lost reply after success. Compare remote refs. C22–C24 prove no blind repeated write and correct policy handling.

Evidence: `push` is a write command with reconciliation. It pushes only the checked-out branch to the remote branch of the same name, without force (`git push --porcelain <remote> refs/heads/<b>:refs/heads/<b>`), and sets the upstream when the branch has none. When the queued job starts, it checks again that the worktree is the same repository (the SDK's repository rule), that the state is the one that the user saw, that the branch is still checked out, and that the remote exists; remote and branch names cannot start with a dash. Failures are typed from Git's porcelain output: `rejected` (for example a non-fast-forward push), `remote_rejected`, `hook_failed` (a local pre-push hook, with its message), `authentication_failed`, and `remote_unavailable`. A lost reply is proven only by the remote, because a push does not change the local state: the remote branch at the local branch head is a success, and anything else stays `outcome_unknown`; the reconciliation never pushes. Cancel reports `outcome_unknown`, because a running Git write is not stopped. The workspace gives the remotes and the branch's push remote; the page has a remote list and a Push button, and reports the result or the reason; the Kitty view has a Push entry whose action expects the shown state. The declared Git bound is 120 seconds, for pushes over the network.

Verification on 2026-09-25 with local bare remotes: package unit cases for a first push that sets the upstream (the remote ref equals the local head), a rejected non-fast-forward push (the remote ref does not change), a refusing pre-push hook, a switched branch, a commit after the read, a removed remote, a lost reply before and after the push, and the Kitty push action. E2E through the kit runner (12 cases pass): a repeated push request is one job and the remote has the local head (C22); a push with a stale state does not run (C23); on the page, the first push reports the new upstream and a push behind the remote is refused with the reason, and the remote keeps its commit. Read-only mode (C24): the host refuses write commands before dispatch (host command tests). Drafts and selections are page state outside the redrawn area, so a failed write keeps them.

### P10-T06 — Add adapters cooperation and release checks

Status: in_progress

Depends on: P10-T04, P10-T05, P09-T06

Context: Direct Git commands and adapters commit or push can describe the same underlying operation. The Git extension must work both with and without adapters.

Task: Consume the adapters public command-activity contract as an optional dependency. Refresh confirmed repository state and link activity through invocation and repository identities. Avoid duplicate operation entries when sources overlap. Run all feature and cooperation scenarios from the external package.

Outcomes: Direct Git, UI actions, adapters commit, and adapters push update the same repository page. Removing adapters removes only its activity integration. The Git package is independently buildable and testable.

Code areas: External optional service consumer, activity correlation, manifest, CI, and E2E cases.

Verification: C15, C16, C26, and C28 cover Git alone, both activation orders, adapters disable, repeated observations, and external-only frontend updates. Run package `make lint`, `make test`, and applicable `make e2e`. Record real Kitty and any live adapters evidence separately.

Evidence (2026-09-25):
- Scope decision, taken as recommended under the user's standing "do the recommended" instruction: the adapters contract stays session-scoped. The host gives a new service, the sessions of a repository. The other option was a working directory in core shell facts, but harnesses do not report one for each command. The host service is in these files:
  - SDK: `contracts/session_lists.py`, `models/session_lists.py`, `runtime/session_access.py`, and the manifest flag `uses_sessions`.
  - Host: `extensions/session_access.py`, `repository/impl/sqlite/session_rows.py`, and the wiring in `worker_host_services.py`, `impl/process/channel.py`, and `app/provider_worker_services.py`.
  - The host service maps each session's working directory to its repository by the SDK's rule. `tests/extension_host/test_session_access.py` covers it.
- The Git package declares an optional dependency on `baqylau.adapters` (`>=0.1,<1`), consumes `baqylau.adapters.git-activity` (`>=1,<2`, not required), and sets `uses_sessions`.
- `baqylau.git.activity` (repository scope, `adapters_activity.py`) reads the service for each session of the repository, and merges the calls in time order. A received call ignores fields that a later 1.x version adds. Without adapters, the read gives the peer's `unavailable` reason, and every other Git read and write works the same.
- The repository page has an Adapters section that shows the calls or the reason.
- Direct Git and adapters Git calls change the same repository, and the existing repository watch refreshes the page after either one. The Git package makes no feed entries of its own for these calls, so the two sources cannot give duplicate entries.
- Tests:
  - `tests/test_adapters_activity.py` covers the merge of two sessions, a disabled peer, and no sessions.
  - `tests/e2e/test_adapters_activity.py` (case `adapters-activity`, peer `baqylau.adapters`) runs Git alone in a private host, through the new session list over the real worker RPC. The page test checks the Adapters section.
  - Package gates `baqylau_dev check --gate lint` pass, and the runner passes 13 repeatable cases.
- Open:
  - Both packages active with a real session in the repository need a live harness session. A repeatable host has no session.
  - Real Kitty runs need the user's approval.
