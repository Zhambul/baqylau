"""The scoreboard's aggregate contract, including live-vs-history counts."""

from __future__ import annotations

from pytest_bdd import parsers, then

from impl.world import World, background_work
from support import observe
from support.daemon import Daemon


@then(parsers.parse(
    "the scoreboard reports at least {prompts:d} prompts, {commands:d} commands, "
    "{failed:d} failed command, and {files:d} file"
))
def _scoreboard_reports_activity(
    world: World,
    daemon: Daemon,
    prompts: int,
    commands: int,
    failed: int,
    files: int,
) -> None:
    actors = observe.actors(daemon, str(world.session_id))
    assert sum(actor.statistics.prompt_count for actor in actors) >= prompts
    assert sum(actor.statistics.shell_command_count for actor in actors) >= commands
    assert sum(actor.statistics.failed_shell_command_count for actor in actors) >= failed
    assert sum(actor.statistics.file_count for actor in actors) >= files


@then("the scoreboard reports added and removed lines with Write and Edit tools")
def _scoreboard_reports_files(world: World, daemon: Daemon) -> None:
    actors = observe.actors(daemon, str(world.session_id))
    assert sum(actor.statistics.lines_added for actor in actors) >= 1
    assert sum(actor.statistics.lines_removed for actor in actors) >= 1
    tools = {
        row.tool
        for actor in actors
        for row in actor.statistics.tool_counts
        if row.count > 0
    }
    assert {"Write", "Edit"} <= tools


@then("the scoreboard reports positive active time and token usage")
def _scoreboard_reports_time_and_usage(world: World, daemon: Daemon) -> None:
    actors = observe.actors(daemon, str(world.session_id))
    assert max(actor.statistics.active_seconds for actor in actors) > 0
    assert sum(actor.usage.tokens.input_tokens or 0 for actor in actors) > 0
    assert sum(actor.usage.tokens.output_tokens or 0 for actor in actors) > 0


@then(parsers.parse("the jobs history contains exactly {count:d} backgrounded command"))
def _jobs_history_contains(world: World, daemon: Daemon, count: int) -> None:
    world.execution = "background"
    jobs = background_work(world, daemon)
    assert len(jobs) == count, [
        (job.command, job.execution, job.backgrounded, job.state) for job in jobs
    ]


@then(parsers.parse(
    "the scoreboard reports exactly {count:d} historical job and no running work"
))
def _scoreboard_reports_finished_job(world: World, daemon: Daemon, count: int) -> None:
    actors = observe.actors(daemon, str(world.session_id))
    assert sum(actor.background.background_job_count for actor in actors) == count
    assert not {
        shell_id
        for actor in actors
        for shell_id in actor.background.running_shell_ids
    }
