Feature: background work reaches the session feed

  Scenario Outline: a backgrounded command is tracked past the end of its turn
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "start delayed echo" to the <worker> with prompt
      """
      Run `sleep 5; echo done` as background work. <background_instruction>
      Do not wait for it. Reply only with the word started.
      """
    Then work "start delayed echo" completes
    And work "start delayed echo" has worker type <worker>
    When I name the only background job in work "start delayed echo" containing 'sleep' "delayed echo"
    Then job "delayed echo" is running
    And job "delayed echo" has output containing 'done'
    And job "delayed echo" ends
    And command "delayed echo" has state succeeded
    And session "primary" has no running work
    And work "start delayed echo" has final answer 'started'

    Examples:
      | harness     | model        | worker   | background_instruction                                                   |
      | codex       | gpt-5.6-luna | lead     | Use the shell execution tool with a 1000 ms yield time and do not poll it. |
      | codex       | gpt-5.6-luna | subagent | Use the shell execution tool with a 1000 ms yield time and do not poll it. |
      | claude_code | haiku        | lead     | Use the Bash tool with run_in_background set to true.                     |

  Scenario Outline: a command backgrounded mid-run keeps reporting
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" as turn "run delayed echo" with prompt
      """
      Run `echo started; sleep 30; echo done` in the foreground and wait for it.
      Do not use run_in_background. If the command is moved to the background,
      do not start a monitor or any other tool. Reply only with the word started.
      """
    And I name the only running foreground command in turn "run delayed echo" containing 'sleep' "delayed echo"
    And I request backgrounding in session "primary" as control "background delayed echo"
    Then control "background delayed echo" response is accepted
    And control "background delayed echo" outcome is acknowledged
    And command "delayed echo" becomes a background job
    And job "delayed echo" is running
    And job "delayed echo" has output containing 'done'
    And job "delayed echo" ends
    And command "delayed echo" has state succeeded
    And session "primary" has no running work

    Examples:
      | harness     | model |
      | claude_code | haiku |
