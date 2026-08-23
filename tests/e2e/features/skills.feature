Feature: skill execution reaches the session feed

  Scenario Outline: a harness skill has a named lifecycle
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" as turn "load communication skill" with prompt
      """
      Use the Skill tool exactly once with skill html-communication and no
      arguments. After the skill loads, do not create files and do not use more
      tools. Reply only with the word loaded.
      """
    Then turn "load communication skill" completes
    When I name the skill in turn "load communication skill" with exact name 'html-communication' "communication skill"
    Then skill "communication skill" has state succeeded
    And skill "communication skill" has no arguments
    And turn "load communication skill" has final answer 'loaded'

    Examples:
      | harness     | model |
      | claude_code | haiku |
