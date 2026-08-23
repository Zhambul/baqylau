Feature: completed session activity reaches insights and resume state

  Scenario: one completed turn appears in application summaries
    Given session configuration "primary" uses codex with model gpt-5.6-luna and low effort
    When I launch session "primary" as turn "insight sample" with prompt
      """
      Reply only with the word measured.
      """
    Then turn "insight sample" completes
    When I close session "primary" as control "finish insight sample"
    Then control "finish insight sample" response is accepted
    And control "finish insight sample" outcome is acknowledged
    And session "primary" finishes
    When I read application insights as "current insights"
    And I read resumable sessions for the workspace as "workspace history"
    Then insights "current insights" report at least 1 session
    And insights "current insights" include the workspace
    And resumable list "workspace history" contains session "primary"
