Feature: file operations reach the session feed

  Scenario Outline: created and edited file content is available to expand
    Given the file operation fixture does not exist
    And session configuration "primary" uses <harness> with model <model> and <effort> effort
    When I launch session "primary" as turn "change fixture" with prompt
      """
      Using file editing tools, not shell commands, first create
      baqylau-e2e-file.txt containing alpha. Then, in a separate tool call,
      edit alpha to beta. After the edit, reply with exactly these four lowercase
      letters and no other text: done
      """
    Then turn "change fixture" completes
    When I name the created fixture operation in turn "change fixture" "fixture creation"
    And I name the updated fixture operation in turn "change fixture" "fixture update"
    Then file operation "fixture creation" has state succeeded
    And file operation "fixture creation" has content containing 'alpha'
    And file operation "fixture update" has state succeeded
    And file operation "fixture update" has content containing 'beta'
    And turn "change fixture" has final answer 'done'

    Examples:
      | harness     | model        | effort |
      | codex       | gpt-5.6-luna | low    |
      | claude_code | haiku        | low    |
