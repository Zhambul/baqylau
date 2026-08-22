@drift
Feature: file operations reach the dashboard

  Scenario Outline: created and edited file content is available to expand
    Given the file operation fixture does not exist
    And a <harness> session on <model> at <effort> effort with prompt 'Using file editing tools, not shell commands, first create baqylau-e2e-file.txt containing alpha, then in a separate tool call edit alpha to beta, then reply only with done'
    Then the turn ends within 3 minutes
    And the feed shows a succeeded created file operation containing 'alpha'
    And the feed shows a succeeded updated file operation containing 'beta'
    And the assistant ends the turn with 'done'

    Examples:
      | harness     | model        | effort |
      | codex       | gpt-5.6-luna | low    |
      | claude_code | haiku        | low    |
