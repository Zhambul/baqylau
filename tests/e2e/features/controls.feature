Feature: session controls change live session state

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

    Examples:
      | harness     | model |
      | claude_code | haiku |
