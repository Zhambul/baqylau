Feature: native permission requests reach the dashboard

  Scenario Outline: an external file request records its permission answer
    # Harness limit: opencode2 only. The other adapters do not expose native permission prompts.
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" with permission work "read external file" for an external file
    And I name the pending question in work "read external file" containing 'external_directory' "file access"
    Then question "file access" is single choice
    And question "file access" offers option 'Allow once'
    And question "file access" offers option 'Always allow'
    And question "file access" offers option 'Reject'
    When I answer question "file access" with option '<decision>' as control "allow file"
    Then control "allow file" response is accepted
    And control "allow file" outcome is acknowledged
    And question "file access" records option '<decision>'
    And question "file access" is resolved
    And work "read external file" completes
    And work "read external file" has final answer 'The access code is 731.'

    Examples:
      | harness   | model                         | decision     |
      | opencode2 | opencode-go/deepseek-v4.1-flash | Allow once   |
      | opencode2 | opencode-go/deepseek-v4.1-flash | Always allow |

  Scenario Outline: an external file request can be rejected
    # Harness limit: opencode2 only. The other adapters do not expose native permission prompts.
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" with permission work "read external file" for an external file
    And I name the pending question in work "read external file" containing 'external_directory' "file access"
    When I answer question "file access" with option 'Reject' as control "reject file"
    Then control "reject file" response is accepted
    And control "reject file" outcome is acknowledged
    And question "file access" records option 'Reject'
    And question "file access" is resolved
    And turn "read external file" has state aborted

    Examples:
      | harness   | model                         |
      | opencode2 | opencode-go/deepseek-v4.1-flash |
