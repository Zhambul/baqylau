Feature: session compaction has a complete lifecycle

  Scenario Outline: a compacted session remains usable
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "first context" to the <worker> with prompt
      """
      Remember the exact phrase amber circle for a later recall check. Do not
      use tools. Acknowledge by replying with exactly this one word: first
      """
    Then work "first context" completes
    And work "first context" has worker type <worker>
    And work "first context" has final answer 'first'
    And work "first context" releases the lead
    When I send prompt to session "primary" as turn "second context"
      """
      Also remember the exact phrase blue square for the same recall check. Do
      not use tools. Acknowledge by replying with exactly this one word: second
      """
    Then turn "second context" completes
    And turn "second context" has final answer 'second'
    When I request compaction in session "primary" as control "compact context"
    Then control "compact context" response is accepted
    And control "compact context" outcome is acknowledged
    When I name the compaction in session "primary" after control "compact context" "context compaction"
    Then compaction "context compaction" finishes
    And compaction "context compaction" has one finished feed entry
    And compaction "context compaction" has <compacted context>
    And compaction "context compaction" leaves its actor ready
    When I send prompt to session "primary" as turn "after compaction"
      """
      Do not inspect files and do not use tools. Your complete response must
      contain only the remembered phrases in this exact format:
      amber circle, blue square
      """
    Then turn "after compaction" completes
    And turn "after compaction" has final answer 'amber circle, blue square'

    Examples:
      | harness     | model        | worker   | compacted context                |
      | codex       | gpt-5.6-luna | lead     | no expandable compacted context |
      | codex       | gpt-5.6-luna | subagent | no expandable compacted context |
      | claude_code | haiku        | lead     | expandable compacted context    |
      | claude_code | haiku        | subagent | expandable compacted context    |
