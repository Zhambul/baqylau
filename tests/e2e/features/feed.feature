Feature: the paged and live feed stays consistent

  Scenario Outline: snapshots and stream updates form one ordered history
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "first work" to the <worker> with prompt
      """
      Reply only with FIRST_DONE.
      """
    Then work "first work" completes
    When I read feed snapshot "before update" for session "primary" with page size 2
    And I save stream checkpoint "before update" from feed snapshot "before update"
    Then feed snapshot "before update" uses more than one page
    And feed snapshot "before update" has unique entries
    And every entry in feed snapshot "before update" is at or before its snapshot cursor
    When I assign work "second work" in session "primary" to the <worker> with prompt
      """
      Reply only with SECOND_DONE.
      """
    Then work "second work" completes
    When I read feed snapshot "after update" for session "primary" with page size 2
    And I read session stream update "second work update" after stream checkpoint "before update"
    And I read global stream update "second work update" after stream checkpoint "before update"
    Then feed snapshot "after update" extends "before update" only with newer entries
    And session stream update "second work update" contains activity after checkpoint "before update"
    And global stream update "second work update" reports session "primary" after checkpoint "before update"
    When I rename session "primary" to 'feed-reconnect-marker' as control "feed rename"
    Then control "feed rename" response is accepted
    And session "primary" has title 'feed-reconnect-marker'
    When I reconnect session stream as update "rename update" after session stream update "second work update" with query cursor 0
    Then session stream update "rename update" is newer than "second work update" and has session title 'feed-reconnect-marker'
    And session stream update "rename update" repeats no entry from "second work update"

    Examples:
      | harness     | model        | worker   |
      | codex       | gpt-5.6-luna | lead     |
      | codex       | gpt-5.6-luna | subagent |
      | claude_code | haiku        | lead     |
      | claude_code | haiku        | subagent |
