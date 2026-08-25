Feature: prompts sent during active work wait for that work

  Scenario Outline: a prompt sent during active work waits for the active command
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" as turn "active work" with prompt
      """
      Run `python -c 'import time; time.sleep(8); print("active-work-finished")'`
      as a foreground shell command. Do not run it in the background. Wait for
      it, and then reply only with ACTIVE_WORK_DONE.
      """
    And I name the only running foreground command in turn "active work" containing 'time.sleep(8)' "active command"
    And I send prompt to session "primary" as turn "queued work" and control "queued delivery"
      """
      Reply only with QUEUED_WORK_DONE.
      """
    Then control "queued delivery" response is accepted
    And control "queued delivery" outcome is acknowledged
    And control "queued delivery" reports queued delivery
    And session "primary" has control "queued delivery" queued as prompt 'Reply only with QUEUED_WORK_DONE.' after a fresh application read
    And command "active command" has state succeeded
    And turn "queued work" produces its final answer after command "active command" finishes
    And turn "queued work" completes
    And session "primary" has no queued prompts after a fresh application read
    And session "primary" has no running work

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |
