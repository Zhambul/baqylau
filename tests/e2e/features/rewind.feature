Feature: rewind restores a named prompt for revision

  Scenario Outline: a harness restores a named prompt for revision
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" as turn "first prompt" with prompt
      """
      Reply only with the word first.
      """
    Then turn "first prompt" completes
    And turn "first prompt" has final answer 'first'
    When I send prompt to session "primary" as turn "second prompt"
      """
      Reply only with the word second.
      """
    Then turn "second prompt" completes
    And turn "second prompt" has final answer 'second'
    When I rewind session "primary" to turn "first prompt" with conversation mode as control "restore first prompt"
    Then control "restore first prompt" response is accepted
    And control "restore first prompt" outcome is acknowledged
    And control "restore first prompt" restores turn "first prompt"
    When I revise the restored draft in session "primary" as turn "revised prompt"
      """
      Reply only with the word revised.
      """
    Then turn "revised prompt" completes
    And turn "revised prompt" has final answer 'revised'
    And session "primary" keeps one live terminal after revision

    Examples:
      | harness     | model |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku |
