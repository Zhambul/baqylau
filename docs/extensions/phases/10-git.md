# P10 — Git extension

Status: not_started

Depends on: P08; P09 for P10-T06

Context: The user wants a Git interface with log, diff, uncommitted changes, a file tree, commit, push, and commit message generation. Repository state must be shared across sessions and available without a session.

Task: Build the Git feature in a separate extension package. Use actual Git commands and the public host services. Keep all product UI and tests in the package.

Outcomes: A Git workspace page and Kitty pane support the requested read and write workflow. Direct Git and adapters operations update the same repository view.

Verification: Real temporary repository E2E covers reads, staging, generated drafts, commit, push, failure, concurrency, and optional adapters cooperation.

Evidence: Not recorded.

## Read first

Read the released SDK instructions, [protocols](../protocols.md), [quality](../quality.md), current `core/git_status.py`, `core/repository.py`, `inference/contract.py`, and the actual Git command documentation for each implemented operation.

### P10-T01 — Define repository identity and read services

Status: not_started

Depends on: P08-T05

Context: A repository can have several sessions and linked worktrees. Paths can change or disappear. A Git page must not create a coding session to read data.

Task: Build the external package and define repository and worktree records. Resolve Git directory and worktree identity through the public process service. Add typed queries for branch, status, log, commit details, tree, and diff. Use Git's documented machine-readable forms, null-delimited paths where supported, explicit cwd, and argument arrays.

Outcomes: Stable repository scope and bounded read APIs. Staged, unstaged, and untracked changes are distinct. Empty repositories, detached HEAD, missing worktrees, binary files, and large diffs have explicit responses.

Code areas: External backend Git service, schemas, record projectors, manifest, and fixture repository builders.

Verification: C21 reads a repository with no session. Test two sessions sharing a worktree, two linked worktrees, paths with spaces and newlines, renamed files, empty history, and detached HEAD. Compare API results with Git output from the same repository state.

Evidence: Not recorded.

### P10-T02 — Build repository views and refresh behavior

Status: not_started

Depends on: P10-T01

Context: The requested interface combines commit history, file tree, working changes, and selected diffs. It must stay external to both host frontends.

Task: Add the Git workspace page and Kitty pane. Provide commit selection, changed-file groups, tree navigation, diff display, and paging. Refresh on relevant canonical shell outcomes, repository file notices, and extension command outcomes. Query Git to confirm the resulting state. Coalesce related notices through the host source API.

Outcomes: Both displays show consistent repository data and selection. A command start does not imply successful completion. External changes appear without an idle full-repository scan.

Code areas: External web components, terminal layouts, source subscriptions, query cache, and view state.

Verification: C18, C20, C21, and C28 cover both views, live file edits, commit selection, paging, resize, reconnect, and disable. Two sessions show one shared repository state. Remove a worktree and display a recoverable unavailable state.

Evidence: Not recorded.

### P10-T03 — Add file selection and commit

Status: not_started

Depends on: P10-T02, P05-T03

Context: Commit must use the user's selected changes and preserve unrelated work. Several sessions or tools can change the same index.

Task: Add explicit stage and unstage actions, an editable commit message, and Commit. Use a per-repository command queue. Record expected HEAD and index state with the selection. Check them before a write. Use the actual Git index and make its shared effect visible. Preserve message drafts after failed actions.

Outcomes: The user can select files and commit the intended staged changes. Concurrent changes produce a clear state conflict. Hook failures return useful results without losing the draft.

Code areas: External command handlers, operation schemas, web commit form, Kitty actions, and repository fixtures.

Verification: Test selected and unselected files, pre-existing staged changes, concurrent index edits, an empty message, a failed hook, and a successful commit. Inspect the resulting commit through Git and the public query API. C22–C24 cover duplicate requests, stale state, and read-only mode.

Evidence: Not recorded.

### P10-T04 — Add commit message generation

Status: not_started

Depends on: P10-T03

Context: The user wants a Generate message button and an editable result. The host already has an inference service.

Task: Add a command that reads the selected staged diff at a recorded revision and calls the public inference service. Bound large input and report any omitted files. Return a draft message. Detect selection changes while generation runs. Let the user edit the result before Commit.

Outcomes: Generation uses the existing configured model service. It does not automatically commit or push. A stale generated result cannot overwrite a newer user edit.

Code areas: External generation command, prompt and result models, draft state, settings, and model fixture.

Verification: Use a deterministic model fixture to inspect the exact selected diff and resulting draft. Test model failure, cancellation, large input, changed selection, and a user edit during generation. Confirm no Git write occurs until the explicit Commit action.

Evidence: Not recorded.

### P10-T05 — Add push and command reconciliation

Status: not_started

Depends on: P10-T03

Context: Push is an external write. A lost worker reply must not trigger an automatic repeated operation with unknown effects.

Task: Add remote and branch selection, a visible Push action, progress, and typed results. Use the normal non-force push path for the first version. Add cancellation reporting and reconciliation for lost commit or push results. Check repository identity and branch again when queued work starts.

Outcomes: Push succeeds or reports authentication, hook, branch, or remote rejection clearly. Unknown results remain explicit until reconciled. User drafts and selections survive failure.

Code areas: External Git command and reconciliation handlers, job result models, web and Kitty actions.

Verification: Use a local bare remote. Test successful push, rejected non-fast-forward push, hook failure, missing upstream, changed branch, canceled request, and lost reply after success. Compare remote refs. C22–C24 prove no blind repeated write and correct policy handling.

Evidence: Not recorded.

### P10-T06 — Add adapters cooperation and release checks

Status: not_started

Depends on: P10-T04, P10-T05, P09-T06

Context: Direct Git commands and adapters commit or push can describe the same underlying operation. The Git extension must work both with and without adapters.

Task: Consume the adapters public command-activity contract as an optional dependency. Refresh confirmed repository state and link activity through invocation and repository identities. Avoid duplicate operation entries when sources overlap. Run all feature and cooperation scenarios from the external package.

Outcomes: Direct Git, UI actions, adapters commit, and adapters push update the same repository page. Removing adapters removes only its activity integration. The Git package is independently buildable and testable.

Code areas: External optional service consumer, activity correlation, manifest, CI, and E2E cases.

Verification: C15, C16, C26, and C28 cover Git alone, both activation orders, adapters disable, repeated observations, and external-only frontend updates. Run package `make lint`, `make test`, and applicable `make e2e`. Record real Kitty and any live adapters evidence separately.

Evidence: Not recorded.
