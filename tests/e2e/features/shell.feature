Feature: shell work reaches the session feed

  Scenario Outline: a command the model runs becomes a shell block
    Given session configuration "primary" uses <harness> with model <model> and <effort> effort
    When I launch session "primary" as turn "run hello" with prompt
      """
      Run the shell command `echo hello world`. Then, reply with exactly these
      four lowercase letters and no other text: done
      """
    Then turn "run hello" completes
    When I name the only shell command in turn "run hello" containing 'echo hello world' "hello command"
    Then command "hello command" has state succeeded
    And command "hello command" has output containing 'hello world'
    And session "primary" has at least 1 shell command
    And turn "run hello" has final answer 'done'

    Examples:
      | harness     | model        | effort |
      | codex       | gpt-5.6-luna | low    |
      | claude_code | haiku        | low    |
