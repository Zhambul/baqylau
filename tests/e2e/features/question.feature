Feature: a dashboard answer resolves a harness question

  Scenario Outline: a question keeps its choices and selected answer
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "prepare colours" to the subagent with prompt
      """
      Read these colour options: Blue and Green. Do not use tools. Reply only
      with the word prepared.
      """
    Then work "prepare colours" completes
    And work "prepare colours" has worker type subagent
    And work "prepare colours" has final answer 'prepared'
    And work "prepare colours" releases the lead
    When I assign work "ask colour" in session "primary" to the lead with prompt
      """
      Use <question_tool> exactly once. Ask "Which colour should I use?" with
      the <title_kind> Colour. Offer Blue with description "Use blue" and Green
      with description "Use green". Allow only one choice. Do not select an
      answer. After the user answers, reply only with the word done.
      """
    And I name the pending question in work "ask colour" containing 'Which colour' "colour choice"
    Then question "colour choice" is single choice
    And question "colour choice" offers option 'Blue'
    And question "colour choice" offers option 'Green'
    When I answer question "colour choice" with option 'Blue' as control "choose blue"
    Then control "choose blue" response is accepted
    And control "choose blue" outcome is acknowledged
    And question "colour choice" records option 'Blue'
    And question "colour choice" is resolved
    And work "ask colour" completes
    And work "ask colour" has worker type lead
    And work "ask colour" has final answer 'done'

    Examples:
      | harness     | model        | question_tool      | title_kind |
      | codex       | gpt-5.6-luna | request_user_input | header     |
      | claude_code | haiku        | AskUserQuestion     | title      |

  Scenario Outline: a question records multiple selected choices
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" as turn "ask colours" with prompt
      """
      Use AskUserQuestion exactly once. Ask "Which colours should I use?" with
      the title Colours. Offer Blue, Green, and Red. Give each option a short
      description. Allow multiple choices. Do not select an answer. After the
      user answers, reply only with the word done.
      """
    And I name the pending question in turn "ask colours" containing 'Which colours' "colour choices"
    Then question "colour choices" is multiple choice
    And question "colour choices" offers option 'Blue'
    And question "colour choices" offers option 'Green'
    And question "colour choices" offers option 'Red'
    When I answer question "colour choices" with options 'Blue' and 'Green' as control "choose colours"
    Then control "choose colours" response is accepted
    And control "choose colours" outcome is acknowledged
    And question "colour choices" records options 'Blue' and 'Green'
    And question "colour choices" is resolved
    And turn "ask colours" completes
    And turn "ask colours" has final answer 'done'

    Examples:
      | harness     | model |
      | claude_code | haiku |
