Feature: sessions cross dashboard and terminal boundaries

  Scenario Outline: a blocked Stop hook keeps the terminal tab busy
    # Harness limit: claude_code only. Only Claude Code supports a Stop hook.
    Given session configuration "primary" uses <harness> with model <model> and low effort in the isolated repository workspace
    And the isolated repository has a blocking Claude Stop hook
    When I start journey session "primary" from the terminal as turn "blocked stop" with prompt
      """
      Reply only with BEFORE_BLOCKED_STOP.
      """
    Then the blocking Claude Stop hook starts
    And the blocked Stop hook feedback starts a new turn in session "primary"
    And for 4 seconds the terminal tab for journey session "primary" does not have color awaiting_response

    Examples:
      | harness     | model |
      | claude_code | haiku |

  Scenario Outline: closing the native terminal finishes every known actor
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I start journey session "primary" from the terminal as turn "native session start" with prompt
      """
      Reply only with NATIVE_SESSION_READY.
      """
    Then turn "native session start" completes
    And turn "native session start" has final answer 'NATIVE_SESSION_READY'
    When I assign work "actor before native exit" in session "primary" to the <worker> with prompt
      """
      Reply only with ACTOR_READY.
      """
    Then work "actor before native exit" completes
    And work "actor before native exit" has worker type <worker>
    And work "actor before native exit" has final answer 'ACTOR_READY'
    When I close the terminal for journey session "primary"
    Then session "primary" and all its actors finish

    Examples:
      | harness     | model        | worker   |
      | codex       | gpt-5.6-luna | lead     |
      | codex       | gpt-5.6-luna | subagent |
      | claude_code | haiku        | lead     |
      | claude_code | haiku        | subagent |

  Scenario Outline: one live session accepts work from both client origins
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I start journey session "primary" from the <start_origin> as turn "first work" with prompt
      """
      Remember the marker journey-memory-417. Reply only with FIRST_DONE.
      """
    Then turn "first work" completes
    And turn "first work" has final answer 'FIRST_DONE'
    When I continue journey session "primary" from the <continue_origin> as turn "second work" with prompt
      """
      If you remember journey-memory-417, reply only with CONTINUE_DONE.
      """
    Then turn "second work" completes
    And turn "second work" has final answer 'CONTINUE_DONE'

    Examples:
      | harness     | model        | start_origin | continue_origin |
      | codex       | gpt-5.6-luna | dashboard    | dashboard        |
      | codex       | gpt-5.6-luna | terminal     | dashboard        |
      | codex       | gpt-5.6-luna | dashboard    | terminal         |
      | claude_code | haiku        | dashboard    | dashboard        |
      | claude_code | haiku        | terminal     | dashboard        |
      | claude_code | haiku        | dashboard    | terminal         |

  Scenario Outline: a closed session resumes across client origins
    Given session configuration "primary" uses <harness> with model <model> and low effort
    And session configuration "primary" uses <account_mode> account
    When I start journey session "primary" from the <start_origin> as turn "before resume" with prompt
      """
      Reply only with BEFORE_RESUME.
      """
    Then turn "before resume" completes
    And turn "before resume" has final answer 'BEFORE_RESUME'
    When I rename session "primary" to 'Saved resume title 638' as control "saved resume name"
    Then control "saved resume name" response is accepted
    And control "saved resume name" outcome is acknowledged
    And session "primary" has title 'Saved resume title 638'
    When I close the terminal for journey session "primary"
    And I resume journey session "primary" from the <resume_origin> as turn "after resume" with prompt
      """
      Reply only with AFTER_RESUME.
      """
    Then turn "after resume" completes
    And turn "after resume" has final answer 'AFTER_RESUME'
    And journey session "primary" uses its exact saved resume metadata
    And journey session "primary" has one live terminal and one logical lineage
    And session "primary" is live
    And the terminal tab for journey session "primary" has color awaiting_response
    When I close the terminal for journey session "primary"
    Then session "primary" and all its actors finish

    Examples:
      | harness     | model        | account_mode         | start_origin | resume_origin |
      | codex       | gpt-5.6-luna | no                   | dashboard    | terminal      |
      | codex       | gpt-5.6-luna | no                   | terminal     | dashboard     |
      | codex       | gpt-5.6-luna | no                   | terminal     | terminal      |
      | claude_code | haiku        | no      | dashboard    | terminal      |
      | claude_code | haiku        | no      | terminal     | dashboard     |
      | claude_code | haiku        | no      | terminal     | terminal      |

  Scenario Outline: a terminal-origin session can assign subagent work
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I start journey session "primary" from the terminal as turn "lead start" with prompt
      """
      Reply only with LEAD_READY.
      """
    Then turn "lead start" completes
    When I assign work "journey child" in session "primary" to the subagent with prompt
      """
      Reply with the exact marker JOURNEY_CHILD_DONE and no other text.
      """
    Then work "journey child" completes
    And work "journey child" has worker type subagent
    And work "journey child" has final answer 'JOURNEY_CHILD_DONE'

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |

  Scenario Outline: a terminal tab stays blue while the lead waits for a subagent
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I start journey session "primary" from the terminal as turn "lead start" with prompt
      """
      Reply only with LEAD_READY.
      """
    Then turn "lead start" completes
    When I assign work "terminal color work" in session "primary" to the named subagent with prompt
      """
      Run the exact foreground shell command `sleep 20`. After it finishes,
      reply only with TERMINAL_COLOR_DONE.
      """
    Then subagent work "terminal color work" is running while its lead has status awaiting_background
    And the terminal tab for journey session "primary" has color awaiting_background
    And work "terminal color work" completes
    And work "terminal color work" has final answer 'TERMINAL_COLOR_DONE'
    And work "terminal color work" releases the lead
    And the terminal tab for journey session "primary" has color awaiting_response

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |

  Scenario Outline: completed parallel commands do not leave the terminal tab blue
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I start journey session "primary" from the terminal as turn "parallel command completion" with prompt
      """
      Run these three shell commands at the same time as three separate
      foreground commands:
      `printf correlation-alpha-917`
      `printf correlation-beta-917`
      `printf correlation-gamma-917`
      Wait for all three commands. Then run `true` as one foreground command.
      Wait for it and reply only with COMMANDS_SETTLED.
      """
    Then turn "parallel command completion" completes
    And turn "parallel command completion" has final answer 'COMMANDS_SETTLED'
    When I name the only shell command in turn "parallel command completion" containing 'true' "blank command"
    Then command "blank command" has state succeeded
    And the terminal tab for journey session "primary" has color awaiting_response

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |

  Scenario Outline: renaming a completed session keeps its terminal tab done
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I start journey session "primary" from the terminal as turn "before rename" with prompt
      """
      Do not use tools. Reply only with READY_TO_RENAME.
      """
    Then turn "before rename" completes
    And turn "before rename" has final answer 'READY_TO_RENAME'
    And the terminal tab for journey session "primary" has color awaiting_response
    When I rename session "primary" to 'Renamed completed E2E session' as control "completed rename"
    Then control "completed rename" response is accepted
    And control "completed rename" outcome is acknowledged
    And session "primary" has title 'Renamed completed E2E session'
    And the lead in session "primary" has status awaiting_response
    And the terminal tab for journey session "primary" has color awaiting_response
    When I request an automatic name for session "primary" as control "completed automatic rename"
    Then control "completed automatic rename" response is accepted
    And control "completed automatic rename" outcome is acknowledged
    And session "primary" title is not 'Renamed completed E2E session'
    And the lead in session "primary" has status awaiting_response
    And the terminal tab for journey session "primary" has color awaiting_response

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |

  Scenario Outline: stopping a background command returns its terminal tab to done
    # Harness limit: claude_code only. Only Claude Code exposes a native background command stop action.
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I start journey session "primary" from the terminal as turn "stop background command" with prompt
      """
      Your first tool call must use Bash to run the exact command `sleep 120`
      with run_in_background set to true. After Bash returns the background task
      ID, load TaskStop if needed and use TaskStop for that exact task ID. Do not
      wait for automatic completion. Then, reply only with BACKGROUND_STOPPED.
      """
    Then turn "stop background command" completes
    And turn "stop background command" has final answer 'BACKGROUND_STOPPED'
    When I name the only background job in turn "stop background command" containing 'sleep 120' "stopped background command"
    Then job "stopped background command" ends
    And session "primary" has no running work
    And the lead in session "primary" has status awaiting_response
    And the terminal tab for journey session "primary" has color awaiting_response

    Examples:
      | harness     | model |
      | claude_code | haiku |
