# BankGuard

**Automated Banking Data Quality Validation on AWS**

BankGuard validates transaction files from multiple banking systems, separates invalid records with explicit rejection reasons, and makes only validated data available for analytics.

**IHK Project – Cloud Data Engineering Specialist**  
**Examination date:** November 10, 2026

All data is synthetic. No real banking customer data is used.

**Operational status, November 4, 2026:** Infrastructure has been deployed again and is active, including MWAA and the NAT gateway. Two MWAA runs succeeded; AWS verification confirms outputs for all three datasets. [AWS verification](docs/evidence/2026-11-04/aws-verification.json), [MWAA runs](docs/evidence/2026-11-04/airflow-runs.json). The [October 28 teardown](docs/evidence/2026-10-28/terraform-destroy/README.md) concerns the earlier deployment.

## Table of Contents

1. [The Problem](#the-problem)
2. [The Solution](#the-solution)
3. [Validation Rules](#validation-rules)
4. [Project Status](#project-status)
5. [Quick Start](#quick-start)
6. [Results](#results)
7. [Deploying to AWS](#deploying-to-aws)
8. [Project Structure](#project-structure)
9. [Tests](#tests)
10. [Known Limitations](#known-limitations)
11. [Tools](#tools)
12. [Additional Documentation](#additional-documentation)

## The Problem

Banks receive transaction files from multiple systems. These files may contain missing values, duplicate IDs, incorrect dates, invalid amounts, and references to accounts that do not exist.

Without validation, these data quality issues can distort financial reports and create additional manual work.

**Project Question:**

How can a bank automatically detect and isolate invalid transaction data before it enters an analytics platform?

## The Solution

BankGuard processes three CSV files:

- `customers.csv`
- `accounts.csv`
- `transactions.csv`

Each record is validated and classified.

**Valid records** are stored as Parquet files in the snapshot directory:

`snapshots/<run-id>/curated/`

Transactions are partitioned by date. Customers and accounts are stored without date partitions. These records can be queried using Amazon Athena.

**Invalid records** are stored with **all rejection reasons** in:

`snapshots/<run-id>/rejected/`

No record is silently discarded.

**Original files** remain unchanged in Amazon S3:

`raw/batches/<id>/`

Previous S3 object versions are deleted after 30 days according to the lifecycle configuration.

### Architecture

```text
customers.csv   accounts.csv   transactions.csv
                       |
                       v
                    S3 raw/
                 Original files
                       |
                       v
              AWS Glue (Python)
                src/validate.py
                       |
             +---------+---------+
             |                   |
             v                   v
      Snapshot curated/   Snapshot rejected/
             |
             v
      published/current.json
             |
             v
      Published Snapshot
             |
             v
      Transactions partitioned by date
             |
             v
       Glue Data Catalog
             |
             v
       Amazon Athena
         SQL Reports
```

AWS Glue writes separate outputs and a dedicated catalog database for each execution.

Only after successful completion does `published/current.json` switch to the new snapshot.

A receipt stored at:

`runs/<Glue-Run-ID>/metrics.json`

confirms the specific execution.

If snapshot preparation fails, the previously published dataset remains unchanged.

### Validation Order

Validation follows a dependency-based sequence:

1. Customers are validated first.
2. Accounts are validated against valid customers.
3. Transactions are validated against valid accounts.

The same `src/validate.py` module is used by both local tests and the AWS Glue job.

This ensures that the tests validate the same code that runs in the cloud.

### Source Systems

Each transaction identifies its originating system:

- `CORE_BANKING`
- `CARDS`
- `ONLINE_BANKING`

Validation results and rework requirements are reported separately for each source system.

## Validation Rules

A record may contain multiple errors.

All detected errors are stored in the `error_reasons` field.

CSV rows with an unexpected number of columns receive:

`INVALID_COLUMN_COUNT`

Additional values are preserved.

Invalid CSV headers and CSV syntax errors explicitly stop the execution.

`customers.csv` supports the optional `email` and `tel` columns. Older customer files without these columns remain valid. Populated values must match the email or telephone format rules; missing contact values are stored as empty strings in accepted outputs. See the [data model](docs/data-model.md) for the exact formats.

| Validation Rule | Error Codes |
|---|---|
| Required fields must be present; empty values and `NULL` are treated as missing | `MISSING_<FIELD>`, e.g. `MISSING_AMOUNT` |
| IDs must not contain embedded ASCII/Unicode control characters (category `Cc`) | `INVALID_CUSTOMER_ID`, `INVALID_ACCOUNT_ID`, `INVALID_TRANSACTION_ID` |
| IDs must be unique; the record with the lowest source line number is validated on its own merits; later occurrences are rejected even if the original is invalid. Conflicting transaction contents additionally receive `DUPLICATE_CONFLICTING_CONTENT` | `DUPLICATE_TRANSACTION_ID`, `DUPLICATE_ACCOUNT_ID`, `DUPLICATE_CUSTOMER_ID`, `DUPLICATE_CONFLICTING_CONTENT` |
| Amount must fit exactly into `DECIMAL(18,2)`; date must use `YYYY-MM-DD` | `INVALID_AMOUNT`, `INVALID_DATE` |
| Currency must be `EUR` or `USD`; transaction type and source system must be supported | `INVALID_CURRENCY`, `INVALID_TRANSACTION_TYPE`, `INVALID_SOURCE_SYSTEM` |
| Amount must be positive; negative amounts are allowed only for `REFUND` and `REVERSAL`; zero is rejected | `AMOUNT_NOT_ALLOWED_FOR_TYPE` |
| Transactions must reference valid accounts; accounts must reference valid customers | `ACCOUNT_NOT_FOUND`, `CUSTOMER_NOT_FOUND` |
| Account status must be `Active` or `Closed` | `INVALID_STATUS` |
| Populated customer contacts must satisfy the format rules | `INVALID_EMAIL`, `INVALID_TEL` |

### Data Integrity

Every execution must satisfy:

**Input Records = Accepted Records + Rejected Records**

This condition must also hold independently for every dataset and source system. The top-level counts in `metrics.json` and `by_source_system` cover transactions. `by_dataset` provides separate customer, account and transaction metrics; `overall` combines all three. Any reported pass rate must match its counts.

### Excerpt from a Rejected Record

```json
{
  "transaction_id": "T0000123",
  "account_id": "A90012",
  "amount": "200.00",
  "source_system": "ONLINE_BANKING",
  "error_reasons": ["ACCOUNT_NOT_FOUND"],
  "line": 124,
  "run_id": "2026-10-28T10-00-00Z",
  "source_file": "transactions.csv"
}
```

### Rework Report

BankGuard generates:

`rework_report.csv`

For each source system and rejection reason, the report contains:

- The number of affected records.
- Up to five example IDs.

This helps each source system identify which data needs correction.

Locally, `dataset_rework_report.csv` additionally covers errors across all three datasets, with the dataset name, rejection reason and example IDs. Glue writes this combined report to `snapshots/<run-id>/dataset_rework_report.json`.

CloudWatch receives separate metrics for each dataset and the overall result, scoped by `JobName` and `Dataset`. Customers, accounts and transactions each have their own quality alarm, alongside an overall alarm. Empty datasets emit zero counts and no pass rate.

## Project Status

**Status on November 4, 2026:** 378 tests passed locally and in the current Docker image. The current version is deployed on AWS. Verification confirms customers, accounts and transactions through twelve Athena queries and a CSV/Parquet comparison; two MWAA runs succeeded. The SNS email subscription is confirmed; delivery of the new quality alarms has not yet been retested. Earlier CI and alarm evidence retains its stated date and version context.

| Component | Status |
|---|---|
| Synthetic data generator, validation rules, processing, rework report, regression tests | **Implemented and executed locally** |
| Terraform, AWS Glue job, Athena tables, SQL | **Redeployed and verified on AWS on November 4, 2026** |
| CloudWatch, SNS, KMS and file-triggered execution | Deployed on November 4; automatic batch run succeeded. EventBridge/SNS alarm delivery was verified on October 28 and has not been retested in the new deployment |
| GitHub Actions (historical evidence) | [CI passed](https://github.com/gholipourmarya/BankGuard-AWS-Data-Engineering/actions/runs/40662802001); [OIDC deployment passed](https://github.com/gholipourmarya/BankGuard-AWS-Data-Engineering/actions/runs/40739499524) on `feature/bankguard` |
| Airflow / MWAA | **Two successful runs on November 4, 2026**, Airflow 3.3.1; [evidence](docs/evidence/2026-11-04/airflow-runs.json) |
| AWS evidence | [Current verification of all three datasets and twelve Athena queries](docs/evidence/2026-11-04/aws-verification.json); [historical measurements](docs/evidence/2026-10-28/aws-verification.json) |

Tests were executed using:

```bash
python -m pytest -q
```

The current number of passing tests is displayed in the command output.

No AWS results are reported unless they have actually been measured.

## Quick Start

BankGuard can be executed locally without AWS. Python 3.12 is recommended, matching CI and Docker. Run all commands from the project root. Processing the small example requires only the Python standard library; install the dependencies below before running tests or AWS scripts.

### Example with 11 Transactions

The example contains:

- Five valid records.
- Six rejected records.
- Examples of six selected error types.

The [example guide](examples/README.md) identifies each invalid line and its expected outputs.

Run:

```bash
python -m src.transform examples output_example

cat output_example/rework_report.csv
cat output_example/dataset_rework_report.csv
```

### Full Dataset with 50,000 Transactions

Create and activate a virtual environment:

```bash
python -m venv .venv

source .venv/bin/activate
```

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

`requirements.txt` uses the fixed package versions in `requirements.lock` for local tests, CI and Docker. The Docker build also pins the Python base image by digest. Version updates are checked deliberately with the tests. Optional Airflow has separate constraints and must be installed in its own environment; see [operations](docs/operations.md#6-optionales-airflow-und-mwaa).

Generate synthetic data:

```bash
python -m src.generate_data
```

This generates:

```text
data/*.csv
data/injected_errors.json
```

Process and validate the dataset:

```bash
python -m src.transform data output
```

The command validates the data, writes the results to `output/`, and displays processing metrics.

Run automated tests:

```bash
python -m pytest -q
```

### Data Generator Options

Available parameters:

| Parameter | Description |
|---|---|
| `--transactions N` | Number of transactions |
| `--seed N` | Random seed for reproducibility |
| `--out FOLDER` | Output directory |

The generator records which errors were intentionally introduced.

The tests use this information to measure whether the validation process detects all injected errors.

### Docker

```bash
docker build -t bankguard .

docker run --rm bankguard
```

**Status on November 4, 2026:** The current image was built and executed successfully: 378 tests and full data processing passed. It uses a pinned base image and dependency lock; local Python backups, environments and Terraform files containing local state are excluded.

## Results

The following results were obtained from a local execution using 51,000 transactions and random seed 42.

| Metric | Result |
|---|---|
| Input transactions | 51,000 (50,000 + 1,000 injected duplicates) |
| Accepted records | 47,500 |
| Rejected records | 3,500 |
| Valid record percentage | 93.14% |
| Injected errors | 3,750 |
| Detected errors | 3,750 |
| Missed errors | 0 |
| False positives | 0 |
| Validation runtime | Approximately 3.6 seconds in an earlier local measurement; depends on version and environment |

The table covers transactions. With an additional 2,500 valid customers and 2,500 valid accounts, the combined pass rate (`overall`) is 93.75%.

These results demonstrate that the validation rules detect every error introduced by the generator in this test.

However, they do not prove that every possible error in real banking data would be detected.

The valid-record percentages across source systems range from approximately 92.7% to 93.3%.

This similarity results from the uniform distribution of injected errors and does not represent real banking systems.

**AWS results, October 28, 2026:** 51,000 input, 47,500 accepted, 3,500 rejected; Glue execution time 135 seconds using two G.1X workers. Athena confirmed the counts. See [evidence](docs/evidence/2026-10-28/aws-verification.json) and [costs](docs/cost-estimate.md).

Historical revalidation: the ETL release from commit `80af12b` was deployed and verified again on October 28, 2026. A full batch with one additional deliberately invalid Unicode ID (`U+0085`) produced **51,001 input = 47,500 accepted + 3,501 rejected**, with **127 seconds** of Glue execution time. The MWAA DAG also succeeded with **1 input, 0 accepted, 1 rejected** and four successful tasks. Both snapshots passed Athena verification; the format comparison was skipped for the zero-accepted snapshot. **243 local tests passed at that time.** See [evidence from this revalidation](docs/evidence/2026-10-28/revalidation-80af12b/summary.json) and [full query results](docs/evidence/2026-10-28/revalidation-80af12b/full-verification.json). Older measurements and images retain their original version context.

## Deploying to AWS

Last deployed and verified in `eu-central-1` on November 4, 2026. Run all commands below from the project root. The helper `scripts/aws_terraform.py` makes a valid `aws login` session available to the pinned Terraform provider 5.x and supports credential renewal during long MWAA creations; see [operations](docs/operations.md).

### Prerequisites

- Python environment with `requirements.txt` installed, as described in Quick Start
- Generated input files under `data/`
- AWS account and a valid AWS CLI session
- Terraform 1.5 or later
- Permissions for the resources defined in `terraform/`, including S3, Glue, IAM, KMS, Lambda, CloudWatch, Athena, SNS, EventBridge and Budgets; optional MWAA also needs its network resources

### Step 1: Initialize Terraform

```bash
terraform -chdir=terraform init
```

### Step 2: Format and Validate Terraform

```bash
terraform -chdir=terraform fmt

terraform -chdir=terraform validate
```

### Step 3: Deploy AWS Infrastructure

```bash
python scripts/aws_terraform.py plan -var="alert_email=you@example.com"
python scripts/aws_terraform.py apply -var="alert_email=you@example.com"
```

Confirm the AWS SNS subscription email.

### Step 4: Retrieve the S3 Bucket Name

```bash
BUCKET=$(terraform -chdir=terraform output -raw bucket_name)
```

### Step 5: Upload the Dataset

```bash
python scripts/upload_dataset.py "$BUCKET" data
```

The upload script stores the three CSV files under a new batch ID.

Finally, it uploads:

`ready.json`

Only this readiness marker triggers the AWS Lambda function.

Previously submitted batches are identified using the AWS Glue execution history. A permanent conditional S3 claim under `submissions/` prevents concurrent automatic submissions of the same batch.

Additional batches are queued in AWS Glue.

For details about execution and retries, see:

`docs/operations.md`

### Step 6: Identify the Published Snapshot

After successful AWS Glue execution, run:

```bash
python scripts/current_snapshot.py "$BUCKET"
```

This identifies the currently published database.

Execute the SQL queries from `sql/` against that database.

The Athena workgroup is provided by Terraform.

The Terraform-created database is used only as a schema template.

### Updating an Existing Installation

The changed quality alarms and dashboard require Terraform apply. Update the ETL code as a separate release; Terraform preserves the previously selected code version. From the project root:

```bash
GLUE_JOB=$(terraform -chdir=terraform output -raw glue_job_name)
python scripts/deploy_release.py "$BUCKET" "$GLUE_JOB"
```

The release adds contact columns when creating new snapshot catalogs, including when the schema template is older. For new receipts, the verification script also checks customer and account outputs; `verified_datasets` identifies the datasets checked. See [operations](docs/operations.md).

### Cleanup

First, stop active Glue runs or allow them to finish. From the project root, list the snapshot catalog databases, then remove them:

```bash
TEMPLATE_DB=$(terraform -chdir=terraform output -raw glue_database)
python scripts/cleanup_snapshot_catalogs.py "$BUCKET" "$TEMPLATE_DB" --include-published
# After reviewing the list, delete it including the published catalog:
python scripts/cleanup_snapshot_catalogs.py "$BUCKET" "$TEMPLATE_DB" --delete --include-published
```

Follow the instructions in:

`docs/operations.md`

Then destroy the Terraform-managed infrastructure:

```bash
python scripts/aws_terraform.py destroy
```

The S3 bucket and Athena workgroup are deleted together with their contents because:

`force_destroy = true`

This behavior is intentional for synthetic test data.

**Do not use this configuration for projects containing real data.**

### Optional Amazon MWAA

Amazon Managed Workflows for Apache Airflow (MWAA) is optional and disabled by default.

Enable it with:

```hcl
enable_mwaa = true
```

The earlier demo infrastructure was removed on October 28, 2026. On November 4, `bankguard-airflow` was deployed again and verified with two successful DAG runs; this deployment is active. The manual DAG expects all three CSV files directly under `raw/`. MWAA and the NAT gateway generate ongoing costs while they exist.

Instructions are available in:

`docs/operations.md`, Section 6.

## Project Structure

```text
BankGuard-AWS-Data-Engineering/
├── src/
│   ├── __init__.py
│   ├── generate_data.py
│   ├── snapshot.py
│   ├── transform.py
│   └── validate.py
├── glue/
│   └── banking_etl.py
├── lambda_fn/
│   ├── __init__.py
│   ├── check_glue_runs.py
│   └── start_glue_job.py
├── terraform/
│   ├── github_oidc.tf
│   ├── glue.tf
│   ├── iam.tf
│   ├── kms.tf
│   ├── lambda.tf
│   ├── main.tf
│   ├── monitoring.tf
│   ├── mwaa.tf
│   ├── outputs.tf
│   ├── run_monitor.tf
│   ├── s3.tf
│   ├── variables.tf
│   └── .terraform.lock.hcl
├── sql/
│   ├── compare_csv_vs_parquet.sql
│   ├── country_analysis.sql
│   ├── daily_transactions.sql
│   ├── data_quality.sql
│   └── rework_report.sql
├── airflow/
│   ├── dags/
│   │   └── bankguard_pipeline.py
│   ├── requirements.lock
│   └── requirements.txt
├── scripts/
│   ├── athena_scan_compare.py
│   ├── aws_terraform.py
│   ├── cleanup_snapshot_catalogs.py
│   ├── current_snapshot.py
│   ├── deploy_release.py
│   ├── upload_dataset.py
│   └── verify_aws.py
├── examples/
│   ├── README.md
│   ├── accounts.csv
│   ├── customers.csv
│   └── transactions.csv
├── tests/
│   ├── __init__.py
│   ├── test_aws_scripts.py
│   ├── test_customer_contacts.py
│   ├── test_glue_output.py
│   ├── test_lambda_trigger.py
│   ├── test_problem_statement.py
│   ├── test_quality_monitoring.py
│   ├── test_release_and_monitor.py
│   ├── test_sdk_contract.py
│   ├── test_snapshot_publication.py
│   ├── test_sql_reports.py
│   ├── test_terraform_credentials.py
│   ├── test_transform.py
│   └── test_validation.py
├── docs/
│   ├── cost-estimate.md
│   ├── data-model.md
│   ├── ihk-plan.md
│   ├── operations.md
│   ├── overview-en.md
│   ├── structure.md
│   ├── tool-map.md
│   └── evidence/
├── .github/
│   └── workflows/
│       ├── ci.yml
│       └── deploy.yml
├── .dockerignore
├── .gitignore
├── Dockerfile
├── README.md
├── README_ENG.md
├── requirements.lock
├── requirements.txt
└── pytest.ini
```

This overview shows source code and project documentation. Generated data, outputs, local environments, and Terraform state are omitted.

### Main Components

| Directory | Purpose |
|---|---|
| `src/` | Synthetic data generation, validation, and transformation |
| `glue/` | AWS Glue ETL job |
| `lambda_fn/` | Starts Glue after a batch readiness marker and checks ERROR/EXPIRED runs on a schedule |
| `terraform/` | AWS infrastructure as code |
| `sql/` | Analytics and data quality SQL queries |
| `airflow/dags/` | Optional Airflow orchestration |
| `scripts/` | Dataset upload, snapshot management, deployment, and performance comparison |
| `examples/` | Example dataset with 11 transactions plus customers and accounts |
| `tests/` | Automated tests |
| `docs/` | Data model, operations, project planning, and tool documentation |
| `.github/workflows/` | CI/CD workflows |

## Tests

Automated tests are executed with:

```bash
python -m pytest -q
```

The current number of tests is displayed in the output.

The file:

`tests/test_problem_statement.py`

contains a test for each statement in the problem description.

The tests verify the following requirements.

### Expected Example Results

The dataset in `examples/` produces exactly the expected results.

### No Data Loss

Every input record appears exactly once in either the accepted or rejected dataset.

### Valid Data Only

Analytics reports contain only valid records.

They must not contain:

- Duplicate IDs.
- References to unknown accounts.
- Invalid amounts.
- Invalid dates.

### Rejection Reasons

Every rejected record contains at least one rejection reason.

### Source System Consistency

For every source system:

**Input = Accepted + Rejected**

### Reproducibility

Processing the same input data twice produces the same results.

Only execution-specific metadata, such as `run_id` and timestamps, may differ.

## Known Limitations

### Snapshot Publication

The published snapshot is selected using a single S3 object.

This reference must be resolved once, and the selected snapshot database must be used for all related queries.

Previous snapshots and their catalog databases remain available until explicitly cleaned up.

### Validation Scope

Accounts with status `Closed` remain valid under the current rules. Transaction dates are not checked against an account closure date. Contact validation checks syntax only and does not confirm reachability.

### Memory Usage

The current validation process loads the relatively small demonstration datasets into memory.

Larger datasets would require distributed validation rules.

### Batch IDs

Batch IDs must not be reused.

### Deduplication

Permanent conditional S3 claims prevent concurrent automatic submissions of the same batch. Interrupted or ambiguous submissions retain their claim and require operator review before an intentional restart. Legacy submissions without a claim are protected only while their Glue history remains available.

Failed or expired executions require an intentional restart.

### AWS Verification

The central AWS pipeline and two manual MWAA runs were verified on November 4, 2026. Long-term operation and load behavior remain unmeasured. The historical SNS metric confirms delivery in the October 28 deployment. Alarm delivery in the new deployment and personal inbox receipt require separate verification.

### Code Releases

Code releases are stored under content-hash-based identifiers.

The AWS Glue script and validation library are switched together only after both have been uploaded successfully.

### Glue Status Monitoring

An additional monitoring process checks the AWS Glue states:

- `ERROR`
- `EXPIRED`

The check runs every five minutes.

### CSV and Parquet Comparison

The format comparison uses the same valid records and verifies:

- Record counts.
- Aggregated amounts.

It compares CSV with date-partitioned Parquet.

Therefore, the comparison measures the combined effects of file format and partitioning rather than isolating each factor.

Instructions are available in:

`docs/cost-estimate.md`

## Tools

The core technologies are:

| Technology | Purpose |
|---|---|
| Python | Data processing and validation |
| Pytest | Automated testing |
| boto3 | AWS SDK for Python |
| Terraform | Infrastructure as Code |
| Amazon S3 | Data storage |
| AWS Glue | ETL processing |
| AWS Glue Data Catalog | Metadata management |
| Amazon Athena | SQL analytics |

Additional technologies support testing, deployment, monitoring, and demonstration:

| Technology | Purpose |
|---|---|
| AWS Lambda | Event-driven pipeline execution |
| Amazon CloudWatch | Logging and monitoring |
| Amazon SNS | Email alerts |
| Amazon EventBridge | Event processing and scheduled checks |
| AWS KMS | Encryption |
| AWS Budgets | Cost monitoring |
| GitHub Actions | CI/CD automation |
| Docker | Containerization |
| Apache Airflow / Amazon MWAA | Optional workflow orchestration |

The implementation and verification status of each tool is documented in:

`docs/tool-map.md`

## Additional Documentation

| Document | Description |
|---|---|
| `docs/data-model.md` | Data model, S3 storage areas, tables, and error codes |
| `docs/operations.md` | Automation, access permissions, monitoring, and MWAA |
| `docs/ihk-plan.md` | Project schedule and presentation plan |
| `docs/tool-map.md` | Tool inventory and verified implementation status |
| [docs/cost-estimate.md](docs/cost-estimate.md) | Historical measurements, cost calculation and worksheet for further usage |
| `docs/overview-en.md` | Technical project overview in English |

---

**BankGuard is an educational and demonstration project using synthetic data. It is not a production system.**
