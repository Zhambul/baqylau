Feature: staged attachments reach a new harness session

  Scenario Outline: a text attachment is available in the first turn
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I stage text attachment 'e2e-context.txt' with content 'attachment-marker-731' as "context file"
    Then staged attachment "context file" is text file 'e2e-context.txt'
    When I launch session "primary" and assign work "read attachment" to the <worker> with attachment "context file" and prompt
      """
      Read the attached text file. Reply only with its exact content.
      """
    Then work "read attachment" completes
    And work "read attachment" has worker type <worker>
    And work "read attachment" has final answer 'attachment-marker-731'

    Examples:
      | harness     | model        | worker   |
      | codex       | gpt-5.6-luna | lead     |
      | codex       | gpt-5.6-luna | subagent |
      | claude_code | haiku        | lead     |
      | claude_code | haiku        | subagent |
