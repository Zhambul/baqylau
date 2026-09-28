# Copyright (c) 2026 Zhambyl Yermagambet
"""Read one worker's own declared record collections from the host store."""

from dataclasses import dataclass

from baqylau_extension_api.contracts.record_reads import ExtensionRecordReader
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.record_reads import RecordPageReply, RecordPageRequest

from repository.contract.extension_records import ExtensionRecordRepository


@dataclass(frozen=True)
class HostRecordReader(ExtensionRecordReader):
    """Bind the record store to one worker's manifest; the owner is always the worker's package."""

    manifest: ExtensionManifest
    store: ExtensionRecordRepository

    def read_records(self, record_page_request: RecordPageRequest) -> RecordPageReply:
        """Read one ordered page of a declared collection.

        Returns:
            The records in key order and the next key.

        Raises:
            ExtensionContractError: If the package does not declare the collection.

        """
        checked = RecordPageRequest.model_validate(record_page_request)
        declared = (collection.name for collection in self.manifest.contributions.collections)
        if checked.collection not in tuple(declared):
            message = "the package does not declare this record collection"
            raise ExtensionContractError(message)
        page = self.store.record_page(
            self.manifest.extension_id, checked.collection, checked.scope, checked.after_key, checked.limit,
        )
        return RecordPageReply(records=page.records, next_key=page.next_key)
