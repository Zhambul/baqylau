Feature: an armed monitor reports its events

  Scenario Outline: monitor events arrive after the turn that armed it
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
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "arm short ticks" to the lead with prompt
      """
      First, use the Monitor tool with description short ticks to watch this exact
      command: `sleep 2; echo absorbed-mid-turn-event`. Do not run that command
      with Bash. Immediately after the Monitor tool returns, use Bash to run the
      exact foreground command `python3 -c 'import time; time.sleep(10)'` and wait
      for it to finish. Do not reply before that foreground command finishes.
      Then reply with exactly these five lowercase letters and no punctuation or
      other text: armed
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
