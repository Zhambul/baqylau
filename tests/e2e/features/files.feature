Feature: file operations reach the session feed

  Scenario Outline: created and edited file content is available to expand
    Given the file operation fixture does not exist
    And session configuration "primary" uses <harness> with model <model> and <effort> effort
    When I launch session "primary" and assign work "change fixture" to the <worker> with prompt
      """
      Using file editing tools, not shell commands, first create
      baqylau-e2e-file.txt containing alpha. Then, in a separate tool call,
      edit alpha to beta. After the edit, reply with exactly these four lowercase
      letters and no other text: done
      """
    Then work "change fixture" completes
    And work "change fixture" has worker type <worker>
    When I name the created fixture operation in work "change fixture" "fixture creation"
    And I name the updated fixture operation in work "change fixture" "fixture update"
    Then file operation "fixture creation" has state succeeded
    And file operation "fixture creation" has content containing 'alpha'
    And file operation "fixture creation" has added lines
    And file operation "fixture update" has state succeeded
    And file operation "fixture update" has content containing 'beta'
    And file operation "fixture update" has added lines
    And file operation "fixture update" has removed lines
    And the file operation fixture contains 'beta'
    And work "change fixture" has final answer 'done'

    Examples:
      | harness     | model        | effort | worker   |
      | codex       | gpt-5.6-luna | low    | lead     |
      | codex       | gpt-5.6-luna | low    | subagent |
      | claude_code | haiku        | low    | lead     |
      | claude_code | haiku        | low    | subagent |

  Scenario Outline: a file read reports its path and content
    # Harness limit: claude_code only. Only Claude Code has a native local text file read event.
    Given session configuration "primary" uses <harness> with model <model> and <effort> effort
    When I launch session "primary" and assign work "read project guide" to the <worker> with prompt
      """
      Use the Read tool exactly once, not a shell command, to read README.md.
      The tool must return the file content in its result. Then, reply only
      with the word done.
      """
    Then work "read project guide" completes
    And work "read project guide" has worker type <worker>
    When I name the read operation in work "read project guide" for workspace file 'README.md' "project guide"
    Then file operation "project guide" has state succeeded
    And file operation "project guide" has content containing 'baqylau'
    And work "read project guide" has final answer 'done'

    Examples:
      | harness     | model        | effort | worker   |
      | claude_code | haiku        | low    | lead     |
      | claude_code | haiku        | low    | subagent |

  Scenario Outline: a deleted file keeps its complete operation history
    # Harness limit: codex only. Only Codex apply_patch reports a native file delete operation.
    Given the file operation fixture does not exist
    And session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "delete fixture" to the <worker> with prompt
      """
      Use apply_patch in two separate calls. First, create
      baqylau-e2e-file.txt with the exact content deletion-marker-731. Second,
      delete that file. Do not use a shell command. Reply only with the word
      done.
      """
    Then work "delete fixture" completes
    And work "delete fixture" has worker type <worker>
    When I name the created fixture operation in work "delete fixture" "fixture creation"
    And I name the deleted fixture operation in work "delete fixture" "fixture deletion"
    Then file operation "fixture creation" has state succeeded
    And file operation "fixture creation" has content containing 'deletion-marker-731'
    And file operation "fixture deletion" has state succeeded
    And file operation "fixture deletion" has removed lines
    And the file operation fixture is absent
    And work "delete fixture" has final answer 'done'

    Examples:
      | harness | model        | worker   |
      | codex   | gpt-5.6-luna | lead     |
      | codex   | gpt-5.6-luna | subagent |

  Scenario Outline: a renamed file keeps both exact paths
    # Harness limit: codex only. Only Codex apply_patch reports a native file move operation.
    Given the file rename fixtures do not exist
    And session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "rename fixture" to the <worker> with prompt
      """
      Use apply_patch in two separate calls. First, create
      baqylau-e2e-rename-source.txt with the exact content rename-marker-852.
      Second, use apply_patch with its Move to header to rename that file to
      baqylau-e2e-rename-target.txt. Do not use a shell command. When complete,
      reply with the exact marker RENAME_DONE and no other text.
      """
    Then work "rename fixture" completes
    And work "rename fixture" has worker type <worker>
    When I name the renamed operation in work "rename fixture" for workspace file 'baqylau-e2e-rename-target.txt' "fixture rename"
    Then file operation "fixture rename" has state succeeded
    And file operation "fixture rename" moved workspace file 'baqylau-e2e-rename-source.txt' to 'baqylau-e2e-rename-target.txt'
    And work "rename fixture" has final answer 'RENAME_DONE'

    Examples:
      | harness | model        | worker   |
      | codex   | gpt-5.6-luna | lead     |
      | codex   | gpt-5.6-luna | subagent |

  Scenario Outline: a failed file read keeps its exact path
    Given the missing file fixture does not exist
    And session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "read missing fixture" to the <worker> with prompt
      """
      Use the file tool named view_image if it is available; otherwise use
      Read. Call the chosen tool exactly once, not a shell command. Pass the
      exact relative path baqylau-e2e-missing-file-963.txt. Do not convert it
      to an absolute path. The file does not exist. Do not create it and do not
      retry with another tool. After the expected error, reply with the exact
      marker MISSING_READ_DONE and no other text.
      """
    Then work "read missing fixture" completes
    And work "read missing fixture" has worker type <worker>
    When I name the read operation in work "read missing fixture" for workspace file 'baqylau-e2e-missing-file-963.txt' "missing file read"
    Then file operation "missing file read" has state failed
    And work "read missing fixture" has final answer 'MISSING_READ_DONE'

    Examples:
      | harness     | model        | worker   |
      | codex       | gpt-5.6-luna | lead     |
      | codex       | gpt-5.6-luna | subagent |
      | claude_code | haiku        | lead     |
      | claude_code | haiku        | subagent |
