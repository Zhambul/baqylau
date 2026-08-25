# E2E coverage completion plan

This file is the completion ledger for the live E2E expansion. Update a row only
when the listed proof exists. `Complete` means that the behavior has a readable
Gherkin case, reusable typed test support, and the required checks pass.

Status values:

- `Complete`: the required proof exists and passes.
- `Partial`: some proof exists, but the complete behavior is not proved.
- `Not started`: no direct E2E proof exists.
- `Blocked`: the test cannot run because a required external capability is not
  available. A blocked row is not complete.

## Coverage table

| ID | Area | Required E2E behavior | Required proof | Status |
|---|---|---|---|---|
| J01 | Journey | Start from the dashboard and continue from the dashboard | Common journey case for Codex and Claude Code | Complete |
| J02 | Journey | Start directly in the terminal and continue from the dashboard | Common journey case for Codex and Claude Code | Complete |
| J03 | Journey | Start from the dashboard and continue from the terminal | Common journey case for Codex and Claude Code | Complete |
| J04 | Journey | Start from the dashboard and resume from the terminal | The old and new native sessions form one lineage | Complete |
| J05 | Journey | Start in the terminal and resume from the dashboard | The old and new native sessions form one lineage | Complete |
| J06 | Journey | Keep one live terminal and one pane set after a continuation | Session lineage and terminal ownership checks | Complete |
| R01 | Restart | Restart Baqylau while a harness session is idle | The same session remains readable and controllable | Complete |
| R02 | Restart | Restart Baqylau while background work runs | Work finishes with no missing or repeated event | Complete |
| R03 | Restart | Keep history, preferences, queue state, and resume state after restart | Black-box restart case with one data directory | Complete |
| R04 | Restart | macOS starts the installed daemon again after it stops | Opt-in launchd test with the installed service | Complete |
| C01 | Compaction | Keep required conversation facts after compaction | The model returns markers learned before compaction | Complete |
| W01 | Rewind | Restore conversation and remove newer conversation state | A post-rewind answer proves kept and removed markers | Complete |
| W02 | Rewind | Restore Claude Code file changes with `code` mode | A real file returns to its checkpoint content | Complete |
| W03 | Rewind | Restore Claude Code files and conversation with `both` mode | File and memory checks pass together | Complete |
| W04 | Rewind | Keep files unchanged with conversation-only mode | A real file keeps its newer content | Complete |
| P01 | Plan | Approve a plan from the dashboard in both harnesses | One common scenario with harness prompt data | Complete |
| P02 | Plan | Dismiss a pending plan in both harnesses | The plan resolves and the harness accepts later work | Complete |
| P03 | Plan | Send change feedback where supported | The plan records `changes_requested` and exact feedback | Complete |
| Q01 | Question | Dismiss a question and continue in chat | The exact chat text reaches the harness | Complete |
| Q02 | Question | Answer with free text | The resolved question records the free-text answer | Complete |
| Q03 | Question | Ask and answer more than one question in one dialog | Each question keeps its own answer | Complete |
| Q04 | Question | Save and restore an unfinished question draft | Session application state returns the exact draft | Complete |
| Q05 | Question | Read and answer a tall native question dialog | All question labels and options remain available | Complete |
| Q06 | Question | Show answered question labels in the dashboard | Each answer is paired with its full question text | Complete |
| A01 | Activity | Perform a real web search | Codex and Claude Code, lead and subagent rows | Complete |
| A02 | Activity | Fetch a real web page | Codex and Claude Code, lead and subagent rows | Complete |
| A03 | Activity | Report real model reasoning | Codex lead and subagent rows; Claude native thinking is unreadable | Partial |
| A04 | Activity | Send input to one running interactive shell | Input and final shell state belong to one shell ID | Complete |
| A05 | Activity | Report a worktree change where supported | Real Claude Code worktree action and typed check | Complete |
| A06 | Activity | Report a file rename where supported | Previous and current paths are exact | Complete |
| A07 | Activity | Report a failed file operation | Failed state and path are exact | Complete |
| A08 | Activity | Report a real session account selection or change | Account ID and display name are exact | Complete |
| A09 | Activity | Report context-window use | Used and window tokens are positive and consistent | Complete |
| A10 | Activity | Report an automatic native title | Both harnesses have a non-empty automatic title | Complete |
| S01 | Subagent | Interrupt active subagent work where supported | Child turn and assignment are cancelled | Complete |
| S02 | Subagent | Keep a failed child command on the child actor | Failed state, output, exit code, and attribution | Complete |
| S03 | Subagent | Resolve a question raised by a subagent where supported | Question and answer keep the child actor | Blocked |
| S04 | Subagent | Use task tools in a subagent where supported | Task ownership and changes keep the child actor | Complete |
| S05 | Subagent | Read a file in a subagent | Codex and Claude Code rows | Complete |
| S06 | Subagent | Start Claude Code background work in a subagent | Child owns the job through completion | Complete |
| S07 | Subagent | Delete a file in a Codex subagent | Creation and deletion keep the child actor | Complete |
| S08 | Subagent | Send a follow-up to an active subagent | The child response proves receipt | Complete |
| S09 | Subagent | Interrupt one of two active subagents where supported | Real Codex children: one cancels and one succeeds | Complete |
| S10 | Subagent | Run nested subagent work where supported | No installed harness exposes a nested spawn tool | Blocked |
| S11 | Subagent | Keep child-to-lead messages on the correct actors where supported | Real Claude sender, lead recipient, and exact content | Complete |
| S12 | Subagent | Keep the lead waiting while its child still runs | Both harnesses report the lead and child states separately | Complete |
| F01 | Feed | Read every entry through more than one page | Small page size, no missing or repeated entry | Complete |
| F02 | Feed | Read all pages at one snapshot cursor | No entry is newer than the snapshot cursor | Complete |
| F03 | Feed | Read new activity in the next snapshot only | Two named snapshots prove the boundary | Complete |
| F04 | Feed | Receive session SSE updates | Real session stream frame and cursor checks | Complete |
| F05 | Feed | Receive global SSE updates | Real global stream frame and cursor checks | Complete |
| F06 | Feed | Resume SSE with `Last-Event-ID` | No missing or repeated update after reconnect | Complete |
| U01 | Composer | Send a prompt while a turn is active | Both harnesses report and persist queued delivery, finish active work before prompt delivery, produce the queued answer, and drain the queue | Complete |
| U02 | Attachment | Send an attachment in a later turn | Both harnesses and both worker types | Complete |
| U03 | Attachment | Send an attachment with no text | Both harnesses read the file | Complete |
| U04 | Attachment | Send more than one attachment | Both harnesses read both exact markers | Complete |
| U05 | Attachment | Send a real image | Both harnesses report the expected visible marker | Complete |
| U06 | Session | Close a session while work is active | Both harnesses and worker types end the session, actors, turn, assignment, and shell | Complete |
| U07 | Session | Let the native harness exit without dashboard close | Real Kitty rows finish both harnesses and all lead or subagent actors | Complete |
| U08 | Resume | Resume a closed session with its model, effort, title, and account | Exact metadata and usable continued conversation | Complete |
| U09 | Resume | Filter and order resumable sessions | Title, ID search, active state, and newest-first checks | Complete |
| U10 | Repository | Report branch, worktree, and dirty state | Real repository changes and typed snapshot checks | Complete |
| U11 | Insights | Attribute exact insight changes to one scenario | Before and after snapshots show the exact delta | Complete |
| U12 | Task | Report pending, active, and completed task states | Real state changes are visible in order | Complete |
| U13 | Task | Keep task owner and description | Exact actor owner and description checks | Complete |
| U14 | Goal | Report active, blocked, complete, and cleared goal states where supported | Each exposed state change is observed | Complete |
| U15 | Title | Rename and auto-name a parked session where supported | Native stored title changes without a live terminal | Complete |
| U16 | Catalog | Prove advertised capabilities and rewind modes | Catalog values match runnable E2E behavior | Complete |
| T01 | Real terminal | Create the real terminal tab and pane set | Opt-in Kitty test uses the installed terminal | Complete |
| T02 | Real terminal | Toggle, grow, shrink, reset, and set pane width | Every pane gesture has a real geometry check | Complete |
| T03 | Real terminal | Paint running and completed tab status colors | Real Kitty blue and green tab color checks, including a busy terminal that is interrupted | Complete |
| T04 | Real terminal | Do not steal focus during pane and dashboard launches | Real focused-window checks | Complete |
| T05 | Real terminal | Recover pane ownership after daemon restart | No duplicate panes and controls still work | Complete |
| B01 | Browser | Start and resume through the new-session form | Browser drives the real form for both harnesses | Complete |
| B02 | Browser | Drain the composer queue | Real browser queue sends each message once | Complete |
| B03 | Browser | Load older feed pages | Every older page loads and the oldest marker appears exactly once | Complete |
| B04 | Browser | Answer question and plan cards | Answer, discuss, approve, dismiss, and feedback paths | Complete |
| B05 | Browser | Update the session list through SSE | New, changed, and finished sessions update without reload | Complete |
| B06 | Browser | Show attention badges | Pending and resolved attention update the badge | Complete |
| B07 | Browser | Keep parked project groups out of the live list | The live list is empty after close and a new live session restores its project group | Complete |
| B08 | Browser | Save notification state | Enable, mute, and session state persist | Complete |
| B09 | Browser | Reconnect after an SSE drop | The page keeps all updates and shows no duplicate | Complete |
| B10 | Browser | Keep a new-session draft through modal close and page reload | Common Codex and Claude Code form rows, with the exact backend draft | Complete |
| B11 | Browser | Render distinct added and removed diff colors | Computed production colors from a real file update | Complete |
| B12 | Browser | Keep a native Codex name through park, restart, and resume | Real `/rename`, application restart, browser header, and resumed turn | Complete |
| B13 | Browser | Show the native Claude Fable model limit | API scope, percentage, reset time, visible bar, and weekly reset | Complete |
| B14 | Browser | Group a linked worktree with its main checkout | Real Git worktree, two live sessions, and one project group | Complete |

