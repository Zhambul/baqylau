Feature: a plain turn reaches the session feed

  Scenario Outline: the harness answers with one word
    Given session configuration "primary" uses <harness> with model <model> and <effort> effort
    When I launch session "primary" as turn "greeting" with prompt
      """
      Only say "Hi" and nothing more
      """
    Then turn "greeting" completes
    And session "primary" reports its configured model
    And session "primary" reports its configured effort
    And turn "greeting" has prompt 'Only say "Hi" and nothing more'
    And turn "greeting" has final answer 'Hi'

    Examples:
      | harness     | model        | effort |
      | codex       | gpt-5.6-luna | low    |
      | claude_code | haiku        | low    |
