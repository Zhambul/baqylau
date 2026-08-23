Feature: session compaction has a complete lifecycle

  Scenario Outline: a compacted session remains usable
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" as turn "first context" with prompt
      """
      Remember the phrase amber circle. Reply only with the word first.
      """
    Then turn "first context" completes
    And turn "first context" has final answer 'first'
    When I send prompt to session "primary" as turn "second context"
      """
      Remember the phrase blue square. Reply only with the word second.
      """
    Then turn "second context" completes
    And turn "second context" has final answer 'second'
    When I request compaction in session "primary" as control "compact context"
    Then control "compact context" response is accepted
    And control "compact context" outcome is acknowledged
    When I name the compaction in session "primary" after control "compact context" "context compaction"
    Then compaction "context compaction" finishes
    And compaction "context compaction" leaves its actor ready
    When I send prompt to session "primary" as turn "after compaction"
      """
      Reply only with the word usable.
      """
    Then turn "after compaction" completes
    And turn "after compaction" has final answer 'usable'

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |
