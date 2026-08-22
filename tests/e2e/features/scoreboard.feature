@drift
Feature: the scoreboard summarizes the session

  Scenario Outline: completed work fills every activity and usage counter
    Given the file operation fixture does not exist
    And a <harness> session on <model> at <effort> effort with prompt 'Run only the shell command `echo scoreboard-ok`, then reply only with success'
    Then the turn ends within 3 minutes
    When I send message 'Run only the shell command `sh -c "echo expected-error >&2; exit 7"`, then reply only with failed-as-expected'
    Then the turn ends within 3 minutes
    When I send message 'Using file editing tools, not shell commands, create baqylau-e2e-file.txt containing alpha, then edit alpha to beta. Finally reply only with done'
    Then the turn ends within 3 minutes
    And the scoreboard reports at least 3 prompts, 2 commands, 1 failed command, and 1 file
    And the scoreboard reports added and removed lines with Write and Edit tools
    And the scoreboard reports positive active time and token usage
    And the assistant ends the turn with 'done'

    Examples:
      | harness     | model        | effort |
      | codex       | gpt-5.6-luna | low    |
      | claude_code | haiku        | low    |

  Scenario: a completed yielded Codex command is history, not live background work
    Given a codex session on gpt-5.6-luna at low effort with prompt 'Use the shell execution tool with a 1000 ms yield time to run `sleep 5; echo yielded-done`. After it yields, poll that same process until it finishes, then reply only with done'
    Then the turn ends within 3 minutes
    And the jobs history contains exactly 1 backgrounded command
    And the scoreboard reports exactly 1 historical job and no running work
    And the assistant ends the turn with 'done'
