# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide e2e harness dependencies."""

from domain.ids import HarnessName as HarnessName
from harness import runtime as _harness_runtime
from harness.impl.discovery import installed as installed
from harness.services.usage import SharedUsageCache as SharedUsageCache
from sdk.client import BaqylauClient as BaqylauClient
from tests.e2e.testkit import failure_diagnostics as failure_diagnostics, journey_contexts as journey_contexts

harness_runtime = _harness_runtime
