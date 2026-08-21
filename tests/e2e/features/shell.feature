@drift
Feature: shell work reaches the dashboard

  Scenario Outline: a command the model runs becomes a shell block
    Given a <harness> session on <model> at <effort> effort with prompt 'Run the shell command `echo hello world`, then reply only with the word done'
    Then the turn ends within 3 minutes
    And the feed shows a succeeded shell command 'echo hello world'
    And that command printed 'hello world'
    And the session counts at least 1 shell command
    And the assistant ends the turn with 'done'

    Examples:
      | harness     | model        | effort |
      | codex       | gpt-5.6-luna | low    |
      | claude_code | haiku        | low    |
