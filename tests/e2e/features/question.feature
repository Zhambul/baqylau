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
    When I assign question work "ask colour" in session "primary" to the lead with prompt
      """
      Ask "Which colour should I use?" with the heading Colour. Offer Blue with
      description "Use blue" and Green with description "Use green". Allow only
      one choice. Do not select an answer. After the user answers, reply only
      with the word done.
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
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |

  Scenario Outline: a question records multiple selected choices
    # Harness limit: claude_code only. Codex questions do not support multiple selected choices.
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign question work "ask colours" to the lead with prompt
      """
      Ask "Which colours should I use?" with the heading Colours. Offer Blue,
      Green, and Red. Give each option a short
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
    And work "ask colours" completes
    And work "ask colours" has final answer 'done'

    Examples:
      | harness     | model |
      | claude_code | haiku |

  Scenario Outline: dismissing a question sends chat text to the harness
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign question work "ask approach" to the lead with prompt
      """
      Ask "Which approach should I use?" with the heading Approach. Offer Direct
      with description "Use the direct
      approach" and Staged with description "Use the staged approach". Allow
      only one choice. If the person declines and sends the exact text
      E2E-QUESTION-CHAT-731, reply only with the word discussed.
      """
    And I name the pending question in turn "ask approach" containing 'Which approach' "approach choice"
    When I dismiss question "approach choice" and send chat text 'E2E-QUESTION-CHAT-731' as control "discuss approach"
    Then control "discuss approach" response is accepted
    And control "discuss approach" outcome is acknowledged
    And question "approach choice" is resolved
    And question "approach choice" sends chat prompt 'E2E-QUESTION-CHAT-731' after control "discuss approach"
    And question "approach choice" is followed by final answer 'discussed' after control "discuss approach"

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |

  Scenario Outline: a question records a free-text answer
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign question work "ask shade" to the lead with prompt
      """
      Ask "Which shade should I use?" with the heading Shade. Offer Blue with
      description "Use blue" and Green with description "Use green". Allow only
      one choice. Do not select an answer. After the user answers, reply only
      with the exact answer text.
      """
    And I name the pending question in work "ask shade" containing 'Which shade' "shade choice"
    When I answer question "shade choice" with free text 'Amber' as control "type amber"
    Then control "type amber" response is accepted
    And control "type amber" outcome is acknowledged
    And question "shade choice" records free text 'Amber'
    And question "shade choice" is resolved
    And work "ask shade" completes
    And work "ask shade" has final answer 'Amber'

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |

  Scenario Outline: one dialog records answers to two questions
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign question work "ask settings" to the lead with prompt
      """
      In one question dialog, ask two questions. First ask "Which colour should
      I use?" with the heading Colour. Offer Blue and Green. Second ask "When
      should I run?" with the heading Time. Offer Now and Later. Give each option
      a short description. Allow only one choice for each question. Do not select
      answers. After the user answers both questions, reply only with the word
      done.
      """
    And I name the pending question in work "ask settings" containing 'Which colour' "setting colour"
    And I name the pending question in work "ask settings" containing 'When should' "setting time"
    When I answer questions "setting colour" with option 'Green' and "setting time" with option 'Later' as control "choose settings"
    Then control "choose settings" response is accepted
    And control "choose settings" outcome is acknowledged
    And question "setting colour" records option 'Green'
    And question "setting time" records option 'Later'
    And question "setting colour" is resolved
    And question "setting time" is resolved
    And work "ask settings" completes
    And work "ask settings" has final answer 'done'

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |

  Scenario Outline: an unfinished question draft returns with its exact input
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign question work "draft shade" to the lead with prompt
      """
      Ask "Which draft shade should I use?" with the heading Shade. Offer Blue
      with description "Use blue" and Green with description "Use green". Allow
      only one choice. Do not select an answer. Wait for the user answer.
      """
    And I name the pending question in work "draft shade" containing 'Which draft shade' "draft choice"
    When I save a draft for question "draft choice" with option 'Blue' and free text 'matte finish'
    Then question draft "draft choice" restores option 'Blue' and free text 'matte finish'
    And question "draft choice" is single choice
    And question "draft choice" offers option 'Blue'
    And question "draft choice" offers option 'Green'

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |

  Scenario Outline: a tall question dialog remains answerable when its prompt scrolls away
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign question work "ask clipped settings" to the lead with prompt
      """
      In one question dialog, ask two questions. First ask "Which base should I
      use?" with heading Base and choices "Remote base" and "Local base".
      Second ask "Which regression scope should I use?" with heading Scope and
      choices "Full regression", "Feature only", and "Blocker only". Give every
      choice a description of at least eighty words. These long descriptions
      must be inside the question tool input. Allow only one choice for each
      question. After both answers, reply only with CLIPPED_DONE.
      """
    And I name the pending question in work "ask clipped settings" containing 'Which base' "clipped base"
    And I name the pending question in work "ask clipped settings" containing 'Which regression scope' "clipped scope"
    When I answer questions "clipped base" with option 'Remote base' and "clipped scope" with option 'Feature only' as control "choose clipped settings"
    Then control "choose clipped settings" response is accepted
    And control "choose clipped settings" outcome is acknowledged
    And question "clipped base" records option 'Remote base'
    And question "clipped scope" records option 'Feature only'
    And work "ask clipped settings" completes
    And work "ask clipped settings" has final answer 'CLIPPED_DONE'

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |
