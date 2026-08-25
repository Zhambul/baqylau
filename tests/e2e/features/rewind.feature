Feature: rewind restores a named prompt for revision

  Scenario Outline: a harness restores a named prompt for revision
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" as turn "first prompt" with prompt
      """
      Reply only with the word first.
      """
    Then turn "first prompt" completes
    And turn "first prompt" has final answer 'first'
    When I send prompt to session "primary" as turn "second prompt"
      """
      Reply only with the word second.
      """
    Then turn "second prompt" completes
    And turn "second prompt" has final answer 'second'
    When I rewind session "primary" to turn "first prompt" with conversation mode as control "restore first prompt"
    Then control "restore first prompt" response is accepted
    And control "restore first prompt" outcome is acknowledged
    And control "restore first prompt" restores turn "first prompt"
    When I revise the restored draft in session "primary" as turn "revised prompt"
      """
      Reply only with the word revised.
      """
    Then turn "revised prompt" completes
    And turn "revised prompt" has final answer 'revised'
    And session "primary" keeps one live terminal after revision

    Examples:
      | harness     | model |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku |

  Scenario Outline: code rewind restores the file at the selected checkpoint
    Given the rewind file contains 'rewind-baseline-194'
    And session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" as turn "file checkpoint" with prompt
      """
      Remember the marker rewind-code-anchor-194. Do not modify any file.
      Reply only with FILE_CHECKPOINT_READY.
      """
    Then turn "file checkpoint" completes
    When I send prompt to session "primary" as turn "file change"
      """
      Use the Edit tool to replace rewind-baseline-194 with
      rewind-newer-code-731 in baqylau-e2e-rewind.txt. Do not use a shell
      command. Then reply only with FILE_CHANGED.
      """
    Then turn "file change" completes
    And the rewind file contains exactly 'rewind-newer-code-731'
    When I rewind session "primary" to turn "file change" with code mode as control "restore code checkpoint"
    Then control "restore code checkpoint" response is accepted
    And control "restore code checkpoint" outcome is acknowledged
    And the rewind file contains exactly 'rewind-baseline-194'

    Examples:
      | harness     | model |
      | claude_code | haiku |

  Scenario Outline: combined rewind restores file and conversation together
    Given the rewind file contains 'rewind-baseline-194'
    And session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" as turn "kept memory" with prompt
      """
      Remember the marker rewind-kept-194. Do not modify any file.
      Reply only with KEPT_MEMORY_READY.
      """
    Then turn "kept memory" completes
    When I send prompt to session "primary" as turn "removed change"
      """
      Remember the marker rewind-removed-731. Use the Edit tool to replace
      rewind-baseline-194 with rewind-newer-code-731 in
      baqylau-e2e-rewind.txt. Do not use a shell command. Then reply only with
      REMOVED_CHANGE_READY.
      """
    Then turn "removed change" completes
    And the rewind file contains exactly 'rewind-newer-code-731'
    When I rewind session "primary" to turn "removed change" with both mode as control "restore both checkpoint"
    Then control "restore both checkpoint" response is accepted
    And control "restore both checkpoint" outcome is acknowledged
    And control "restore both checkpoint" restores turn "removed change"
    And the rewind file contains exactly 'rewind-baseline-194'
    When I revise the restored draft in session "primary" as turn "verify combined rewind"
      """
      Reply only with the complete list of marker strings I asked you to
      remember before this prompt, in chronological order, separated by a
      comma. Do not include an explanation.
      """
    Then turn "verify combined rewind" completes
    And turn "verify combined rewind" has final answer 'rewind-kept-194'

    Examples:
      | harness     | model |
      | claude_code | haiku |

  Scenario Outline: conversation rewind does not restore the file
    Given the rewind file contains 'rewind-baseline-194'
    And session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" as turn "kept conversation" with prompt
      """
      Remember the marker conversation-kept-194. Do not modify any file.
      Reply only with KEPT_CONVERSATION_READY.
      """
    Then turn "kept conversation" completes
    When I send prompt to session "primary" as turn "removed conversation"
      """
      Remember the marker conversation-removed-731. Use the Edit tool to
      replace rewind-baseline-194 with rewind-newer-code-731 in
      baqylau-e2e-rewind.txt. Do not use a shell command. Then reply only with
      REMOVED_CONVERSATION_READY.
      """
    Then turn "removed conversation" completes
    And the rewind file contains exactly 'rewind-newer-code-731'
    When I rewind session "primary" to turn "removed conversation" with conversation mode as control "restore conversation checkpoint"
    Then control "restore conversation checkpoint" response is accepted
    And control "restore conversation checkpoint" outcome is acknowledged
    And control "restore conversation checkpoint" restores turn "removed conversation"
    And the rewind file contains exactly 'rewind-newer-code-731'
    When I revise the restored draft in session "primary" as turn "verify conversation rewind"
      """
      Reply only with the complete list of marker strings I asked you to
      remember before this prompt, in chronological order, separated by a
      comma. Do not include an explanation.
      """
    Then turn "verify conversation rewind" completes
    And turn "verify conversation rewind" has final answer 'conversation-kept-194'

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |
