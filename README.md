<div align="center">

# S3 Batch Operations Demo

### Tag thousands—or millions—of Amazon S3 objects with one managed batch job.

[![AWS S3](https://img.shields.io/badge/AWS-S3%20Batch%20Operations-orange?logo=amazons3&logoColor=white)](https://docs.aws.amazon.com/AmazonS3/latest/userguide/batch-ops.html)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![PowerShell](https://img.shields.io/badge/PowerShell-5.1%2B-5391FE?logo=powershell&logoColor=white)](https://learn.microsoft.com/powershell/)

[Get started](#-choose-your-path) · [Quick demo](#-run-a-safe-20-object-demo) · [Project files](#-explore-the-project) · [Troubleshooting](#-troubleshooting) · [Cost & cleanup](#-cost-safety--cleanup)

</div>

> [!CAUTION]
> **A 10-million-object run can incur substantial AWS charges.** Begin with the
> 20-object demo, review AWS pricing, and use a bucket you can safely modify.
> Infrastructure setup creates real AWS resources and does not delete them.

## Contents

- [Choose your path](#-choose-your-path)
- [What happens in the demo](#-what-happens-in-the-demo)
- [Requirements and setup](#-requirements-and-setup)
- [Run a safe 20-object demo](#-run-a-safe-20-object-demo)
- [Explore the job template](#-explore-the-job-template)
- [Scale up to 10 million objects](#-scale-up-to-10-million-objects)
- [Run tests](#-run-tests)
- [Explore the project](#-explore-the-project)
- [Troubleshooting](#-troubleshooting)
- [Cost, safety, and cleanup](#-cost-safety--cleanup)

## 🧭 Choose your path

<details>
<summary><strong>I'm new here — show me the recommended route</strong></summary>

Follow **Requirements and setup → 20-object demo → Job template** in that
order. The sample is deliberately small so you can check your credentials,
permissions, manifest, and tagging operation before using AWS at scale.

</details>

<details>
<summary><strong>I only want to inspect the code or run tests</strong></summary>

No AWS account is needed to browse the templates or run the mocked unit tests.
Go to [Run tests](#-run-tests) or jump to [Project files](#-explore-the-project).

</details>

<details>
<summary><strong>I'm ready for the 10-million-object workflow</strong></summary>

First complete the small demo successfully. Then read every warning in
[Scale up to 10 million objects](#-scale-up-to-10-million-objects), especially
the storage, request, runtime, and billing implications.

</details>

## 🔄 What happens in the demo

```text
PowerShell setup ──> Private source + reports buckets ──> Numbered test objects
                                                               │
                                                               v
Batch job template <── Completion report <── S3 Batch Operations <── CSV manifest
```

The uploader creates keys such as `production/object-00000001.dat`. The
manifest generator writes a headerless CSV listing the objects to process.
An S3 Batch Operations job reads that manifest, applies the example
`lifecycle=archive` tag, and writes a completion report.

## 🧰 Requirements and setup

- Windows PowerShell 5.1 or later.
- AWS CLI v2.
- Python 3.10 or later.
- An AWS profile authorized to create S3 buckets, configure bucket security,
  create/update IAM roles and policies, upload objects, and (when submitting a
  job) create S3 Batch Operations jobs.

Install the Python dependency and check which AWS identity the CLI will use:

```powershell
py -m pip install -r requirements.txt
$env:AWS_PROFILE = "your-aws-profile"
aws sts get-caller-identity
```

> [!IMPORTANT]
> Keep access keys in your normal AWS credential provider or profile. Never
> paste credentials into a script, config template, or GitHub commit.

To provision the demo buckets and role, run this from the project directory:

```powershell
.\01_setup_infrastructure.ps1
```

The setup creates private buckets in `ap-south-1`, enables S3 Block Public
Access and SSE-S3 default encryption, and writes the generated bucket names and
role ARN to `project-config.json`. The generated file is ignored by Git.

## 🚀 Run a safe 20-object demo

<details open>
<summary><strong>Step 1 — Upload a small test range</strong></summary>

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

The uploader writes in chunks, retries transient service/network errors, and
only advances its checkpoint after **every object in a chunk succeeds**. A
permanent error stops the run and records the failed object numbers; rerun with
the same bucket, prefix, range, and checkpoint to retry that chunk. Checkpoints
are tied to the exact bucket, prefix, and range so they cannot silently resume a
different run.

</details>

<details>
<summary><strong>Step 2 — Generate and upload the matching manifest</strong></summary>

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

The generated CSV has no header and contains one `bucket,key` pair per line,
matching `S3BatchOperations_CSV_20180820`. The generator streams rows directly
to disk rather than holding a large manifest in memory.

</details>

<details>
<summary><strong>Step 3 — Review and submit a tagging job</strong></summary>

The [job template](./batch-job.json) and related JSON files are **examples**,
not ready-to-submit credentials or deployment configuration. Replace the
example source/reports bucket names, account ID, manifest ETag, and unique
client request token. For a multipart-uploaded manifest, use the exact ETag
returned by `head-object`.

Before submission, verify that:

- The manifest object is in the reports bucket and its ETag is current.
- The manifest lists exactly the objects you intend to change.
- The job's role ARN and account ID are correct.
- The role is authorized to read the manifest, tag the source objects, and
  write the completion report.
- The job operation applies the tag you intend.

After reviewing the completed template, submit it with:

```powershell
aws s3control create-job `
  --cli-input-json file://batch-job.json `
  --region $config.Region
```

Job submission changes objects in your account and may incur charges. The
template uses `"ConfirmationRequired": false`; change that setting if you want
to require an additional confirmation before a job runs.

</details>

## 🔬 Explore the job template

<details>
<summary><strong>What each template does</strong></summary>

| File | Purpose |
| --- | --- |
| [`batch-job.json`](./batch-job.json) | Complete example tagging job request and report destination |
| [`batch-trust-policy.json`](./batch-trust-policy.json) | Allows the S3 Batch Operations service to assume its IAM role |
| [`batch-permissions-policy.json`](./batch-permissions-policy.json) | Example S3 permissions for the source and reports buckets |
| [`operation.json`](./operation.json) | The `lifecycle=archive` tagging operation |
| [`manifest-config.json`](./manifest-config.json) | Manifest format and object location/ETag |
| [`report.json`](./report.json) | Successful job completion-report destination |
| [`lifecycle.json`](./lifecycle.json) | Example lifecycle transition for tagged objects |
| [`failure-*.json`](./failure-report.json) | Failed-job report and retry-manifest examples |
| [`retry-*.json`](./retry-report.json) | Example job inputs for retrying selected objects |

The PowerShell provisioning script also generates and attaches a project-
specific inline role policy. Treat the checked-in IAM JSON as examples: replace
its bucket resources with your own and review the required permissions before
using it.

</details>

## 📈 Scale up to 10 million objects

**Do not start here.** Complete the 20-object demo first and confirm the
resulting object tags and completion report. A 10-million-object run can
generate millions of requests, use significant local disk space, take
substantial time, and incur AWS charges.

When you have reviewed the cost and are ready to proceed:

```powershell
$config = Get-Content .\project-config.json -Raw | ConvertFrom-Json
$env:AWS_S3_BUCKET = $config.SourceBucket

py .\upload_10m_objects.py `
  --start-object 1 `
  --end-object 10000000 `
  --prefix production/

py .\generate_manifest.py `
  --bucket $config.SourceBucket `
  --prefix production/ `
  --start-object 1 `
  --end-object 10000000 `
  --output .\10m-manifest.csv
```

Review the script's `--help` output before changing worker or chunk settings.
The uploader creates bucket-specific checkpoint and failure files by default;
generated run state is local and excluded from Git. The 10-million-row
`10m-manifest.csv` is approximately **680 MB**, generated on demand, and
intentionally excluded from GitHub.

## 🧪 Run tests

The unit tests mock S3 calls and use temporary files; they do not create AWS
resources:

```powershell
py -m unittest -v
```

## 🗂️ Explore the project

<details>
<summary><strong>Show the project map</strong></summary>

| File or folder | What you'll find |
| --- | --- |
| [`01_setup_infrastructure.ps1`](./01_setup_infrastructure.ps1) | Setup entry point |
| [`01_setup_infrastructure_fixed.ps1`](./01_setup_infrastructure_fixed.ps1) | Bucket security, IAM, and configuration setup |
| [`upload_10m_objects.py`](./upload_10m_objects.py) | Concurrent, chunked, resumable object uploader |
| [`generate_manifest.py`](./generate_manifest.py) | Streaming manifest generator |
| [`test_upload_10m_objects.py`](./test_upload_10m_objects.py) | Mock-based uploader and manifest tests |
| [`requirements.txt`](./requirements.txt) | Python dependency |
| [`manifest.csv`](./manifest.csv) | Twenty-object sample manifest |
| [`test-data/`](./test-data/) | Small local sample object bodies |
| [`retry-demo-manifest.csv`](./retry-demo-manifest.csv) | Example manifest containing a missing key |
| [`failure-results.csv`](./failure-results.csv) | Sanitized example of a failed-object result |
| [`.gitignore`](./.gitignore) | Excludes generated manifests, checkpoints, and local configuration |

</details>

## 🩹 Troubleshooting

<details>
<summary><strong><code>Unable to locate credentials</code> or access denied</strong></summary>

Select the correct AWS profile and verify it with `aws sts get-caller-identity`.
Check that the identity has the AWS permissions needed for the step you're
running. Infrastructure setup additionally needs IAM and bucket-creation
permissions.

</details>

<details>
<summary><strong>A checkpoint doesn't match this run</strong></summary>

Check that the bucket, prefix, start object, and end object are the same ones
used to create that checkpoint. Use a new `--checkpoint` filename to begin a
separate run; do not edit a checkpoint to claim that objects succeeded.

</details>

<details>
<summary><strong>The batch job rejects the manifest</strong></summary>

Check the CSV format (no header, bucket and key columns), object ARN, manifest
ETag, bucket/region, and role permissions. Refresh the ETag after uploading or
replacing the manifest.

</details>

<details>
<summary><strong>The job reports <code>NoSuchKey</code></strong></summary>

The manifest refers to an object key that isn't present in the source bucket.
Check the bucket and exact key spelling. The included
[`retry-demo-manifest.csv`](./retry-demo-manifest.csv) deliberately lists a
missing object to demonstrate a failed-task report.

</details>

## 💸 Cost, safety, and cleanup

- The provisioning script creates real buckets and IAM resources; it does not
  roll them back automatically if a later step fails.
- The object uploader **writes/overwrites** the numbered keys it targets.
- Batch tagging changes object metadata. Confirm the manifest and tagging
  operation before submitting a job.
- S3 storage, requests, Batch Operations jobs, and reports may incur charges.
- After the demo, review the buckets and reports, then remove the created
  resources using your organization's cleanup and retention procedures. Empty
  buckets before deleting them, and do not remove reports you still need.

---

<div align="center">

**Ready to explore?** Start with [the 20-object demo](#-run-a-safe-20-object-demo).

</div>
