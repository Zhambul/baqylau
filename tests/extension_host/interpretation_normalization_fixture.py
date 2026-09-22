# Copyright (c) 2026 Zhambyl Yermagambet
"""Build shared large-reply and stored-body evidence for journal tests."""

from baqylau_extension_api.models import transforms

from extensions.models import interpretation_normalization as normalization, interpretations
from tests.extension_host import interpretation_fixture as fixtures
from tests.extension_host.interpretation_large_fixture import (
    FACT_COUNT as FACT_COUNT,
    INSERTION_COUNT as INSERTION_COUNT,
    LARGE_DOCUMENT as LARGE_DOCUMENT,
    large_insertions,
)
from tests.extension_host.processing_pipeline_fixture import PipelineCase

NORMALIZED_CODEC_VERSION = 2
DEFAULT_HISTORY = "default"
_BODY_COUNT_SQL = (
    "SELECT (SELECT COUNT(*) FROM interpretation_bodies WHERE kind='canonical_fact') AS facts, "
    "(SELECT COUNT(*) FROM interpretation_bodies WHERE kind='prior_state') AS priors, "
    "(SELECT COUNT(*) FROM interpretation_bodies) AS bodies, "
    "(SELECT COUNT(*) FROM interpretation_journal_bodies) AS links"
)


def large_reply(request: transforms.CanonicalTransformRequest) -> transforms.CanonicalTransformResult:
    """Return one valid canonical reply above the old expanded journal check.

    Returns:
        The complete reply with distinct large additions.

    """
    return transforms.CanonicalTransformResult(
        operations=large_insertions(request.inputs[0], INSERTION_COUNT, LARGE_DOCUMENT),
    )


def normalized_length(proposal: interpretations.InterpretationProposal) -> int:
    """Count the exact normalized journal bytes for one proposal.

    Returns:
        The admitted byte count.

    """
    journal, store = normalization.normalize_proposal(proposal)
    return normalization.normalized_byte_length(journal, store)


def body_counts(case: fixtures.InterpretationCase) -> tuple[int, ...]:
    """Read fact, prior-state, distinct-body, and ownership-link counts.

    Returns:
        The four counts in order.

    """
    with case.store.database.read() as connection:
        row = connection.execute(_BODY_COUNT_SQL).fetchone()
    columns = ("facts", "priors", "bodies", "links")
    return tuple(int(row[column]) for column in columns)


def keep_first_fact(request: interpretations.InterpretationCommit) -> transforms.Keep:
    """Keep the first final fact of one fixture proposal.

    Returns:
        The identity-preserving operation.

    """
    return transforms.Keep(input_id=request.proposal.facts[0].event_id)


def canonical_request(case: PipelineCase) -> transforms.CanonicalTransformRequest:
    """Read the canonical request one pipeline case sent to its worker.

    Returns:
        The exact recorded request.

    """
    transform = case.probes.canonical.transform
    request: transforms.CanonicalTransformRequest = transform.call_args.args[0]
    return request


def journal_count(case: fixtures.InterpretationCase) -> int:
    """Count the stored journals for one private database.

    Returns:
        The journal row count.

    """
    with case.store.database.read() as connection:
        return int(connection.execute("SELECT COUNT(*) FROM interpretation_journals").fetchone()[0])


def stored_step_stages(case: fixtures.InterpretationCase) -> tuple[int, int, tuple[str, ...]]:
    """Read the journal codec, old-view row count, and stored step stages.

    Returns:
        The codec version, the inline-view count, and the owned step stages.

    """
    with case.store.database.read() as connection:
        codec = connection.execute("SELECT codec_version FROM interpretation_journals").fetchone()[0]
        view_count = len(connection.execute("SELECT * FROM interpretation_steps").fetchall())
        stages = tuple(
            row[0] for row in connection.execute(
                "SELECT stage FROM interpretation_journal_steps ORDER BY step_index",
            ).fetchall()
        )
    return (int(codec), view_count, stages)
