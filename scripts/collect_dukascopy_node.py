from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]


def resolve_command(name: str) -> str:
    return shutil.which(name) or shutil.which(f"{name}.cmd") or name


def require_command(name: str) -> str:
    command = resolve_command(name)
    if shutil.which(command) or Path(command).exists():
        return command
    raise RuntimeError(
        f"Required command '{name}' is not available. "
        "Install Node/npm in the API runtime or rebuild the Docker API image."
    )


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_assets(universe_path: Path, group: str, limit: int | None) -> list[str]:
    payload = yaml.safe_load(universe_path.read_text(encoding="utf-8")) or {}
    symbols = [
        row["symbol"]
        for row in payload.get("assets", [])
        if not group or row.get("group") == group
    ]
    return symbols[:limit] if limit is not None else symbols


def load_aliases(symbols_path: Path, source: str) -> dict[str, str]:
    payload = yaml.safe_load(symbols_path.read_text(encoding="utf-8")) or {}
    aliases: dict[str, str] = {}
    for symbol in payload.get("symbols", []):
        for alias in symbol.get("aliases", []):
            if alias.get("source") == source:
                aliases[symbol["symbol_code"]] = alias["source_symbol"]
    return aliases


def run(args: list[str], *, capture_json: bool = False) -> dict[str, Any] | None:
    printable = " ".join(args)
    print(f"$ {printable}", flush=True)
    completed = subprocess.run(args, cwd=ROOT, text=True, capture_output=True)
    if completed.returncode != 0:
        if completed.stdout:
            print(completed.stdout)
        if completed.stderr:
            print(completed.stderr, file=sys.stderr)
        message = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(message or f"Command failed with exit code {completed.returncode}: {printable}")
    if not capture_json:
        for line in completed.stdout.splitlines():
            if "File saved:" in line or "Download time:" in line:
                print(line)
        return None
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        print(completed.stdout)
        if completed.stderr:
            print(completed.stderr, file=sys.stderr)
        raise


def run_with_retries(args: list[str], *, retries: int) -> None:
    for attempt in range(1, retries + 2):
        try:
            run(args)
            return
        except RuntimeError as exc:
            if attempt > retries:
                raise
            delay = min(30, 5 * attempt)
            print(f"Retry {attempt}/{retries} in {delay}s: {exc}", flush=True)
            time.sleep(delay)


def csv_path(download_dir: Path, source_symbol: str, timeframe: str, from_day: str, to_day: str) -> Path:
    return download_dir / f"{source_symbol}-{timeframe.lower()}-bid-{from_day}-{to_day}.csv"


def parse_csv_time(value: str) -> datetime:
    value = value.strip()
    if value.isdigit():
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    return parse_utc(value)


def csv_stats(path: Path) -> dict[str, Any]:
    first_data: str | None = None
    last_data: str | None = None
    rows = 0
    with path.open("r", encoding="utf-8") as handle:
        header = handle.readline()
        if not header:
            return {"rows": 0, "first": None, "last": None}
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            first_data = first_data or stripped
            last_data = stripped
            rows += 1
    if first_data is None or last_data is None:
        return {"rows": 0, "first": None, "last": None}
    return {
        "rows": rows,
        "first": parse_csv_time(first_data.split(",", 1)[0]),
        "last": parse_csv_time(last_data.split(",", 1)[0]),
    }


