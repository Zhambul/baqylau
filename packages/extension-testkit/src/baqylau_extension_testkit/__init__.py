# Copyright (c) 2026 Zhambyl Yermagambet
"""Start a private Baqylau host from an installed executable and read it through typed HTTP calls.

The kit owns the host process and its private roots; `HostClient` only reads
and sends typed requests. The kit has no product parsers: feature assertions stay
in the extension package.
"""
