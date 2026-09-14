Feature: a lead is released by a background subagent

  Scenario Outline: the work a background subagent does is attributed to that subagent
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "ticker work" to the background subagent with prompt
      """
      Run the shell command `echo from-the-subagent` and then reply only with
      the word gathered.
      """
    Then work "ticker work" completes
    And work "ticker work" has worker type subagent
    When I name the only shell command in work "ticker work" containing 'echo from-the-subagent' "ticker command"
    Then subagent work "ticker work" has assignment state succeeded
    And subagent work "ticker work" has assignment result containing 'gathered'
    And work "ticker work" releases the lead
    And command "ticker command" has state succeeded
    And command "ticker command" has output containing 'from-the-subagent'
    And the lead actor in session "primary" has no command containing 'echo from-the-subagent'
    When I assign work "confirm delegation" in session "primary" to the lead with prompt
      """
      The assigned subagent completed. Reply only with the word delegated.
      """
    Then work "confirm delegation" completes
    And work "confirm delegation" has worker type lead
    And work "confirm delegation" has final answer 'delegated'

    Examples:
      | harness     | model                           |
      | codex       | gpt-5.6-luna                    |
      | claude_code | haiku                           |
      | opencode2   | opencode-go/deepseek-v4.1-flash |

  Scenario Outline: a lead is free while a background subagent runs
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "color work" to the background subagent with prompt
      """
      Run the exact foreground shell command `sleep 20`. After it finishes,
      reply only with COLOR_WORK_DONE.
      """
    Then subagent work "color work" is running while its lead has status awaiting_background
    And work "color work" completes
    And work "color work" has final answer 'COLOR_WORK_DONE'
    And work "color work" releases the lead

    Examples:
      | harness     | model                           |
      | codex       | gpt-5.6-luna                    |
      | claude_code | haiku                           |
      | opencode2   | opencode-go/deepseek-v4.1-flash |
