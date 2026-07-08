from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import cast

from src.core.config.settings import settings


class AttachmentStorage(ABC):
    @abstractmethod
    def write_bytes(self, relative_path: str, data: bytes) -> None: ...

    @abstractmethod
    def read_bytes(self, relative_path: str) -> bytes: ...

    @abstractmethod
    def exists(self, relative_path: str) -> bool: ...

    @abstractmethod
    def copy(self, source_relative_path: str, dest_relative_path: str) -> None: ...


class LocalAttachmentStorage(AttachmentStorage):
    def __init__(self, root_dir: str) -> None:
        self._root = Path(root_dir)

    def _resolve(self, relative_path: str) -> Path:
        return self._root / relative_path

    def write_bytes(self, relative_path: str, data: bytes) -> None:
        destination = self._resolve(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)

    def read_bytes(self, relative_path: str) -> bytes:
        return self._resolve(relative_path).read_bytes()

    def exists(self, relative_path: str) -> bool:
        return self._resolve(relative_path).is_file()

    def copy(self, source_relative_path: str, dest_relative_path: str) -> None:
        source = self._resolve(source_relative_path)
        destination = self._resolve(dest_relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


class GcsAttachmentStorage(AttachmentStorage):
    def __init__(self, bucket_name: str, prefix: str) -> None:
        from google.cloud import storage

        self._client = storage.Client()
        self._bucket = self._client.bucket(bucket_name)
        self._prefix = prefix.strip("/")

    def _blob_name(self, relative_path: str) -> str:
        return f"{self._prefix}/{relative_path}".lstrip("/")

    def write_bytes(self, relative_path: str, data: bytes) -> None:
        blob = self._bucket.blob(self._blob_name(relative_path))
        blob.upload_from_string(data)

    def read_bytes(self, relative_path: str) -> bytes:
        blob = self._bucket.blob(self._blob_name(relative_path))
        return cast(bytes, blob.download_as_bytes())

    def exists(self, relative_path: str) -> bool:
        blob = self._bucket.blob(self._blob_name(relative_path))
        return cast(bool, blob.exists())

    def copy(self, source_relative_path: str, dest_relative_path: str) -> None:
        source_blob = self._bucket.blob(self._blob_name(source_relative_path))
        self._bucket.copy_blob(
            source_blob, self._bucket, new_name=self._blob_name(dest_relative_path)
        )


def get_attachment_storage() -> AttachmentStorage:
    if settings.STORAGE_BACKEND == "gcs":
        if not settings.GCS_BUCKET:
            raise ValueError("GCS_BUCKET is required when STORAGE_BACKEND=gcs")
        return GcsAttachmentStorage(settings.GCS_BUCKET, settings.GCS_PREFIX)
    return LocalAttachmentStorage(settings.CASE_ATTACHMENT_STORAGE_DIR)
