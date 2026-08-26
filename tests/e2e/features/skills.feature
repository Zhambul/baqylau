Feature: skill execution reaches the session feed

  Scenario Outline: a harness skill has a named lifecycle
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "load communication skill" to the <worker> using test skill "baqylau-e2e-communication"
    Then work "load communication skill" completes
    And work "load communication skill" has worker type <worker>
    When I name test skill "baqylau-e2e-communication" in work "load communication skill" "communication skill"
    Then skill "communication skill" has state succeeded
    And skill "communication skill" <argument_check>
    And skill "communication skill" has result containing 'Do not create or change files.'
    And work "load communication skill" has final answer 'loaded'

    Examples:
      | harness     | model        | worker   | argument_check                            |
      | codex       | gpt-5.6-luna | lead     | has no arguments                          |
      | codex       | gpt-5.6-luna | subagent | has no arguments                          |
      | claude_code | haiku        | lead     | has arguments 'baqylau-e2e-argument'     |
      | claude_code | haiku        | subagent | has arguments 'baqylau-e2e-argument'     |
