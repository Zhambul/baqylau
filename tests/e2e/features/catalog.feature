Feature: harness catalogs describe available session controls

  Scenario Outline: an installed harness publishes its launch and control contract
    When I read the installed harnesses as "launch options"
    Then harness list "launch options" contains <harness>
    And harness list "launch options" has exactly one default
    And each harness in list "launch options" is launchable
    And harness <harness> in list "launch options" advertises control send_text
    And harness <harness> in list "launch options" advertises control interrupt
    And harness <harness> in list "launch options" advertises control answer_question
    And harness <harness> in list "launch options" advertises exactly controls '<controls>'

    Examples:
      | harness     | controls |
      | codex       | answer_question,apply_rewind,auto_name_session,close_session,compact,decide_plan,interrupt,read_plan_choices,rename_session,select_effort,select_model,send_text |
      | claude_code | answer_question,apply_rewind,auto_name_session,background,close_session,compact,decide_plan,interrupt,open_rewind,read_plan_choices,rename_session,select_effort,select_model,send_text |

  Scenario Outline: a harness catalog has usable model and command choices
    When I read the <harness> catalog as "selected catalog"
    Then catalog "selected catalog" has model <model> with effort low
    And catalog "selected catalog" has exactly one default model
    And each model in catalog "selected catalog" has exactly one default effort
    And catalog "selected catalog" has command compact
    And catalog "selected catalog" advertises exactly rewind modes '<rewind_modes>'

    Examples:
      | harness     | model        | rewind_modes          |
      | codex       | gpt-5.6-luna | conversation          |
      | claude_code | haiku        | both,conversation,code |
