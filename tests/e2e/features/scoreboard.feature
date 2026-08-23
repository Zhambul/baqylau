Feature: the scoreboard summarizes the session

  Scenario Outline: completed work fills every activity and usage counter
    Given the file operation fixture does not exist
    And session configuration "primary" uses <harness> with model <model> and <effort> effort
    When I launch session "primary" as turn "successful command" with prompt
      """
      Run only the shell command `echo scoreboard-ok`. Then, reply only with success.
      """
    Then turn "successful command" completes
    When I send prompt to session "primary" as turn "failed command"
      """
      Run only the shell command `sh -c "echo expected-error >&2; exit 7"`.
      Then, reply only with failed-as-expected.
      """
    Then turn "failed command" completes
    When I name the only shell command in turn "failed command" containing 'expected-error' "expected failure"
    When I send prompt to session "primary" as turn "file changes"
      """
      Use file editing tools, not shell commands. Create baqylau-e2e-file.txt
      with the content alpha. Then, edit alpha to beta. Finally, reply only
      with exactly these four lowercase letters and no other text: done
      """
    Then turn "file changes" completes
    And command "expected failure" has state failed
    And command "expected failure" has exit code 7
    And command "expected failure" has output containing 'expected-error'
    And session "primary" has at least 3 prompts
    And session "primary" has at least 2 shell commands
    And session "primary" has at least 1 failed shell command
    And session "primary" has at least 1 file operation
    And session "primary" has added lines
    And session "primary" has removed lines
    And session "primary" used tool Write
    And session "primary" used tool Edit
    And session "primary" has positive active time
    And session "primary" has positive input token usage
    And session "primary" has positive output token usage
    And turn "file changes" has final answer 'done'

    Examples:
      | harness     | model        | effort |
      | codex       | gpt-5.6-luna | low    |
      | claude_code | haiku        | low    |

  Scenario Outline: a completed yielded command is history, not live background work
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" as turn "yielded command" with prompt
      """
      Use the shell execution tool with a 1000 ms yield time to run
      `sleep 5; echo yielded-done`. After it yields, poll that same process until
      it finishes. Then, reply with exactly these four lowercase letters and no
      other text: done
      """
    Then turn "yielded command" completes
    And turn "yielded command" has exactly 1 backgrounded command
    And session "primary" has exactly 1 historical job
    And session "primary" has no running work
    And turn "yielded command" has final answer 'done'

    Examples:
      | harness | model        |
      | codex   | gpt-5.6-luna |
