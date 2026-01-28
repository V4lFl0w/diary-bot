from __future__ import annotations

import os
import uuid
import asyncio
import logging
from typing import Optional

import boto3.session
from botocore.client import Config as BotoConfig
assert boto3.session and BotoConfig

_LOG = logging.getLogger(__name__)

S3_ENDPOINT = os.getenv("S3_ENDPOINT")  # e.g. https://fra1.digitaloceanspaces.com
S3_REGION = os.getenv("S3_REGION", "fra1")
S3_BUCKET = os.getenv("S3_BUCKET")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY_ID")
S3_SECRET_KEY = os.getenv("S3_SECRET_ACCESS_KEY")
S3_PUBLIC_BASE = os.getenv("S3_PUBLIC_BASE")  # e.g. https://<bucket>.<region>.digitaloceanspaces.com

_session = boto3.session.Session()

_s3 = _session.client(
    "s3",
    region_name=S3_REGION,
    endpoint_url=S3_ENDPOINT,
    aws_access_key_id=S3_ACCESS_KEY,
    aws_secret_access_key=S3_SECRET_KEY,
    config=BotoConfig(signature_version="s3v4"),
)

def _must_env(name: str, val: Optional[str]) -> str:
    if val:
        return val
    raise RuntimeError(f"Missing env var: {name}")

def _content_type_for_ext(ext: str) -> str:
    ext = (ext or "").lower().lstrip(".")
    if ext in ("jpg", "jpeg"):
        return "image/jpeg"
    if ext == "png":
        return "image/png"
    if ext == "webp":
        return "image/webp"
    return "application/octet-stream"

async def upload_bytes_get_url(data: bytes, ext: str = "jpg", prefix: str = "frames") -> str:
    """
    Upload raw image bytes to DigitalOcean Spaces (S3 compatible) and return public URL.
    Requires env:
      S3_ENDPOINT, S3_REGION, S3_BUCKET, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY, S3_PUBLIC_BASE
    """
    _must_env("S3_ENDPOINT", S3_ENDPOINT)
    _must_env("S3_BUCKET", S3_BUCKET)
    _must_env("S3_ACCESS_KEY_ID", S3_ACCESS_KEY)
    _must_env("S3_SECRET_ACCESS_KEY", S3_SECRET_KEY)
    _must_env("S3_PUBLIC_BASE", S3_PUBLIC_BASE)

    ext2 = (ext or "jpg").lower().lstrip(".")
    key = f"{prefix}/{uuid.uuid4().hex}.{ext2}"
    ctype = _content_type_for_ext(ext2)

    def _put():
        _s3.put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=data,
            ACL="public-read",
            ContentType=ctype,
        )

    # boto3 sync -> run in thread to not block event loop
    await asyncio.to_thread(_put)

    return f"{S3_PUBLIC_BASE.rstrip('/')}/{key}"
