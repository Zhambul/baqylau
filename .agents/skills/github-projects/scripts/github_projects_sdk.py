#!/usr/bin/env python3
"""Python SDK and CLI for the Baqylau GitHub Project."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_BASE = "https://api.github.com"
DEFAULT_OWNER = "Zhambul"
DEFAULT_REPOSITORY = "Zhambul/baqylau"
DEFAULT_PROJECT_NUMBER = 1
STATUSES = ("Backlog", "Planning", "In Progress", "Done")
WORK_TYPES = ("Tech Debt", "Code Quality", "Feature", "Bug")
PRIORITIES = ("P0 — Critical", "P1 — High", "P2 — Medium", "P3 — Low")
AREAS = ("Frontend", "Backend", "Frontend + Backend", "Terminal", "Terminal + Backend")
STATUS_FIELD = "Status"
TYPE_FIELD = "Work Type"
PRIORITY_FIELD = "Priority"
AREA_FIELD = "Area"
PRIORITY_PATTERN = re.compile(r"^P(?P<rank>\d+)\b", re.IGNORECASE)
VIEW_LAYOUTS = ("BOARD_LAYOUT", "TABLE_LAYOUT", "ROADMAP_LAYOUT")


class GitHubProjectError(RuntimeError):
    """Report an API, authentication, or project data error."""


@dataclass(frozen=True, slots=True)
class FieldOption:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class ProjectField:
    id: str
    name: str
    options: tuple[FieldOption, ...]


@dataclass(frozen=True, slots=True)
class ProjectSchema:
    id: str
    title: str
    url: str
    fields: tuple[ProjectField, ...]

    def field(self, name: str) -> ProjectField:
        matches = [field for field in self.fields if field.name.casefold() == name.casefold()]
        return _one(matches, f"project field named {name!r}")


@dataclass(frozen=True, slots=True)
class Issue:
    item_id: str
    content_id: str
    number: int
    title: str
    body: str
    url: str
    state: str
    status: str | None
    area: str | None
    work_type: str | None
    priority: str | None


@dataclass(frozen=True, slots=True)
class ProjectView:
    id: str
    number: int
    name: str
    layout: str
    filter: str | None


class Transport(Protocol):
    def request(self, method: str, path: str, data: dict[str, Any] | None = None) -> Any: ...


class HttpTransport:
    """Send authenticated requests to GitHub without automatic retries."""

    def __init__(self, token: str, base_url: str = API_BASE) -> None:
        if not token: raise GitHubProjectError("GitHub authentication token is missing")
        self._token = token
        self._base_url = base_url.rstrip("/")

    @classmethod
    def from_environment(cls) -> HttpTransport:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or _gh_token()
        return cls(token)

    def request(self, method: str, path: str, data: dict[str, Any] | None = None) -> Any:
        body = json.JSONEncoder().encode(data).encode() if data is not None else None
        request = Request(
            f"{self._base_url}{path}",
            data=body,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "User-Agent": "baqylau-github-projects-sdk",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method=method,
        )
        try:
            with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed HTTPS base
                if response.status == 204: return {}
                return json.load(response)
        except HTTPError as error:
            detail = error.read().decode(errors="replace").strip()
            raise GitHubProjectError(f"GitHub returned HTTP {error.code}: {detail}") from error
        except URLError as error:
            raise GitHubProjectError(f"Could not reach GitHub: {error.reason}") from error
        except json.JSONDecodeError as error:
            raise GitHubProjectError("GitHub returned invalid JSON") from error


class GitHubProjectClient:
    """Manage Baqylau issues and their GitHub Project fields."""

    def __init__(
        self,
        transport: Transport,
        *,
        owner: str = DEFAULT_OWNER,
        repository: str = DEFAULT_REPOSITORY,
        project_number: int = DEFAULT_PROJECT_NUMBER,
    ) -> None:
        self._transport = transport
        self.owner = owner
        self.repository = repository
        self.project_number = project_number

    @classmethod
    def from_environment(cls) -> GitHubProjectClient:
        return cls(
            HttpTransport.from_environment(),
            owner=os.environ.get("GITHUB_PROJECT_OWNER", DEFAULT_OWNER),
            repository=os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPOSITORY),
            project_number=int(os.environ.get("GITHUB_PROJECT_NUMBER", DEFAULT_PROJECT_NUMBER)),
        )

    def rest(self, method: str, path: str, data: dict[str, Any] | None = None) -> Any:
        return self._transport.request(method, path, data)

    def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = _object(
            self.rest("POST", "/graphql", {"query": query, "variables": variables or {}}),
            "GraphQL response",
        )
        errors = payload.get("errors")
        if errors:
            detail = json.JSONEncoder(ensure_ascii=False).encode(errors)
            raise GitHubProjectError(f"GitHub GraphQL error: {detail}")
        return _object(payload.get("data"), "GraphQL data")

    def schema(self) -> ProjectSchema:
        query = """
        query($owner: String!, $number: Int!) {
          user(login: $owner) {
            projectV2(number: $number) {
              id title url
              fields(first: 50) {
                nodes {
                  ... on ProjectV2FieldCommon { id name }
                  ... on ProjectV2SingleSelectField { options { id name } }
                }
              }
            }
          }
        }
        """
        data = self.graphql(query, {"owner": self.owner, "number": self.project_number})
        user = _object(data.get("user"), f"GitHub user {self.owner!r}")
        project = _object(user.get("projectV2"), f"project {self.project_number}")
        fields_payload = _object(project.get("fields"), "field connection").get("nodes")
        fields = tuple(_project_field(item) for item in _objects(fields_payload, "project fields") if item.get("id"))
        schema = ProjectSchema(
            id=_text(project, "id"),
            title=_text(project, "title"),
            url=_text(project, "url"),
            fields=fields,
        )
        self._validate_schema(schema)
        return schema

    def issues(
        self,
        *,
        status: str | None = None,
        area: str | None = None,
        work_type: str | None = None,
        priority: str | None = None,
    ) -> list[Issue]:
        if status is not None: _require_choice(status, STATUSES, "status")
        if area is not None: _require_choice(area, AREAS, "area")
        if work_type is not None: _require_choice(work_type, WORK_TYPES, "work type")
        if priority is not None: _require_choice(priority, PRIORITIES, "priority")
        query = """
        query($owner: String!, $number: Int!, $cursor: String) {
          user(login: $owner) {
            projectV2(number: $number) {
              items(first: 100, after: $cursor) {
                pageInfo { hasNextPage endCursor }
                nodes {
                  id
                  fieldValues(first: 30) {
                    nodes {
                      ... on ProjectV2ItemFieldSingleSelectValue {
                        name
                        field { ... on ProjectV2FieldCommon { name } }
                      }
                    }
                  }
                  content {
                    ... on Issue {
                      id number title body url state
                      repository { nameWithOwner }
                    }
                  }
                }
              }
            }
          }
        }
        """
        cursor: str | None = None
        result: list[Issue] = []
        while True:
            data = self.graphql(query, {"owner": self.owner, "number": self.project_number, "cursor": cursor})
            project = _object(_object(data.get("user"), "GitHub user").get("projectV2"), "project")
            connection = _object(project.get("items"), "project item connection")
            for item in _objects(connection.get("nodes"), "project items"):
                issue = _issue(item, self.repository)
                if issue is not None: result.append(issue)
            page = _object(connection.get("pageInfo"), "page information")
            if not page.get("hasNextPage"): break
            cursor = _optional_text(page, "endCursor")
            if cursor is None: raise GitHubProjectError("GitHub omitted the next page cursor")
        if status is not None: result = [issue for issue in result if issue.status == status]
        if area is not None: result = [issue for issue in result if issue.area == area]
        if work_type is not None: result = [issue for issue in result if issue.work_type == work_type]
        if priority is not None: result = [issue for issue in result if issue.priority == priority]
        return result

    def views(self) -> list[ProjectView]:
        query = """
        query($owner: String!, $number: Int!) {
          user(login: $owner) {
            projectV2(number: $number) {
              views(first: 50) {
                nodes { id number name layout filter }
              }
            }
          }
        }
        """
        data = self.graphql(query, {"owner": self.owner, "number": self.project_number})
        project = _object(_object(data.get("user"), "GitHub user").get("projectV2"), "project")
        connection = _object(project.get("views"), "project view connection")
        return [_project_view(item) for item in _objects(connection.get("nodes"), "project views")]

    def create_view(
        self,
        name: str,
        *,
        filter_query: str = "",
        layout: str = "BOARD_LAYOUT",
        visible_fields: tuple[str, ...] = (AREA_FIELD, TYPE_FIELD, PRIORITY_FIELD),
    ) -> ProjectView:
        if not name.strip(): raise GitHubProjectError("View name must not be empty")
        _require_choice(layout, VIEW_LAYOUTS, "view layout")
        duplicates = [view for view in self.views() if view.name.casefold() == name.casefold()]
        if duplicates: raise GitHubProjectError(f"Project view already exists: {duplicates[0].name}")
        schema = self.schema()
        visible_ids = [schema.field(field_name).id for field_name in visible_fields]
        mutation = """
        mutation($project: ID!, $name: String!, $layout: ProjectV2ViewLayout!, $fields: [ID!]) {
          createProjectV2View(input: {
            projectId: $project,
            name: $name,
            layout: $layout,
            configuration: {visibleFieldIds: $fields}
          }) { projectV2View { id number name layout filter } }
        }
        """
        data = self.graphql(
            mutation,
            {"project": schema.id, "name": name, "layout": layout, "fields": visible_ids},
        )
        result = _object(data.get("createProjectV2View"), "create view result")
        view = _project_view(_object(result.get("projectV2View"), "project view"))
        if filter_query:
            return self.update_view(view.id, filter_query=filter_query)
        return view

    def update_view(
        self,
        query: str,
        *,
        name: str | None = None,
        filter_query: str | None = None,
        layout: str | None = None,
        visible_fields: tuple[str, ...] | None = None,
    ) -> ProjectView:
        if layout is not None: _require_choice(layout, VIEW_LAYOUTS, "view layout")
        matches = [
            view for view in self.views()
            if query in (view.id, str(view.number)) or view.name.casefold() == query.casefold()
        ]
        view = _one(matches, f"project view matching {query!r}")
        variables: dict[str, Any] = {"view": view.id}
        declarations = ["$view: ID!"]
        assignments: list[str] = ["viewId: $view"]
        if name is not None:
            variables["name"] = name
            declarations.append("$name: String")
            assignments.append("name: $name")
        if filter_query is not None:
            variables["filter"] = filter_query
            declarations.append("$filter: String")
            assignments.append("filter: $filter")
        if layout is not None:
            variables["layout"] = layout
            declarations.append("$layout: ProjectV2ViewLayout")
            assignments.append("layout: $layout")
        if visible_fields is not None:
            schema = self.schema()
            variables["fields"] = [schema.field(field_name).id for field_name in visible_fields]
            declarations.append("$fields: [ID!]")
            assignments.append("configuration: {visibleFieldIds: $fields}")
        if len(assignments) == 1: raise GitHubProjectError("No view update was provided")
        mutation = (
            f"mutation({', '.join(declarations)}) {{ "
            f"updateProjectV2View(input: {{{', '.join(assignments)}}}) {{ "
            "projectV2View { id number name layout filter } } }"
        )
        data = self.graphql(mutation, variables)
        result = _object(data.get("updateProjectV2View"), "update view result")
        return _project_view(_object(result.get("projectV2View"), "project view"))

    def find_issue(self, query: str | int) -> Issue:
        text = str(query)
        exact = [
            issue for issue in self.issues()
            if text in (str(issue.number), issue.url) or issue.title.casefold() == text.casefold()
        ]
        if exact: return _one(exact, f"issue matching {text!r}")
        return _one(
            [issue for issue in self.issues() if text.casefold() in issue.title.casefold()],
            f"issue matching {text!r}",
        )

    def create_issue(
        self,
        title: str,
        *,
        area: str,
        work_type: str,
        priority: str,
        status: str = "Backlog",
        body: str = "",
        allow_duplicate: bool = False,
    ) -> Issue:
        if not title.strip(): raise GitHubProjectError("Issue title must not be empty")
        _require_choice(area, AREAS, "area")
        _require_choice(work_type, WORK_TYPES, "work type")
        _require_choice(priority, PRIORITIES, "priority")
        _require_choice(status, STATUSES, "status")
        duplicates = [
            issue for issue in self.issues()
            if issue.title.casefold() == title.casefold() and issue.state == "OPEN"
        ]
        if duplicates and not allow_duplicate:
            raise GitHubProjectError(f"Open issue already exists: {duplicates[0].url}")
        created = _object(
            self.rest("POST", f"/repos/{self.repository}/issues", {"title": title, "body": body}),
            "created issue",
        )
        content_id = _text(created, "node_id")
        schema = self.schema()
        mutation = """
        mutation($project: ID!, $content: ID!) {
          addProjectV2ItemById(input: {projectId: $project, contentId: $content}) {
            item { id }
          }
        }
        """
        data = self.graphql(mutation, {"project": schema.id, "content": content_id})
        add_result = _object(data.get("addProjectV2ItemById"), "add item result")
        item_id = _text(_object(add_result.get("item"), "project item"), "id")
        self._set_field(item_id, schema, STATUS_FIELD, status)
        self._set_field(item_id, schema, AREA_FIELD, area)
        self._set_field(item_id, schema, TYPE_FIELD, work_type)
        self._set_field(item_id, schema, PRIORITY_FIELD, priority)
        issue = Issue(
            item_id=item_id,
            content_id=content_id,
            number=_integer(created, "number"),
            title=_text(created, "title"),
            body=_text(created, "body"),
            url=_text(created, "html_url"),
            state=_text(created, "state").upper(),
            status=status,
            area=area,
            work_type=work_type,
            priority=priority,
        )
        if status == "Backlog":
            self.sort_backlog(apply=True)
        return issue

    def update_issue(self, query: str | int, *, title: str | None = None, body: str | None = None) -> Issue:
        if title is None and body is None: raise GitHubProjectError("No issue update was provided")
        issue = self.find_issue(query)
        data: dict[str, Any] = {}
        if title is not None: data["title"] = title
        if body is not None: data["body"] = body
        self.rest("PATCH", f"/repos/{self.repository}/issues/{issue.number}", data)
        return self.find_issue(issue.number)

    def set_status(self, query: str | int, status: str) -> Issue:
        _require_choice(status, STATUSES, "status")
        return self._set_issue_field(query, STATUS_FIELD, status)

    def set_work_type(self, query: str | int, work_type: str) -> Issue:
        _require_choice(work_type, WORK_TYPES, "work type")
        return self._set_issue_field(query, TYPE_FIELD, work_type)

    def set_area(self, query: str | int, area: str) -> Issue:
        _require_choice(area, AREAS, "area")
        return self._set_issue_field(query, AREA_FIELD, area)

    def set_priority(self, query: str | int, priority: str) -> Issue:
        _require_choice(priority, PRIORITIES, "priority")
        return self._set_issue_field(query, PRIORITY_FIELD, priority)

    def close_issue(self, query: str | int) -> Issue:
        issue = self.find_issue(query)
        self.rest("PATCH", f"/repos/{self.repository}/issues/{issue.number}", {"state": "closed"})
        return self.find_issue(issue.number)

    def reopen_issue(self, query: str | int) -> Issue:
        issue = self.find_issue(query)
        self.rest("PATCH", f"/repos/{self.repository}/issues/{issue.number}", {"state": "open"})
        return self.find_issue(issue.number)

    def add_comment(self, query: str | int, body: str) -> dict[str, Any]:
        if not body.strip(): raise GitHubProjectError("Comment must not be empty")
        issue = self.find_issue(query)
        return _object(
            self.rest(
                "POST",
                f"/repos/{self.repository}/issues/{issue.number}/comments",
                {"body": body},
            ),
            "comment",
        )

    def comments(self, query: str | int) -> list[dict[str, Any]]:
        issue = self.find_issue(query)
        return _objects(
            self.rest("GET", f"/repos/{self.repository}/issues/{issue.number}/comments?per_page=100"),
            "comments",
        )

    def backlog(self) -> list[Issue]:
        return sorted(self.issues(status="Backlog"), key=lambda issue: (_priority_rank(issue.priority), issue.number))

    def sort_backlog(self, *, apply: bool = False) -> list[Issue]:
        ordered = self.backlog()
        if not apply: return ordered
        schema = self.schema()
        mutation = """
        mutation($project: ID!, $item: ID!, $after: ID) {
          updateProjectV2ItemPosition(input: {
            projectId: $project,
            itemId: $item,
            afterId: $after
          }) { items(first: 1) { nodes { id } } }
        }
        """
        previous: str | None = None
        for issue in ordered:
            self.graphql(
                mutation,
                {"project": schema.id, "item": issue.item_id, "after": previous},
            )
            previous = issue.item_id
        return self.backlog()

    def _set_issue_field(self, query: str | int, field_name: str, value: str) -> Issue:
        issue = self.find_issue(query)
        self._set_field(issue.item_id, self.schema(), field_name, value)
        return self.find_issue(issue.number)

    def _set_field(self, item_id: str, schema: ProjectSchema, field_name: str, value: str) -> None:
        field = schema.field(field_name)
        option = _one([item for item in field.options if item.name == value], f"{field_name} option {value!r}")
        mutation = """
        mutation($project: ID!, $item: ID!, $field: ID!, $option: String!) {
          updateProjectV2ItemFieldValue(input: {
            projectId: $project,
            itemId: $item,
            fieldId: $field,
            value: {singleSelectOptionId: $option}
          }) { projectV2Item { id } }
        }
        """
        self.graphql(mutation, {"project": schema.id, "item": item_id, "field": field.id, "option": option.id})

    @staticmethod
    def _validate_schema(schema: ProjectSchema) -> None:
        expected = {
            STATUS_FIELD: STATUSES,
            AREA_FIELD: AREAS,
            TYPE_FIELD: WORK_TYPES,
            PRIORITY_FIELD: PRIORITIES,
        }
        for name, values in expected.items():
            field = schema.field(name)
            actual = tuple(option.name for option in field.options)
            if actual != values: raise GitHubProjectError(f"{name} options do not match the SDK schema: {actual}")


def _gh_token() -> str:
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise GitHubProjectError("Set GITHUB_TOKEN or authenticate with gh") from error
    return result.stdout.strip()


def _project_field(item: dict[str, Any]) -> ProjectField:
    raw_options = item.get("options", [])
    return ProjectField(
        id=_text(item, "id"),
        name=_text(item, "name"),
        options=tuple(
            FieldOption(id=_text(option, "id"), name=_text(option, "name"))
            for option in _objects(raw_options, "field options")
        ),
    )


def _issue(item: dict[str, Any], repository: str) -> Issue | None:
    content = item.get("content")
    if not isinstance(content, dict) or content.get("repository", {}).get("nameWithOwner") != repository: return None
    values: dict[str, str] = {}
    connection = _object(item.get("fieldValues"), "field value connection")
    for value in _objects(connection.get("nodes"), "field values"):
        field = value.get("field")
        name = value.get("name")
        if isinstance(field, dict) and isinstance(field.get("name"), str) and isinstance(name, str):
            values[field["name"]] = name
    return Issue(
        item_id=_text(item, "id"),
        content_id=_text(content, "id"),
        number=_integer(content, "number"),
        title=_text(content, "title"),
        body=_text(content, "body"),
        url=_text(content, "url"),
        state=_text(content, "state"),
        status=values.get(STATUS_FIELD),
        area=values.get(AREA_FIELD),
        work_type=values.get(TYPE_FIELD),
        priority=values.get(PRIORITY_FIELD),
    )


def _project_view(item: dict[str, Any]) -> ProjectView:
    return ProjectView(
        id=_text(item, "id"),
        number=_integer(item, "number"),
        name=_text(item, "name"),
        layout=_text(item, "layout"),
        filter=_optional_text(item, "filter"),
    )


def _priority_rank(value: str | None) -> int:
    match = PRIORITY_PATTERN.match(value or "")
    return int(match.group("rank")) if match else 1_000_000


def _require_choice(value: str, choices: tuple[str, ...], description: str) -> None:
    if value not in choices:
        raise GitHubProjectError(
            f"Invalid {description}: {value}. Choose from: {', '.join(choices)}"
        )


def _object(value: Any, description: str) -> dict[str, Any]:
    if not isinstance(value, dict): raise GitHubProjectError(f"GitHub returned an invalid {description}")
    return value


def _objects(value: Any, description: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise GitHubProjectError(f"GitHub returned invalid {description}")
    return value


def _one(items: list[Any], description: str) -> Any:
    if not items: raise GitHubProjectError(f"Could not find {description}")
    if len(items) > 1: raise GitHubProjectError(f"Found more than one {description}")
    return items[0]


def _text(item: dict[str, Any], field: str) -> str:
    value = item.get(field)
    if not isinstance(value, str): raise GitHubProjectError(f"GitHub field is not text: {field}")
    return value


def _optional_text(item: dict[str, Any], field: str) -> str | None:
    value = item.get(field)
    if value is not None and not isinstance(value, str): raise GitHubProjectError(f"GitHub field is not text: {field}")
    return value


def _integer(item: dict[str, Any], field: str) -> int:
    value = item.get(field)
    if not isinstance(value, int): raise GitHubProjectError(f"GitHub field is not an integer: {field}")
    return value


def _emit(value: Any) -> None:
    json.dump(value, sys.stdout, ensure_ascii=False, indent=2)
    print()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("schema")
    issues = commands.add_parser("issues")
    issues.add_argument("--status", choices=STATUSES)
    issues.add_argument("--area", choices=AREAS)
    issues.add_argument("--type", choices=WORK_TYPES)
    issues.add_argument("--priority", choices=PRIORITIES)
    commands.add_parser("backlog")
    sort_backlog = commands.add_parser("sort-backlog")
    sort_backlog.add_argument("--apply", action="store_true")
    commands.add_parser("views")
    show = commands.add_parser("show")
    show.add_argument("issue")
    create = commands.add_parser("create")
    create.add_argument("title")
    create.add_argument("--area", required=True, choices=AREAS)
    create.add_argument("--type", required=True, choices=WORK_TYPES)
    create.add_argument("--priority", required=True, choices=PRIORITIES)
    create.add_argument("--status", default="Backlog", choices=STATUSES)
    create.add_argument("--body", default="")
    create.add_argument("--allow-duplicate", action="store_true")
    update = commands.add_parser("update")
    update.add_argument("issue")
    update.add_argument("--title")
    update.add_argument("--body")
    move = commands.add_parser("move")
    move.add_argument("issue")
    move.add_argument("status", choices=STATUSES)
    set_type = commands.add_parser("set-type")
    set_type.add_argument("issue")
    set_type.add_argument("work_type", choices=WORK_TYPES)
    set_area = commands.add_parser("set-area")
    set_area.add_argument("issue")
    set_area.add_argument("area", choices=AREAS)
    set_priority = commands.add_parser("set-priority")
    set_priority.add_argument("issue")
    set_priority.add_argument("priority", choices=PRIORITIES)
    close = commands.add_parser("close")
    close.add_argument("issue")
    reopen = commands.add_parser("reopen")
    reopen.add_argument("issue")
    comment = commands.add_parser("comment")
    comment.add_argument("issue")
    comment.add_argument("body")
    comments = commands.add_parser("comments")
    comments.add_argument("issue")
    create_view = commands.add_parser("create-view")
    create_view.add_argument("name")
    create_view.add_argument("--filter", default="")
    create_view.add_argument("--layout", default="BOARD_LAYOUT", choices=VIEW_LAYOUTS)
    return parser


def _run(args: argparse.Namespace) -> int:
    client = GitHubProjectClient.from_environment()
    if args.command == "schema": _emit(asdict(client.schema()))
    elif args.command == "issues":
        _emit([
            asdict(issue)
            for issue in client.issues(
                status=args.status,
                area=args.area,
                work_type=args.type,
                priority=args.priority,
            )
        ])
    elif args.command == "backlog": _emit([asdict(issue) for issue in client.backlog()])
    elif args.command == "sort-backlog": _emit([asdict(issue) for issue in client.sort_backlog(apply=args.apply)])
    elif args.command == "views": _emit([asdict(view) for view in client.views()])
    elif args.command == "show": _emit(asdict(client.find_issue(args.issue)))
    elif args.command == "create":
        _emit(asdict(client.create_issue(
            args.title,
            area=args.area,
            work_type=args.type,
            priority=args.priority,
            status=args.status,
            body=args.body,
            allow_duplicate=args.allow_duplicate,
        )))
    elif args.command == "update": _emit(asdict(client.update_issue(args.issue, title=args.title, body=args.body)))
    elif args.command == "move": _emit(asdict(client.set_status(args.issue, args.status)))
    elif args.command == "set-type": _emit(asdict(client.set_work_type(args.issue, args.work_type)))
    elif args.command == "set-area": _emit(asdict(client.set_area(args.issue, args.area)))
    elif args.command == "set-priority": _emit(asdict(client.set_priority(args.issue, args.priority)))
    elif args.command == "close": _emit(asdict(client.close_issue(args.issue)))
    elif args.command == "reopen": _emit(asdict(client.reopen_issue(args.issue)))
    elif args.command == "comment": _emit(client.add_comment(args.issue, args.body))
    elif args.command == "comments": _emit(client.comments(args.issue))
    elif args.command == "create-view":
        _emit(asdict(client.create_view(args.name, filter_query=args.filter, layout=args.layout)))
    else: raise GitHubProjectError(f"Unsupported command: {args.command}")
    return 0


def main() -> int:
    try:
        return _run(_parser().parse_args())
    except GitHubProjectError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
