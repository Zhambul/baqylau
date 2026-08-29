Feature: the installed dashboard daemon survives process replacement

  Scenario: macOS restarts the installed daemon after it stops
    # Harness limit: no harness. This scenario tests the installed dashboard service.
    When I stop the installed dashboard daemon
    Then the installed dashboard health endpoint reports a new process
    And the installed dashboard launch agent is running with automatic startup enabled
