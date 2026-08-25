Feature: completed session activity reaches insights and resume state

  Scenario Outline: one completed turn appears in application summaries
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I read application insights as "before insight sample"
    When I launch session "primary" and assign work "insight sample" to the <worker> with prompt
      """
      Reply only with the word measured.
      """
    Then work "insight sample" completes
    And work "insight sample" has worker type <worker>
    And work "insight sample" releases the lead
    When I close session "primary" as control "finish insight sample"
    Then control "finish insight sample" response is accepted
    And control "finish insight sample" outcome is acknowledged
    And session "primary" finishes
    When I read application insights as "after insight sample"
    And I read resumable sessions for the workspace as "workspace history"
    Then insights "after insight sample" differ from "before insight sample" by exactly completed session "primary"
    And resumable list "workspace history" contains session "primary"

    Examples:
      | harness     | model        | worker   |
      | codex       | gpt-5.6-luna | lead     |
      | codex       | gpt-5.6-luna | subagent |
      | claude_code | haiku        | lead     |
      | claude_code | haiku        | subagent |

  Scenario Outline: resume history supports search, activity state, and newest-first order
    Given session configuration "older" uses <harness> with model <model> and low effort
    And session configuration "newer" uses <harness> with model <model> and low effort
    When I launch session "older" as turn "older activity" with prompt
      """
      Reply only with OLDER_READY.
      """
    Then turn "older activity" completes
    When I rename session "older" to '<older_title>' as control "name older"
    Then control "name older" response is accepted
    And control "name older" outcome is acknowledged
    And session "older" has title '<older_title>'
    When I close session "older" as control "close older"
    Then control "close older" response is accepted
    And control "close older" outcome is acknowledged
    And session "older" finishes
    When I launch session "newer" as turn "newer activity" with prompt
      """
      Reply only with NEWER_READY.
      """
    Then turn "newer activity" completes
    When I rename session "newer" to '<newer_title>' as control "name newer"
    Then control "name newer" response is accepted
    And control "name newer" outcome is acknowledged
    And session "newer" has title '<newer_title>'
    When I read resumable sessions for the workspace as "all resume history"
    Then resumable list "all resume history" shows session "older" as inactive
    And resumable list "all resume history" shows session "newer" as active
    And resumable list "all resume history" orders session "newer" before session "older" by newest activity
    When I search resumable sessions for '<newer_title>' as "title search"
    Then resumable list "title search" contains only session "newer"
    When I search resumable sessions for session "older" ID as "ID search"
    Then resumable list "ID search" contains only session "older"

    Examples:
      | harness     | model        | older_title                    | newer_title                    |
      | codex       | gpt-5.6-luna | Codex resume older title 9021 | Codex resume newer title 9021 |
      | claude_code | haiku        | Claude resume older title 9021 | Claude resume newer title 9021 |
