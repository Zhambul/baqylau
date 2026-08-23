Feature: completed session activity reaches insights and resume state

  Scenario Outline: one completed turn appears in application summaries
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "insight sample" to the <worker> with prompt
      """
      Reply only with the word measured.
      """
    Then work "insight sample" completes
    And work "insight sample" has worker type <worker>
    And work "insight sample" releases the lead
    When I close session "primary" as control "finish insight sample"
    Then control "finish insight sample" response is accepted
    And control "finish insight sample" outcome is acknowledged
    And session "primary" finishes
    When I read application insights as "current insights"
    And I read resumable sessions for the workspace as "workspace history"
    Then insights "current insights" report at least 1 session
    And insights "current insights" include the workspace
    And resumable list "workspace history" contains session "primary"

    Examples:
      | harness     | model        | worker   |
      | codex       | gpt-5.6-luna | lead     |
      | codex       | gpt-5.6-luna | subagent |
      | claude_code | haiku        | lead     |
      | claude_code | haiku        | subagent |
