#requires -Version 5.1
$ErrorActionPreference = "Stop"

# ============================================================
# Amazon S3 Batch Operations - Phase 1 Infrastructure Setup
# Region: ap-south-1 (Mumbai)
# ============================================================

$Region = "ap-south-1"
$Project = "s3-batch-retag"
$RoleName = "S3BatchRetagJobRole"
$ScriptDir = $PSScriptRoot

Write-Host "`n=== S3 Batch Retagging Project: Infrastructure Setup ===" -ForegroundColor Cyan

# ---------- Helper ----------
function Invoke-Aws {
    param(
        [Parameter(Mandatory=$true)]
        [string[]]$Args
    )
    & aws @Args
    if ($LASTEXITCODE -ne 0) {
        throw "AWS CLI command failed: aws $($Args -join ' ')"
    }
}

# Check AWS CLI and identity
$Version = aws --version 2>&1
if ($LASTEXITCODE -ne 0) { throw "AWS CLI is not available." }
Write-Host $Version

$AccountId = (aws sts get-caller-identity --query Account --output text).Trim()
if (-not $AccountId -or $AccountId.Length -ne 12) {
    throw "Could not determine the AWS account ID."
}

# Unique suffix
$Timestamp = Get-Date -Format "yyyyMMddHHmmss"
$RandomSuffix = [guid]::NewGuid().ToString("N").Substring(0, 8)
$Suffix = "$Timestamp$RandomSuffix"
$SourceBucket  = "$Project-source-$Suffix"
$ReportsBucket = "$Project-reports-$Suffix"

Write-Host "Account: $AccountId"
Write-Host "Region : $Region"
Write-Host "Source bucket : $SourceBucket"
Write-Host "Reports bucket: $ReportsBucket"
Write-Host "IAM role      : $RoleName"

# ---------- 1. S3 buckets ----------
Write-Host "`n[1/5] Creating S3 buckets..." -ForegroundColor Yellow

Invoke-Aws @(
    "s3api","create-bucket",
    "--bucket",$SourceBucket,
    "--region",$Region,
    "--create-bucket-configuration","LocationConstraint=$Region"
)

Invoke-Aws @(
    "s3api","create-bucket",
    "--bucket",$ReportsBucket,
    "--region",$Region,
    "--create-bucket-configuration","LocationConstraint=$Region"
)

# Block public access
foreach ($Bucket in @($SourceBucket, $ReportsBucket)) {
    Invoke-Aws @(
        "s3api","put-public-access-block",
        "--bucket",$Bucket,
        "--region",$Region,
        "--public-access-block-configuration",
        "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
    )
}

# Use a JSON file for encryption configuration so PowerShell quoting cannot corrupt it.
$EncryptionPath = Join-Path $ScriptDir "s3-encryption.json"
$EncryptionJson = @'
{
  "Rules": [
    {
      "ApplyServerSideEncryptionByDefault": {
        "SSEAlgorithm": "AES256"
      }
    }
  ]
}
'@
Set-Content -Path $EncryptionPath -Value $EncryptionJson -Encoding ASCII

foreach ($Bucket in @($SourceBucket, $ReportsBucket)) {
    Invoke-Aws @(
        "s3api","put-bucket-encryption",
        "--bucket",$Bucket,
        "--region",$Region,
        "--server-side-encryption-configuration","file://$EncryptionPath"
    )
}

# ---------- 2. IAM trust policy ----------
Write-Host "`n[2/5] Creating Batch Operations IAM role..." -ForegroundColor Yellow

$TrustPolicyPath = Join-Path $ScriptDir "batch-trust-policy.json"
$TrustPolicy = @'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "batchoperations.s3.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
'@
Set-Content -Path $TrustPolicyPath -Value $TrustPolicy -Encoding ASCII

$RoleExists = $false
$null = aws iam get-role --role-name $RoleName 2>$null
if ($LASTEXITCODE -eq 0) {
    $RoleExists = $true
}

if (-not $RoleExists) {
    Invoke-Aws @(
        "iam","create-role",
        "--role-name",$RoleName,
        "--assume-role-policy-document","file://$TrustPolicyPath"
    )
    Write-Host "Created role: $RoleName"
} else {
    Write-Host "Role already exists: $RoleName"
}

# ---------- 3. IAM permissions ----------
Write-Host "`n[3/5] Attaching Batch Operations permissions..." -ForegroundColor Yellow

$PolicyPath = Join-Path $ScriptDir "batch-job-policy.json"

$PolicyObject = @{
    Version = "2012-10-17"
    Statement = @(
        @{
            Sid = "TagSourceObjects"
            Effect = "Allow"
            Action = @(
                "s3:PutObjectTagging",
                "s3:PutObjectVersionTagging"
            )
            Resource = "arn:aws:s3:::$SourceBucket/*"
        },
        @{
            Sid = "ReadSourceObjects"
            Effect = "Allow"
            Action = @(
                "s3:GetObject",
                "s3:GetObjectVersion",
                "s3:GetObjectTagging",
                "s3:GetObjectVersionTagging"
            )
            Resource = "arn:aws:s3:::$SourceBucket/*"
        },
        @{
            Sid = "ListSourceBucket"
            Effect = "Allow"
            Action = @(
                "s3:ListBucket",
                "s3:GetBucketLocation"
            )
            Resource = "arn:aws:s3:::$SourceBucket"
        },
        @{
            Sid = "ReadWriteManifestAndReports"
            Effect = "Allow"
            Action = @(
                "s3:GetObject",
                "s3:GetObjectVersion",
                "s3:PutObject"
            )
            Resource = "arn:aws:s3:::$ReportsBucket/*"
        },
        @{
            Sid = "GeneratedManifest"
            Effect = "Allow"
            Action = @(
                "s3:PutInventoryConfiguration"
            )
            Resource = "arn:aws:s3:::$SourceBucket"
        }
    )
}

$PolicyJson = $PolicyObject | ConvertTo-Json -Depth 10
Set-Content -Path $PolicyPath -Value $PolicyJson -Encoding ASCII

Invoke-Aws @(
    "iam","put-role-policy",
    "--role-name",$RoleName,
    "--policy-name","S3BatchRetagProjectPolicy",
    "--policy-document","file://$PolicyPath"
)

$RoleArn = (aws iam get-role --role-name $RoleName --query "Role.Arn" --output text).Trim()

# ---------- 4. Save configuration ----------
Write-Host "`n[4/5] Saving project configuration..." -ForegroundColor Yellow

$config = [ordered]@{
    Region = $Region
    AccountId = $AccountId
    SourceBucket = $SourceBucket
    ReportsBucket = $ReportsBucket
    RoleName = $RoleName
    RoleArn = $RoleArn
    CreatedAt = (Get-Date).ToString("o")
}

$configPath = Join-Path $ScriptDir "project-config.json"
$config | ConvertTo-Json | Set-Content -Path $configPath -Encoding UTF8

# ---------- 5. Verify ----------
Write-Host "`n[5/5] Verifying infrastructure..." -ForegroundColor Yellow

Invoke-Aws @("s3api","head-bucket","--bucket",$SourceBucket)
Invoke-Aws @("s3api","head-bucket","--bucket",$ReportsBucket)
Invoke-Aws @("iam","get-role","--role-name",$RoleName)

Write-Host "`n============================================================" -ForegroundColor Green
Write-Host "PHASE 1 COMPLETE" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host "Source bucket : $SourceBucket"
Write-Host "Reports bucket: $ReportsBucket"
Write-Host "IAM role      : $RoleArn"
Write-Host "Config file   : $configPath"
Write-Host "`nNext phase: create test objects and verify S3 access."