## GitHub bug regression map

| Issue | Regression proof | Harness coverage | Status |
|---|---|---|---|
| #1 | Active-turn interrupt cancels the command and returns the lead to `awaiting_response`; the real terminal journey checks blue to green | Codex and Claude Code | Complete |
| #29 | One native compaction produces one finished feed entry | Codex | Complete |
| #32 | Separate session composer drafts survive navigation and reload, and only the sent draft clears | Codex and Claude Code | Complete |
| #33 | A queued prompt has its visible badge through reload and loses it only after delivery | Codex and Claude Code | Complete |
| #34 | A real file update uses distinct computed added and removed colors | Codex and Claude Code | Complete |
| #35 | Main checkout and linked worktree have one project group | Codex and Claude Code | Complete |
| #38 | A native Codex name survives park, Baqylau restart, and real resume | Codex | Complete |
| #39 | Native Claude profile usage supplies the Fable percentage and reset; collection failures preserve it and remain visible | Claude Code | Complete |
| #40 | Parked sessions stay out of the live list and enter the resume catalog only in resume mode | Codex and Claude Code | Complete |

## Current blockers

- `S03`: Codex CLI 0.149.1 reports that `request_user_input` is for the root
  thread only. Claude Code 2.1.241 does not give `AskUserQuestion` to an Agent
  child. Both limits were verified with real subagents on 2026-08-24.
