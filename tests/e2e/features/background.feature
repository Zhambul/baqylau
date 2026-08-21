@drift
Feature: background work reaches the dashboard

  Scenario Outline: a backgrounded command is tracked past the end of its turn
    Given a <harness> session on <model> at <effort> effort
    When I send message 'Start a background terminal running `sleep 5; echo done`. Do not wait for it. Reply only with the word started'
    Then the turn ends within 3 minutes
    And the session lists a background job 'sleep'
    And that job is still running
    And that job prints 'done' within 2 minutes
    And that job ends within 2 minutes

    Examples:
      | harness     | model | effort |
      | claude_code | haiku | low    |

  Scenario Outline: a command backgrounded mid-run keeps reporting
    Given a <harness> session on <model> at <effort> effort with prompt 'Run `echo started; sleep 30; echo done` in the foreground and wait for it. Do not use run_in_background.'
    When I move that command to the background
    Then the session lists a background job 'sleep'
    And that job is still running
    And that job prints 'done' within 2 minutes

    Examples:
      | harness     | model | effort |
      | claude_code | haiku | low    |
