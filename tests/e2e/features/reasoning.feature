Feature: model activity updates one worker and its session

  Scenario Outline: readable model reasoning stays with its worker
    # Harness limit: codex, opencode2 only. Claude Code does not expose readable model reasoning.
    Given session configuration "primary" uses <harness> with model <model> and high effort
    When I launch session "primary" and assign work "inspect reasoning" to the <worker> with prompt
      """
      Find the smallest positive integer that has remainder 3 when divided
      by 7, remainder 4 when divided by 11, and remainder 5 when divided by
      13. Think carefully. Do not use tools. Reply only with the integer.
      """
    Then work "inspect reasoning" completes
    And work "inspect reasoning" has worker type <worker>
    And work "inspect reasoning" has final answer '213'
    When I name the reasoning trace in work "inspect reasoning" "calculation reasoning"
    Then reasoning trace "calculation reasoning" has at least 1 part
    And each part of reasoning trace "calculation reasoning" contains text

    Examples:
      | harness   | model                           | worker   |
      | codex     | gpt-5.6-luna                    | lead     |
      | codex     | gpt-5.6-luna                    | subagent |
      | opencode2 | opencode-go/deepseek-v4.1-flash | lead     |
      | opencode2 | opencode-go/deepseek-v4.1-flash | subagent |

  Scenario Outline: completed work reports context use and a native title
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "inspect activity" to the <worker> with prompt
      """
      Reply with the exact marker ACTIVITY_DONE and no other text.
      """
    Then work "inspect activity" completes
    And work "inspect activity" has worker type <worker>
    And work "inspect activity" has final answer 'ACTIVITY_DONE'
    And work "inspect activity" has positive context use
    And work "inspect activity" context use does not exceed its window
    And session "primary" has a non-empty native title

    Examples:
      | harness     | model                           | worker   |
      | codex       | gpt-5.6-luna                    | lead     |
      | codex       | gpt-5.6-luna                    | subagent |
      | claude_code | haiku                           | lead     |
      | opencode2   | opencode-go/deepseek-v4.1-flash | lead     |
      | claude_code | haiku                           | subagent |
      | opencode2   | opencode-go/deepseek-v4.1-flash | subagent |
