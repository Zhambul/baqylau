Feature: browser-owned preferences round trip through the application

  Scenario: new-session form state returns the last saved values
    When I save new-session choices for codex model gpt-5.6-luna and low effort
    And I save new-session draft 'continue the E2E work'
    Then global new-session choices are codex model gpt-5.6-luna and low effort
    And global new-session draft is 'continue the E2E work'

  Scenario: session display and composer state return the last saved values
    Given session configuration "primary" uses codex with model gpt-5.6-luna and low effort
    When I launch session "primary" as turn "open preferences" with prompt
      """
      Use update_plan exactly once with one completed step named "Saved task".
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