- `S10`: Codex CLI 0.149.1 gives the lead `multi_agent_v2__spawn_agent`, but a
  real child reports that this tool is unavailable. Claude Code 2.1.241 does
  not give the `Agent` tool to an Agent child.
- `A03`: Claude Code 2.1.241 reports real thinking-token use, but its transcript
  stores an empty thinking body and a signature. Baqylau does not convert the
  signature into readable reasoning. Codex uses concise native summaries.
- Claude Code browser rows can skip when all configured accounts have no current
  capacity. The browser matrix selects an account from live capacity and reports
  the exact account state instead of starting an unusable session.
- `U14`: Codex exposes active, blocked, and complete through its goal tools.
  It emits a cleared event, but no installed harness exposes a user or agent
  control that clears a goal. The runnable states have direct E2E proof.
- `U15`: Both installed harnesses support stored rename for a parked session.
  Automatic naming uses a live native dialog, so both harnesses reject it after
  the terminal closes.

Claude Code 2.1.241 does not expose a usable live-child stop path. Child agents
do not have `TaskStop`. A root `TaskStop` prompt and a session interrupt are
both applied only after the child completion notification. The S01 case uses
the Codex worker-control row only.

## Work order

1. Add typed client states and named references for every new activity.
2. Add question discussion and plan dismissal or feedback.
3. Add the missing canonical activity features.
4. Add true subagent behavior cases.
5. Add feed pagination, snapshot, and SSE cases.
6. Add session journeys and black-box application restart support.
7. Add the remaining attachment, resume, repository, insight, task, goal, and
   title cases.
8. Add the opt-in real Kitty suite.
9. Add the browser suite with the same application process and named references.
10. Run the complete static and unit suite, the live harness rows, the real
    terminal suite, and the browser suite. Audit every row above before the goal
    is complete.

## Suite rules

- Use real harness actions. Do not replay recorded native events.
- Keep harness names and native tool names out of behavior text. Put required
  native prompt differences in example data or a typed adapter.
- Run each common happy path on the lead and on a subagent when the behavior can
  run on that worker.
- Use a worker row only when the tested behavior runs on that worker.
- Test session-origin and resume combinations in journey cases. Do not multiply
  every feature by the complete journey matrix.
- Read assertions through the typed client. Do not read logs or databases.
- At sign-off, every raw event has a verdict, every pipeline is drained, and the
  audit has no error.
