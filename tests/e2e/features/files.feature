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
    And file operation "fixture creation" has added lines
    And file operation "fixture update" has state succeeded
    And file operation "fixture update" has content containing 'beta'
    And file operation "fixture update" has added lines
    And file operation "fixture update" has removed lines
    And the file operation fixture contains 'beta'
    And turn "change fixture" has final answer 'done'

    Examples:
      | harness     | model        | effort |
      | codex       | gpt-5.6-luna | low    |
      | claude_code | haiku        | low    |

  Scenario Outline: a file read reports its path and content
    Given session configuration "primary" uses <harness> with model <model> and <effort> effort
    When I launch session "primary" as turn "read project guide" with prompt
      """
      Use a file reading tool, not a shell command, to read README.md. The
      reading tool must return the file content in its result. Then, reply only
      with the word done.
      """
    Then turn "read project guide" completes
    When I name the read operation in turn "read project guide" for workspace file 'README.md' "project guide"
    Then file operation "project guide" has state succeeded
    And file operation "project guide" has content containing 'baqylau'
    And turn "read project guide" has final answer 'done'

    Examples:
      | harness     | model        | effort |
      | codex       | gpt-5.6-luna | low    |
      | claude_code | haiku        | low    |

  Scenario: a deleted file keeps its complete operation history
    Given the file operation fixture does not exist
    And session configuration "primary" uses codex with model gpt-5.6-luna and low effort
    When I launch session "primary" as turn "delete fixture" with prompt
      """
      Use apply_patch in two separate calls. First, create
      baqylau-e2e-file.txt with the exact content deletion-marker-731. Second,
      delete that file. Do not use a shell command. Reply only with the word
      done.
      """
    Then turn "delete fixture" completes
    When I name the created fixture operation in turn "delete fixture" "fixture creation"
    And I name the deleted fixture operation in turn "delete fixture" "fixture deletion"
    Then file operation "fixture creation" has state succeeded
    And file operation "fixture creation" has content containing 'deletion-marker-731'
    And file operation "fixture deletion" has state succeeded
    And file operation "fixture deletion" has removed lines
    And the file operation fixture is absent
    And turn "delete fixture" has final answer 'done'
