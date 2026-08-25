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

  Scenario Outline: input continues the same interactive command
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "answer interactive command" to the <worker> with prompt
      """
      Start the shell command below with a 250 ms yield time so it waits for
      input:

      python3 -c 'value=input(); print("received:" + value)'

      After the command yields a running process, use the process input tool on
      that same process. Send interactive-marker-417 followed by a newline.
      Wait for the process to finish. Then reply with the exact marker
      INTERACTIVE_DONE and no other text.
      """
    Then work "answer interactive command" completes
    And work "answer interactive command" has worker type <worker>
    When I name the successful shell command in work "answer interactive command" containing 'value=input' "interactive command"
    Then command "interactive command" has output containing 'received:interactive-marker-417'
    And command "interactive command" has state succeeded
    And command "interactive command" has exit code 0
    And work "answer interactive command" has final answer 'INTERACTIVE_DONE'

    Examples:
      | harness | model        | worker   |
      | codex   | gpt-5.6-luna | lead     |
      | codex   | gpt-5.6-luna | subagent |
