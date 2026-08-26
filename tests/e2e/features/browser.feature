Feature: the browser controls real harness sessions

  Scenario Outline: usage appears after the first application read misses it
    Given the next browser application read omits usage for <harness>
    When I open the browser session list
    Then the browser shows the <harness> usage row without reloading the document

    Examples:
      | harness |
      | codex   |

  Scenario Outline: a new-session draft survives modal close and page reload
    Given session configuration "draft form" uses <harness> with model <model> and low effort
    And the browser is on the session list
    When I open configured browser session form "first draft form" using session configuration "draft form"
    And I type 'preserve this browser draft 732' in browser session form "first draft form"
    And I close browser session form "first draft form"
    Then global new-session draft is 'preserve this browser draft 732'
    When I reload the browser session list
    And I open configured browser session form "restored draft form" using session configuration "draft form"
    Then browser session form "restored draft form" contains exact draft 'preserve this browser draft 732'
    When I close browser session form "restored draft form"

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |

  Scenario Outline: each session keeps its composer draft until it is sent
    Given session configuration "primary" uses <harness> with model <model> and low effort
    And session configuration "secondary" uses <harness> with model <model> and low effort
    When I start browser session "primary" as turn "primary ready" with prompt
      """
      Do not use tools. Reply only with PRIMARY_READY.
      """
    Then turn "primary ready" completes
    And turn "primary ready" has final answer 'PRIMARY_READY'
    When I start browser session "secondary" as turn "secondary ready" with prompt
      """
      Do not use tools. Reply only with SECONDARY_READY.
      """
    Then turn "secondary ready" completes
    And turn "secondary ready" has final answer 'SECONDARY_READY'
    When I open session "primary" in the browser
    And I type composer draft 'preserve primary draft 732' in the browser
    And I open session "secondary" in the browser
    And I type composer draft 'Do not use tools. Reply only with DRAFT_SENT_732.' in the browser
    And I open session "primary" in the browser
    Then the browser composer contains exact draft 'preserve primary draft 732'
    And session "primary" has composer draft 'preserve primary draft 732' after a fresh application read
    When I open session "secondary" in the browser
    Then the browser composer contains exact draft 'Do not use tools. Reply only with DRAFT_SENT_732.'
    When I reload browser session "secondary"
    Then the browser composer contains exact draft 'Do not use tools. Reply only with DRAFT_SENT_732.'
    When I send the browser composer for session "secondary" as turn "sent draft"
    Then turn "sent draft" completes
    And turn "sent draft" has final answer 'DRAFT_SENT_732'
    And the browser composer is empty
    And session "secondary" has no composer draft after a fresh application read
    When I open session "primary" in the browser
    Then the browser composer contains exact draft 'preserve primary draft 732'

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |

  Scenario Outline: file diffs use distinct added and removed colors
    Given the file operation fixture does not exist
    And session configuration "primary" uses <harness> with model <model> and low effort
    And session configuration "primary" uses <account> account
    When I launch session "primary" and assign work "browser diff" to the <worker> with prompt
      """
      Use file editing tools, not shell commands. First create
      baqylau-e2e-file.txt with the exact line browser-color-old. In a separate
      tool call, replace that line with browser-color-new. Reply only with
      BROWSER_DIFF_DONE.
      """
    Then work "browser diff" completes
    And work "browser diff" has worker type <worker>
    And work "browser diff" has final answer 'BROWSER_DIFF_DONE'
    When I name the updated fixture operation in work "browser diff" "browser diff operation"
    Then file operation "browser diff operation" has added lines
    And file operation "browser diff operation" has removed lines
    When I open session "primary" in the browser
    Then the browser renders added and removed colors for file operation "browser diff operation"

    Examples:
      | harness     | model        | account      | worker   |
      | codex       | gpt-5.6-luna | no           | lead     |
      | claude_code | haiku        | no           | subagent |

  Scenario Outline: a Git worktree and its main checkout share one project group
    Given session configuration "main checkout" uses <harness> with model <model> and low effort in the isolated repository root
    And session configuration "linked worktree" uses <harness> with model <model> and low effort in the isolated repository workspace
    And session configuration "main checkout" uses <account> account
    And session configuration "linked worktree" uses <account> account
    When I launch session "main checkout" as turn "main checkout ready" with prompt
      """
      Do not use tools. Reply only with MAIN_CHECKOUT_READY.
      """
    Then turn "main checkout ready" completes
    And turn "main checkout ready" has final answer 'MAIN_CHECKOUT_READY'
    When I launch session "linked worktree" as turn "linked worktree ready" with prompt
      """
      Do not use tools. Reply only with LINKED_WORKTREE_READY.
      """
    Then turn "linked worktree ready" completes
    And turn "linked worktree ready" has final answer 'LINKED_WORKTREE_READY'
    When I open the browser session list
    Then browser sessions "main checkout" and "linked worktree" share the isolated project group
    When I remove the isolated linked worktree
    And I reload the browser session list
    Then browser sessions "main checkout" and "linked worktree" share the isolated project group

    Examples:
      | harness     | model        | account      |
      | codex       | gpt-5.6-luna | no           |
      | claude_code | haiku        | no           |

  Scenario Outline: a native session name survives parking restart and resume
    Given session configuration "primary" uses <harness> with model <model> and low effort
    And session configuration "primary" uses <account> account
    When I launch session "primary" as turn "native name ready" with prompt
      """
      Do not use tools. Reply only with NATIVE_NAME_READY.
      """
    Then turn "native name ready" completes
    And turn "native name ready" has final answer 'NATIVE_NAME_READY'
    When I open session "primary" in the browser
    And I send native command '/rename Native E2E 738' to session "primary" as control "native rename"
    Then control "native rename" response is accepted
    And control "native rename" outcome is acknowledged
    And session "primary" has title 'Native E2E 738'
    And the browser session header has title 'Native E2E 738'
    When I close session "primary" as control "park native named session"
    Then control "park native named session" response is accepted
    And control "park native named session" outcome is acknowledged
    And session "primary" finishes
    When I mark the current browser document for connection recovery
    When I restart Baqylau as application restart "native name restart"
    Then application restart "native name restart" replaces the server process
    And the browser event stream reconnects without a reload
    And session "primary" has title 'Native E2E 738'
    When I resume browser session "primary" as turn "after native name resume" with prompt
      """
      Reply only with NATIVE_NAME_RESUMED.
      """
    Then turn "after native name resume" completes
    And turn "after native name resume" has final answer 'NATIVE_NAME_RESUMED'
    And session "primary" has title 'Native E2E 738'
    And the browser session header has title 'Native E2E 738'

    Examples:
      | harness     | model        | account      |
      | codex       | gpt-5.6-luna | no           |
      | claude_code | haiku        | no           |

  Scenario Outline: the browser shows one default profile and live Fable usage
    Given session configuration "claude form" uses <harness> with model <model> and low effort
    And session configuration "claude form" uses no account
    Given the browser is on the session list
    Then the browser shows the claude_code fable model usage limit for its default account
    When I open configured browser session form "claude form" using session configuration "claude form"
    Then browser session form "claude form" has no account selection
    When I close browser session form "claude form"

    Examples:
      | harness     | model |
      | claude_code | haiku |

  Scenario Outline: a browser starts and resumes one real session
    Given session configuration "primary" uses <harness> with model <model> and low effort
    And session configuration "primary" uses <account> account
    And the browser is on the session list
    When I start browser session "primary" as turn "before browser resume" with prompt
      """
      Remember the marker browser-resume-824. Reply only with BROWSER_STARTED.
      """
    Then turn "before browser resume" completes
    And turn "before browser resume" has final answer 'BROWSER_STARTED'
    And the browser shows session "primary"
    And the browser shows the exact text 'BROWSER_STARTED'
    When I rename session "primary" to 'Browser resume title 824' as control "saved browser resume name"
    Then control "saved browser resume name" response is accepted
    And control "saved browser resume name" outcome is acknowledged
    And session "primary" has title 'Browser resume title 824'
    When I close session "primary" as control "prepare browser resume"
    Then control "prepare browser resume" response is accepted
    And control "prepare browser resume" outcome is acknowledged
    And session "primary" finishes
    When I open the browser session list
    And I reload the browser session list
    Then the browser session list does not show session "primary"
    And a fresh application session list does not contain session "primary"
    When I open fresh browser session form "parked session picker" for session "primary"
    Then browser session form "parked session picker" has not requested the resume catalog
    When I switch browser session form "parked session picker" to resume mode
    Then browser session form "parked session picker" requests the resume catalog
    And browser session form "parked session picker" offers session "primary"
    When I resume session "primary" from browser session form "parked session picker" as turn "after browser resume" with prompt
      """
      If you remember browser-resume-824, reply only with BROWSER_RESUMED.
      """
    Then turn "after browser resume" completes
    And turn "after browser resume" has final answer 'BROWSER_RESUMED'
    And the browser shows session "primary"
    And the browser shows the exact text 'BROWSER_RESUMED'
    And browser resume "primary" keeps its metadata and one live session
    When I close browser session "primary"
    Then session "primary" finishes
    And the browser session list does not show session "primary"
    And a fresh application session list does not contain session "primary"

    Examples:
      | harness     | model        | account |
      | codex       | gpt-5.6-luna | no      |
      | claude_code | haiku        | no           |

  Scenario Outline: a browser keeps and drains a prompt queued during active work
    Given session configuration "primary" uses <harness> with model <model> and low effort
    And session configuration "primary" uses <account> account
    When I launch session "primary" as turn "active work" with prompt
      """
      Run `while [ ! -f .baqylau-browser-active-release ]; do sleep 0.2; done; printf 'browser-active-finished\n'`
      as a foreground shell command. Do not run it in the background. Wait for
      it, and then reply only with BROWSER_ACTIVE_DONE.
      """
    And I name the only running foreground command in turn "active work" containing 'baqylau-browser-active-release' "observed active command"
    And I open session "primary" in the browser
    And I send browser prompt to session "primary" as turn "queued work"
      """
      Reply with the exact marker BROWSER_QUEUED_DONE and no other text.
      """
    Then the browser shows queued prompt 'Reply with the exact marker BROWSER_QUEUED_DONE and no other text.'
    And session "primary" has queued prompt 'Reply with the exact marker BROWSER_QUEUED_DONE and no other text.' after a fresh application read
    When I reload browser session "primary"
    Then the browser shows queued prompt 'Reply with the exact marker BROWSER_QUEUED_DONE and no other text.'
    When I release active browser work in session "primary"
    And I name a successful shell attempt in turn "active work" containing 'baqylau-browser-active-release' "active command"
    Then command "active command" has state succeeded
    And turn "queued work" produces its final answer after command "active command" finishes
    And turn "queued work" completes
    And turn "queued work" has final answer 'BROWSER_QUEUED_DONE'
    And the browser does not show queued prompt 'Reply with the exact marker BROWSER_QUEUED_DONE and no other text.'
    And session "primary" has no queued prompts after a fresh application read
    And session "primary" has no running work

    Examples:
      | harness     | model        | account |
      | codex       | gpt-5.6-luna | no      |
      | claude_code | haiku        | no           |

  Scenario Outline: a browser loads older activity from one consistent feed
    Given session configuration "primary" uses <harness> with model <model> and low effort
    And session configuration "primary" uses <account> account
    When I launch session "primary" and assign work "oldest browser activity" to the lead with prompt
      """
      Remember the marker BROWSER-OLDEST-ACTIVITY-731.
      Use 21 separate foreground shell-tool calls in one parallel batch.
      Each tool call must run only
      one command of the form `printf 'history-NN\n'`, where NN starts at 01
      and ends at 21. Do not combine the commands into one shell command.
      Wait for all 21 calls to finish. Then reply only with BROWSER_HISTORY_DONE.
      """
    Then work "oldest browser activity" completes
    And work "oldest browser activity" has final answer 'BROWSER_HISTORY_DONE'
    When I open session "primary" in the browser
    Then the browser can load older session activity automatically containing 'BROWSER-OLDEST-ACTIVITY-731'
    When I scroll to older session activity in the browser
    Then the browser feed shows text containing 'BROWSER-OLDEST-ACTIVITY-731'

    Examples:
      | harness     | model        | account |
      | codex       | gpt-5.6-luna | no      |
      | claude_code | haiku        | no           |

  Scenario Outline: browser question cards support answers and discussion
    Given session configuration "primary" uses <harness> with model <model> and low effort
    And session configuration "primary" uses <account> account
    When I launch session "primary" and assign question work "answer card" to the lead with prompt
      """
      Ask "Which browser path should I use?" with the heading Path. Offer
      Direct with description "Use the direct path" and Safe with description
      "Use the safe path". Allow only one choice. After the answer, reply only
      with BROWSER_QUESTION_ANSWERED.
      """
    And I name the pending question in work "answer card" containing 'Which browser path' "path choice"
    And I open session "primary" in the browser
    Then the browser session header has status awaiting_attention and its canonical color
    And the browser attention badge for "primary" has status awaiting_attention and its canonical color
    And the browser has 1 asking session badges
    When I answer question "path choice" in the browser with option 'Safe'
    Then question "path choice" records option 'Safe'
    And question "path choice" is resolved
    And work "answer card" completes
    And work "answer card" has final answer 'BROWSER_QUESTION_ANSWERED'
    And the browser session header has status awaiting_response and its canonical color
    And the browser attention badge for "primary" has status awaiting_response and its canonical color
    And the browser has 0 asking session badges
    When I assign question work "discuss card" in session "primary" to the lead with prompt
      """
      Ask "Should we discuss the browser path?" with the heading Discussion.
      Offer Yes with description "Discuss it" and No with description "Do not
      discuss it". Allow only one choice. If the person chooses to chat about
      it, wait for the next message.
      """
    And I name the pending question in work "discuss card" containing 'Should we discuss' "discussion choice"
    Then the browser session header has status awaiting_attention and its canonical color
    And the browser has 1 asking session badges
    When I choose chat about question "discussion choice" in the browser
    Then question "discussion choice" is resolved
    When I send browser prompt to session "primary" as turn "discussion reply"
      """
      Reply only with BROWSER_QUESTION_DISCUSSION.
      """
    Then turn "discussion reply" completes
    And turn "discussion reply" has final answer 'BROWSER_QUESTION_DISCUSSION'
    And the browser has 0 asking session badges

    Examples:
      | harness     | model        | account |
      | codex       | gpt-5.6-luna | no      |
      | claude_code | haiku        | no           |

  Scenario Outline: browser plan cards support approval and discussion
    Given session configuration "primary" uses <harness> with model <model> and low effort
    And session configuration "primary" uses <account> account
    When I launch session "primary" as turn "ready for browser plans" with prompt
      """
      Do not use tools. Reply only with BROWSER_PLAN_READY.
      """
    Then turn "ready for browser plans" completes
    When I open session "primary" in the browser
    When I start plan work "approve browser plan" in session "primary" with prompt
      """
      Make a plan that contains the exact marker BROWSER-PLAN-APPROVE-731.
      The plan must not change files or run commands. Wait for the person to
      decide. After approval, reply only with BROWSER_PLAN_APPROVED.
      """
    And I name the pending plan in turn "approve browser plan" containing 'BROWSER-PLAN-APPROVE-731' "approved browser plan"
    When I approve plan "approved browser plan" in the browser as action "approve browser plan"
    Then plan "approved browser plan" has state approved
    And plan "approved browser plan" is followed by final answer 'BROWSER_PLAN_APPROVED' after browser action "approve browser plan"
    When I start plan work "discuss browser plan" in session "primary" with prompt
      """
      Make a plan that contains the exact marker BROWSER-PLAN-DISCUSS-731.
      The plan must not change files or run commands. Wait for the person to
      decide.
      """
    And I name the pending plan in turn "discuss browser plan" containing 'BROWSER-PLAN-DISCUSS-731' "discussed browser plan"
    When I choose chat about plan "discussed browser plan" in the browser
    Then plan "discussed browser plan" has state rejected
    When I send browser prompt to session "primary" as turn "continue after browser plan"
      """
      Reply only with BROWSER_PLAN_DISCUSSION.
      """
    Then turn "continue after browser plan" completes
    And turn "continue after browser plan" has final answer 'BROWSER_PLAN_DISCUSSION'

    Examples:
      | harness     | model        | account |
      | codex       | gpt-5.6-luna | no      |
      | claude_code | haiku        | no           |

  Scenario Outline: a browser plan card sends feedback where supported
    Given session configuration "primary" uses <harness> with model <model> and low effort
    And session configuration "primary" uses <account> account
    When I launch session "primary" as turn "ready for browser feedback" with prompt
      """
      Do not use tools. Reply only with BROWSER_FEEDBACK_READY.
      """
    Then turn "ready for browser feedback" completes
    When I open session "primary" in the browser
    When I start plan work "feedback browser plan" in session "primary" with prompt
      """
      Make a plan that contains the exact marker BROWSER-PLAN-FEEDBACK-731.
      The plan must not change files or run commands. Wait for the person to
      decide.
      """
    And I name the pending plan in turn "feedback browser plan" containing 'BROWSER-PLAN-FEEDBACK-731' "feedback browser plan"
    When I request plan changes 'start with browser tests' for plan "feedback browser plan" in the browser
    Then plan "feedback browser plan" has state changes_requested
    And plan "feedback browser plan" has feedback 'start with browser tests'

    Examples:
      | harness     | model | account |
      | claude_code | haiku | no      |

  Scenario Outline: browser preferences and the live list survive a connection change
    Given session configuration "primary" uses <harness> with model <model> and low effort
    And session configuration "primary" uses <account> account
    And session configuration "restored" uses <harness> with model <model> and low effort
    And session configuration "restored" uses <account> account
    And the browser is on the session list
    When I launch session "primary" and assign work "live browser state" to the subagent with prompt
      """
      Run `python3 -c 'import time; time.sleep(8)'` as one foreground command.
      Wait for it to finish. Then reply only with BROWSER_SUBAGENT_DONE.
      """
    Then subagent work "live browser state" is running while its lead has status awaiting_background
    And the browser session list shows session "primary"
    And the browser session card for "primary" has status awaiting_background and its canonical color
    And the browser attention badge for "primary" has status awaiting_background and its canonical color
    And work "live browser state" completes
    And work "live browser state" has final answer 'BROWSER_SUBAGENT_DONE'
    And work "live browser state" releases the lead
    And the browser session card for "primary" has status awaiting_response and its canonical color
    And the browser attention badge for "primary" has status awaiting_response and its canonical color
    When I open session "primary" in the browser
    And I mute alerts for session "primary" in the browser
    Then browser alerts for session "primary" are muted
    When I reload browser session "primary"
    Then browser alerts for session "primary" are muted
    When I enable alerts for session "primary" in the browser
    Then browser alerts for session "primary" are enabled
    When I close session "primary" as control "close browser preference session"
    Then control "close browser preference session" response is accepted
    And control "close browser preference session" outcome is acknowledged
    And session "primary" finishes
    When I open the browser session list
    And I disable global alerts in the browser
    Then global browser alerts are disabled
    When I reload the browser session list
    Then global browser alerts are disabled
    When I enable global alerts in the browser
    And I reload the browser session list
    Then global browser alerts are enabled
    And the browser event stream is connected
    When I mark the current browser document for connection recovery
    And I restart Baqylau as application restart "browser stream outage"
    Then application restart "browser stream outage" replaces the server process
    And the browser event stream reconnects without a reload
    When I launch session "restored" as turn "restore hidden workspace" with prompt
      """
      Do not use tools. Reply only with BROWSER_WORKSPACE_RESTORED.
      """
    Then the browser session list shows session "restored"
    And the browser shows the configured workspace group
    And turn "restore hidden workspace" completes
    And turn "restore hidden workspace" has final answer 'BROWSER_WORKSPACE_RESTORED'

    Examples:
      | harness     | model        | account |
      | codex       | gpt-5.6-luna | no      |
      | claude_code | haiku        | no           |
