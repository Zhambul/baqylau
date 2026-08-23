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
    And work "arm ticks" has final answer 'armed'

    Examples:
      | harness     | model | worker |
      | claude_code | haiku | lead   |
