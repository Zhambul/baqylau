Feature: sessions cross dashboard and terminal boundaries

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
      Remember the marker resume-memory-638. Reply only with BEFORE_RESUME.
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
      If you remember resume-memory-638, reply only with AFTER_RESUME.
      """
    Then turn "after resume" completes
    And turn "after resume" has final answer 'AFTER_RESUME'
    And journey session "primary" uses its exact saved resume metadata
    And journey session "primary" has one live terminal and one logical lineage

    Examples:
      | harness     | model        | account_mode         | start_origin | resume_origin |
      | codex       | gpt-5.6-luna | no                   | dashboard    | terminal      |
      | codex       | gpt-5.6-luna | no                   | terminal     | dashboard     |
      | claude_code | haiku        | no      | dashboard    | terminal      |
      | claude_code | haiku        | no      | terminal     | dashboard     |

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
      Use the exec tool exactly two times.

      In the first exec JavaScript cell, call tools.exec_command for each of
      these three shell commands at the same time with Promise.all:
      `printf correlation-alpha-917`
      `printf correlation-beta-917`
      `printf correlation-gamma-917`
      Set yield_time_ms to 30000 for each command. Wait for all three results
      and print their outputs from the cell.

      In the second exec JavaScript cell, call tools.exec_command for the shell
      command `true` with yield_time_ms set to 30000. Print only r.output from
      that cell. Then reply only with COMMANDS_SETTLED.
      """
    Then turn "parallel command completion" completes
    And turn "parallel command completion" has final answer 'COMMANDS_SETTLED'
    When I name the only shell command in turn "parallel command completion" containing 'true' "blank command"
    Then command "blank command" has state succeeded
    And command "blank command" has exit code 0
    And the terminal tab for journey session "primary" has color awaiting_response

    Examples:
      | harness | model        |
      | codex   | gpt-5.6-luna |

  Scenario Outline: interrupting a busy terminal returns its tab to done
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I start journey session "primary" from the terminal as turn "interrupted terminal work" with prompt
      """
      Run `python -c 'import time; time.sleep(30); print("should-not-finish")'`
      as a foreground shell command. Do not run it in the background. Wait for
      it before you reply.
      """
    And I name the only running foreground command in turn "interrupted terminal work" containing 'time.sleep(30)' "interrupted terminal command"
    Then the terminal tab for journey session "primary" has color executing
    When I request interruption in session "primary" as control "interrupt busy terminal"
    Then control "interrupt busy terminal" response is accepted
    And control "interrupt busy terminal" outcome is acknowledged
    And command "interrupted terminal command" has state cancelled
    And the lead in session "primary" has status awaiting_response
    And the terminal tab for journey session "primary" has color awaiting_response
    And session "primary" has no running work

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |
