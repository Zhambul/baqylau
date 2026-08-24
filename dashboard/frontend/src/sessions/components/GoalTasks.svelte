<script lang="ts">
  import type { Session } from '../model';

  let { session }: { session: Session } = $props();

  function taskClass(state: Session['tasks'][number]['state']): string {
    switch (state) {
      case 'pending':
        return 'pend';
      case 'in_progress':
        return 'active';
      case 'completed':
        return 'done';
      case 'deleted':
        return 'done';
    }
  }

  function taskMark(state: Session['tasks'][number]['state']): string {
    switch (state) {
      case 'pending':
        return '○';
      case 'in_progress':
        return '◉';
      case 'completed':
        return '✓';
      case 'deleted':
        return '−';
    }
  }
</script>

{#if session.goal?.objective}
  <div class="goalwrap">
    <div class:met={session.goal.completed} class="goalcard">
      <div class="goalhead">
        <span class="goalmark">{session.goal.completed ? '✓' : '◎'}</span>
        <span class="goaltitle">goal</span>
        <span class="goalstate"
          >{session.goal.completed ? 'achieved' : 'active'}</span
        >
      </div>
      <div class="goalcond">{session.goal.objective}</div>
    </div>
  </div>
{/if}

{#if session.tasks.length > 0}
  <div class="taskswrap">
    <div class="taskscard">
      <div class="taskshead">
        <span class="taskstitle">tasks</span>
        <span class="taskscount">{session.tasks.length}</span>
      </div>
      <div class="tasklist">
        {#each session.tasks as task (task.taskId)}
          <div class={['taskrow', taskClass(task.state)]}>
            <span class="taskmark">{taskMark(task.state)}</span>
            <span class="taskid">{task.taskId}</span>
            <span class="tasksubj">{task.subject}</span>
            {#if task.state === 'in_progress'}
              <span class="taskactive">active</span>
            {/if}
          </div>
        {/each}
      </div>
    </div>
  </div>
{/if}
