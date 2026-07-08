from __future__ import annotations

import base64
import hashlib
import json
import os
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Protocol
import time as time_module

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ict.core.config import get_settings
from ict.db.repositories import json_safe
from ict.live.config import LiveSource, load_live_sources
from ict.live.providers import fetch_live_source, previous_utc_day_window
from ict.live.sync import LiveSyncResult, read_remote_candles, upsert_remote_frame

ARCHIVE_SCHEMA_VERSION = 1
ARCHIVE_DATA_FORMAT = "canonical_market_candles_v1"
ARCHIVE_MAGIC = b"ICTARCH1"
PARQUET_COLUMNS = [
    "symbol_code",
    "source_name",
    "source_symbol",
    "timeframe",
    "time_open",
    "open",
    "high",
    "low",
    "close",
    "tick_volume",
    "real_volume",
    "spread",
    "quality_flags",
    "metadata",
]


class ObjectStore(Protocol):
    bucket: str

    def put_bytes(self, key: str, payload: bytes, content_type: str = "application/octet-stream") -> None:
        ...

    def get_bytes(self, key: str) -> bytes:
        ...

    def list_keys(self, prefix: str) -> list[str]:
        ...

    def list_objects(self, prefix: str) -> list["ObjectSummary"]:
        ...

    def get_object_size(self, key: str) -> int:
        ...


@dataclass(frozen=True)
class ObjectSummary:
    key: str
    size: int


@dataclass(frozen=True)
class ArchiveBucketUsage:
    bucket: str
    prefix: str
    object_count: int
    total_bytes: int
    max_bytes: int | None = None

    @property
    def remaining_bytes(self) -> int | None:
        if self.max_bytes is None:
            return None
        return self.max_bytes - self.total_bytes

    @property
    def usage_ratio(self) -> float | None:
        if not self.max_bytes:
            return None
        return self.total_bytes / self.max_bytes

    def as_dict(self) -> dict[str, Any]:
        return {
            "bucket": self.bucket,
            "prefix": self.prefix,
            "object_count": self.object_count,
            "total_bytes": self.total_bytes,
            "total_gb": self.total_bytes / (1024**3),
            "max_bytes": self.max_bytes,
            "max_gb": self.max_bytes / (1024**3) if self.max_bytes is not None else None,
            "remaining_bytes": self.remaining_bytes,
            "remaining_gb": self.remaining_bytes / (1024**3) if self.remaining_bytes is not None else None,
            "usage_ratio": self.usage_ratio,
            "over_limit": self.max_bytes is not None and self.total_bytes > self.max_bytes,
        }


@dataclass(frozen=True)
class ArchivePartition:
    symbol_code: str
    source_name: str
    source_symbol: str
    timeframe: str
    day: date
    object_key: str
    manifest_key: str
    rows: int
    encrypted_bytes: int
    encrypted_sha256: str
    parquet_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol_code": self.symbol_code,
            "source_name": self.source_name,
            "source_symbol": self.source_symbol,
            "timeframe": self.timeframe,
            "day": self.day.isoformat(),
            "object_key": self.object_key,
            "manifest_key": self.manifest_key,
            "rows": self.rows,
            "encrypted_bytes": self.encrypted_bytes,
            "encrypted_sha256": self.encrypted_sha256,
            "parquet_sha256": self.parquet_sha256,
        }


@dataclass(frozen=True)
class ArchiveExportResult:
    status: str
    since: datetime
    until: datetime
    rows_read: int
    partitions: list[ArchivePartition] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "since": self.since,
            "until": self.until,
            "rows_read": self.rows_read,
            "partition_count": len(self.partitions),
            "rows_archived": sum(partition.rows for partition in self.partitions),
            "encrypted_bytes": sum(partition.encrypted_bytes for partition in self.partitions),
            "partitions": [partition.as_dict() for partition in self.partitions],
            "skipped": self.skipped,
        }


@dataclass(frozen=True)
class ArchiveRestoreResult:
    status: str
    since: datetime
    until: datetime
    rows_read: int
    rows_written: int
    rows_inserted: int
    rows_updated: int
    partitions: list[dict[str, Any]] = field(default_factory=list)
    missing: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "since": self.since,
            "until": self.until,
            "rows_read": self.rows_read,
            "rows_written": self.rows_written,
            "rows_inserted": self.rows_inserted,
            "rows_updated": self.rows_updated,
            "partition_count": len(self.partitions),
            "missing_count": len(self.missing),
            "partitions": self.partitions,
            "missing": self.missing,
        }


