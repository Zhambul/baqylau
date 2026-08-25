Feature: worktree changes reach the session feed

  Scenario Outline: a temporary worktree reports its complete lifecycle
    Given session configuration "primary" uses <harness> with model <model> and low effort in a versioned workspace
    When I launch session "primary" and assign work "use temporary worktree" to the lead with prompt
      """
      Use the EnterWorktree tool exactly once to enter a temporary worktree.
      Do not change any files. Then use the ExitWorktree tool exactly once and
      discard the temporary worktree. When complete, reply with the exact
      marker WORKTREE_DONE and no other text.
      """
    Then work "use temporary worktree" completes
    And work "use temporary worktree" has worker type lead
    And work "use temporary worktree" has final answer 'WORKTREE_DONE'
    When I name the entered worktree change in work "use temporary worktree" "temporary worktree entered"
    And I name the exited worktree change in work "use temporary worktree" "temporary worktree exited"
    Then worktree change "temporary worktree entered" has state succeeded
    And worktree change "temporary worktree exited" has state succeeded

    Examples:
      | harness     | model |
      | claude_code | haiku |
