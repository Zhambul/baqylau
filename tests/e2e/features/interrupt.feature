Feature: interrupting a turn reports the turn and command outcomes

  Scenario Outline: an interrupt cancels the active turn and command
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "prepare interruption" to the subagent with prompt
      """
      Reply only with the word prepared.
      """
    Then work "prepare interruption" completes
    And work "prepare interruption" has worker type subagent
    And work "prepare interruption" has final answer 'prepared'
    And work "prepare interruption" releases the lead
    When I assign work "long command" in session "primary" to the lead with prompt
      """
      Run `python -c 'import time; time.sleep(30); print("should-not-finish")'`
      as a foreground shell command. Do not run it in the background. Wait for
      it before you reply.
      """
    And I name the only running foreground command in work "long command" containing 'time.sleep(30)' "long sleep"
    And I request interruption in session "primary" as control "stop long command"
    Then control "stop long command" response is accepted
    And control "stop long command" outcome is acknowledged
    And work "long command" has state aborted
    And command "long sleep" has state cancelled
    And session "primary" has no running work

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |
