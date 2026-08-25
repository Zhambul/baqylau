Feature: web activity stays on the work that requested it

  Scenario Outline: a real web search belongs to one worker
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "search example" to the <worker> with prompt
      """
      Use the web search tool. Search with the exact query
      "IANA Example Domain reserved". Use the search result. When the work is
      complete, reply with the exact marker WEB_SEARCH_DONE and no other text.
      """
    Then work "search example" completes
    And work "search example" has worker type <worker>
    And work "search example" has final answer 'WEB_SEARCH_DONE'
    When I name the search in work "search example" with query containing 'IANA Example Domain reserved' "example search"
    Then search "example search" has state succeeded

    Examples:
      | harness     | model        | worker   |
      | codex       | gpt-5.6-luna | lead     |
      | codex       | gpt-5.6-luna | subagent |
      | claude_code | haiku        | lead     |
      | claude_code | haiku        | subagent |

  Scenario Outline: a real page fetch belongs to one worker
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "fetch example" to the <worker> with prompt
      """
      Use the web page fetch tool. Fetch the exact URL
      https://example.com without a web search. Read the page. When the work is
      complete, reply with the exact marker WEB_FETCH_DONE and no other text.
      """
    Then work "fetch example" completes
    And work "fetch example" has worker type <worker>
    And work "fetch example" has final answer 'WEB_FETCH_DONE'
    When I name the web fetch in work "fetch example" for URL 'https://example.com' "example page"
    Then web fetch "example page" has state succeeded
    And web fetch "example page" has result containing 'Example Domain'

    Examples:
      | harness     | model        | worker   |
      | codex       | gpt-5.6-luna | lead     |
      | codex       | gpt-5.6-luna | subagent |
      | claude_code | haiku        | lead     |
      | claude_code | haiku        | subagent |
