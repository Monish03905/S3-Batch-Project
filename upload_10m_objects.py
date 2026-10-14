"""Upload a numbered object range to S3 with safe, resumable checkpoints."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, HTTPClientError

BODY = b"S3 Batch Operations scale-test object\n"
RETRYABLE_CODES = {
    "InternalError",
    "RequestLimitExceeded",
    "RequestTimeout",
    "ServiceUnavailable",
    "SlowDown",
    "Throttling",
    "ThrottlingException",
}


def load_checkpoint(
    path: Path, bucket: str, prefix: str, start_object: int, end_object: int
) -> int:
    if not path.exists():
        return start_object - 1

    with path.open(encoding="utf-8") as checkpoint_file:
        checkpoint = json.load(checkpoint_file)

    expected = {
        "bucket": bucket,
        "prefix": prefix,
        "target": end_object,
    }
    for field, value in expected.items():
        if checkpoint.get(field) != value:
            raise ValueError(
                f"Checkpoint {path} has a different {field}; "
                "use a matching checkpoint or choose another path."
            )

    saved_start = checkpoint.get("start_object")
    if saved_start != start_object:
        raise ValueError(
            f"Checkpoint {path} has a different start_object; "
            "use a matching checkpoint or choose another path."
        )

    last_completed = checkpoint.get("last_completed")
    if (
        not isinstance(last_completed, int)
        or last_completed < start_object - 1
        or last_completed > end_object
    ):
        raise ValueError(f"Checkpoint {path} contains an invalid last_completed value.")

    return last_completed


def save_checkpoint(
    path: Path,
    bucket: str,
    prefix: str,
    start_object: int,
    end_object: int,
    last_completed: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint = {
        "bucket": bucket,
        "prefix": prefix,
        "start_object": start_object,
        "last_completed": last_completed,
        "target": end_object,
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    temporary_path = path.with_name(path.name + ".tmp")
    with temporary_path.open("w", encoding="utf-8", newline="\n") as checkpoint_file:
        json.dump(checkpoint, checkpoint_file, indent=2)
        checkpoint_file.write("\n")
    os.replace(temporary_path, path)


def upload_object(
    s3: Any,
    bucket: str,
    prefix: str,
    number: int,
    retries: int,
) -> tuple[int, str | None]:
    key = f"{prefix}object-{number:08d}.dat"

    for attempt in range(1, retries + 1):
        try:
            s3.put_object(
                Bucket=bucket,
                Key=key,
                Body=BODY,
                ContentType="text/plain",
            )
            return number, None
        except ClientError as error:
            response = error.response
            error_code = response.get("Error", {}).get("Code", "Unknown")
            status_code = response.get("ResponseMetadata", {}).get(
                "HTTPStatusCode", 0
            )
            if (
                error_code not in RETRYABLE_CODES
                and status_code not in (408, 429)
                and status_code < 500
            ):
                message = response.get("Error", {}).get("Message", str(error))
                return number, f"{error_code}: {message}"
            message = str(error)
        except HTTPClientError as error:
            message = str(error)
        except BotoCoreError as error:
            return number, str(error)

        if attempt == retries:
            return number, message
        time.sleep(min(2 ** (attempt - 1), 30))

    return number, "Upload failed without an AWS error response."


def write_failures(path: Path, failures: list[tuple[int, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as failure_file:
        for number, message in failures:
            failure_file.write(f"{number}\t{message.replace(chr(10), ' ')}\n")


def run_upload(
    s3: Any,
    bucket: str,
    prefix: str,
    start_object: int,
    end_object: int,
    workers: int,
    chunk_size: int,
    checkpoint_path: Path,
    failed_path: Path,
    retries: int = 10,
) -> int:
    if start_object < 1 or end_object < start_object:
        raise ValueError("Object range must satisfy 1 <= start_object <= end_object.")
    if workers < 1 or chunk_size < 1 or retries < 1:
        raise ValueError("workers, chunk_size, and retries must all be positive.")

    last_completed = load_checkpoint(
        checkpoint_path, bucket, prefix, start_object, end_object
    )
    failed_path.parent.mkdir(parents=True, exist_ok=True)
    failed_path.write_text("", encoding="utf-8")

    total_objects = end_object - start_object + 1
    completed_count = max(0, last_completed - start_object + 1)
    started_at = time.monotonic()
    while last_completed < end_object:
        chunk_start = last_completed + 1
        chunk_end = min(chunk_start + chunk_size - 1, end_object)
        print(f"\nUploading {chunk_start:,} -> {chunk_end:,}")
        failures: list[tuple[int, str]] = []
        chunk_started_at = time.monotonic()

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(
                    upload_object, s3, bucket, prefix, number, retries
                )
                for number in range(chunk_start, chunk_end + 1)
            ]
            for future in concurrent.futures.as_completed(futures):
                result_number, error = future.result()
                if error is not None:
                    failures.append((result_number, error))

        if failures:
            failures.sort()
            write_failures(failed_path, failures)
            print(
                f"Chunk failed: {len(failures):,}/{chunk_end - chunk_start + 1:,}. "
                f"See {failed_path}; rerun to retry this chunk.",
                file=sys.stderr,
            )
            return 1

        last_completed = chunk_end
        completed_count = last_completed - start_object + 1
        save_checkpoint(
            checkpoint_path,
            bucket,
            prefix,
            start_object,
            end_object,
            last_completed,
        )

        elapsed = time.monotonic() - started_at
        rate = completed_count / elapsed if elapsed > 0 else 0
        remaining = total_objects - completed_count
        eta_hours = remaining / rate / 3600 if rate > 0 else 0
        chunk_elapsed = time.monotonic() - chunk_started_at
        print(
            f"Completed: {completed_count:,}/{total_objects:,} | "
            f"Chunk: {chunk_end - chunk_start + 1:,} objects in "
            f"{chunk_elapsed:.1f}s | Speed: {rate:,.0f} objects/s | "
            f"Estimated remaining: {eta_hours:.2f} hours"
        )

    print(f"\nAll {total_objects:,} objects uploaded successfully.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload numbered test objects to S3 in resumable chunks."
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("AWS_S3_BUCKET"),
        help="Destination bucket (or set AWS_S3_BUCKET).",
    )
    parser.add_argument("--prefix", default="production/")
    parser.add_argument("--start-object", type=int, default=1)
    parser.add_argument("--end-object", type=int, default=10_000_000)
    parser.add_argument("--workers", type=int, default=64)
    parser.add_argument("--chunk-size", type=int, default=10_000)
    parser.add_argument("--retries", type=int, default=10)
    parser.add_argument("--region", default="ap-south-1")
    parser.add_argument(
        "--checkpoint",
        type=Path,
        help="Checkpoint path (defaults to a bucket-specific filename).",
    )
    parser.add_argument(
        "--failed-file",
        type=Path,
        help="Failure report path (defaults to a bucket-specific filename).",
    )
    args = parser.parse_args()
    if not args.bucket:
        parser.error("--bucket is required unless AWS_S3_BUCKET is set.")
    return args


def main() -> int:
    args = parse_args()
    config = Config(
        region_name=args.region,
        max_pool_connections=args.workers,
        retries={"mode": "adaptive", "max_attempts": 3},
    )
    s3 = boto3.client("s3", config=config)
    return run_upload(
        s3=s3,
        bucket=args.bucket,
        prefix=args.prefix,
        start_object=args.start_object,
        end_object=args.end_object,
        workers=args.workers,
        chunk_size=args.chunk_size,
        checkpoint_path=args.checkpoint
        or Path(f"{args.bucket}-upload-checkpoint.json"),
        failed_path=args.failed_file
        or Path(f"{args.bucket}-upload-failures.txt"),
        retries=args.retries,
    )


if __name__ == "__main__":
    sys.exit(main())
