Feature: background work reaches the session feed

  Scenario Outline: redirected output from one background command reaches its shell block
    # Harness limit: claude_code only. Only Claude Code reports its redirected output file.
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" as turn "redirect background output" with prompt
      """
      Use the Bash tool with run_in_background set to true. Run this exact command:

      python3 -c 'import time; print("redirect-one", flush=True); time.sleep(2); print("redirect-two", flush=True)' > baqylau-e2e-background-redirect.log 2>&1; echo "exit=$?" >> baqylau-e2e-background-redirect.log; printf 'pipe-output\n' | tee baqylau-e2e-background-pipe.log >/dev/null

      Do not read either file and do not start another tool. Reply only with the
      exact marker REDIRECT_STARTED.
      """
    When I name the only background job in turn "redirect background output" containing 'baqylau-e2e-background-redirect.log' "redirected background command"
    Then job "redirected background command" has output containing 'redirect-one'
    And job "redirected background command" has output containing 'redirect-two'
    And job "redirected background command" has output containing 'exit=0'
    And job "redirected background command" has output containing 'pipe-output'
    And job "redirected background command" ends
    And command "redirected background command" has state succeeded
    And turn "redirect background output" has final answer 'REDIRECT_STARTED'

    Examples:
      | harness     | model |
      | claude_code | haiku |

  Scenario Outline: a backgrounded command is tracked past the end of its turn
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "start delayed echo" to the <worker> with prompt
      """
      Run `sleep 30; echo done` as background work. <background_instruction>
      Do not wait for it. Reply only with the word started.
      """
    Then work "start delayed echo" has worker type <worker>
    When I name the only background job in work "start delayed echo" containing 'sleep' "delayed echo"
    Then job "delayed echo" is running
    And work "start delayed echo" completes
    And job "delayed echo" has output containing 'done'
    And job "delayed echo" ends
    And command "delayed echo" has state succeeded
    And session "primary" has no running work
    And work "start delayed echo" has final answer 'started'

    Examples:
      | harness     | model        | worker   | background_instruction                                                   |
      | codex       | gpt-5.6-luna | lead     | Run the command exactly as written: do not add &, nohup, or shell background syntax. Your first action must be the shell execution tool with a 1000 ms yield time. Once it yields, do not call wait or poll. |
      | codex       | gpt-5.6-luna | subagent | Run the command exactly as written: do not add &, nohup, or shell background syntax. Your first action must be the shell execution tool with a 1000 ms yield time. Once it yields, do not call wait or poll. |
      | claude_code | haiku        | lead     | Use the Bash tool with run_in_background set to true.                     |

  Scenario Outline: a completed empty command is not background work
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" as turn "empty command" with prompt
      """
      Run only the shell command `true`. <execution_instruction>
      Do not poll the command or run another tool. Then, reply only with the exact
      marker EMPTY_COMMAND_DONE.
      """
    Then turn "empty command" completes
    When I name the only shell command in turn "empty command" containing 'true' "empty command"
    Then session "primary" has no running work
    And turn "empty command" has exactly 0 backgrounded command
    And command "empty command" has state succeeded
    And turn "empty command" has final answer 'EMPTY_COMMAND_DONE'

    Examples:
      | harness     | model        | execution_instruction                                                       |
      | codex       | gpt-5.6-luna | Use the shell execution tool with a 10000 ms yield time.                     |
      | claude_code | haiku        | Use the Bash tool in the foreground. Do not set run_in_background to true.  |

  Scenario Outline: a subagent owns background work through completion
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "complete child job" to the subagent with prompt
      """
      Run `sleep 3; echo child-job-done` as background work.
      <completion_instruction> Wait until that same command completes. Then,
      reply only with the exact marker CHILD_JOB_DONE.
      """
    Then work "complete child job" completes
    And work "complete child job" has worker type subagent
    When I name the only background job in work "complete child job" containing 'sleep 3' "child job"
    Then job "child job" has output containing 'child-job-done'
    And job "child job" ends
    And command "child job" has state succeeded
    And command "child job" belongs to worker of work "complete child job"
    And subagent work "complete child job" has assignment state succeeded
    And work "complete child job" has final answer 'CHILD_JOB_DONE'

    Examples:
      | harness     | model        | completion_instruction                                                                                   |
      | codex       | gpt-5.6-luna | Use the shell execution tool with a 1000 ms yield time. Use the process wait operation after it yields.   |
      | claude_code | haiku        | Your first tool call must be Bash with run_in_background set to true; do not call ToolSearch or another tool first. After Bash returns, wait for its automatic completion notification. |

  Scenario Outline: a command backgrounded mid-run keeps reporting
    # Harness limit: claude_code only. Only Claude Code supports the background control.
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
