@drift
Feature: harness usage reaches the dashboard

  Scenario Outline: plan usage is visible on the web dashboard
    Given a <harness> session on <model> at <effort> effort with prompt 'Only say "Hi" and nothing more'
    Then the turn ends within 3 minutes
    Then the dashboard reports <harness> usage with at least 1 window within 30 seconds

    Examples:
      | harness     | model        | effort |
      | codex       | gpt-5.6-luna | low    |
      | claude_code | haiku        | low    |
