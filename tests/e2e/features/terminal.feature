Feature: a real terminal owns one composable session surface

  Scenario Outline: a session terminal supports its complete pane lifecycle
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I start journey session "primary" from the terminal as turn "terminal pane start" with prompt
      """
      Reply only with TERMINAL_PANES_READY.
      """
    Then turn "terminal pane start" completes
    And turn "terminal pane start" has final answer 'TERMINAL_PANES_READY'
    And journey session "primary" has its exact terminal pane set
    When I remember journey session "primary" pane geometry as "opened"
    And I toggle journey session "primary" terminal panes
    Then journey session "primary" has no auxiliary terminal panes
    When I toggle journey session "primary" terminal panes
    Then journey session "primary" has its exact terminal pane set
    When I remember journey session "primary" pane geometry as "reopened"
    And I grow journey session "primary" activity pane by 7 columns
    Then journey session "primary" activity pane is wider than "reopened"
    When I remember journey session "primary" pane geometry as "grown"
    And I shrink journey session "primary" activity pane by 5 columns
    Then journey session "primary" activity pane is narrower than "grown"
    When I set journey session "primary" activity pane to 35 percent
    Then journey session "primary" activity pane uses 35 percent
    When I toggle journey session "primary" terminal panes
    Then journey session "primary" has no auxiliary terminal panes
    When I toggle journey session "primary" terminal panes
    Then journey session "primary" activity pane uses 35 percent
    When I reset journey session "primary" activity pane width
    Then journey session "primary" activity pane uses 25 percent

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |

  Scenario Outline: dashboard launch and pane setup preserve terminal focus
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I remember current terminal focus as "before dashboard launch"
    And I start journey session "primary" from the dashboard as turn "background launch" with prompt
      """
      Reply only with BACKGROUND_LAUNCH_READY.
      """
    Then turn "background launch" completes
    And turn "background launch" has final answer 'BACKGROUND_LAUNCH_READY'
    And journey session "primary" has its exact terminal pane set
    And current terminal focus remains "before dashboard launch"

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |

  Scenario Outline: pane ownership and controls survive an application restart
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I start journey session "primary" from the terminal as turn "before pane restart" with prompt
      """
      Remember the marker pane-restart-284. Reply only with BEFORE_PANE_RESTART.
      """
    Then turn "before pane restart" completes
    And turn "before pane restart" has final answer 'BEFORE_PANE_RESTART'
    And journey session "primary" has its exact terminal pane set
    When I restart Baqylau as application restart "pane restart"
    Then application restart "pane restart" replaces the server process
    And session "primary" remains live and keeps turn "before pane restart" after restart
    And journey session "primary" has its exact terminal pane set
    When I toggle journey session "primary" terminal panes
    Then journey session "primary" has no auxiliary terminal panes
    When I toggle journey session "primary" terminal panes
    Then journey session "primary" has its exact terminal pane set
    When I continue journey session "primary" from the dashboard as turn "after pane restart" with prompt
      """
      If you remember pane-restart-284, reply only with AFTER_PANE_RESTART.
      """
    Then turn "after pane restart" completes
    And turn "after pane restart" has final answer 'AFTER_PANE_RESTART'

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |

  Scenario Outline: a detached harness cannot inherit another session terminal
    Given session configuration "host" uses <host_harness> with model <host_model> and low effort
    And session configuration "detached" uses <detached_harness> with model <detached_model> and low effort
    When I start journey session "host" from the terminal as turn "host start" with prompt
      """
      Remember the marker detached-terminal-owner-731. Reply with exactly this text and no other text: HOST_READY
      """
    Then turn "host start" completes
    And turn "host start" has final answer 'HOST_READY'
    When I run unattended session "detached" with the terminal environment from journey session "host" and prompt
      """
      Reply with exactly this text and no other text: DETACHED_READY
      """
    Then session "detached" finishes
    When I close session "detached" as control "close detached"
    Then control "close detached" response is rejected
    And control "close detached" outcome is rejected
    When I continue journey session "host" from the terminal as turn "host after detached close" with prompt
      """
      If you remember detached-terminal-owner-731, reply with exactly this text and no other text: HOST_STILL_READY
      """
    Then turn "host after detached close" completes
    And turn "host after detached close" has final answer 'HOST_STILL_READY'

    Examples:
      | host_harness | host_model   | detached_harness | detached_model |
      | codex        | gpt-5.6-luna | claude_code      | haiku          |
      | claude_code  | haiku        | codex            | gpt-5.6-luna   |

  Scenario Outline: native exit finishes the harness but keeps the shell tab
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I start journey session "primary" from the terminal as turn "before native exit" with prompt
      """
      Reply with exactly this text and no other text: BEFORE_NATIVE_EXIT
      """
    Then turn "before native exit" completes
    And turn "before native exit" has final answer 'BEFORE_NATIVE_EXIT'
    When I submit native command '/exit' to journey session "primary"
    Then session "primary" finishes
    And session "primary" is not live
    And a fresh application session list does not contain session "primary"
    And journey session "primary" keeps its shell tab

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |

  Scenario Outline: native new transfers one terminal to one new session
    Given session configuration "original" uses <harness> with model <model> and low effort
    When I start journey session "original" from the terminal as turn "before native new" with prompt
      """
      Reply with exactly this text and no other text: BEFORE_NATIVE_NEW
      """
    Then turn "before native new" completes
    And turn "before native new" has final answer 'BEFORE_NATIVE_NEW'
    When I start journey session "replacement" with native /new in journey session "original" as turn "after native new" with prompt
      """
      Reply with exactly this text and no other text: AFTER_NATIVE_NEW
      """
    Then session "original" finishes
    And session "original" is not live
    And a fresh application session list does not contain session "original"
    And session "replacement" is live
    And journey session "replacement" reuses the terminal from journey session "original"
    When I close session "original" as control "close replaced session"
    Then control "close replaced session" response is rejected
    And control "close replaced session" outcome is rejected
    Then turn "after native new" completes
    And turn "after native new" has final answer 'AFTER_NATIVE_NEW'

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |
