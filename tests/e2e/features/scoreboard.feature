Feature: the scoreboard summarizes the session

  Scenario Outline: completed work fills every activity and usage counter
    Given the file operation fixture does not exist
    And session configuration "primary" uses <harness> with model <model> and <effort> effort
    When I launch session "primary" and assign work "successful command" to the lead with prompt
      """
      Run only the shell command `echo scoreboard-ok`. Then, reply only with success.
      """
    Then work "successful command" completes
    And work "successful command" has worker type lead
    When I assign work "failed command" in session "primary" to the subagent with prompt
      """
      Run only the shell command `sh -c "echo expected-error >&2; exit 7"`.
      Then, reply only with failed-as-expected.
      """
    Then work "failed command" completes
    And work "failed command" has worker type subagent
    And work "failed command" releases the lead
    When I name the only shell command in work "failed command" containing 'expected-error' "expected failure"
    When I assign work "file changes" in session "primary" to the lead with prompt
      """
      Use file editing tools, not shell commands. Create baqylau-e2e-file.txt
      with the content alpha. Then, edit alpha to beta. Finally, reply only
      with exactly these four lowercase letters and no other text: done
      """
    Then work "file changes" completes
    And work "file changes" has worker type lead
    And command "expected failure" has state failed
    And command "expected failure" has exit code 7
    And command "expected failure" has output containing 'expected-error'
    And command "expected failure" belongs to worker of work "failed command"
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
    And work "file changes" has final answer 'done'

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
