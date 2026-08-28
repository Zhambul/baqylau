Feature: interrupting a turn reports the turn and command outcomes

  Scenario Outline: one subagent can stop while another subagent continues
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" as turn "parallel work" and assign these work items in parallel to subagents
      | work          | prompt                                                                                                                                                                                                 |
      | stopped work  | Run `python3 -c 'import time; time.sleep(30); print("stopped-work-finished")'` as a foreground shell command. Do not run it in the background. Wait for it before you reply.                                               |
      | survivor work | Run `python3 -c 'import time; time.sleep(12); print("survivor-work-finished")'` as a foreground shell command. Do not run it in the background. Wait for it, and then reply only with SURVIVOR_DONE.                     |
    And I name the only running foreground command in work "stopped work" containing 'time.sleep(30)' "stopped command"
    And I name the only running foreground command in work "survivor work" containing 'time.sleep(12)' "survivor command"
    And I request interruption of work "stopped work" in session "primary" as worker control "stop one child"
    Then worker control "stop one child" request completes
    And work "stopped work" has state aborted
    And subagent work "stopped work" has assignment state cancelled
    And command "stopped command" has state cancelled
    And command "stopped command" belongs to worker of work "stopped work"
    And work "survivor work" completes
    And work "survivor work" has final answer 'SURVIVOR_DONE'
    And subagent work "survivor work" has assignment state succeeded
    And command "survivor command" has state succeeded
    And command "survivor command" has output containing 'survivor-work-finished'
    And command "survivor command" belongs to worker of work "survivor work"
    And session "primary" has no running work

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |

  Scenario Outline: an interrupt cancels active subagent work
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "child sleep" to the subagent with prompt
      """
      Run `python3 -c 'import time; time.sleep(30); print("child-finished")'`
      as a foreground shell command. Do not run it in the background. Wait for
      it before you reply.
      """
    And I name the only running foreground command in work "child sleep" containing 'time.sleep(30)' "child command"
    And I request interruption of work "child sleep" in session "primary" as worker control "stop child work"
    Then worker control "stop child work" request completes
    And work "child sleep" has state aborted
    And subagent work "child sleep" has assignment state cancelled
    And command "child command" has state cancelled
    And command "child command" belongs to worker of work "child sleep"
    And session "primary" has no running work

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |

  Scenario Outline: an interrupt cancels the active turn and command
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "prepare interruption" to the subagent with prompt
      """
      Reply only with the word prepared.
      """
    Then work "prepare interruption" completes
    And work "prepare interruption" has worker type subagent
    And work "prepare interruption" has final answer 'prepared'
    And work "prepare interruption" releases the lead
    When I assign work "long command" in session "primary" to the lead with prompt
      """
      Run `python3 -c 'import time; time.sleep(30); print("should-not-finish")'`
      as a foreground shell command. Do not run it in the background. Wait for
      it before you reply.
      """
    And I name the only running foreground command in work "long command" containing 'time.sleep(30)' "long sleep"
    And I request interruption in session "primary" as control "stop long command"
    Then control "stop long command" response is accepted
    And control "stop long command" outcome is acknowledged
    And work "long command" has state aborted
    And command "long sleep" has state cancelled
    And the lead in session "primary" has status awaiting_response
    And session "primary" has no running work

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |
