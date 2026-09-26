Feature: extension packages see a real harness session

  Scenario Outline: an enabled package projects a feed card for a real turn
    Given session configuration "primary" uses <harness> with model <model> and low effort
    And extension package "hello" is enabled
    When I launch session "primary" as turn "hello turn" with prompt
      """
      Reply only with HELLO_READY.
      """
    Then turn "hello turn" completes
    And extension package "hello" projects a "example.hello.card" entry in session "primary"

    Examples:
      | harness     | model                           |
      | codex       | gpt-5.6-luna                    |
      | claude_code | haiku                           |
      | opencode2   | opencode-go/deepseek-v4.1-flash |

  Scenario Outline: the adapters package records a real CLI call from the harness shell
    Given session configuration "primary" uses <harness> with model <model> and low effort
    And extension package "adapters" is enabled
    When I launch session "primary" as turn "read logs" with prompt
      """
      Run the exact shell command `adapters logs -e preprod -s 5m -n 3` once. Do not
      change the command and do not run another command. Then reply only with LOGS_READ.
      """
    Then turn "read logs" completes
    And the adapters "logs" call in session "primary" finishes
    And extension package "adapters" projects a "baqylau.adapters.invocation" entry in session "primary"

    Examples:
      | harness     | model                           |
      | codex       | gpt-5.6-luna                    |
      | claude_code | haiku                           |
      | opencode2   | opencode-go/deepseek-v4.1-flash |

  Scenario Outline: the git package reads the repository's adapters calls through the adapters service
    Given session configuration "primary" uses <harness> with model <model> and low effort in the isolated repository workspace
    And extension package "adapters" is enabled
    And extension package "git" is enabled
    When I launch session "primary" as turn "commit help" with prompt
      """
      Run the exact shell command `adapters commit --help` once. Do not change the
      command and do not run another command. Then reply only with HELP_READ.
      """
    Then turn "commit help" completes
    And the adapters "commit" call in session "primary" finishes
    And the git activity of the repository of session "primary" has a "commit" call

    Examples:
      | harness     | model                           |
      | codex       | gpt-5.6-luna                    |
      | claude_code | haiku                           |
      | opencode2   | opencode-go/deepseek-v4.1-flash |

  Scenario Outline: a package disabled during a live session stops answering
    # Harness limit: claude_code only. A disable is a host lifecycle action, and the harness does not change it.
    Given session configuration "primary" uses <harness> with model <model> and low effort
    And extension package "adapters" is enabled
    When I launch session "primary" as turn "read logs" with prompt
      """
      Run the exact shell command `adapters logs -e preprod -s 5m -n 3` once. Do not
      change the command and do not run another command. Then reply only with LOGS_READ.
      """
    Then turn "read logs" completes
    And the adapters "logs" call in session "primary" finishes
    When I disable extension package "adapters"
    Then the adapters extension answers no calls for session "primary"

    Examples:
      | harness     | model                           |
      | claude_code | haiku                           |
