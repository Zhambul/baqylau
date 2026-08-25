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
