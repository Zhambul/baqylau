Feature: terminal input during active work

  Scenario Outline: terminal Enter inserts a message at the first safe point
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I start journey session "primary" from the terminal as turn "active work" with prompt
      """
      Run `python3 -c 'import time; time.sleep(12); print("active-work-finished")'`
      as a foreground shell command. Do not run it in the background. Wait for
      it, and then reply only with ORIGINAL_ACTIVE_REPLY.
      """
    And I name the only running foreground command in turn "active work" containing 'time.sleep(12)' "active command"
    And I continue journey session "primary" from the terminal as turn "terminal steering" with prompt
      """
      Change the final reply for this active turn. After the command finishes, reply only with TERMINAL_STEERING_DONE.
      """
    Then command "active command" has state succeeded
    And command "active command" finishes before message from "terminal steering" enters the chat
    And turn "terminal steering" completes
    And turn "terminal steering" has final answer 'TERMINAL_STEERING_DONE'
    And session "primary" has no running work
    When I close the terminal for journey session "primary"

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |
