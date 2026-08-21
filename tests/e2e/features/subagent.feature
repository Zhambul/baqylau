@drift
Feature: work handed to a subagent reaches the dashboard

  Scenario Outline: the work a subagent does is attributed to that subagent
    Given a <harness> session on <model> at <effort> effort with prompt 'Use the Agent tool exactly once to launch a general-purpose subagent with description ticker, giving it this prompt: run the shell command `echo from-the-subagent` and then reply only with the word gathered. Do not run any shell command yourself. Once it returns, reply only with the word delegated'
    Then the turn ends within 5 minutes
    And the feed shows a succeeded agent assignment
    And that assignment reported 'gathered'
    And the session lists a subagent 'ticker'
    And that subagent finishes within 2 minutes
    When I look at that subagent
    Then the feed shows a succeeded shell command 'echo from-the-subagent'
    And that command printed 'from-the-subagent'
    When I look at the session itself
    Then the feed shows no shell command 'echo from-the-subagent'

    Examples:
      | harness     | model | effort |
      | claude_code | haiku | low    |

  Scenario Outline: two subagents launched at once stay two
    Given a <harness> session on <model> at <effort> effort with prompt 'Use the Agent tool twice in one go to launch two general-purpose subagents in parallel: the first with description alpha and the prompt "reply only with the word alpha", the second with description beta and the prompt "reply only with the word beta". Do not do their work yourself. Once both return, reply only with the word both'
    Then the turn ends within 5 minutes
    And the feed shows 2 succeeded agent assignments
    And the session lists 2 subagents
    And every subagent finishes within 2 minutes

    Examples:
      | harness     | model | effort |
      | claude_code | haiku | low    |
