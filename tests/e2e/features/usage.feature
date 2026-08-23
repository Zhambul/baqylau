Feature: harness usage reaches global application state

  Scenario Outline: plan usage is available to a dashboard client
    Then global usage for <harness> has at least 1 window
    And each global usage window for <harness> has a positive duration
    And each global usage window for <harness> has a valid percentage
    And global usage window keys for <harness> are unique per account

    Examples:
      | harness     |
      | codex       |
      | claude_code |
