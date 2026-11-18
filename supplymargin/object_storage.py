from __future__ import annotations

import mimetypes
from pathlib import Path

from supplymargin.config import settings


class ObjectStorage:
    def __init__(self) -> None:
        self.backend = settings.storage_backend
        self.root = settings.object_store_dir
        self._client = None
        if self.backend == "local":
            self.root.mkdir(parents=True, exist_ok=True)
        else:
            import boto3

            kwargs: dict[str, str] = {
                "service_name": "s3",
                "region_name": settings.s3_region,
                "aws_access_key_id": settings.s3_access_key_id,
                "aws_secret_access_key": settings.s3_secret_access_key,
            }
            if settings.s3_endpoint_url:
                kwargs["endpoint_url"] = settings.s3_endpoint_url
            self._client = boto3.client(**kwargs)

    def _local_path(self, key: str) -> Path:
        normalized = key.strip("/")
        target = (self.root / normalized).resolve()
        root = self.root.resolve()
        if target != root and root not in target.parents:
            raise ValueError("Недопустимый ключ объекта.")
        return target

    def put_bytes(
        self,
        key: str,
        content: bytes,
        content_type: str | None = None,
    ) -> str:
        content_type = content_type or mimetypes.guess_type(key)[0] or "application/octet-stream"
        if self.backend == "local":
            target = self._local_path(key)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        else:
            assert self._client is not None
            kwargs = {
                "Bucket": settings.s3_bucket,
                "Key": key,
                "Body": content,
                "ContentType": content_type,
            }
            if settings.s3_sse:
                kwargs["ServerSideEncryption"] = settings.s3_sse
            self._client.put_object(
                **kwargs,
            )
        return key

    def get_bytes(self, key: str) -> bytes:
        if self.backend == "local":
            return self._local_path(key).read_bytes()
        assert self._client is not None
        response = self._client.get_object(Bucket=settings.s3_bucket, Key=key)
        return response["Body"].read()

    def delete(self, key: str) -> None:
        if self.backend == "local":
            self._local_path(key).unlink(missing_ok=True)
            return
        assert self._client is not None
        self._client.delete_object(Bucket=settings.s3_bucket, Key=key)

    def delete_prefix(self, prefix: str) -> None:
        if self.backend == "local":
            base = self._local_path(prefix)
            if base.is_file():
                base.unlink(missing_ok=True)
                return
            if base.exists():
                for item in sorted(
                    base.rglob("*"), key=lambda value: len(value.parts), reverse=True
                ):
                    if item.is_file() or item.is_symlink():
                        item.unlink(missing_ok=True)
                    elif item.is_dir():
                        item.rmdir()
                base.rmdir()
            return
        assert self._client is not None
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=settings.s3_bucket, Prefix=prefix):
            objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if objects:
                self._client.delete_objects(
                    Bucket=settings.s3_bucket,
                    Delete={"Objects": objects, "Quiet": True},
                )

    def ping(self) -> bool:
        try:
            if self.backend == "local":
                self.root.mkdir(parents=True, exist_ok=True)
                return self.root.is_dir()
            assert self._client is not None
            self._client.head_bucket(Bucket=settings.s3_bucket)
            return True
        except Exception:
            return False


storage = ObjectStorage()
