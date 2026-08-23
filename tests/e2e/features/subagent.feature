Feature: subagent work reaches the session feed

  Scenario Outline: the work a subagent does is attributed to that subagent
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "ticker work" to the subagent with prompt
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
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |

  Scenario Outline: two subagents launched at once stay two
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" as turn "parallel delegation" and assign these work items in parallel to subagents
      | work       | prompt                         |
      | alpha work | Reply only with the word alpha. |
      | beta work  | Reply only with the word beta.  |
    Then turn "parallel delegation" completes
    And turn "parallel delegation" has final answer 'launched'
    And work "alpha work" completes
    And work "alpha work" has worker type subagent
    And work "alpha work" has final answer 'alpha'
    And work "beta work" completes
    And work "beta work" has worker type subagent
    And work "beta work" has final answer 'beta'
    And turn "parallel delegation" has exactly 2 assignments
    And session "primary" has exactly 2 subagents
    And every subagent in session "primary" has state finished
    When I send prompt to session "primary" as turn "confirm two delegations"
      """
      Both assigned subagents completed. Reply only with the word both.
      """
    Then turn "confirm two delegations" completes
    And turn "confirm two delegations" has final answer 'both'

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |
