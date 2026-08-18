"""S3-compatible object storage client.

MinIO locally, any S3-compatible service in deployed environments — the only
difference is the endpoint. Used from M5 for action file attachments; M0 only
probes reachability of the configured bucket.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import boto3
from botocore.config import Config as BotoConfig

from app.config import Settings

if TYPE_CHECKING:  # boto3-stubs is a dev dependency, absent at runtime
    from mypy_boto3_s3.client import S3Client


def create_s3_client(settings: Settings) -> S3Client:
    """Build the shared S3 client.

    Path-style addressing is required: MinIO does not serve virtual-host style
    buckets on a bare endpoint.
    """
    timeout = int(settings.health_check_timeout_seconds)
    config = BotoConfig(
        signature_version="s3v4",
        s3={"addressing_style": "path"},
        connect_timeout=timeout,
        read_timeout=timeout,
        retries={"max_attempts": 2, "mode": "standard"},
    )
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        region_name=settings.s3_region,
        config=config,
    )
