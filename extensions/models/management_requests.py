# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep lifecycle and settings requests in one durable idempotency namespace."""

from extensions.models.lifecycle_requests import LifecycleRequestOrigin
from extensions.models.settings_requests import SettingsRequestOrigin

type ManagementRequestOrigin = LifecycleRequestOrigin | SettingsRequestOrigin
