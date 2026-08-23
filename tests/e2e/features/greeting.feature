Feature: a plain turn reaches the session feed

  Scenario Outline: the harness answers with one word
    Given session configuration "primary" uses <harness> with model <model> and <effort> effort
    When I launch session "primary" and assign work "greeting" to the <worker> with prompt
      """
      Only say "Hi" and nothing more
      """
    Then work "greeting" completes
    And work "greeting" has worker type <worker>
    And session "primary" reports its configured model
    And session "primary" reports its configured effort
    And work "greeting" has requested prompt 'Only say "Hi" and nothing more'
    And work "greeting" has final answer 'Hi'

    Examples:
      | harness     | model        | effort | worker   |
      | codex       | gpt-5.6-luna | low    | lead     |
      | codex       | gpt-5.6-luna | low    | subagent |
      | claude_code | haiku        | low    | lead     |
      | claude_code | haiku        | low    | subagent |
