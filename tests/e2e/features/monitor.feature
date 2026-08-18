@drift
Feature: an armed monitor reports its events

  Scenario Outline: monitor events arrive after the turn that armed it
    Given a <harness> session on <model> at <effort> effort
    When I ask 'Use the Monitor tool with description ticks to watch this command: for i in 1 2 3 4 5 6; do echo tick-$i; sleep 5; done — do not run it with Bash and do not wait for it. Reply only with the word armed'
    Then the turn ends within 3 minutes
    And the session lists a monitor 'tick'
    And that monitor is still running
    And that monitor reports the event 'tick-1' within 2 minutes
    And that monitor ends within 2 minutes

    Examples:
      | harness     | model | effort |
      | claude_code | haiku | low    |
