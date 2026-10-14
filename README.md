# Amazon S3 Batch Operations Demo

Automate S3 object tagging at scale with **Amazon S3 Batch Operations**. This
project provisions a private source bucket and reports bucket, uploads numbered
test objects, generates a Batch Operations CSV manifest, and includes job,
IAM, lifecycle, and retry examples.

> **Cost and safety:** the 10-million-object workflow creates millions of S3
> requests and can incur substantial AWS charges. Start with a small test range
> and a disposable AWS account. The setup script creates AWS resources and does
> not automatically delete them.

## What is included

- PowerShell setup for S3 buckets, public-access blocking, default encryption,
  and an IAM role/policy.
- A resumable, chunked uploader with retry handling and failure reporting.
- A streaming CSV manifest generator that does not hold the manifest in memory.
- Example Batch Operations tagging jobs, completion reports, and retry manifests.
- Small local test objects and a 20-object sample manifest.

## Requirements

- Windows PowerShell 5.1 or later.
- AWS CLI v2 configured with credentials and permissions to create S3 buckets
  and IAM roles/policies.
- Python 3.10 or later for the uploader and manifest generator.

Install the Python dependency:

```powershell
py -m pip install -r requirements.txt
```

Use an AWS profile with the required permissions. Do not put AWS access keys in
this repository:

```powershell
$env:AWS_PROFILE = "your-aws-profile"
aws sts get-caller-identity
```

## Quick start

### 1. Create the AWS resources

Run the setup script from this directory:

```powershell
.\01_setup_infrastructure.ps1
```

The script creates two timestamp-and-random-suffix bucket names in `ap-south-1`, blocks public
access, enables SSE-S3 encryption, creates the Batch Operations role/policy,
and writes account-specific values to `project-config.json`. That generated
configuration is ignored by Git.

### 2. Upload a small test range

Start with a few objects to validate credentials, bucket access, and the upload
path before attempting a large run:

```powershell
$config = Get-Content .\project-config.json -Raw | ConvertFrom-Json
$env:AWS_S3_BUCKET = $config.SourceBucket

py .\upload_10m_objects.py `
  --start-object 1 `
  --end-object 20 `
  --prefix production/ `
  --checkpoint .\smoke-checkpoint.json `
  --failed-file .\smoke-failures.txt
```

The uploader resumes only from a checkpoint whose bucket, prefix, and object
range match the current run. It advances the checkpoint after a whole chunk
succeeds; if a chunk has permanent failures, it records them and stops rather
than skipping failed objects. Re-run the same command to retry that chunk.

### 3. Generate and upload a manifest

Generate a manifest for the same object range and prefix:

```powershell
py .\generate_manifest.py `
  --bucket $config.SourceBucket `
  --prefix production/ `
  --start-object 1 `
  --end-object 20 `
  --output .\demo-manifest.csv

aws s3 cp .\demo-manifest.csv `
  "s3://$($config.ReportsBucket)/manifests/demo-manifest.csv" `
  --region $config.Region
```

The manifest is a headerless CSV with `bucket,key` rows, as expected by the
`S3BatchOperations_CSV_20180820` format.

### 4. Configure and submit a Batch Operations job

The JSON files in this repository are **templates**, not ready-to-submit
account configuration. Replace `example-source-bucket`,
`example-reports-bucket`, `000000000000`,
`REPLACE_WITH_UPLOADED_MANIFEST_ETAG`, and
`REPLACE_WITH_UNIQUE_CLIENT_REQUEST_TOKEN` with values for your account and
uploaded manifest. For multipart-uploaded manifests, use the exact ETag
returned by `head-object`.

The job template requests object tagging (`lifecycle=archive`) and writes a
completion report. Before creating a job, verify that:

1. The manifest points to the uploaded CSV and its exact ETag.
2. The IAM role ARN is the role created by the setup script.
3. The role policy grants access to the chosen source and reports buckets.
4. The manifest contains only objects that should be tagged.

Review the AWS CLI `s3control create-job` options for your account and region;
job submission requires an account ID and a unique client request token. Once
the template values have been replaced, submit it with:

```powershell
aws s3control create-job `
  --cli-input-json file://batch-job.json `
  --region $config.Region
```

## 10-million-object run

After a successful small-scale test, the uploader and manifest generator
default to objects `1` through `10,000,000`. Set the bucket explicitly or use
`AWS_S3_BUCKET`, then run:

```powershell
$env:AWS_S3_BUCKET = $config.SourceBucket

py .\upload_10m_objects.py

py .\generate_manifest.py `
  --bucket $config.SourceBucket `
  --output .\10m-manifest.csv
```

`10m-manifest.csv` is approximately 680 MB and is intentionally excluded from
GitHub. Generate it locally when needed. Checkpoint, failure, and generated
configuration files are also ignored.

## Tests

The unit tests use mocked S3 responses; they do not create AWS resources:

```powershell
py -m unittest -v
```

## Repository layout

| File | Purpose |
| --- | --- |
| `01_setup_infrastructure.ps1` | Entry point for AWS infrastructure setup |
| `01_setup_infrastructure_fixed.ps1` | Infrastructure setup implementation |
| `upload_10m_objects.py` | Chunked, resumable S3 uploader |
| `generate_manifest.py` | Streaming S3 Batch Operations manifest generator |
| `batch-job.json` | Example object-tagging job template |
| `batch-permissions-policy.json` | IAM policy template for Batch Operations |
| `batch-trust-policy.json` | IAM trust policy for the S3 Batch Operations service |
| `manifest.csv` | Small sample manifest using `test-data/` objects |
| `retry-*.json`, `retry-*.csv`, `failure-*.json` | Retry and failed-task examples |
| `lifecycle.json` | Example lifecycle rule for archive-tagged objects |
| `test-data/` | Small sample object bodies |

## Cleanup

The setup script does not delete resources. After jobs finish and reports are
no longer needed, review the resources and remove the created buckets (including
their contents), IAM role, and IAM policy according to your organization's
retention requirements. Do not delete a bucket until its contents and reports
have been checked.