@dataclass(frozen=True)
class ArchiveAssetResult:
    symbol_code: str
    source_name: str
    source_symbol: str
    provider: str
    timeframe: str
    status: str
    rows_fetched: int
    partitions_written: int
    encrypted_bytes: int
    last_candle_time: datetime | None = None
    error_message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol_code": self.symbol_code,
            "source_name": self.source_name,
            "source_symbol": self.source_symbol,
            "provider": self.provider,
            "timeframe": self.timeframe,
            "status": self.status,
            "rows_fetched": self.rows_fetched,
            "partitions_written": self.partitions_written,
            "encrypted_bytes": self.encrypted_bytes,
            "last_candle_time": self.last_candle_time,
            "error_message": self.error_message,
        }


@dataclass(frozen=True)
class ArchiveCollectionResult:
    status: str
    since: datetime
    until: datetime
    assets_requested: int
    assets_succeeded: int
    assets_failed: int
    rows_fetched: int
    partitions_written: int
    encrypted_bytes: int
    results: list[ArchiveAssetResult]
    bucket_usage_before_bytes: int | None = None
    bucket_usage_after_bytes: int | None = None
    bucket_limit_bytes: int | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "status": self.status,
            "since": self.since,
            "until": self.until,
            "assets_requested": self.assets_requested,
            "assets_succeeded": self.assets_succeeded,
            "assets_failed": self.assets_failed,
            "rows_fetched": self.rows_fetched,
            "partitions_written": self.partitions_written,
            "encrypted_bytes": self.encrypted_bytes,
            "results": [result.as_dict() for result in self.results],
        }
        if self.bucket_usage_before_bytes is not None:
            payload["bucket_usage_before_bytes"] = self.bucket_usage_before_bytes
        if self.bucket_usage_after_bytes is not None:
            payload["bucket_usage_after_bytes"] = self.bucket_usage_after_bytes
        if self.bucket_limit_bytes is not None:
            payload["bucket_limit_bytes"] = self.bucket_limit_bytes
            payload["bucket_limit_gb"] = self.bucket_limit_bytes / (1024**3)
        return payload


class R2ObjectStore:
    def __init__(
        self,
        bucket: str,
        account_id: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        endpoint_url: str | None = None,
    ) -> None:
        import boto3

        if not endpoint_url:
            if not account_id:
                raise ValueError("Use R2_ENDPOINT_URL or R2_ACCOUNT_ID.")
            endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"
        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
        )

    def put_bytes(self, key: str, payload: bytes, content_type: str = "application/octet-stream") -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=payload, ContentType=content_type)

    def get_bytes(self, key: str) -> bytes:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read()
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code in {"NoSuchKey", "404", "NotFound"}:
                raise FileNotFoundError(key) from exc
            raise

    def list_keys(self, prefix: str) -> list[str]:
        return [item.key for item in self.list_objects(prefix)]

    def list_objects(self, prefix: str) -> list[ObjectSummary]:
        objects: list[ObjectSummary] = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            objects.extend(
                ObjectSummary(key=str(item["Key"]), size=int(item.get("Size") or 0))
                for item in page.get("Contents", [])
            )
        return objects

    def get_object_size(self, key: str) -> int:
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
            return int(response.get("ContentLength") or 0)
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code in {"NoSuchKey", "404", "NotFound"}:
                raise FileNotFoundError(key) from exc
            raise


