Feature: planning tools update the session goal and tasks

  Scenario: Codex goal and plan tools update shared session state
    Given session configuration "primary" uses codex with model gpt-5.6-luna and low effort
    When I launch session "primary" as turn "record plan" with prompt
      """
      Use create_goal exactly once with objective "Complete the E2E planning sample".
      Use update_plan with exactly two steps named "Inspect the sample" and
      "Finish the sample". Complete both steps. Then, use update_goal to mark
      the goal complete. Do not inspect files. Reply only with the word done.
      """
    Then turn "record plan" completes
    When I name the task in session "primary" with subject 'Inspect the sample' "inspection"
    And I name the task in session "primary" with subject 'Finish the sample' "completion"
    Then session "primary" has exactly 2 tasks
    And task "inspection" has state completed
    And task "completion" has state completed
    And session "primary" has goal 'Complete the E2E planning sample'
    And the goal in session "primary" is complete
    And turn "record plan" has final answer 'done'

  Scenario: Claude Code task tools update shared session state
    Given session configuration "primary" uses claude_code with model haiku and low effort
    When I launch session "primary" as turn "record tasks" with prompt
      """
      Use TaskCreate exactly twice. Create tasks with subjects "Inspect the
      sample" and "Finish the sample". Then, use TaskUpdate to mark both tasks
      completed. Do not inspect files. Reply only with the word done.
      """
    Then turn "record tasks" completes
    When I name the task in session "primary" with subject 'Inspect the sample' "inspection"
    And I name the task in session "primary" with subject 'Finish the sample' "completion"
    Then session "primary" has exactly 2 tasks
    And task "inspection" has state completed
    And task "completion" has state completed
    And turn "record tasks" has final answer 'done'

  Scenario: a dashboard choice approves a Claude Code plan
    Given session configuration "primary" uses claude_code with model haiku and low effort
    When I launch session "primary" as turn "propose plan" with prompt
      """
      Use EnterPlanMode. Make a plan that contains the exact marker
      E2E-PLAN-MARKER-731. The plan must not change files or run commands. Use
      ExitPlanMode with the plan. Wait for the user decision. After approval,
      reply only with the word approved.
      """
    And I name the pending plan in turn "propose plan" containing 'E2E-PLAN-MARKER-731' "safe plan"
    Then plan "safe plan" contains 'E2E-PLAN-MARKER-731'
    When I read choices for plan "safe plan" as control "plan choices"
    Then control "plan choices" response is accepted
    And control "plan choices" outcome is acknowledged
    And control "plan choices" offers a plan option containing 'bypass permissions'
    When I choose plan option containing 'bypass permissions' from control "plan choices" for plan "safe plan" as control "approve plan"
    Then control "approve plan" response is accepted
    And control "approve plan" outcome is acknowledged
    And plan "safe plan" has state approved
    And turn "propose plan" completes
    And turn "propose plan" has final answer 'approved'
