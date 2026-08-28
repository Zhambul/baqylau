"""The Protocols — the only thing a caller outside this package imports.

Every method takes and returns MODEL objects: never a row, never a dict, never
a `sqlite3.Row`. Every method is ONE whole transaction — none returns a
connection, a cursor, or a context manager, so no caller above this line
manages a transaction or holds a handle.
"""

from repository.contract.audit import (
    AuditReadRepository,
    AuditWriteRepository,
)
from repository.contract.facts import (
    CanonicalEventRepository,
    RawEventRepository,
    RawEventAuditRepository,
)
from repository.contract.shell_output import ShellOutputRepository
from repository.contract.preferences import (
    HiddenDirectoryRepository,
    NewSessionRepository,
    NotificationSettingRepository,
    PushSigningKeyRepository,
    PushSubscriptionRepository,
    TaskDismissalRepository,
    ViewModeRepository,
)
from repository.contract.sessions import SessionRepository
from repository.contract.terminal import PaneWidthRepository
from repository.contract.titles import NativeSessionTitleRepository
from repository.contract.uploads import UploadRepository
from repository.contract.workspace import SessionWorkspaceRepository

__all__ = [
    "CanonicalEventRepository",
    "AuditReadRepository",
    "AuditWriteRepository",
    "HiddenDirectoryRepository",
    "NativeSessionTitleRepository",
    "NewSessionRepository",
    "NotificationSettingRepository",
    "ShellOutputRepository",
    "PaneWidthRepository",
    "PushSigningKeyRepository",
    "PushSubscriptionRepository",
    "RawEventRepository",
    "SessionRepository",
    "SessionWorkspaceRepository",
    "TaskDismissalRepository",
    "RawEventAuditRepository",
    "UploadRepository",
    "ViewModeRepository",
]
