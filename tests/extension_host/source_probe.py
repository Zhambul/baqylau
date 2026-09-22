# Copyright (c) 2026 Zhambyl Yermagambet
"""Use explicit local source protocols for scheduler boundary tests, not worker evidence."""

from dataclasses import dataclass, field, replace

from baqylau_extension_api.contracts.plugin import ExtensionCapabilities, ExtensionPlugin
from baqylau_extension_api.contracts.sources import ExtensionSources
from baqylau_extension_api.models import lifecycle, source_results, sources
from baqylau_extension_api.runtime.host_context import current_host_call_id
from baqylau_extension_api.sources.batches import source_read_binding

from tests.extension_api import operation_samples, source_example, source_samples


@dataclass
class SourceTrace:
    """Retain exact local calls and watch order for assertions."""

    actions: list[str] = field(default_factory=list)
    descriptions: list[sources.SourceContext] = field(default_factory=list)
    reads: list[sources.SourceReadRequest] = field(default_factory=list)
    releases: list[sources.SourceReleaseRequest] = field(default_factory=list)
    grants: list[str | None] = field(default_factory=list)


@dataclass
class SourceBehavior:
    """Select finite pages and explicit source failure cases."""

    pages: int = 1
    fail_describe: bool = False
    fail_read: bool = False
    wrong_binding: bool = False
    pending_release: bool = False
    next_due_at: float | None = None


@dataclass
class SourceProbe(ExtensionSources):
    """Return source-owned pages with host-selected call bindings and scope."""

    plan: tuple[sources.SourceDescriptor, ...] = field(default_factory=lambda: (source_samples.descriptor(),))
    trace: SourceTrace = field(default_factory=SourceTrace)
    behavior: SourceBehavior = field(default_factory=SourceBehavior)

    def describe(self, source_context: sources.SourceContext) -> source_results.SourcePlan:
        """Record a describe call and return its configured complete plan.

        Returns:
            A correctly bound plan unless this case requests a failure.

        Raises:
            RuntimeError: If the test requests a failed describe call.

        """
        self.trace.actions.append("describe")
        self.trace.descriptions.append(source_context)
        if self.behavior.fail_describe:
            message = "fixture describe failed"
            raise RuntimeError(message)
        return source_results.SourcePlan(binding=source_context.binding, sources=self.plan)

    def read(self, source_request: sources.SourceReadRequest) -> source_results.SourceReadResult:
        """Read one finite fixture page and record active host authority.

        Returns:
            An original observation, or an unchanged empty reply after the last page.

        Raises:
            RuntimeError: If the test requests a failed read call.

        """
        self.trace.actions.append("read")
        self.trace.reads.append(source_request)
        self.trace.grants.append(current_host_call_id())
        if self.behavior.fail_read:
            message = "fixture read failed"
            raise RuntimeError(message)
        response = fixture_page(source_request, self.behavior.pages, self.behavior.next_due_at)
        if self.behavior.wrong_binding:
            response = response.model_copy(update={"binding": response.binding.model_copy(update={
                "call": response.binding.call.model_copy(update={"call_id": "wrong"}),
            })})
        return response

    def release(self, release_request: sources.SourceReleaseRequest) -> source_results.SourceReleaseResult:
        """Record exact cleanup requests without pretending to own an external process.

        Returns:
            The selected complete or pending acknowledgment.

        """
        self.trace.actions.append("release")
        self.trace.releases.append(release_request)
        return source_results.SourceReleaseResult(
            binding=release_request.binding, source_identity=release_request.source_identity,
            status="pending" if self.behavior.pending_release else "released",
        )


def fixture_page(
    request: sources.SourceReadRequest, pages: int, next_due_at: float | None,
) -> source_results.SourceBatch:
    """Use numeric fixture positions only inside this source implementation.

    Returns:
        A complete source reply which retains the exact host binding.

    """
    position = 0 if request.after_position is None else int(request.after_position)
    if position >= pages:
        return source_results.SourceBatch(
            binding=source_read_binding(request), next_position=request.after_position, next_due_at=next_due_at,
        )
    position += 1
    original = operation_samples.observation().model_copy(update={
        "observation_key": f"entry-{position}", "source_identity": request.source.source_identity,
        "source_type": request.source.source_type, "scope": request.context.binding.scope,
    })
    return source_results.SourceBatch(
        binding=source_read_binding(request), next_position=str(position), has_more=position < pages,
        observations=(source_results.PositionedObservation(position=str(position), observation=original),),
        next_due_at=next_due_at,
    )


@dataclass(frozen=True)
class SourcePlugin(ExtensionPlugin):
    """Use the SDK fixture's lifecycle and translator around a controlled local source."""

    base: source_example.SourceExample
    source: SourceProbe

    @property
    def extension_info(self) -> lifecycle.ExtensionInfo:
        """The exact installed fixture identity."""
        return self.base.extension_info

    @property
    def capabilities(self) -> ExtensionCapabilities:
        """Replace only the controlled source capability."""
        return replace(self.base.capabilities, sources=self.source)
