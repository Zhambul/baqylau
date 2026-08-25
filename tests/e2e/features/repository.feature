Feature: sessions report their current repository state

  Scenario Outline: a session reports exact repository state before and after a change
    Given session configuration "primary" uses <harness> with model <model> and low effort in the isolated repository workspace
    When I launch session "primary" as turn "repository ready" with prompt
      """
      Reply only with REPOSITORY_READY.
      """
    Then turn "repository ready" completes
    And session "primary" reports the exact clean isolated repository state
    When I assign work "change repository" in session "primary" to the <worker> with prompt
      """
      Replace the complete content of repository-state.txt with the exact text
      DIRTY_REPOSITORY_STATE. Do not create a Git commit. When the file change
      is complete, reply only with REPOSITORY_CHANGED.
      """
    Then work "change repository" completes
    And work "change repository" has worker type <worker>
    And work "change repository" releases the lead
    And session "primary" reports the exact dirty isolated repository state
    When I close session "primary" as control "close repository session"
    Then control "close repository session" response is accepted
    And control "close repository session" outcome is acknowledged
    And session "primary" finishes

    Examples:
      | harness     | model        | worker   |
      | codex       | gpt-5.6-luna | lead     |
      | codex       | gpt-5.6-luna | subagent |
      | claude_code | haiku        | lead     |
      | claude_code | haiku        | subagent |
