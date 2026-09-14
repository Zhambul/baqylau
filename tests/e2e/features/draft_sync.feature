Feature: Shared terminal and web drafts

  Scenario Outline: native agent selection preserves dashboard draft controls
    # Harness limit: opencode2 only. OpenCode2 exposes Plan and Build as native agents.
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I start journey session "primary" from the terminal as turn "agent ready" with prompt
      """
      Do not use tools. Reply only with READY.
      """
    Then turn "agent ready" completes
    When I press terminal key 'shift+tab' in journey session "primary"
    Then journey session "primary" terminal contains '┃  Plan ·'
    When I select high effort in session "primary" as control "plan effort"
    Then control "plan effort" outcome is acknowledged
    When I select model <new_model> in session "primary" as control "plan model"
    Then control "plan model" outcome is acknowledged
    And session "primary" reports model <new_model>
    When I insert terminal draft 'Reply only with PLAN_DRAFT_SENT.' in journey session "primary"
    Then journey session "primary" terminal draft is exactly 'Reply only with PLAN_DRAFT_SENT.'
    And session "primary" has composer draft 'Reply only with PLAN_DRAFT_SENT.' after a fresh application read
    When I send the shared draft for journey session "primary" as turn "sent plan draft"
    Then turn "sent plan draft" completes
    And turn "sent plan draft" has final answer 'PLAN_DRAFT_SENT'
    And session "primary" reports model <new_model>
    And journey session "primary" terminal draft is exactly ''

    Examples:
      | harness   | model                         | new_model             |
      | opencode2 | opencode-go/deepseek-v4.1-flash | opencode-go/gpt-5.6-luna |

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
      | opencode2   | opencode-go/deepseek-v4.1-flash | standard |
