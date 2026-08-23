Feature: browser-owned preferences round trip through the application

  Scenario Outline: new-session form state returns the last saved values
    When I save new-session choices for <harness> model <model> and low effort
    And I save new-session draft 'continue the E2E work'
    Then global new-session choices are <harness> model <model> and low effort
    And global new-session draft is 'continue the E2E work'

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |

  Scenario Outline: session display and composer state return the last saved values
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" as turn "open preferences" with prompt
      """
      <task_instruction>
      Reply only with the word ready.
      """
    Then turn "open preferences" completes
    When I save composer draft 'unsent detail' for session "primary"
    And I queue message 'follow-up detail' for session "primary"
    And I set view mode focus for session "primary"
    And I mute notifications for session "primary"
    And I hide tasks for session "primary"
    Then composer draft for session "primary" is 'unsent detail'
    And composer queue for session "primary" contains 'follow-up detail'
    And view mode for session "primary" is focus
    And notifications for session "primary" are muted
    And tasks for session "primary" are hidden

    Examples:
      | harness     | model        | task_instruction                                                     |
      | codex       | gpt-5.6-luna | Use update_plan exactly once with one completed step named "Saved task". |
      | claude_code | haiku        | Use TaskCreate once for "Saved task", then mark it completed.        |
