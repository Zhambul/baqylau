Feature: session controls change live session state

  Scenario Outline: a first prompt receives a concise isolated automatic title
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" as turn "automatic initial name" with prompt
      """
      https://example.invalid/a/very/long/request <section>Run the migration</section>
      Command output: processing-row-1 processing-row-2 processing-row-3.
      Reply only with READY_AFTER_LONG_PROMPT.
      """
    Then turn "automatic initial name" completes
    And session "primary" has a concise title unlike 'https://example.invalid/a/very/long/request <section>Run the migration</section>'
    And the application contains exactly session "primary"

    Examples:
      | harness | model |
      | codex   | gpt-5.6-luna |

  Scenario Outline: closing a session stops its active work
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "work during close" to the <worker> with prompt
      """
      Run `python -c 'import time; time.sleep(30); print("unexpected-finish")'`
      as a foreground shell command. Do not run it in the background. Wait for
      it before you reply.
      """
    And I name the only running foreground command in work "work during close" containing 'time.sleep(30)' "command during close"
    And I close session "primary" as control "close active session"
    Then control "close active session" response is accepted
    And control "close active session" outcome is acknowledged
    And session "primary" and all its actors finish
    And work "work during close" has state aborted
    And command "command during close" has state cancelled
    And command "command during close" belongs to worker of work "work during close"
    And session "primary" has no running work

    Examples:
      | harness     | model        | worker   |
      | codex       | gpt-5.6-luna | lead     |
      | codex       | gpt-5.6-luna | subagent |
      | claude_code | haiku        | lead     |
      | claude_code | haiku        | subagent |

  Scenario Outline: a quiet session can be renamed, reconfigured, and closed
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "open controls" to the <worker> with prompt
      """
      Reply only with the word ready.
      """
    Then work "open controls" completes
    And work "open controls" has worker type <worker>
    And work "open controls" releases the lead
    When I rename session "primary" to 'E2E control sample' as control "rename sample"
    Then control "rename sample" response is accepted
    And control "rename sample" outcome is acknowledged
    And session "primary" has title 'E2E control sample'
    When I select model <new_model> in session "primary" as control "change model"
    Then control "change model" response is accepted
    And control "change model" outcome is acknowledged
    And session "primary" reports model <new_model>
    When I select medium effort in session "primary" as control "increase effort"
    Then control "increase effort" response is accepted
    And control "increase effort" outcome is acknowledged
    And session "primary" reports effort medium
    When I request backgrounding in session "primary" as control "idle background request"
    Then control "idle background request" response is rejected
    And control "idle background request" outcome is rejected
    When I close session "primary" as control "close sample"
    Then control "close sample" response is accepted
    And control "close sample" outcome is acknowledged
    And session "primary" finishes

    Examples:
      | harness     | model        | new_model      | worker   |
      | codex       | gpt-5.6-luna | gpt-5.6-terra | lead     |
      | codex       | gpt-5.6-luna | gpt-5.6-terra | subagent |
      | claude_code | haiku        | sonnet         | lead     |
      | claude_code | haiku        | sonnet         | subagent |

  Scenario Outline: a harness can replace a custom title with an automatic name
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" as turn "name sample" with prompt
      """
      Reply only with the word ready.
      """
    Then turn "name sample" completes
    When I rename session "primary" to 'Temporary E2E title' as control "temporary name"
    Then control "temporary name" response is accepted
    And control "temporary name" outcome is acknowledged
    And session "primary" has title 'Temporary E2E title'
    When I request an automatic name for session "primary" as control "automatic name"
    Then control "automatic name" response is accepted
    And control "automatic name" outcome is acknowledged
    And session "primary" title is not 'Temporary E2E title'
    When I request an automatic name for session "primary" as control "second automatic name"
    Then control "second automatic name" response is accepted
    And control "second automatic name" outcome is acknowledged

    Examples:
      | harness     | model |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku |

  Scenario Outline: a parked session keeps a durable custom name
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" as turn "parked name sample" with prompt
      """
      Do not use tools. Reply only with PARKED_NAME_READY.
      """
    Then turn "parked name sample" completes
    When I close session "primary" as control "park name session"
    Then control "park name session" response is accepted
    And control "park name session" outcome is acknowledged
    And session "primary" finishes
    When I rename session "primary" to '<parked_title>' as control "name parked session"
    Then control "name parked session" response is accepted
    And control "name parked session" outcome is acknowledged
    And session "primary" has title '<parked_title>'
    When I request an automatic name for session "primary" as control "auto-name parked session"
    Then control "auto-name parked session" response is rejected
    And control "auto-name parked session" outcome is rejected
    And session "primary" has title '<parked_title>'

    Examples:
      | harness     | model        | parked_title                  |
      | claude_code | haiku        | Parked Claude Code title 82451 |

  Scenario Outline: a parked generic session can replace a custom title automatically
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" as turn "parked generic name" with prompt
      """
      Reply only with PARKED_GENERIC_READY.
      """
    Then turn "parked generic name" completes
    When I close session "primary" as control "park generic session"
    Then control "park generic session" response is accepted
    And control "park generic session" outcome is acknowledged
    And session "primary" finishes
    When I rename session "primary" to 'Temporary parked generic title' as control "temporary parked name"
    Then control "temporary parked name" response is accepted
    And control "temporary parked name" outcome is acknowledged
    And session "primary" has title 'Temporary parked generic title'
    When I request an automatic name for session "primary" as control "automatic parked name"
    Then control "automatic parked name" response is accepted
    And control "automatic parked name" outcome is acknowledged
    And session "primary" title is not 'Temporary parked generic title'

    Examples:
      | harness | model |
      | codex   | gpt-5.6-luna |
