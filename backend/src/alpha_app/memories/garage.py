"""Garage S3 client — thin wrapper around boto3 for object storage.

Three functions: put, head, get. That's the whole API.
Garage runs on Primer, stores images and fetched content.
"""

import os

import boto3
import logfire
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from alpha_app.constants import GARAGE_BUCKET, GARAGE_ENDPOINT, GARAGE_REGION

# Lazy singleton — created on first use, reused thereafter.
_client = None


def _get_client():
    """Get or create the S3 client."""
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=GARAGE_ENDPOINT,
            region_name=GARAGE_REGION,
            aws_access_key_id=os.environ.get("GARAGE_ACCESS_KEY_ID", ""),
            aws_secret_access_key=os.environ.get("GARAGE_SECRET_ACCESS_KEY", ""),
            config=BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )
    return _client


def put_object(key: str, data: bytes, content_type: str = "application/octet-stream") -> bool:
    """Upload an object to Garage. Returns True on success, False on failure."""
    with logfire.span("garage.put", key=key, size=len(data)):
        try:
            _get_client().put_object(
                Bucket=GARAGE_BUCKET,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
            return True
        except Exception as e:
            logfire.warn("garage.put failed: {error}", error=str(e))
            return False


def head_object(key: str) -> bool:
    """Check if an object exists. Returns True if found, False otherwise."""
    try:
        _get_client().head_object(Bucket=GARAGE_BUCKET, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            return False
        logfire.warn("garage.head failed: {error}", error=str(e))
        return False
    except Exception:
        return False


def get_object(key: str) -> bytes | None:
    """Download an object. Returns bytes on success, None on failure."""
    with logfire.span("garage.get", key=key):
        try:
            response = _get_client().get_object(Bucket=GARAGE_BUCKET, Key=key)
            return response["Body"].read()
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                return None
            logfire.warn("garage.get failed: {error}", error=str(e))
            return None
        except Exception:
            return None
