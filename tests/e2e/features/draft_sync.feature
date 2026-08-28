Feature: Shared terminal and web drafts

  Scenario Outline: a rename preserves the terminal draft for each harness
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I start journey session "primary" from the terminal as turn "draft ready" with prompt
      """
      Do not use tools. Reply only with DRAFT_READY.
      """
    Then turn "draft ready" completes
    And turn "draft ready" has final answer 'DRAFT_READY'
    When I insert terminal draft 'test' in journey session "primary"
    And I put journey session "primary" in <editor_mode> editor mode
    Then session "primary" has composer draft 'test' after a fresh application read
    And journey session "primary" terminal draft is exactly 'test'
    When I rename session "primary" to 'Draft-safe rename' as control "draft-safe rename"
    Then control "draft-safe rename" response is accepted
    And control "draft-safe rename" outcome is acknowledged
    And session "primary" has title 'Draft-safe rename'
    And session "primary" has composer draft 'test' after a fresh application read
    And journey session "primary" terminal draft is exactly 'test'
    When I send the shared draft for journey session "primary" as turn "sent shared draft"
    Then turn "sent shared draft" completes
    And turn "sent shared draft" has prompt 'test'
    And session "primary" has no composer draft after a fresh application read
    And journey session "primary" terminal draft is exactly ''

    Examples:
      | harness     | model        | editor_mode |
      | codex       | gpt-5.6-luna | standard    |
      | claude_code | haiku        | visual      |
