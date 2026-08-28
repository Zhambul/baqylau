Feature: subagent work reaches the session feed

  Scenario Outline: a subagent can send an exact message to the lead
    # Harness limit: claude_code only. Codex subagents do not receive the send_message tool.
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "message work" to a subagent that sends 'CHILD_TO_LEAD_529' to the lead and returns 'MESSAGE_WORK_DONE'
    Then work "message work" completes
    And work "message work" has worker type subagent
    And work "message work" has final answer 'MESSAGE_WORK_DONE'
    When I name the exact message 'CHILD_TO_LEAD_529' sent by worker of work "message work" "child message"
    Then actor message "child message" goes from worker of work "message work" to the lead

    Examples:
      | harness     | model        |
      | claude_code | haiku        |

  Scenario Outline: an active subagent receives one follow-up
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "follow-up work" to a subagent with follow-up 'FOLLOWUP_MARKER_417' using prompt
      """
      Wait for one follow-up message. When it arrives, reply only with its exact
      text. Do not use tools.
      """
    Then work "follow-up work" completes
    And work "follow-up work" has worker type subagent
    And follow-up 'FOLLOWUP_MARKER_417' is observed by worker of work "follow-up work"

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |

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

  Scenario Outline: a lead keeps a running color while it waits for a subagent
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "color work" to the subagent with prompt
      """
      Run the exact foreground shell command `sleep 20`. After it finishes,
      reply only with COLOR_WORK_DONE.
      """
    Then subagent work "color work" is running while its lead has status awaiting_background
    And work "color work" completes
    And work "color work" has final answer 'COLOR_WORK_DONE'
    And work "color work" releases the lead

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