def validate_existing_csv(
    path: Path,
    start: datetime,
    end: datetime,
    min_coverage_ratio: float,
) -> tuple[bool, str]:
    if not path.exists():
        return False, "missing"
    stats = csv_stats(path)
    first = stats["first"]
    last = stats["last"]
    rows = stats["rows"]
    if first is None or last is None:
        return False, "empty"
    expected_minutes = int((end - start).total_seconds() // 60) + 1
    min_rows = int(expected_minutes * min_coverage_ratio)
    if rows < min_rows:
        return False, f"too few rows: {rows} < {min_rows}"
    if first > start + timedelta(days=3):
        return False, f"starts too late: {first.isoformat()}"
    if last < end - timedelta(hours=1):
        return False, f"ends too early: {last.isoformat()}"
    return True, f"rows={rows}, first={first.isoformat()}, last={last.isoformat()}"


def quarantine_invalid_csv(path: Path, reason: str) -> None:
    partial_dir = path.parent / "partial"
    partial_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    target = partial_dir / f"{path.stem}.{stamp}.partial{path.suffix}"
    path.replace(target)
    print(f"Quarantined invalid CSV: {target.relative_to(ROOT)} ({reason})", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Download and ingest Dukascopy-node CSV candles.")
    parser.add_argument("--universe", default="configs/universe_default_40.yaml")
    parser.add_argument("--symbols-config", default="configs/symbols.yaml")
    parser.add_argument("--source", default="dukascopy")
    parser.add_argument("--group", default="forex")
    parser.add_argument("--symbols", help="Comma-separated platform symbols to collect instead of the universe group.")
    parser.add_argument("--from", dest="from_date", required=True)
    parser.add_argument("--to", dest="to_date", required=True, help="Exclusive date for dukascopy-node.")
    parser.add_argument("--timeframe", default="M1")
    parser.add_argument("--mapping", default="configs/csv_dukascopy_node_mapping.yaml")
    parser.add_argument("--download-dir", default="download")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--min-coverage-ratio", type=float, default=0.8)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--no-datasets", action="store_true")
    args = parser.parse_args()

    from_day = args.from_date[:10]
    to_day = args.to_date[:10]
    start = parse_utc(args.from_date)
    to_exclusive = parse_utc(args.to_date)
    dataset_to = to_exclusive - timedelta(minutes=1)
    timeframe = args.timeframe.upper()
    download_dir = ROOT / args.download_dir
    download_dir.mkdir(parents=True, exist_ok=True)

    if args.symbols:
        assets = [symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip()]
        assets = assets[: args.limit] if args.limit is not None else assets
    else:
        assets = load_assets(ROOT / args.universe, args.group, args.limit)
    aliases = load_aliases(ROOT / args.symbols_config, args.source)
    summaries = []

    for symbol in assets:
        try:
            source_symbol = aliases.get(symbol)
            if not source_symbol:
                raise RuntimeError(f"No {args.source} alias configured for {symbol}.")

            file_path = csv_path(download_dir, source_symbol, args.timeframe, from_day, to_day)
            if file_path.exists():
                valid, reason = validate_existing_csv(file_path, start, dataset_to, args.min_coverage_ratio)
                if valid:
                    print(f"Using existing CSV for {symbol}: {file_path.relative_to(ROOT)} ({reason})", flush=True)
                elif args.skip_download:
                    raise RuntimeError(f"Existing CSV is invalid and --skip-download is set: {reason}")
                else:
                    quarantine_invalid_csv(file_path, reason)

            if args.skip_download or (args.skip_existing and file_path.exists()):
                pass
            else:
                run_with_retries(
                    [
                        require_command("npx"),
                        "--yes",
                        "dukascopy-node",
                        "-i",
                        source_symbol,
                        "-from",
                        from_day,
                        "-to",
                        to_day,
                        "-t",
                        args.timeframe.lower(),
                        "-f",
                        "csv",
                        "-r",
                        str(args.retries),
                        "-rp",
                        "1500",
                        "-bs",
                        "2",
                        "-bp",
                        "1500",
                    ],
                    retries=args.retries,
                )
            if not file_path.exists():
                raise RuntimeError(f"Expected CSV was not created: {file_path}")

            ingest = run(
                [
                    sys.executable,
                    "-m",
                    "ict.cli",
                    "ingest-csv",
                    "--symbol",
                    symbol,
                    "--source",
                    args.source,
                    "--file",
                    str(file_path.relative_to(ROOT)),
                    "--timeframe",
                    timeframe,
                    "--from",
                    start.isoformat(),
                    "--to",
                    dataset_to.isoformat(),
                    "--mapping",
                    args.mapping,
                ],
                capture_json=True,
            )
            assert ingest is not None

            dataset = None
            if not args.no_datasets:
                dataset = run(
                    [
                        sys.executable,
                        "-m",
                        "ict.cli",
                        "datasets",
                        "create",
                        "--symbol",
                        symbol,
                        "--source",
                        args.source,
                        "--timeframe",
                        timeframe,
                        "--from",
                        start.isoformat(),
                        "--to",
                        dataset_to.isoformat(),
                        "--name",
                        f"{symbol}_{args.source}_{timeframe}_{start:%Y%m%d}_{dataset_to:%Y%m%d}",
                    ],
                    capture_json=True,
                )

            summary = {
                "symbol": symbol,
                "source_symbol": source_symbol,
                "rows_fetched": ingest["rows_fetched"],
                "rows_inserted": ingest["rows_inserted"],
                "rows_updated": ingest["rows_updated"],
                "rows_skipped": ingest["rows_skipped"],
                "quality_score": ingest["quality_report"]["quality_score"],
                "gaps": len(ingest["quality_report"]["gaps"]),
            }
            if dataset is not None:
                summary.update(
                    {
                        "dataset_id": dataset["dataset_id"],
                        "dataset_candles": dataset["candles_count"],
                        "dataset_quality": dataset["quality_score"],
                    }
                )
        except RuntimeError as exc:
            summary = {"symbol": symbol, "status": "failed", "error": str(exc)}
            if not args.continue_on_error:
                raise SystemExit(str(exc))
        summaries.append(summary)
        print(json.dumps(summary, sort_keys=True), flush=True)

    print(json.dumps({"assets": len(summaries), "summaries": summaries}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
