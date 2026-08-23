Feature: shell work reaches the session feed

  Scenario Outline: a command the model runs becomes a shell block
    Given session configuration "primary" uses <harness> with model <model> and <effort> effort
    When I launch session "primary" and assign work "run hello" to the <worker> with prompt
      """
      Run the shell command `echo hello world`. Then, reply with exactly these
      four lowercase letters and no other text: done
      """
    Then work "run hello" completes
    And work "run hello" has worker type <worker>
    When I name the only shell command in work "run hello" containing 'echo hello world' "hello command"
    Then command "hello command" has state succeeded
    And command "hello command" has output containing 'hello world'
    And session "primary" has at least 1 shell command
    And work "run hello" has final answer 'done'

    Examples:
      | harness     | model        | effort | worker   |
      | codex       | gpt-5.6-luna | low    | lead     |
      | codex       | gpt-5.6-luna | low    | subagent |
      | claude_code | haiku        | low    | lead     |
      | claude_code | haiku        | low    | subagent |
