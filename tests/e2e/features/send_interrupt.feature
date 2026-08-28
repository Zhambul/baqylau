Feature: Browser send and interrupt confirmation

  Scenario Outline: an immediate interrupt keeps a confirmed prompt
    Given session configuration "primary" uses <harness> with model <model> and low effort
    And session configuration "primary" uses no account
    When I launch session "primary" as turn "ready" with prompt
      """
      Do not use tools. Reply only with READY_TO_INTERRUPT.
      """
    Then turn "ready" completes
    And turn "ready" has final answer 'READY_TO_INTERRUPT'
    When I open session "primary" in the browser
    And I send browser prompt to session "primary" as turn "interrupted prompt"
      """
      Run `until [ -f baqylau-interrupt-release ]; do sleep 2; done` in the shell. After it finishes, reply only with SHOULD_NOT_FINISH.
      """
    And I request interruption in session "primary" as control "immediate stop"
    Then control "immediate stop" response is accepted
    And control "immediate stop" outcome is acknowledged
    And turn "interrupted prompt" has state aborted
    And the browser shows confirmed prompt 'Run `until [ -f baqylau-interrupt-release ]; do sleep 2; done` in the shell. After it finishes, reply only with SHOULD_NOT_FINISH.'
    When I reload browser session "primary"
    Then the browser shows confirmed prompt 'Run `until [ -f baqylau-interrupt-release ]; do sleep 2; done` in the shell. After it finishes, reply only with SHOULD_NOT_FINISH.'

    Examples:
      | harness     | model          |
      | codex       | gpt-5.6-luna   |
      | claude_code | haiku          |

  Scenario Outline: one idle Escape does not request Stop
    Given session configuration "primary" uses <harness> with model <model> and low effort
    And session configuration "primary" uses no account
    When I launch session "primary" as turn "ready" with prompt
      """
      Do not use tools. Reply only with READY_FOR_IDLE_ESCAPE.
      """
    Then turn "ready" completes
    And turn "ready" has final answer 'READY_FOR_IDLE_ESCAPE'
    When I open session "primary" in the browser
    Then one idle Escape does not request Stop

    Examples:
      | harness     | model          |
      | codex       | gpt-5.6-luna   |
      | claude_code | haiku          |
