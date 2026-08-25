Feature: planning tools update the session goal and tasks

  Scenario Outline: a subagent owns the task that it records
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "record child task" to the subagent with prompt
      """
      <task_instruction> Create exactly one task with subject "Child sample".
      Mark it completed. Do not inspect files. Reply only with the word done.
      """
    Then work "record child task" completes
    And work "record child task" has worker type subagent
    When I name the task in session "primary" with subject 'Child sample' "child task"
    Then session "primary" has exactly 1 tasks
    And task "child task" has state completed
    And task "child task" belongs to worker of work "record child task"
    And work "record child task" has final answer 'done'

    Examples:
      | harness     | model        | task_instruction                                                                 |
      | codex       | gpt-5.6-luna | Use update_plan with the exact step text "Child sample".                   |

  Scenario Outline: a task keeps its native description where supported
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "describe task" to the lead with prompt
      """
      Use TaskCreate to create exactly one task with subject "Described sample"
      and description "Recorded by the child". Use TaskUpdate to mark it
      completed. Do not inspect files. Reply only with the word done.
      """
    Then work "describe task" completes
    And work "describe task" has worker type lead
    When I name the task in session "primary" with subject 'Described sample' "described task"
    Then task "described task" has state completed
    And task "described task" has description 'Recorded by the child'
    And task "described task" belongs to worker of work "describe task"

    Examples:
      | harness     | model |
      | claude_code | haiku |

  Scenario Outline: task tools update shared session state
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "prepare tasks" to the subagent with prompt
      """
      Read these task subjects: "Inspect the sample" and "Finish the sample".
      Do not use tools. Reply only with the word prepared.
      """
    Then work "prepare tasks" completes
    And work "prepare tasks" has worker type subagent
    And work "prepare tasks" has final answer 'prepared'
    And work "prepare tasks" releases the lead
    When I assign work "record tasks" in session "primary" to the lead with prompt
      """
      <task_instruction> Create exactly two tasks with subjects "Inspect the
      sample" and "Finish the sample". Mark both tasks completed. Do not inspect
      files. Reply only with the word done.
      """
    Then work "record tasks" completes
    And work "record tasks" has worker type lead
    When I name the task in session "primary" with subject 'Inspect the sample' "inspection"
    And I name the task in session "primary" with subject 'Finish the sample' "completion"
    Then session "primary" has exactly 2 tasks
    And task "inspection" has state completed
    And task "completion" has state completed
    And work "record tasks" has final answer 'done'

    Examples:
      | harness     | model        | task_instruction                                             |
      | codex       | gpt-5.6-luna | Use update_plan for all task creation and state changes.      |
      | claude_code | haiku        | Use TaskCreate for creation and TaskUpdate for state changes. |

  Scenario Outline: one task advances through each task state
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" and assign work "create tracked task" to the lead with prompt
      """
      <create_instruction> Keep the task pending. Do not inspect files. Reply
      only with TASK_PENDING.
      """
    Then work "create tracked task" completes
    When I name the task in session "primary" with subject 'Tracked state sample' "tracked task"
    Then task "tracked task" has state pending
    When I assign work "start tracked task" in session "primary" to the lead with prompt
      """
      <start_instruction> Do not complete the task. Do not inspect files. Reply
      only with TASK_ACTIVE.
      """
    Then work "start tracked task" completes
    And task "tracked task" has state in_progress
    When I assign work "complete tracked task" in session "primary" to the lead with prompt
      """
      <complete_instruction> Do not inspect files. Reply only with TASK_COMPLETED.
      """
    Then work "complete tracked task" completes
    And task "tracked task" has state completed

    Examples:
      | harness     | model        | create_instruction                                                                  | start_instruction                                                                                               | complete_instruction                                                                                          |
      | codex       | gpt-5.6-luna | Use update_plan exactly once with one step named "Tracked state sample".            | Use update_plan exactly once. Keep the one step named "Tracked state sample" and set its status to in_progress. | Use update_plan exactly once. Keep the one step named "Tracked state sample" and set its status to completed. |
      | claude_code | haiku        | Use TaskCreate exactly once with subject "Tracked state sample" and no description. | Use TaskList to find "Tracked state sample", then use TaskUpdate exactly once to set its status to in_progress. | Use TaskList to find "Tracked state sample", then use TaskUpdate exactly once to set its status to completed. |

  Scenario Outline: goal tools update shared session state
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" as turn "record goal" with prompt
      """
      Use create_goal exactly once with objective "Complete the E2E planning sample".
      Then, use update_goal to mark the goal complete. Do not inspect files.
      Reply only with the word done.
      """
    Then turn "record goal" completes
    And session "primary" has goal 'Complete the E2E planning sample'
    And the goal in session "primary" is complete
    And turn "record goal" has final answer 'done'

    Examples:
      | harness | model        |
      | codex   | gpt-5.6-luna |

  Scenario Outline: a goal reports active blocked and completed states
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" as turn "start waiting goal" with prompt
      """
      Use create_goal exactly once with objective "Receive the E2E goal value".
      The required value has not been provided. This is the first blocked turn.
      Do not use update_goal. Reply only with WAITING_ONE.
      """
    Then session "primary" has goal 'Receive the E2E goal value'
    And the goal in session "primary" has state active
    And turn "start waiting goal" completes
    And the goal in session "primary" has state blocked
    When I send prompt to session "primary" as turn "complete waiting goal"
      """
      The required value is E2E-GOAL-VALUE. The goal is now achieved. Use
      update_goal exactly once to mark the goal complete. Reply only with
      GOAL_COMPLETED.
      """
    Then turn "complete waiting goal" completes
    And the goal in session "primary" has state completed
    And the goal in session "primary" is complete

    Examples:
      | harness | model        |
      | codex   | gpt-5.6-luna |

  Scenario Outline: a dashboard choice approves a harness plan
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" as turn "ready" with prompt
      """
      Do not use tools. Reply only with the word ready.
      """
    Then turn "ready" completes
    And turn "ready" has final answer 'ready'
    When I start plan work "propose plan" in session "primary" with prompt
      """
      Make a plan that contains the exact marker E2E-PLAN-MARKER-731. The plan
      must not change files or run commands. Wait for the person to decide.
      After approval, reply only with the word approved.
      """
    And I name the pending plan in turn "propose plan" containing 'E2E-PLAN-MARKER-731' "safe plan"
    Then plan "safe plan" contains 'E2E-PLAN-MARKER-731'
    When I read choices for plan "safe plan" as control "plan choices"
    Then control "plan choices" response is accepted
    And control "plan choices" outcome is acknowledged
    And control "plan choices" offers an approval plan option
    When I approve plan "safe plan" from control "plan choices" as control "approve plan"
    Then control "approve plan" response is accepted
    And control "approve plan" outcome is acknowledged
    And plan "safe plan" has state approved
    And plan "safe plan" is followed by final answer 'approved' after control "approve plan"

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |

  Scenario Outline: dismissing a plan keeps the session usable
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" as turn "ready" with prompt
      """
      Do not use tools. Reply only with the word ready.
      """
    Then turn "ready" completes
    When I start plan work "propose plan" in session "primary" with prompt
      """
      Make a plan that contains the exact marker E2E-DISMISS-PLAN-731. The plan
      must not change files or run commands. Wait for the person to decide.
      """
    And I name the pending plan in turn "propose plan" containing 'E2E-DISMISS-PLAN-731' "dismissed plan"
    When I dismiss plan "dismissed plan" as control "dismiss plan"
    Then control "dismiss plan" response is accepted
    And control "dismiss plan" outcome is acknowledged
    And plan "dismissed plan" has state rejected
    When I send prompt to session "primary" as turn "continue after dismiss"
      """
      Do not use tools. Reply only with the word continued.
      """
    Then turn "continue after dismiss" completes
    And turn "continue after dismiss" has final answer 'continued'

    Examples:
      | harness     | model        |
      | codex       | gpt-5.6-luna |
      | claude_code | haiku        |

  Scenario Outline: plan feedback records the requested change
    Given session configuration "primary" uses <harness> with model <model> and low effort
    When I launch session "primary" as turn "ready" with prompt
      """
      Do not use tools. Reply only with the word ready.
      """
    Then turn "ready" completes
    When I start plan work "propose plan" in session "primary" with prompt
      """
      Make a plan that contains the exact marker E2E-FEEDBACK-PLAN-731. The plan
      must not change files or run commands. Wait for the person to decide.
      """
    And I name the pending plan in turn "propose plan" containing 'E2E-FEEDBACK-PLAN-731' "changeable plan"
    When I request plan changes 'start with the tests' for plan "changeable plan" as control "change plan"
    Then control "change plan" response is accepted
    And control "change plan" outcome is acknowledged
    And plan "changeable plan" has state changes_requested
    And plan "changeable plan" has feedback 'start with the tests'

    Examples:
      | harness     | model |
      | claude_code | haiku |
