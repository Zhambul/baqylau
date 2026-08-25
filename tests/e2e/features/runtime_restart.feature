Feature: live harness sessions survive a Baqylau restart

  Scenario Outline: an idle live session remains usable after restart
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I start journey session "primary" from the dashboard as turn "before restart" with prompt
      """
      Remember restart-memory-852. Reply only with BEFORE_RESTART.
      """
    Then turn "before restart" completes
    And turn "before restart" has final answer 'BEFORE_RESTART'
    When I restart Baqylau as application restart "idle restart"
    Then application restart "idle restart" replaces the server process
    And session "primary" remains live and keeps turn "before restart" after restart
    When I continue journey session "primary" from the dashboard as turn "after restart" with prompt
      """
      If you remember restart-memory-852, reply only with AFTER_RESTART.
      """
    Then turn "after restart" completes
    And turn "after restart" has final answer 'AFTER_RESTART'
    And session "primary" has no repeated entry identity

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |

  Scenario Outline: active lead or subagent work completes through restart
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I start journey session "primary" from the dashboard as turn "lead ready" with prompt
      """
      Reply only with LEAD_READY.
      """
    Then turn "lead ready" completes
    When I assign work "restart work" in session "primary" to the <worker> with prompt
      """
      Run `python -c 'import time; time.sleep(12); print("restart-survived")'`
      as one foreground shell command. Wait for it. Then reply only with
      RESTART_WORK_DONE.
      """
    And I name the only running foreground command in work "restart work" containing 'time.sleep(12)' "restart command"
    When I restart Baqylau as application restart "active restart"
    Then application restart "active restart" replaces the server process
    And command "restart command" has output containing 'restart-survived'
    And command "restart command" has state succeeded
    And work "restart work" completes
    And work "restart work" has worker type <worker>
    And work "restart work" has final answer 'RESTART_WORK_DONE'
    And session "primary" has no repeated entry identity

    Examples:
      | harness     | model        | worker   |
      | codex       | gpt-5.6-luna | lead     |
      | codex       | gpt-5.6-luna | subagent |
      | claude_code | haiku        | lead     |
      | claude_code | haiku        | subagent |

  Scenario Outline: history preferences queue and resume state survive restart
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I start journey session "primary" from the dashboard as turn "active work" with prompt
      """
      Run `python -c 'import time; time.sleep(15); print("persistence-survived")'`
      as one foreground shell command. Wait for it. Then reply only with
      ACTIVE_PERSISTENCE_DONE.
      """
    And I name the only running foreground command in turn "active work" containing 'time.sleep(15)' "persistence command"
    And I save composer draft 'durable restart draft' for session "primary"
    And I set view mode focus for session "primary"
    And I mute notifications for session "primary"
    And I send prompt to session "primary" as turn "queued work" and control "queued persistence"
      """
      Reply only with QUEUED_PERSISTENCE_DONE.
      """
    Then control "queued persistence" reports queued delivery
    And session "primary" has control "queued persistence" queued as prompt 'Reply only with QUEUED_PERSISTENCE_DONE.' after a fresh application read
    When I restart Baqylau as application restart "persistence restart"
    Then application restart "persistence restart" replaces the server process
    And session "primary" remains live and keeps turn "active work" after restart
    And composer draft for session "primary" is 'durable restart draft'
    And view mode for session "primary" is focus
    And notifications for session "primary" are muted
    And session "primary" has control "queued persistence" queued as prompt 'Reply only with QUEUED_PERSISTENCE_DONE.' after a fresh application read
    And command "persistence command" has output containing 'persistence-survived'
    And command "persistence command" has state succeeded
    And turn "queued work" prompt is delivered after command "persistence command" finishes
    And turn "queued work" completes
    And turn "queued work" has final answer 'QUEUED_PERSISTENCE_DONE'
    And session "primary" has no queued prompts after a fresh application read
    When I close the terminal for journey session "primary"
    And I restart Baqylau as application restart "parked persistence restart"
    Then application restart "parked persistence restart" replaces the server process
    When I read resumable sessions for the workspace as "restart resume state"
    Then resumable list "restart resume state" contains session "primary"
    And resumable list "restart resume state" shows session "primary" as inactive
    And session "primary" has no repeated entry identity

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |
