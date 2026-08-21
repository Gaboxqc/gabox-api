"""Cloudflare R2 object storage, for club crests.

R2 speaks the S3 API, so this is boto3 pointed at a different endpoint. Two
things about how it is used matter more than the client itself:

**Nothing in the request path touches this module.** Crests are written by
`scripts/backfill_crests.py` and read by the browser straight from the CDN; the
API only ever stores the resulting URL in a column. That is what makes ESPN a
seeding-time dependency rather than a runtime one — if it vanishes tomorrow,
every crest already fetched keeps serving.

**Keys are content-addressed and immutable.** The object name contains a hash of
the bytes, and it is written with a one-year immutable cache header. A changed
crest becomes a new key and a column update, so there is no purge to forget and
no window where the CDN serves the old image. Rolling back is a column update
too.

boto3 is imported lazily and is not a dependency of the API — botocore alone
would add tens of megabytes to a serverless bundle that has no use for it.
Install it with `pip install -e ".[crests]"`.
"""

import hashlib
import logging
import re
from functools import lru_cache
from typing import Any

from api.core.config import settings

log = logging.getLogger("statpitch.storage")

# A year, and immutable: the key changes whenever the bytes do, so the CDN can
# never be holding something stale under this name.
CACHE_CONTROL = "public, max-age=31536000, immutable"


class StorageUnavailable(RuntimeError):
    """R2 is not configured, or boto3 is not installed."""


def is_configured() -> bool:
    return bool(
        settings.r2_account_id
        and settings.r2_access_key_id
        and settings.r2_secret_access_key
        and settings.r2_bucket
    )


@lru_cache
def _client() -> Any:
    """The S3 client, built once.

    `region_name="auto"` is what R2 expects; SigV4 is explicit because the
    signature version is what R2 validates against, and boto3's default has
    changed before.
    """
    if not is_configured():
        raise StorageUnavailable(
            "R2 is not configured. Set R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, "
            "R2_SECRET_ACCESS_KEY and R2_BUCKET."
        )

    try:
        import boto3
        from botocore.config import Config
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on the extra
        raise StorageUnavailable('boto3 is not installed. Run: pip install -e ".[crests]"') from exc

    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )


def content_hash(payload: bytes) -> str:
    """The first 16 hex characters of the SHA-256 — 64 bits.

    Enough that a collision between a few hundred club badges is not a thing
    that happens, short enough to keep the URL readable.
    """
    return hashlib.sha256(payload).hexdigest()[:16]


_UNSAFE_IN_A_KEY = re.compile(r"[^a-z0-9]+")


def key_slug(slug: str) -> str:
    """A registry slug, made safe to put in a URL path.

    Registry slugs are space-separated tokens — `matching.normalize` produces
    "rayo vallecano madrid" — which is right for matching and wrong for a key.
    Object storage accepts the spaces, and then every URL built from one carries
    a raw space: invalid in an href, mangled in logs, and encoded differently by
    whichever client touches it first.
    """
    return _UNSAFE_IN_A_KEY.sub("-", slug.strip().lower()).strip("-")


def crest_key(slug: str, source: bytes, size: int) -> str:
    """Where a crest lives.

    Deterministic in the bytes, so re-running the backfill on unchanged images
    produces the same key and uploads nothing.

    The hash is of the *source* image, not of the encoded variant, so every size
    of one club shares it:

        .../arsenal/6c5549aba32b6d17-512.webp
        .../arsenal/6c5549aba32b6d17-128.webp

    which lets a caller reach any size by swapping the suffix. Hashing each
    variant separately gave every size an unrelated name, so the small one was
    uploaded and then unreachable by anything holding the large one's URL.

    The prefix comes from `r2_crest_prefix` because the bucket is shared with
    other projects. There are no real folders in object storage — a prefix is
    simply what the dashboard draws as one — so this is the entire mechanism
    that keeps crests out of everything else's way.
    """
    prefix = settings.r2_crest_prefix.strip("/")
    stem = f"{key_slug(slug)}/{content_hash(source)}-{size}.webp"
    return f"{prefix}/{stem}" if prefix else stem


def public_url(key: str) -> str:
    """The CDN URL for a key.

    Built from the custom domain, never from the R2 endpoint: the S3 endpoint
    stays credentialed and private, and the bucket is reached publicly only
    through Cloudflare.
    """
    return f"{settings.r2_public_base_url.rstrip('/')}/{key.lstrip('/')}"


def object_exists(key: str) -> bool:
    client = _client()
    try:
        client.head_object(Bucket=settings.r2_bucket, Key=key)
        return True
    except Exception as exc:  # noqa: BLE001 - botocore's ClientError is lazy
        if getattr(exc, "response", {}).get("Error", {}).get("Code") in {"404", "NoSuchKey"}:
            return False
        raise


def put_crest(key: str, payload: bytes, *, force: bool = False) -> tuple[str, bool]:
    """Upload one crest. Returns `(public_url, uploaded)`.

    `uploaded` is False when the object was already there, which is what makes
    the backfill safe to re-run and lets the report say how much changed.

    `force` exists because the key names the *source* image, not the encoded
    bytes. That is what makes the sizes derivable from one another, but it also
    means improving the encoder produces the same key — so the existence check
    would quietly keep the old, worse bytes forever. `--refresh` sets this.
    """
    client = _client()

    if not force and object_exists(key):
        log.debug("Crest already stored at %s", key)
        return public_url(key), False

    client.put_object(
        Bucket=settings.r2_bucket,
        Key=key,
        Body=payload,
        ContentType="image/webp",
        CacheControl=CACHE_CONTROL,
    )
    log.info("Uploaded %s (%d bytes)", key, len(payload))
    return public_url(key), True
