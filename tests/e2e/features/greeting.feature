@drift
Feature: a plain turn reaches the dashboard

  Scenario Outline: the harness answers with one word
    Given a <harness> session on <model> at <effort> effort
    When I ask 'Only say "Hi" and nothing more'
    Then the turn ends within 3 minutes
    And the session reports the model <model>
    And the session reports <effort> effort
    And the feed shows my prompt 'Only say "Hi" and nothing more'
    And the assistant ends the turn with 'Hi'

    Examples:
      | harness     | model        | effort |
      | codex       | gpt-5.6-luna | low    |
      | claude_code | haiku        | low    |