class LocalObjectStore:
    """Filesystem object store used by tests and local dry runs."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.bucket = str(self.root)

    def put_bytes(self, key: str, payload: bytes, content_type: str = "application/octet-stream") -> None:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    def get_bytes(self, key: str) -> bytes:
        return (self.root / key).read_bytes()

    def list_keys(self, prefix: str) -> list[str]:
        return [item.key for item in self.list_objects(prefix)]

    def list_objects(self, prefix: str) -> list[ObjectSummary]:
        root = self.root / prefix
        if not root.exists():
            return []
        return [
            ObjectSummary(key=path.relative_to(self.root).as_posix(), size=path.stat().st_size)
            for path in root.rglob("*")
            if path.is_file()
        ]

    def get_object_size(self, key: str) -> int:
        path = self.root / key
        if not path.exists():
            raise FileNotFoundError(key)
        return path.stat().st_size


def archive_configured() -> bool:
    settings = get_settings()
    return bool(
        settings.market_archive_key
        and settings.r2_bucket
        and (settings.r2_endpoint_url or settings.r2_account_id)
        and settings.r2_access_key_id
        and settings.r2_secret_access_key
    )


def archive_bucket_usage(
    store: ObjectStore | None = None,
    prefix: str = "",
    max_bytes: int | None = None,
) -> ArchiveBucketUsage:
    if store is None and not archive_configured():
        return ArchiveBucketUsage(bucket="", prefix=prefix, object_count=0, total_bytes=0, max_bytes=max_bytes)
    store = store or _r2_store_from_settings()
    objects = store.list_objects(prefix)
    return ArchiveBucketUsage(
        bucket=store.bucket,
        prefix=prefix,
        object_count=len(objects),
        total_bytes=sum(item.size for item in objects),
        max_bytes=max_bytes,
    )


def collect_live_sources_to_r2(
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    symbols: Iterable[str] | None = None,
    config: str = "configs/live_sources.yaml",
    max_priority: int | None = None,
    max_workers: int = 4,
    submit_pause_seconds: float = 0.25,
    max_upload_mb: float = 512,
    max_bucket_gb: float | None = None,
    dry_run: bool = False,
    log_path: str | Path | None = None,
    store: ObjectStore | None = None,
    archive_key: bytes | None = None,
    prefix: str | None = None,
    fetch_source=fetch_live_source,
) -> ArchiveCollectionResult:
    start, end = _collection_window(since, until)
    sources = _selected_free_sources(config, symbols, max_priority=max_priority)
    logger = ArchiveJsonlLogger(log_path)
    logger.emit(
        "archive_run_started",
        dry_run=dry_run,
        since=start.isoformat(),
        until=end.isoformat(),
        assets_requested=len(sources),
        symbols=[source.symbol_code for source in sources],
        max_priority=max_priority,
    )
    if dry_run:
        results = [
            ArchiveAssetResult(
                symbol_code=source.symbol_code,
                source_name=source.source_name,
                source_symbol=source.source_symbol,
                provider=source.provider,
                timeframe=source.timeframe,
                status="dry_run",
                rows_fetched=0,
                partitions_written=0,
                encrypted_bytes=0,
            )
            for source in sources
        ]
        summary = _archive_collection_summary("dry_run", start, end, results)
        logger.emit("archive_run_completed", **_archive_summary_log(summary))
        return summary

    store = store or _r2_store_from_settings()
    archive_key = archive_key or _archive_key_from_settings()
    prefix = _clean_prefix(prefix or get_settings().market_archive_prefix)
    max_bucket_bytes = _max_bucket_bytes(max_bucket_gb)
    bucket_usage_before = archive_bucket_usage(store=store, prefix="", max_bytes=max_bucket_bytes)
    results: list[ArchiveAssetResult] = []
    max_workers = 1 if max_bucket_bytes is not None else max(1, min(max_workers, max(1, len(sources))))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for source in sources:
            logger.emit("archive_asset_submitted", **_source_payload(source))
            futures[executor.submit(fetch_source, source, start, end)] = source
            if submit_pause_seconds > 0:
                time_module.sleep(submit_pause_seconds)

        for future in as_completed(futures):
            source = futures[future]
            try:
                frame = future.result()
                result = archive_asset_frame(
                    source,
                    frame,
                    store=store,
                    archive_key=archive_key,
                    prefix=prefix,
                    max_upload_mb=max_upload_mb,
                    max_bucket_bytes=max_bucket_bytes,
                )
            except Exception as exc:  # noqa: BLE001 - per-asset failure is logged and summarized
                result = ArchiveAssetResult(
                    symbol_code=source.symbol_code,
                    source_name=source.source_name,
                    source_symbol=source.source_symbol,
                    provider=source.provider,
                    timeframe=source.timeframe,
                    status="error",
                    rows_fetched=0,
                    partitions_written=0,
                    encrypted_bytes=0,
                    error_message=_short_error(exc),
                )
            results.append(result)
            logger.emit(
                "archive_asset_completed" if result.status == "ok" else "archive_asset_failed",
                **result.as_dict(),
            )

    bucket_usage_after = archive_bucket_usage(store=store, prefix="", max_bytes=max_bucket_bytes)
    summary = _archive_collection_summary(
        "completed",
        start,
        end,
        results,
        bucket_usage_before_bytes=bucket_usage_before.total_bytes,
        bucket_usage_after_bytes=bucket_usage_after.total_bytes,
        bucket_limit_bytes=max_bucket_bytes,
    )
    logger.emit("archive_run_completed", **_archive_summary_log(summary))
    return summary


def archive_asset_frame(
    source: LiveSource,
    frame: pd.DataFrame,
    *,
    store: ObjectStore,
    archive_key: bytes,
    prefix: str,
    max_upload_mb: float,
    max_bucket_bytes: int | None = None,
) -> ArchiveAssetResult:
    if frame.empty:
        return ArchiveAssetResult(
            symbol_code=source.symbol_code,
            source_name=source.source_name,
            source_symbol=source.source_symbol,
            provider=source.provider,
            timeframe=source.timeframe,
            status="stale",
            rows_fetched=0,
            partitions_written=0,
            encrypted_bytes=0,
            error_message="No closed candles returned for the requested window.",
        )
    partitions = export_frame_to_store(
        frame,
        store=store,
        archive_key=archive_key,
        prefix=prefix,
        max_partitions=7,
        max_upload_bytes=int(max_upload_mb * 1024 * 1024),
        max_bucket_bytes=max_bucket_bytes,
    )
    return ArchiveAssetResult(
        symbol_code=source.symbol_code,
        source_name=source.source_name,
        source_symbol=source.source_symbol,
        provider=source.provider,
        timeframe=source.timeframe,
        status="ok",
        rows_fetched=int(len(frame)),
        partitions_written=len(partitions),
        encrypted_bytes=sum(partition.encrypted_bytes for partition in partitions),
        last_candle_time=pd.Timestamp(frame["time_open"].max()).to_pydatetime(),
    )


def export_remote_to_r2(
    remote_database_url: str,
    since: datetime | None = None,
    until: datetime | None = None,
    symbols: Iterable[str] | None = None,
    source_names: Iterable[str] | None = None,
    timeframe: str = "M1",
    limit: int = 500000,
    max_partitions: int = 500,
    max_upload_mb: float = 512,
    max_bucket_gb: float | None = None,
    store: ObjectStore | None = None,
    archive_key: bytes | None = None,
    prefix: str | None = None,
) -> ArchiveExportResult:
    start, end = _default_window(since, until)
    store = store or _r2_store_from_settings()
    archive_key = archive_key or _archive_key_from_settings()
    prefix = _clean_prefix(prefix or get_settings().market_archive_prefix)

    frame = read_remote_candles(
        remote_database_url,
        since=start,
        until=end - timedelta(microseconds=1),
        symbols=[symbol.strip().upper() for symbol in symbols or [] if symbol.strip()] or None,
        limit=limit,
    )
    frame = _filter_frame(frame, source_names=source_names, timeframe=timeframe)
    partitions = export_frame_to_store(
        frame,
        store=store,
        archive_key=archive_key,
        prefix=prefix,
        max_partitions=max_partitions,
        max_upload_bytes=int(max_upload_mb * 1024 * 1024),
        max_bucket_bytes=_max_bucket_bytes(max_bucket_gb),
    )
    return ArchiveExportResult(
        status="completed" if partitions else "empty",
        since=start,
        until=end,
        rows_read=int(len(frame)),
        partitions=partitions,
    )


def export_frame_to_store(
    frame: pd.DataFrame,
    store: ObjectStore,
    archive_key: bytes,
    prefix: str = "market-candles",
    max_partitions: int = 500,
    max_upload_bytes: int = 512 * 1024 * 1024,
    max_bucket_bytes: int | None = None,
) -> list[ArchivePartition]:
    if frame.empty:
        return []
    prepared: list[tuple[ArchivePartition, bytes, bytes]] = []
    total_bytes = 0
    normalized = _normalize_archive_frame(frame)
    normalized["archive_day"] = normalized["time_open"].dt.date
    groups = normalized.groupby(["source_name", "symbol_code", "source_symbol", "timeframe", "archive_day"], dropna=False)
    if len(groups) > max_partitions:
        raise ValueError(f"Archive would write {len(groups)} partitions, above max_partitions={max_partitions}.")

    for (source_name, symbol_code, source_symbol, timeframe, day), group in groups:
        partition_frame = group.drop(columns=["archive_day"]).sort_values("time_open")
        parquet_payload = _frame_to_parquet_bytes(partition_frame)
        encrypted_payload = _encrypt_bytes(parquet_payload, archive_key)
        total_bytes += len(encrypted_payload)
        if total_bytes > max_upload_bytes:
            raise ValueError(
                f"Archive upload would exceed max_upload_bytes={max_upload_bytes}. "
                "Use a smaller window or raise the explicit guard."
            )
        object_key, manifest_key = _partition_keys(prefix, str(source_name), str(symbol_code), str(timeframe), day)
        manifest = _manifest_payload(
            object_key=object_key,
            bucket=store.bucket,
            frame=partition_frame,
            encrypted_payload=encrypted_payload,
            parquet_payload=parquet_payload,
        )
        partition = ArchivePartition(
            symbol_code=str(symbol_code),
            source_name=str(source_name),
            source_symbol=str(source_symbol),
            timeframe=str(timeframe).upper(),
            day=day,
            object_key=object_key,
            manifest_key=manifest_key,
            rows=len(partition_frame),
            encrypted_bytes=len(encrypted_payload),
            encrypted_sha256=manifest["encrypted_sha256"],
            parquet_sha256=manifest["parquet_sha256"],
        )
        prepared.append((partition, encrypted_payload, _json_bytes(manifest)))

    _enforce_bucket_budget(store, prepared, max_bucket_bytes)
    for partition, encrypted_payload, manifest_payload in prepared:
        store.put_bytes(partition.object_key, encrypted_payload)
        store.put_bytes(partition.manifest_key, manifest_payload, content_type="application/json")
    return [partition for partition, _, _ in prepared]


def restore_from_r2(
    since: datetime,
    until: datetime,
    symbols: Iterable[str],
    source_names: Iterable[str],
    timeframe: str = "M1",
    continue_on_missing: bool = True,
    max_download_mb: float = 1024,
    store: ObjectStore | None = None,
    archive_key: bytes | None = None,
    prefix: str | None = None,
) -> ArchiveRestoreResult:
    start = _utc_dt(since)
    end = _utc_dt(until)
    if end <= start:
        raise ValueError("until must be greater than since.")
    store = store or _r2_store_from_settings()
    archive_key = archive_key or _archive_key_from_settings()
    prefix = _clean_prefix(prefix or get_settings().market_archive_prefix)
    symbol_list = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
    source_list = [source.strip() for source in source_names if source.strip()]
    if not symbol_list or not source_list:
        raise ValueError("Use at least one symbol and one source.")

    results: list[LiveSyncResult] = []
    restored: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    downloaded = 0
    for source_name in source_list:
        for symbol_code in symbol_list:
            for day in _days_between(start, end):
                _, manifest_key = _partition_keys(prefix, source_name, symbol_code, timeframe, day)
                try:
                    manifest = json.loads(store.get_bytes(manifest_key).decode("utf-8"))
                    encrypted_payload = store.get_bytes(manifest["object_key"])
                except FileNotFoundError:
                    row = {"symbol_code": symbol_code, "source_name": source_name, "timeframe": timeframe.upper(), "day": day.isoformat()}
                    missing.append(row)
                    if not continue_on_missing:
                        raise ValueError(f"Missing archive partition: {row}") from None
                    continue
                downloaded += len(encrypted_payload)
                if downloaded > int(max_download_mb * 1024 * 1024):
                    raise ValueError(f"Archive restore would exceed max_download_mb={max_download_mb}.")
                _verify_manifest_blob(manifest, encrypted_payload)
                parquet_payload = _decrypt_bytes(encrypted_payload, archive_key)
                if _sha256(parquet_payload) != manifest.get("parquet_sha256"):
                    raise ValueError(f"Parquet checksum mismatch for {manifest_key}.")
                frame = _parquet_bytes_to_frame(parquet_payload)
                frame = frame[(frame["time_open"] >= start) & (frame["time_open"] < end)]
                result = upsert_remote_frame(frame)
                results.append(result)
                restored.append(
                    {
                        "symbol_code": symbol_code,
                        "source_name": source_name,
                        "timeframe": timeframe.upper(),
                        "day": day.isoformat(),
                        "rows_read": result.rows_read,
                        "rows_written": result.rows_written,
                        "manifest_key": manifest_key,
                    }
                )

    combined = _combine_sync_results(results)
    status = "completed" if restored and not missing else "partial" if restored else "missing"
    return ArchiveRestoreResult(
        status=status,
        since=start,
        until=end,
        rows_read=combined.rows_read,
        rows_written=combined.rows_written,
        rows_inserted=combined.rows_inserted,
        rows_updated=combined.rows_updated,
        partitions=restored,
        missing=missing,
    )


def verify_r2_archive(
    symbols: Iterable[str],
    source_names: Iterable[str],
    since: datetime,
    until: datetime,
    timeframe: str = "M1",
    store: ObjectStore | None = None,
    prefix: str | None = None,
) -> dict[str, Any]:
    start = _utc_dt(since)
    end = _utc_dt(until)
    store = store or _r2_store_from_settings()
    prefix = _clean_prefix(prefix or get_settings().market_archive_prefix)
    ok = []
    missing = []
    failed = []
    for source_name in source_names:
        for symbol_code in symbols:
            for day in _days_between(start, end):
                _, manifest_key = _partition_keys(prefix, source_name, symbol_code, timeframe, day)
                try:
                    manifest = json.loads(store.get_bytes(manifest_key).decode("utf-8"))
                    encrypted_payload = store.get_bytes(manifest["object_key"])
                    _verify_manifest_blob(manifest, encrypted_payload)
                    ok.append({"symbol_code": symbol_code, "source_name": source_name, "day": day.isoformat()})
                except FileNotFoundError:
                    missing.append({"symbol_code": symbol_code, "source_name": source_name, "day": day.isoformat()})
                except Exception as exc:  # noqa: BLE001 - returned for operator inspection
                    failed.append(
                        {
                            "symbol_code": symbol_code,
                            "source_name": source_name,
                            "day": day.isoformat(),
                            "error": str(exc),
                        }
                    )
    return {"status": "ok" if not missing and not failed else "partial", "ok": ok, "missing": missing, "failed": failed}


def archive_status(
    symbols: Iterable[str],
    source_names: Iterable[str],
    timeframe: str = "M1",
    lookback_days: int = 220,
    store: ObjectStore | None = None,
    prefix: str | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    if store is None and not archive_configured():
        return {}
    store = store or _r2_store_from_settings()
    prefix = _clean_prefix(prefix or get_settings().market_archive_prefix)
    statuses: dict[tuple[str, str], dict[str, Any]] = {}
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=lookback_days)
    for source_name in source_names:
        for symbol_code in symbols:
            manifest_prefix = f"{prefix}/source={source_name}/symbol={symbol_code.upper()}/timeframe={timeframe.upper()}/"
            keys = [key for key in store.list_keys(manifest_prefix) if key.endswith("/manifest.json")]
            latest_day = None
            complete_partitions = 0
            rows = 0
            encrypted_bytes = 0
            for key in keys:
                try:
                    manifest = json.loads(store.get_bytes(key).decode("utf-8"))
                    day = date.fromisoformat(str(manifest["partition"]["day"]))
                except Exception:
                    continue
                if day < cutoff:
                    continue
                complete_partitions += 1
                latest_day = max(latest_day, day) if latest_day else day
                rows += int(manifest.get("rows") or 0)
                encrypted_bytes += int(manifest.get("encrypted_bytes") or 0)
            statuses[(symbol_code.upper(), source_name)] = {
                "r2_available": complete_partitions > 0,
                "r2_last": latest_day.isoformat() if latest_day else None,
                "r2_partitions": complete_partitions,
                "r2_rows": rows,
                "r2_encrypted_bytes": encrypted_bytes,
            }
    return statuses


def _r2_store_from_settings() -> R2ObjectStore:
    settings = get_settings()
    if not archive_configured():
        raise ValueError("R2 archive is not configured. Set R2_* and MARKET_ARCHIVE_KEY.")
    assert settings.r2_bucket is not None
    return R2ObjectStore(
        bucket=settings.r2_bucket,
        account_id=settings.r2_account_id,
        access_key_id=settings.r2_access_key_id,
        secret_access_key=settings.r2_secret_access_key,
        endpoint_url=settings.r2_endpoint_url,
    )


def _archive_key_from_settings() -> bytes:
    value = get_settings().market_archive_key
    if not value:
        raise ValueError("MARKET_ARCHIVE_KEY is not configured.")
    try:
        key = base64.b64decode(value)
    except Exception as exc:
        raise ValueError("MARKET_ARCHIVE_KEY must be base64 encoded.") from exc
    if len(key) != 32:
        raise ValueError("MARKET_ARCHIVE_KEY must decode to exactly 32 bytes.")
    return key


def _default_window(since: datetime | None, until: datetime | None) -> tuple[datetime, datetime]:
    if since and until:
        start = _utc_dt(since)
        end = _utc_dt(until)
    elif since or until:
        raise ValueError("Use both since and until, or neither.")
    else:
        today = datetime.now(timezone.utc).date()
        end = datetime.combine(today, time.min, tzinfo=timezone.utc)
        start = end - timedelta(days=1)
    if end <= start:
        raise ValueError("until must be greater than since.")
    return start, end


def _collection_window(since: datetime | None, until: datetime | None) -> tuple[datetime, datetime]:
    if since and until:
        start = _utc_dt(since)
        end = _utc_dt(until)
    elif since or until:
        raise ValueError("Use both since and until, or neither.")
    else:
        start, end = previous_utc_day_window(overlap_minutes=0)
    if end <= start:
        raise ValueError("until must be greater than since.")
    return start, end


def _selected_free_sources(
    config: str,
    symbols: Iterable[str] | None,
    max_priority: int | None = None,
) -> list[LiveSource]:
    symbol_set = {symbol.strip().upper() for symbol in symbols or [] if symbol.strip()}
    return [
        source
        for source in load_live_sources(config)
        if source.enabled
        and source.provider != "databento"
        and (max_priority is None or source.priority <= max_priority)
        and (not symbol_set or source.symbol_code.upper() in symbol_set)
    ]


def _filter_frame(
    frame: pd.DataFrame,
    source_names: Iterable[str] | None = None,
    timeframe: str = "M1",
) -> pd.DataFrame:
    if frame.empty:
        return frame
    output = frame.copy()
    output["timeframe"] = output["timeframe"].astype(str).str.upper()
    output = output[output["timeframe"] == timeframe.upper()]
    source_set = {source.strip() for source in source_names or [] if source.strip()}
    if source_set:
        output = output[output["source_name"].astype(str).isin(source_set)]
    return output


def _normalize_archive_frame(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    for column in PARQUET_COLUMNS:
        if column not in output.columns:
            output[column] = None
    output = output[PARQUET_COLUMNS]
    output["symbol_code"] = output["symbol_code"].astype(str).str.upper()
    output["source_name"] = output["source_name"].astype(str)
    output["source_symbol"] = output["source_symbol"].astype(str)
    output["timeframe"] = output["timeframe"].astype(str).str.upper()
    output["time_open"] = pd.to_datetime(output["time_open"], utc=True)
    output["quality_flags"] = output["quality_flags"].map(_json_dumps_object)
    output["metadata"] = output["metadata"].map(_json_dumps_object)
    return output


def _frame_to_parquet_bytes(frame: pd.DataFrame) -> bytes:
    table = pa.Table.from_pandas(_normalize_archive_frame(frame), preserve_index=False)
    buffer = BytesIO()
    pq.write_table(table, buffer, compression="zstd")
    return buffer.getvalue()


def _parquet_bytes_to_frame(payload: bytes) -> pd.DataFrame:
    frame = pq.read_table(BytesIO(payload)).to_pandas()
    frame["time_open"] = pd.to_datetime(frame["time_open"], utc=True)
    frame["quality_flags"] = frame["quality_flags"].map(_json_object)
    frame["metadata"] = frame["metadata"].map(_json_object)
    return frame


def _manifest_payload(
    object_key: str,
    bucket: str,
    frame: pd.DataFrame,
    encrypted_payload: bytes,
    parquet_payload: bytes,
) -> dict[str, Any]:
    first = frame.iloc[0]
    day = pd.Timestamp(first["time_open"]).date()
    return {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "data_format": ARCHIVE_DATA_FORMAT,
        "status": "complete",
        "bucket": bucket,
        "object_key": object_key,
        "partition": {
            "source_name": str(first["source_name"]),
            "symbol_code": str(first["symbol_code"]),
            "source_symbol": str(first["source_symbol"]),
            "timeframe": str(first["timeframe"]).upper(),
            "day": day.isoformat(),
        },
        "start": pd.Timestamp(frame["time_open"].min()).isoformat(),
        "end": pd.Timestamp(frame["time_open"].max()).isoformat(),
        "rows": int(len(frame)),
        "encrypted_bytes": len(encrypted_payload),
        "encrypted_sha256": _sha256(encrypted_payload),
        "parquet_sha256": _sha256(parquet_payload),
        "compression": "parquet:zstd",
        "encryption": "AES-256-GCM",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _partition_keys(prefix: str, source_name: str, symbol_code: str, timeframe: str, day: date) -> tuple[str, str]:
    base = (
        f"{prefix}/source={source_name}/symbol={symbol_code.upper()}/timeframe={timeframe.upper()}/"
        f"year={day:%Y}/month={day:%m}/day={day:%d}"
    )
    return f"{base}/candles.parquet.zst.enc", f"{base}/manifest.json"


def _encrypt_bytes(payload: bytes, key: bytes) -> bytes:
    nonce = os.urandom(12)
    encrypted = AESGCM(key).encrypt(nonce, payload, None)
    return ARCHIVE_MAGIC + nonce + encrypted


def _decrypt_bytes(payload: bytes, key: bytes) -> bytes:
    if not payload.startswith(ARCHIVE_MAGIC):
        raise ValueError("Invalid archive magic.")
    nonce = payload[len(ARCHIVE_MAGIC) : len(ARCHIVE_MAGIC) + 12]
    encrypted = payload[len(ARCHIVE_MAGIC) + 12 :]
    return AESGCM(key).decrypt(nonce, encrypted, None)


def _verify_manifest_blob(manifest: dict[str, Any], encrypted_payload: bytes) -> None:
    if manifest.get("status") != "complete":
        raise ValueError("Archive manifest is not complete.")
    if int(manifest.get("schema_version") or 0) != ARCHIVE_SCHEMA_VERSION:
        raise ValueError("Unsupported archive schema version.")
    if manifest.get("data_format") != ARCHIVE_DATA_FORMAT:
        raise ValueError("Unsupported archive data format.")
    if int(manifest.get("encrypted_bytes") or -1) != len(encrypted_payload):
        raise ValueError("Encrypted size mismatch.")
    if str(manifest.get("encrypted_sha256")) != _sha256(encrypted_payload):
        raise ValueError("Encrypted checksum mismatch.")


def _days_between(start: datetime, end: datetime) -> list[date]:
    current = start.date()
    last = (end - timedelta(microseconds=1)).date()
    days = []
    while current <= last:
        days.append(current)
        current += timedelta(days=1)
    return days


def _combine_sync_results(results: Iterable[LiveSyncResult]) -> LiveSyncResult:
    rows_read = rows_written = rows_inserted = rows_updated = 0
    groups = []
    for result in results:
        rows_read += result.rows_read
        rows_written += result.rows_written
        rows_inserted += result.rows_inserted
        rows_updated += result.rows_updated
        groups.extend(result.groups)
    return LiveSyncResult(rows_read, rows_written, rows_inserted, rows_updated, groups)


def _json_object(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if value is None or pd.isna(value):
        return {}
    return json.loads(str(value))


def _json_dumps_object(value: Any) -> str:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return json.dumps(json_safe(parsed), sort_keys=True)
        except json.JSONDecodeError:
            pass
    return json.dumps(json_safe(value or {}), sort_keys=True)


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(json_safe(payload), indent=2, sort_keys=True).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _utc_dt(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _clean_prefix(value: str) -> str:
    return value.strip().strip("/")


def _max_bucket_bytes(max_bucket_gb: float | None = None) -> int | None:
    value = get_settings().market_archive_max_bucket_gb if max_bucket_gb is None else max_bucket_gb
    if value is None or value <= 0:
        return None
    return int(float(value) * 1024 * 1024 * 1024)


def _enforce_bucket_budget(
    store: ObjectStore,
    prepared: list[tuple[ArchivePartition, bytes, bytes]],
    max_bucket_bytes: int | None,
) -> None:
    if max_bucket_bytes is None or not prepared:
        return
    current_usage = archive_bucket_usage(store=store, prefix="", max_bytes=max_bucket_bytes)
    replacement_bytes = 0
    new_bytes = 0
    for partition, encrypted_payload, manifest_payload in prepared:
        new_bytes += len(encrypted_payload) + len(manifest_payload)
        for key in (partition.object_key, partition.manifest_key):
            try:
                replacement_bytes += store.get_object_size(key)
            except FileNotFoundError:
                continue
    projected_bytes = current_usage.total_bytes - replacement_bytes + new_bytes
    if projected_bytes > max_bucket_bytes:
        raise ValueError(
            "R2 archive bucket budget would be exceeded. "
            f"current_bytes={current_usage.total_bytes}, "
            f"replacement_bytes={replacement_bytes}, "
            f"new_bytes={new_bytes}, "
            f"projected_bytes={projected_bytes}, "
            f"max_bucket_bytes={max_bucket_bytes}. "
            "Reduce the collection window, disable low-priority assets, or prune verified old partitions first."
        )


def _archive_collection_summary(
    status: str,
    since: datetime,
    until: datetime,
    results: list[ArchiveAssetResult],
    bucket_usage_before_bytes: int | None = None,
    bucket_usage_after_bytes: int | None = None,
    bucket_limit_bytes: int | None = None,
) -> ArchiveCollectionResult:
    return ArchiveCollectionResult(
        status=status,
        since=since,
        until=until,
        assets_requested=len(results),
        assets_succeeded=sum(1 for result in results if result.status == "ok"),
        assets_failed=sum(1 for result in results if result.status == "error"),
        rows_fetched=sum(result.rows_fetched for result in results),
        partitions_written=sum(result.partitions_written for result in results),
        encrypted_bytes=sum(result.encrypted_bytes for result in results),
        results=sorted(results, key=lambda result: (result.symbol_code, result.source_name)),
        bucket_usage_before_bytes=bucket_usage_before_bytes,
        bucket_usage_after_bytes=bucket_usage_after_bytes,
        bucket_limit_bytes=bucket_limit_bytes,
    )


def _archive_summary_log(summary: ArchiveCollectionResult) -> dict[str, Any]:
    payload = {
        "status": summary.status,
        "assets_requested": summary.assets_requested,
        "assets_succeeded": summary.assets_succeeded,
        "assets_failed": summary.assets_failed,
        "rows_fetched": summary.rows_fetched,
        "partitions_written": summary.partitions_written,
        "encrypted_bytes": summary.encrypted_bytes,
        "since": summary.since.isoformat(),
        "until": summary.until.isoformat(),
    }
    if summary.bucket_usage_before_bytes is not None:
        payload["bucket_usage_before_bytes"] = summary.bucket_usage_before_bytes
    if summary.bucket_usage_after_bytes is not None:
        payload["bucket_usage_after_bytes"] = summary.bucket_usage_after_bytes
    if summary.bucket_limit_bytes is not None:
        payload["bucket_limit_bytes"] = summary.bucket_limit_bytes
    return payload


def _source_payload(source: LiveSource) -> dict[str, Any]:
    return {
        "symbol_code": source.symbol_code,
        "source_name": source.source_name,
        "source_symbol": source.source_symbol,
        "provider": source.provider,
        "timeframe": source.timeframe,
        "priority": source.priority,
    }


def _short_error(exc: Exception | str, limit: int = 800) -> str:
    message = str(exc).replace("\n", " ")
    return message if len(message) <= limit else message[: limit - 3] + "..."


class ArchiveJsonlLogger:
    def __init__(self, path: str | Path | None):
        self.path = Path(path) if path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: str, **payload) -> None:
        row = {"event": event, "time": datetime.now(timezone.utc).isoformat(), **json_safe(payload)}
        print(f"[archive:{event}] {json.dumps(row, sort_keys=True, default=str)}", flush=True)
        if self.path:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
