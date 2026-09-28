# Copyright (c) 2026 Zhambyl Yermagambet
"""Expose the session-data server-sent event routes."""

from api.sessiondata import stream_global_frames, stream_routes, stream_session_frames
from api.sessiondata.stream_global_models import GlobalFrameSources as GlobalFrameSources
from api.sessiondata.stream_session_models import (
    SessionStreamPosition as SessionStreamPosition,
    SessionStreamServices as SessionStreamServices,
)

router = stream_routes.router
session_stream = stream_routes.session_stream
global_stream = stream_routes.global_stream
session_frames = stream_session_frames.session_frames
global_frames = stream_global_frames.global_frames
