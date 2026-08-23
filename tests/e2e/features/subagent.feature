Feature: subagent work reaches the session feed

  Scenario Outline: the work a subagent does is attributed to that subagent
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" as turn "delegate ticker" with prompt
      """
      Use the <agent_tool> tool exactly once to launch a subagent with
      description ticker. Give it this prompt: run the shell command
      `echo from-the-subagent` and then reply only with the word gathered.
      Do not run a shell command yourself. After the launch, reply only with
      the word waiting.
      """
    Then turn "delegate ticker" completes
    And turn "delegate ticker" has final answer 'waiting'
    When I name the only assignment in turn "delegate ticker" "ticker work"
    And I name the actor assigned to assignment "ticker work" "ticker actor"
    And I name the only command for actor "ticker actor" containing 'echo from-the-subagent' "ticker command"
    Then assignment "ticker work" has state succeeded
    And assignment "ticker work" has result containing 'gathered'
    And actor "ticker actor" has state finished
    And command "ticker command" has state succeeded
    And command "ticker command" has output containing 'from-the-subagent'
    And the lead actor in session "primary" has no command containing 'echo from-the-subagent'
    When I send prompt to session "primary" as turn "confirm delegation"
      """
      The assigned subagent completed. Reply only with the word delegated.
      """
    Then turn "confirm delegation" completes
    And turn "confirm delegation" has final answer 'delegated'

    Examples:
      | harness     | model        | agent_tool  |
      | codex       | gpt-5.6-luna | multi_agent_v1__spawn_agent |
      | claude_code | haiku        | Agent       |

  Scenario Outline: two subagents launched at once stay two
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" as turn "delegate twice" with prompt
      """
      Use the <agent_tool> tool twice in one response to launch two
      subagents in parallel. Use description alpha and prompt "reply only with
      the word alpha" for the first subagent. Use description beta and prompt
      "reply only with the word beta" for the second subagent. Do not do their
      work yourself. After both launches, reply only with the word launched.
      """
    Then turn "delegate twice" completes
    And turn "delegate twice" has final answer 'launched'
    And turn "delegate twice" has exactly 2 assignments
    And session "primary" has exactly 2 subagents
    And every subagent in session "primary" has state finished
    When I send prompt to session "primary" as turn "confirm two delegations"
      """
      Both assigned subagents completed. Reply only with the word both.
      """
    Then turn "confirm two delegations" completes
    And turn "confirm two delegations" has final answer 'both'

    Examples:
      | harness     | model        | agent_tool  |
      | codex       | gpt-5.6-luna | multi_agent_v1__spawn_agent |
      | claude_code | haiku        | Agent       |
