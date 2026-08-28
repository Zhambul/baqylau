Feature: background state survives a Baqylau restart

  Scenario Outline: an empty foreground command completes through restart
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I start journey session "primary" from the dashboard as turn "empty command through restart" with prompt
      """
      Run only `python3 -c 'import time; time.sleep(12)'` as one foreground shell
      command. <execution_instruction> Wait for it. Do not run another tool.
      Then, reply only with the exact marker EMPTY_RESTART_DONE.
      """
    And I name the only running foreground command in turn "empty command through restart" containing 'time.sleep(12)' "empty restart command"
    When I restart Baqylau as application restart "empty command restart"
    Then application restart "empty command restart" replaces the server process
    And command "empty restart command" has state succeeded
    And turn "empty command through restart" completes
    And session "primary" has no running work
    And turn "empty command through restart" has exactly 0 backgrounded command
    And turn "empty command through restart" has final answer 'EMPTY_RESTART_DONE'

    Examples:
      | harness     | model        | execution_instruction                                                       |
      | codex       | gpt-5.6-luna | Use the shell execution tool with a 30000 ms yield time.                     |
      | claude_code | haiku        | Use the Bash tool in the foreground. Do not set run_in_background to true.  |
