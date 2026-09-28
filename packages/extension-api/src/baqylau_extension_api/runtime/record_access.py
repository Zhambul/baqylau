# Copyright (c) 2026 Zhambyl Yermagambet
"""Call the host record route from a worker, and register it on the host channel."""

from dataclasses import dataclass

from pydantic import TypeAdapter

from baqylau_extension_api.contracts.record_reads import ExtensionRecordReader
from baqylau_extension_api.models.record_reads import RecordPageReply, RecordPageRequest
from baqylau_extension_api.runtime import channel, codec, methods
from baqylau_extension_api.runtime.contract import RemoteCaller

RECORD_PAGE: TypeAdapter[RecordPageReply] = TypeAdapter(RecordPageReply)


@dataclass(frozen=True)
class RemoteRecordReader(ExtensionRecordReader):
    """Read records only in the live lane; a pure call gets its records in the request."""

    caller: RemoteCaller

    def read_records(self, record_page_request: RecordPageRequest) -> RecordPageReply:
        """Read one ordered page through the host.

        Returns:
            The records and the next key.

        """
        return self.caller.invoke_typed(methods.RECORDS_READ, record_page_request, RECORD_PAGE)


def register_record_access(rpc: channel.RpcChannel, records: ExtensionRecordReader) -> None:
    """Register the host record callback bound to one worker connection."""
    record_handler = codec.ModelHandler(RecordPageRequest, RECORD_PAGE, records.read_records)
    rpc.register(methods.RECORDS_READ, record_handler, "live")
