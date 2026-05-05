"""Garage S3 client — async wrapper around aiobotocore for object storage.

Three functions: put, head, get. That's the whole API.
Garage runs inside the Alpha compose stack, stores images and fetched content.
"""

import os

import logfire
from aiobotocore.session import get_session
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from alpha_app.constants import GARAGE_BUCKET, GARAGE_ENDPOINT, GARAGE_REGION

# Session is thread-safe and reusable — one per process.
_session = get_session()


def _client_context():
    """Create an async context manager for the S3 client."""
    return _session.create_client(
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


async def put_object(key: str, data: bytes, content_type: str = "application/octet-stream") -> bool:
    """Upload an object to Garage. Returns True on success, False on failure."""
    with logfire.span("garage.put", key=key, size=len(data)):
        try:
            async with _client_context() as client:
                await client.put_object(
                    Bucket=GARAGE_BUCKET,
                    Key=key,
                    Body=data,
                    ContentType=content_type,
                )
            return True
        except Exception as e:
            logfire.warn("garage.put failed: {error}", error=str(e))
            return False


async def head_object(key: str) -> bool:
    """Check if an object exists. Returns True if found, False otherwise."""
    try:
        async with _client_context() as client:
            await client.head_object(Bucket=GARAGE_BUCKET, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            return False
        logfire.warn("garage.head failed: {error}", error=str(e))
        return False
    except Exception:
        return False


async def get_object(key: str) -> bytes | None:
    """Download an object. Returns bytes on success, None on failure."""
    with logfire.span("garage.get", key=key):
        try:
            async with _client_context() as client:
                response = await client.get_object(Bucket=GARAGE_BUCKET, Key=key)
                async with response["Body"] as stream:
                    return await stream.read()
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                return None
            logfire.warn("garage.get failed: {error}", error=str(e))
            return None
        except Exception:
            return None
