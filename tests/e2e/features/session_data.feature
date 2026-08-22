@drift
Feature: persisted session data survives dashboard upgrades

  Scenario: a previous actor model document remains readable after restart
    Given a claude_code session on haiku at low effort with prompt 'Only say "Hi" and nothing more'
    Then the turn ends within 3 minutes
    When the dashboard restarts with that actor model stored in the previous format
    Then the session list still includes that session
