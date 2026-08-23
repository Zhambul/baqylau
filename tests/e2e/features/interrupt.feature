Feature: interrupting a turn reports the turn and command outcomes

  Scenario: a Codex interrupt aborts its turn and tracks the command that continues
    Given session configuration "primary" uses codex with model gpt-5.6-luna and low effort
    When I launch session "primary" as turn "long command" with prompt
      """
      Run `sleep 15; echo native-command-finished` as a foreground shell command.
      Do not run it in the background. Wait for it before you reply.
      """
    And I name the only running foreground command in turn "long command" containing 'sleep 15' "long sleep"
    And I request interruption in session "primary" as control "stop long command"
    Then control "stop long command" response is accepted
    And control "stop long command" outcome is acknowledged
    And turn "long command" has state aborted
    And command "long sleep" becomes a background job
    And job "long sleep" is running
    And job "long sleep" has output containing 'native-command-finished'
    And job "long sleep" ends
    And command "long sleep" has state succeeded
    And session "primary" has no running work

  Scenario: a Claude Code interrupt cancels a long command and aborts its turn
    Given session configuration "primary" uses claude_code with model haiku and low effort
    When I launch session "primary" as turn "long command" with prompt
      """
      Run `python -c 'import time; time.sleep(60); print("should-not-finish")'` as a foreground shell command.
      Do not run it in the background. Wait for it before you reply.
      """
    And I name the only running foreground command in turn "long command" containing 'time.sleep(60)' "long sleep"
    And I request interruption in session "primary" as control "stop long command"
    Then control "stop long command" response is accepted
    And control "stop long command" outcome is acknowledged
    And turn "long command" has state aborted
    And command "long sleep" has state cancelled
    And session "primary" has no running work
