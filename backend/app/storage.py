"""바이너리 스토리지: Cloudflare R2(영속) + 로컬 디스크(캐시). learning "방식 2".

설계:
  - R2가 바이너리(자료 파일 + 렌더된 page jpg)의 source of truth.
  - 로컬 캐시 디렉터리는 빠른 읽기 경로. 읽기는 로컬 우선, miss 시 R2에서 받아
    write-through.
  - R2 미설정(R2_* env 없음) 시 *local-only*로 동작: 캐시 디렉터리가 유일한 집.
    dev/test를 클라우드 없이 돌릴 수 있다.

객체 키 규약:
  users/<user_id>/sessions/<session_id>/material.<pdf|md>
  users/<user_id>/sessions/<session_id>/pages/page-001.jpg

boto3는 sync라 asyncio.to_thread로 감싼다. R2 I/O는 업로드 시점과 캐시 miss에만
일어나고 토큰마다는 아니므로 thread hop 비용은 무시할 만하다.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from app.config import DEFAULT_DATA_DIR, StorageSettings, load_storage_settings

CACHE_ROOT = DEFAULT_DATA_DIR


def session_prefix(user_id: str, session_id: str) -> str:
    return f"users/{user_id}/sessions/{session_id}"


def material_key(user_id: str, session_id: str, kind: str) -> str:
    return f"{session_prefix(user_id, session_id)}/material.{kind}"


def page_key(user_id: str, session_id: str, idx: int) -> str:
    return f"{session_prefix(user_id, session_id)}/pages/page-{idx:03d}.jpg"


class Storage:
    """R2 + 로컬 캐시 바이너리 스토어. 프로세스당 한 인스턴스(아래 `storage`)."""

    def __init__(self, settings: StorageSettings, cache_root: Path = CACHE_ROOT) -> None:
        self._settings = settings
        self._cache_root = cache_root
        self._client = None  # lazy boto3 client

    @property
    def enabled(self) -> bool:
        return self._settings.enabled

    def _s3(self):
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=self._settings.endpoint_url,
                aws_access_key_id=self._settings.access_key_id,
                aws_secret_access_key=self._settings.secret_access_key,
                region_name=self._settings.region,
                config=BotoConfig(signature_version="s3v4", retries={"max_attempts": 3}),
            )
        return self._client

    # ----- 로컬 캐시 경로 ------------------------------------------------
    def cache_path(self, key: str) -> Path:
        return self._cache_root / key

    # ----- write-through -------------------------------------------------
    async def put_bytes(self, key: str, data: bytes) -> None:
        path = self.cache_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if self.enabled:
            await asyncio.to_thread(
                self._s3().put_object,
                Bucket=self._settings.bucket,
                Key=key,
                Body=data,
            )

    async def put_file(self, key: str, src: Path) -> None:
        await self.put_bytes(key, src.read_bytes())

    # ----- read (cache-first, R2 fallback) ------------------------------
    async def get_bytes(self, key: str) -> bytes:
        path = self.cache_path(key)
        if path.exists():
            return path.read_bytes()
        if self.enabled:
            data = await self._download(key)
            if data is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                return data
        raise FileNotFoundError(key)

    async def ensure_cached(self, key: str) -> Path | None:
        """객체가 로컬 디스크에 있도록 보장하고 경로를 반환(없으면 None). 재배포 후
        page 이미지를 rehydrate하는 데 쓴다."""
        path = self.cache_path(key)
        if path.exists():
            return path
        if self.enabled:
            data = await self._download(key)
            if data is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                return path
        return None

    async def _download(self, key: str) -> bytes | None:
        def _get() -> bytes | None:
            try:
                resp = self._s3().get_object(Bucket=self._settings.bucket, Key=key)
                return resp["Body"].read()
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code", "")
                if code in ("NoSuchKey", "404", "NotFound"):
                    return None
                raise

        return await asyncio.to_thread(_get)

    # ----- delete --------------------------------------------------------
    async def delete_prefix(self, prefix: str) -> None:
        import shutil

        shutil.rmtree(self.cache_path(prefix), ignore_errors=True)
        if not self.enabled:
            return
        await asyncio.to_thread(self._delete_prefix_sync, prefix)

    def _delete_prefix_sync(self, prefix: str) -> None:
        s3 = self._s3()
        bucket = self._settings.bucket
        paginator = s3.get_paginator("list_objects_v2")
        to_delete: list[dict[str, str]] = []
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                to_delete.append({"Key": obj["Key"]})
                if len(to_delete) == 1000:
                    s3.delete_objects(Bucket=bucket, Delete={"Objects": to_delete})
                    to_delete = []
        if to_delete:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": to_delete})


storage = Storage(load_storage_settings())
