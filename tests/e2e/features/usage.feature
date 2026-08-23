Feature: harness usage reaches global application state

  Scenario Outline: plan usage is available to a dashboard client
    Then global usage for <harness> has at least 1 window
    And each global usage window for <harness> has a positive duration

    Examples:
      | harness     |
      | codex       |
      | claude_code |
