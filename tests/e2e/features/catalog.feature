Feature: harness catalogs describe available session controls

  Scenario: installed harnesses publish their launch and control contracts
    When I read the installed harnesses as "launch options"
    Then harness list "launch options" contains codex
    And harness list "launch options" contains claude_code
    And harness list "launch options" has exactly one default
    And each harness in list "launch options" is launchable
    And harness codex in list "launch options" advertises control send_text
    And harness codex in list "launch options" advertises control interrupt
    And harness codex in list "launch options" advertises control answer_question
    And harness claude_code in list "launch options" advertises control send_text
    And harness claude_code in list "launch options" advertises control interrupt
    And harness claude_code in list "launch options" advertises control answer_question
    And harness claude_code in list "launch options" advertises control apply_rewind

  Scenario Outline: a harness catalog has usable model and command choices
    When I read the <harness> catalog as "selected catalog"
    Then catalog "selected catalog" has model <model> with effort low
    And catalog "selected catalog" has exactly one default model
    And each model in catalog "selected catalog" has exactly one default effort
    And catalog "selected catalog" has command compact

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |
