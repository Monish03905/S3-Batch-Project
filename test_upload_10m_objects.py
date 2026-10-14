import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError

from generate_manifest import write_manifest
from upload_10m_objects import (
    load_checkpoint,
    run_upload,
    save_checkpoint,
    upload_object,
)


class UploadCheckpointTests(unittest.TestCase):
    def test_checkpoint_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint_path = Path(directory) / "state" / "checkpoint.json"
            save_checkpoint(
                checkpoint_path, "test-bucket", "objects/", 1, 3, 2
            )

            self.assertEqual(
                load_checkpoint(checkpoint_path, "test-bucket", "objects/", 1, 3),
                2,
            )

    def test_checkpoint_rejects_different_bucket(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint_path = Path(directory) / "checkpoint.json"
            save_checkpoint(checkpoint_path, "test-bucket", "", 1, 3, 1)

            with self.assertRaisesRegex(ValueError, "different bucket"):
                load_checkpoint(checkpoint_path, "another-bucket", "", 1, 3)

    def test_checkpoint_rejects_invalid_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint_path = Path(directory) / "checkpoint.json"
            checkpoint_path.write_text(
                json.dumps(
                    {
                        "bucket": "test-bucket",
                        "prefix": "",
                        "start_object": 1,
                        "target": 3,
                        "last_completed": 4,
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "invalid last_completed"):
                load_checkpoint(checkpoint_path, "test-bucket", "", 1, 3)

    def test_checkpoint_rejects_legacy_state_without_range_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint_path = Path(directory) / "checkpoint.json"
            checkpoint_path.write_text(
                json.dumps(
                    {
                        "bucket": "test-bucket",
                        "prefix": "",
                        "target": 3,
                        "last_completed": 3,
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "different start_object"):
                load_checkpoint(checkpoint_path, "test-bucket", "", 1, 3)


class UploadExecutionTests(unittest.TestCase):
    def test_transient_s3_error_is_retried(self):
        s3 = Mock()
        s3.put_object.side_effect = [
            ClientError(
                {
                    "Error": {"Code": "ServiceUnavailable", "Message": "Try again"},
                    "ResponseMetadata": {"HTTPStatusCode": 503},
                },
                "PutObject",
            ),
            {},
        ]

        with patch("upload_10m_objects.time.sleep") as sleep:
            result = upload_object(s3, "test-bucket", "", 1, retries=2)

        self.assertEqual(result, (1, None))
        self.assertEqual(s3.put_object.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_failed_chunk_does_not_advance_checkpoint_or_skip_objects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint_path = root / "checkpoint.json"
            failed_path = root / "failures.txt"
            s3 = Mock()

            def put_object(**kwargs):
                if kwargs["Key"].endswith("object-00000002.dat"):
                    raise ClientError(
                        {
                            "Error": {
                                "Code": "NoSuchBucket",
                                "Message": "Bucket does not exist",
                            },
                            "ResponseMetadata": {"HTTPStatusCode": 404},
                        },
                        "PutObject",
                    )

            s3.put_object.side_effect = put_object
            result = run_upload(
                s3,
                "test-bucket",
                "",
                1,
                3,
                workers=1,
                chunk_size=2,
                checkpoint_path=checkpoint_path,
                failed_path=failed_path,
            )

            self.assertEqual(result, 1)
            self.assertFalse(checkpoint_path.exists())
            self.assertEqual(s3.put_object.call_count, 2)
            self.assertIn("2\tNoSuchBucket", failed_path.read_text(encoding="utf-8"))

    def test_successful_run_checkpoints_only_uploaded_range(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint_path = root / "checkpoint.json"
            failed_path = root / "failures.txt"
            s3 = Mock()

            result = run_upload(
                s3,
                "test-bucket",
                "objects/",
                1,
                3,
                workers=1,
                chunk_size=2,
                checkpoint_path=checkpoint_path,
                failed_path=failed_path,
            )

            self.assertEqual(result, 0)
            self.assertEqual(s3.put_object.call_count, 3)
            self.assertEqual(
                load_checkpoint(checkpoint_path, "test-bucket", "objects/", 1, 3),
                3,
            )
            self.assertEqual(failed_path.read_text(encoding="utf-8"), "")

    def test_resume_starts_after_last_successful_object(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint_path = root / "checkpoint.json"
            failed_path = root / "failures.txt"
            save_checkpoint(checkpoint_path, "test-bucket", "", 1, 3, 1)
            s3 = Mock()

            result = run_upload(
                s3,
                "test-bucket",
                "",
                1,
                3,
                workers=1,
                chunk_size=2,
                checkpoint_path=checkpoint_path,
                failed_path=failed_path,
            )

            self.assertEqual(result, 0)
            uploaded_keys = [
                call.kwargs["Key"] for call in s3.put_object.call_args_list
            ]
            self.assertEqual(
                uploaded_keys,
                ["object-00000002.dat", "object-00000003.dat"],
            )


class ManifestGenerationTests(unittest.TestCase):
    def test_manifest_rows_match_upload_key_format(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "manifest.csv"

            count = write_manifest(
                "test-bucket", "production/", 1, 2, manifest_path
            )

            self.assertEqual(count, 2)
            self.assertEqual(
                manifest_path.read_text(encoding="utf-8"),
                "test-bucket,production/object-00000001.dat\n"
                "test-bucket,production/object-00000002.dat\n",
            )


if __name__ == "__main__":
    unittest.main()
