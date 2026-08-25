"""Tests for the Baqylau GitHub Projects SDK."""

from __future__ import annotations

import unittest
from typing import Any

from github_projects_sdk import GitHubProjectClient, GitHubProjectError, Issue, ProjectSchema, _priority_rank


class NoRequestTransport:
    def request(self, method: str, path: str, data: dict[str, Any] | None = None) -> Any:
        raise AssertionError((method, path, data))


class GitHubProjectsSdkTests(unittest.TestCase):
    def test_priority_rank(self) -> None:
        self.assertEqual(0, _priority_rank("P0 — Critical"))
        self.assertEqual(3, _priority_rank("P3 — Low"))
        self.assertGreater(_priority_rank(None), 3)

    def test_create_rejects_unknown_work_type_before_request(self) -> None:
        client = GitHubProjectClient(NoRequestTransport())
        with self.assertRaisesRegex(GitHubProjectError, "Invalid work type"):
            client.create_issue("Invalid", area="Backend", work_type="Chore", priority="P2 — Medium")

    def test_create_rejects_unknown_priority_before_request(self) -> None:
        client = GitHubProjectClient(NoRequestTransport())
        with self.assertRaisesRegex(GitHubProjectError, "Invalid priority"):
            client.create_issue("Invalid", area="Backend", work_type="Feature", priority="Medium")

    def test_create_rejects_unknown_status_before_request(self) -> None:
        client = GitHubProjectClient(NoRequestTransport())
        with self.assertRaisesRegex(GitHubProjectError, "Invalid status"):
            client.create_issue(
                "Invalid",
                area="Backend",
                work_type="Feature",
                priority="P2 — Medium",
                status="Todo",
            )

    def test_create_rejects_unknown_area_before_request(self) -> None:
        client = GitHubProjectClient(NoRequestTransport())
        with self.assertRaisesRegex(GitHubProjectError, "Invalid area"):
            client.create_issue("Invalid", area="Fullstack", work_type="Feature", priority="P2 — Medium")

    def test_create_backlog_issue_sorts_backlog(self) -> None:
        client = GitHubProjectClient(NoRequestTransport())
        client.issues = lambda **kwargs: []  # type: ignore[method-assign]
        client.rest = lambda method, path, data=None: {  # type: ignore[method-assign]
            "node_id": "content-1",
            "number": 42,
            "title": "New issue",
            "body": "",
            "html_url": "https://example.test/issues/42",
            "state": "open",
        }
        client.schema = lambda: ProjectSchema("project-1", "Project", "https://example.test", ())  # type: ignore[method-assign]
        client.graphql = lambda query, variables=None: {  # type: ignore[method-assign]
            "addProjectV2ItemById": {"item": {"id": "item-1"}}
        }
        client._set_field = lambda *args: None  # type: ignore[method-assign]
        sort_calls: list[bool] = []
        client.sort_backlog = lambda *, apply=False: sort_calls.append(apply) or []  # type: ignore[method-assign]

        issue = client.create_issue(
            "New issue",
            area="Frontend",
            work_type="Bug",
            priority="P2 — Medium",
        )

        self.assertEqual(42, issue.number)
        self.assertEqual([True], sort_calls)

    def test_backlog_sorts_by_priority_then_issue_number(self) -> None:
        client = GitHubProjectClient(NoRequestTransport())
        issues = [
            Issue("i3", "c3", 3, "low", "", "u3", "OPEN", "Backlog", "Frontend", "Bug", "P3 — Low"),
            Issue("i2", "c2", 2, "high-b", "", "u2", "OPEN", "Backlog", "Frontend", "Bug", "P1 — High"),
            Issue("i1", "c1", 1, "high-a", "", "u1", "OPEN", "Backlog", "Frontend", "Bug", "P1 — High"),
        ]
        client.issues = lambda **kwargs: issues  # type: ignore[method-assign]
        self.assertEqual([1, 2, 3], [issue.number for issue in client.backlog()])


if __name__ == "__main__":
    unittest.main()
