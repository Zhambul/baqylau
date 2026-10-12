Feature: an armed monitor reports its events

  Scenario Outline: monitor events arrive after the turn that armed it
    # Harness limit: claude_code only. Only Claude Code supports monitors.
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "arm ticks" to the <worker> with prompt
      """
      Use the Monitor tool with description ticks to watch this command:
      `for i in 1 2 3 4 5 6; do echo tick-$i; sleep 5; done`.
      Do not run it with Bash. Do not wait for it. Reply with exactly these five
      lowercase letters and no punctuation or other text: armed
      """
    Then work "arm ticks" completes
    And work "arm ticks" has worker type <worker>
    When I name the only monitor in work "arm ticks" containing 'tick' "tick monitor"
    Then monitor "tick monitor" is running
    And monitor "tick monitor" has event containing 'tick-2'
    And monitor "tick monitor" ends
    And command "tick monitor" has state succeeded
    And session "primary" has no running work
    And work "arm ticks" has first final answer 'armed'

    Examples:
      | harness     | model | worker |
      | claude_code | haiku | lead   |

  Scenario Outline: monitor completion arrives while the lead is still working
    # Harness limit: claude_code only. Only Claude Code supports monitors.
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "arm short ticks" to the lead with prompt
      """
      Use the Monitor tool with description short ticks to watch this exact
      command: `printf 'absorbed-mid-turn-event\n'`. Do not run that command with
      Bash. Do not wait for the monitor. Immediately reply with exactly these
      five lowercase letters and no punctuation or other text: armed
      """
    Then work "arm short ticks" completes
    When I name the only monitor in work "arm short ticks" containing 'absorbed-mid-turn-event' "short tick monitor"
    Then session "primary" has no running work
    And monitor "short tick monitor" has event containing 'absorbed-mid-turn-event'
    And command "short tick monitor" has state succeeded
    And work "arm short ticks" has final answer 'armed'

    Examples:
      | harness     | model |
      | claude_code | haiku |

  Scenario Outline: stopping a monitor ends its background work
    # Harness limit: claude_code only. Only Claude Code supports monitors.
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" as turn "stop monitor" with prompt
      """
      Your first tool call must use the Monitor tool with description endless
      ticks to watch this exact command:
      `while true; do echo endless-tick; sleep 20; done`. Do not run it with
      Bash. After Monitor returns the task ID, load TaskStop if needed and use
      TaskStop for that exact task ID. Do not wait for monitor events. Then,
      reply only with MONITOR_STOPPED.
      """
    Then turn "stop monitor" completes
    And turn "stop monitor" has final answer 'MONITOR_STOPPED'
    When I name the only monitor in turn "stop monitor" containing 'endless-tick' "stopped monitor"
    Then monitor "stopped monitor" ends
    And session "primary" has no running work
    And the lead in session "primary" has status awaiting_response

    Examples:
      | harness     | model |
      | claude_code | haiku |
