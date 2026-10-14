"""Generate an S3 Batch Operations CSV manifest without buffering it in memory."""

import argparse
import csv
from pathlib import Path


def write_manifest(
    bucket: str, prefix: str, start_object: int, end_object: int, output: Path
) -> int:
    if start_object < 1 or end_object < start_object:
        raise ValueError("Object range must satisfy 1 <= start_object <= end_object.")

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as manifest_file:
        writer = csv.writer(manifest_file, lineterminator="\n")
        for number in range(start_object, end_object + 1):
            writer.writerow((bucket, f"{prefix}object-{number:08d}.dat"))

    return end_object - start_object + 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stream a numbered-object S3 Batch Operations CSV manifest."
    )
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", default="production/")
    parser.add_argument("--start-object", type=int, default=1)
    parser.add_argument("--end-object", type=int, default=10_000_000)
    parser.add_argument("--output", type=Path, default=Path("10m-manifest.csv"))
    args = parser.parse_args()

    count = write_manifest(
        args.bucket,
        args.prefix,
        args.start_object,
        args.end_object,
        args.output,
    )
    print(f"Wrote {count:,} manifest rows to {args.output}.")


if __name__ == "__main__":
    main()
