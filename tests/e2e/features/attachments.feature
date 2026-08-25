Feature: staged attachments reach a new harness session

  Scenario Outline: a text attachment is available in the first turn
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I stage text attachment 'e2e-context.txt' with content 'attachment-marker-731' as "context file"
    Then staged attachment "context file" is text file 'e2e-context.txt'
    When I launch session "primary" and assign work "read attachment" to the <worker> with attachment "context file" and prompt
      """
      Read the attached text file. Reply only with its exact content.
      """
    Then work "read attachment" completes
    And work "read attachment" has worker type <worker>
    And work "read attachment" has final answer 'attachment-marker-731'

    Examples:
      | harness     | model        | worker   |
      | codex       | gpt-5.6-luna | lead     |
      | codex       | gpt-5.6-luna | subagent |
      | claude_code | haiku        | lead     |
      | claude_code | haiku        | subagent |

  Scenario Outline: a text attachment is available in a later turn
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I stage text attachment 'later-context.txt' with content 'later-attachment-marker-184' as "later context"
    And I group staged attachments as "later files"
      | attachment    |
      | later context |
    And I launch session "primary" as turn "ready" with prompt
      """
      Reply only with READY.
      """
    Then turn "ready" completes
    And turn "ready" has final answer 'READY'
    When I assign work "read later attachment" in session "primary" to the <worker> with attachment bundle "later files" and prompt
      """
      Read the attached text file. Reply only with its exact content.
      """
    Then work "read later attachment" completes
    And work "read later attachment" has worker type <worker>
    And work "read later attachment" has final answer 'later-attachment-marker-184'

    Examples:
      | harness     | model        | worker   |
      | codex       | gpt-5.6-luna | lead     |
      | codex       | gpt-5.6-luna | subagent |
      | claude_code | haiku        | lead     |
      | claude_code | haiku        | subagent |

  Scenario Outline: an attachment without composer text is still delivered
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I stage text attachment 'attachment-only.txt' with content 'Read this file. Reply with exactly NO_TEXT_ATTACHMENT_963 and no punctuation.' as "attachment instruction"
    And I group staged attachments as "attachment-only files"
      | attachment            |
      | attachment instruction |
    And I launch session "primary" as turn "ready" with prompt
      """
      Reply only with READY.
      """
    Then turn "ready" completes
    When I assign attachment-only work "read attachment-only input" in session "primary" with attachment bundle "attachment-only files"
    Then work "read attachment-only input" completes

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |

  Scenario Outline: more than one attachment is delivered together
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I stage text attachment 'first-context.txt' with content 'MULTI_FIRST_147' as "first context"
    And I stage text attachment 'second-context.txt' with content 'MULTI_SECOND_258' as "second context"
    And I group staged attachments as "both context files"
      | attachment    |
      | first context |
      | second context |
    And I launch session "primary" as turn "ready" with prompt
      """
      Reply only with READY.
      """
    Then turn "ready" completes
    When I assign work "read both attachments" in session "primary" to the lead with attachment bundle "both context files" and prompt
      """
      Read both attached text files. Reply only with both exact markers joined
      by a plus sign, with the first file marker first.
      """
    Then work "read both attachments" completes
    And work "read both attachments" has final answer 'MULTI_FIRST_147+MULTI_SECOND_258'

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |

  Scenario Outline: a real image attachment keeps its visible content
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I stage marker image 'visible-marker.png' showing '123' as "marker image"
    Then staged attachment "marker image" is PNG image 'visible-marker.png'
    When I group staged attachments as "image files"
      | attachment   |
      | marker image |
    And I launch session "primary" as turn "ready" with prompt
      """
      Reply only with READY.
      """
    Then turn "ready" completes
    When I assign work "inspect image" in session "primary" to the lead with attachment bundle "image files" and prompt
      """
      Inspect the attached image. Reply only with the three digits visible in it.
      """
    Then work "inspect image" completes
    And work "inspect image" has final answer '123'

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |
