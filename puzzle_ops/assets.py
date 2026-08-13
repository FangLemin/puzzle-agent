from __future__ import annotations

from dataclasses import dataclass
import hashlib
import mimetypes
import os
from pathlib import Path
import shutil
import time
from urllib.parse import quote


@dataclass(frozen=True)
class StoredAsset:
    object_key: str
    public_url: str
    sha256: str
    content_type: str
    size_bytes: int
    source_filename: str
    created_by: str = ""


class AssetStorageProvider:
    provider = "base"

    def upload(self, local_path: Path | str, content_type: str = "", actor: str = "") -> StoredAsset:
        raise NotImplementedError

    def download(self, object_key: str) -> bytes:
        raise NotImplementedError

    def presigned_url(self, object_key: str, expires_seconds: int = 3600) -> str:
        raise NotImplementedError

    def healthcheck(self) -> dict[str, object]:
        return {"provider": self.provider, "configured": False, "ready": False}


class LocalAssetStorageProvider(AssetStorageProvider):
    provider = "local"

    def __init__(self, root_dir: Path | str, *, public_base_url: str = ""):
        self.root_dir = Path(root_dir)
        self.public_base_url = public_base_url.rstrip("/")
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def upload(self, local_path: Path | str, content_type: str = "", actor: str = "") -> StoredAsset:
        source = Path(local_path)
        data = source.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        suffix = source.suffix.lower() or mimetypes.guess_extension(content_type or "") or ".bin"
        object_key = f"assets/{digest[:2]}/{digest}{suffix}"
        target = self.root_dir / object_key
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copyfile(source, target)
        resolved_content_type = content_type or mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        return StoredAsset(
            object_key=object_key,
            public_url=self._url_for(object_key),
            sha256=digest,
            content_type=resolved_content_type,
            size_bytes=len(data),
            source_filename=source.name,
            created_by=actor,
        )

    def download(self, object_key: str) -> bytes:
        return (self.root_dir / object_key).read_bytes()

    def presigned_url(self, object_key: str, expires_seconds: int = 3600) -> str:
        if self.public_base_url:
            return f"{self._url_for(object_key)}?expires={int(time.time()) + max(expires_seconds, 0)}"
        return str((self.root_dir / object_key).resolve())

    def healthcheck(self) -> dict[str, object]:
        return {"provider": self.provider, "configured": True, "ready": self.root_dir.exists(), "root_dir": str(self.root_dir)}

    def _url_for(self, object_key: str) -> str:
        if not self.public_base_url:
            return str((self.root_dir / object_key).resolve())
        return f"{self.public_base_url}/{quote(object_key)}"


class AliyunOssAssetStorageProvider(AssetStorageProvider):
    provider = "oss"

    def __init__(
        self,
        *,
        endpoint: str,
        bucket: str,
        access_key_id: str,
        access_key_secret: str,
        public_base_url: str = "",
    ):
        self.endpoint = endpoint.strip()
        self.bucket = bucket.strip()
        self.access_key_id = access_key_id.strip()
        self.access_key_secret = access_key_secret.strip()
        self.public_base_url = public_base_url.strip().rstrip("/")

    @classmethod
    def from_env(cls) -> "AliyunOssAssetStorageProvider":
        return cls(
            endpoint=os.environ.get("ALIYUN_OSS_ENDPOINT", ""),
            bucket=os.environ.get("ALIYUN_OSS_BUCKET", ""),
            access_key_id=os.environ.get("ALIYUN_OSS_ACCESS_KEY_ID", ""),
            access_key_secret=os.environ.get("ALIYUN_OSS_ACCESS_KEY_SECRET", ""),
            public_base_url=os.environ.get("ALIYUN_OSS_PUBLIC_BASE_URL", ""),
        )

    def upload(self, local_path: Path | str, content_type: str = "", actor: str = "") -> StoredAsset:
        if not self.healthcheck()["configured"]:
            raise RuntimeError("阿里云 OSS 未配置完整，无法上传资产")
        try:
            import oss2  # type: ignore
        except ImportError as exc:
            raise RuntimeError("缺少 oss2 依赖，请安装 aliyun-oss2 后再启用 OSS 上传") from exc
        source = Path(local_path)
        data = source.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        suffix = source.suffix.lower() or mimetypes.guess_extension(content_type or "") or ".bin"
        object_key = f"assets/{digest[:2]}/{digest}{suffix}"
        auth = oss2.Auth(self.access_key_id, self.access_key_secret)
        bucket = oss2.Bucket(auth, self.endpoint, self.bucket)
        headers = {"Content-Type": content_type or mimetypes.guess_type(source.name)[0] or "application/octet-stream"}
        bucket.put_object(object_key, data, headers=headers)
        return StoredAsset(
            object_key=object_key,
            public_url=self._url_for(object_key),
            sha256=digest,
            content_type=headers["Content-Type"],
            size_bytes=len(data),
            source_filename=source.name,
            created_by=actor,
        )

    def download(self, object_key: str) -> bytes:
        try:
            import oss2  # type: ignore
        except ImportError as exc:
            raise RuntimeError("缺少 oss2 依赖，请安装 aliyun-oss2 后再启用 OSS 下载") from exc
        auth = oss2.Auth(self.access_key_id, self.access_key_secret)
        bucket = oss2.Bucket(auth, self.endpoint, self.bucket)
        return bucket.get_object(object_key).read()

    def presigned_url(self, object_key: str, expires_seconds: int = 3600) -> str:
        if self.public_base_url:
            return self._url_for(object_key)
        try:
            import oss2  # type: ignore
        except ImportError as exc:
            raise RuntimeError("缺少 oss2 依赖，请安装 aliyun-oss2 后再生成签名 URL") from exc
        auth = oss2.Auth(self.access_key_id, self.access_key_secret)
        bucket = oss2.Bucket(auth, self.endpoint, self.bucket)
        return bucket.sign_url("GET", object_key, max(expires_seconds, 1))

    def healthcheck(self) -> dict[str, object]:
        configured = bool(self.endpoint and self.bucket and self.access_key_id and self.access_key_secret)
        return {
            "provider": self.provider,
            "configured": configured,
            "ready": configured,
            "bucket": self.bucket,
            "endpoint": self.endpoint,
        }

    def _url_for(self, object_key: str) -> str:
        if self.public_base_url:
            return f"{self.public_base_url}/{quote(object_key)}"
        return f"https://{self.bucket}.{self.endpoint.replace('https://', '').replace('http://', '').strip('/')}/{quote(object_key)}"


def asset_storage_from_env(runtime_dir: Path | str) -> AssetStorageProvider:
    provider = os.environ.get("ASSET_STORAGE_PROVIDER", "local").strip().lower()
    if provider == "oss":
        return AliyunOssAssetStorageProvider.from_env()
    return LocalAssetStorageProvider(Path(runtime_dir) / "assets", public_base_url=os.environ.get("ASSET_PUBLIC_BASE_URL", ""))
