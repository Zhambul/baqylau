Feature: subagent work reaches the session feed

  Scenario: the work a subagent does is attributed to that subagent
    Given session configuration "primary" uses claude_code with model haiku and low effort
    When I launch session "primary" as turn "delegate ticker" with prompt
      """
      Use the Agent tool exactly once to launch a general-purpose subagent with
      description ticker. Give it this prompt: run the shell command
      `echo from-the-subagent` and then reply only with the word gathered.
      Do not run a shell command yourself. When it returns, reply only with the
      word delegated.
      """
    Then turn "delegate ticker" completes
    When I name the only assignment in turn "delegate ticker" "ticker work"
    And I name the subagent in session "primary" with exact name 'ticker' "ticker actor"
    And I name the only command for actor "ticker actor" containing 'echo from-the-subagent' "ticker command"
    Then assignment "ticker work" has state succeeded
    And assignment "ticker work" has result containing 'gathered'
    And actor "ticker actor" has state finished
    And command "ticker command" has state succeeded
    And command "ticker command" has output containing 'from-the-subagent'
    And the lead actor in session "primary" has no command containing 'echo from-the-subagent'

  Scenario: two subagents launched at once stay two
    Given session configuration "primary" uses claude_code with model haiku and low effort
    When I launch session "primary" as turn "delegate twice" with prompt
      """
      Use the Agent tool twice in one response to launch two general-purpose
      subagents in parallel. Use description alpha and prompt "reply only with
      the word alpha" for the first subagent. Use description beta and prompt
      "reply only with the word beta" for the second subagent. Do not do their
      work yourself. When both return, reply only with the word both.
      """
    Then turn "delegate twice" completes
    And turn "delegate twice" has exactly 2 assignments
    And session "primary" has exactly 2 subagents
    And every subagent in session "primary" has state finished
