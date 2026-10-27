# BankGuard

**Automated Banking Data Quality Validation on AWS**

BankGuard validates transaction files from multiple banking systems, separates invalid records with explicit rejection reasons, and makes only validated data available for analytics.

**IHK Project – Cloud Data Engineering Specialist**  
**Examination date:** November 10, 2026

All data is synthetic. No real banking customer data is used.

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

**Valid records** are stored as date-partitioned Parquet files in the snapshot directory:

`snapshots/<run-id>/curated/`

These records can be queried using Amazon Athena.

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
      Date-partitioned data
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

| Validation Rule | Error Codes |
|---|---|
| Required fields must be present; empty values and `NULL` are treated as missing | `MISSING_<FIELD>`, e.g. `MISSING_AMOUNT` |
| IDs must not contain embedded control characters | `INVALID_CUSTOMER_ID`, `INVALID_ACCOUNT_ID`, `INVALID_TRANSACTION_ID` |
| IDs must be unique; the record with the lowest source line number is retained | `DUPLICATE_TRANSACTION_ID`, `DUPLICATE_ACCOUNT_ID`, `DUPLICATE_CUSTOMER_ID`, `DUPLICATE_CONFLICTING_CONTENT` |
| Amount must fit exactly into `DECIMAL(18,2)`; date must use `YYYY-MM-DD` | `INVALID_AMOUNT`, `INVALID_DATE` |
| Currency must be `EUR` or `USD`; transaction type and source system must be supported | `INVALID_CURRENCY`, `INVALID_TRANSACTION_TYPE`, `INVALID_SOURCE_SYSTEM` |
| Amount must be positive; negative amounts are allowed only for `REFUND` and `REVERSAL`; zero is rejected | `AMOUNT_NOT_ALLOWED_FOR_TYPE` |
| Transactions must reference valid accounts; accounts must reference valid customers | `ACCOUNT_NOT_FOUND`, `CUSTOMER_NOT_FOUND` |
| Account status must be `Active` or `Closed` | `INVALID_STATUS` |

### Data Integrity

Every execution must satisfy:

**Input Records = Accepted Records + Rejected Records**

This condition must also hold independently for every source system.

### Example of a Rejected Record

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

## Project Status

| Component | Status |
|---|---|
| Synthetic data generator, validation rules, processing, rework report, regression tests | **Implemented and executed locally** |
| Terraform, AWS Glue job, Athena tables, SQL | Terraform validated locally; **not yet executed on AWS** |
| Monitoring (CloudWatch, email alerts), encryption, file-triggered execution, GitHub Actions, Airflow | Written; **not yet executed** |
| AWS results, execution times, and screenshots | Pending |

Tests were executed using:

```bash
pytest -q
```

The current number of passing tests is displayed in the command output.

No AWS results are reported unless they have actually been measured.

## Quick Start

BankGuard can be executed locally using Python 3 without AWS.

### Example with 11 Records

The example contains:

- Five valid records.
- Six rejected records.
- One example for each error type.

Run:

```bash
python -m src.transform examples output_example

cat output_example/rework_report.csv
```

### Full Dataset with 50,000 Transactions

Create and activate a virtual environment:

```bash
python -m venv .venv

source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

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
pytest -q
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

**Status:** Docker has not yet been built or executed.

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
| Validation runtime | Approximately 3.6 seconds in the current local test run |

These results demonstrate that the validation rules detect every error introduced by the generator in this test.

However, they do not prove that every possible error in real banking data would be detected.

The valid-record percentages across source systems range from approximately 92.7% to 93.3%.

This similarity results from the uniform distribution of injected errors and does not represent real banking systems.

**AWS execution results are not yet available.**

## Deploying to AWS

**Not yet verified:** The following deployment procedure has not been executed. Minor adjustments may be required during the first deployment.

### Prerequisites

- AWS account
- Configured AWS CLI
- Terraform
- Permissions for Amazon S3, AWS Glue, IAM, Athena, SNS, EventBridge, and AWS Budgets

### Step 1: Initialize Terraform

```bash
cd terraform

terraform init
```

### Step 2: Format and Validate Terraform

```bash
terraform fmt

terraform validate
```

### Step 3: Deploy AWS Infrastructure

```bash
terraform apply -var="alert_email=you@example.com"
```

Confirm the AWS SNS subscription email.

### Step 4: Retrieve the S3 Bucket Name

```bash
BUCKET=$(terraform output -raw bucket_name)

cd ..
```

### Step 5: Upload the Dataset

```bash
python scripts/upload_dataset.py "$BUCKET" data
```

The upload script stores the three CSV files under a new batch ID.

Finally, it uploads:

`ready.json`

Only this readiness marker triggers the AWS Lambda function.

Previously submitted batches are identified using the AWS Glue execution history.

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

### Cleanup

First, remove snapshot catalog databases using:

```bash
python scripts/cleanup_snapshot_catalogs.py
```

Follow the instructions in:

`docs/operations.md`

Then destroy the Terraform-managed infrastructure:

```bash
cd terraform

terraform destroy
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

MWAA generates ongoing costs while the environment exists.

Instructions are available in:

`docs/operations.md`, Section 6.

## Project Structure

```text
bankguard/
│
├── src/
│   ├── generate_data.py
│   ├── validate.py
│   └── transform.py
│
├── glue/
│   └── banking_etl.py
│
├── lambda_fn/
│   └── start_glue_job.py
│
├── terraform/
│   └── modules/
│       ├── s3/
│       ├── kms/
│       ├── iam/
│       ├── glue/
│       ├── lambda/
│       ├── monitoring/
│       ├── github_oidc/
│       └── mwaa/
│
├── sql/
│   ├── daily_transactions
│   ├── country_analysis
│   ├── data_quality
│   ├── rework_report
│   └── compare_csv_vs_parquet
│
├── airflow/
│   └── dags/
│       └── bankguard_pipeline.py
│
├── scripts/
│   ├── upload_dataset.py
│   ├── current_snapshot.py
│   ├── athena_scan_compare.py
│   ├── deploy_release.py
│   └── cleanup_snapshot_catalogs.py
│
├── examples/
│
├── tests/
│   ├── test_validation.py
│   ├── test_transform.py
│   ├── test_problem_statement.py
│   └── test_lambda_trigger.py
│
├── docs/
│   ├── data-model.md
│   ├── operations.md
│   ├── ihk-plan.md
│   ├── tool-map.md
│   ├── cost-estimate.md
│   └── overview-en.md
│
├── .github/
│   └── workflows/
│       ├── ci.yml
│       └── deploy.yml
│
├── Dockerfile
├── requirements.txt
└── pytest.ini
```

### Main Components

| Directory | Purpose |
|---|---|
| `src/` | Synthetic data generation, validation, and transformation |
| `glue/` | AWS Glue ETL job |
| `lambda_fn/` | Starts the Glue job when the three files are available |
| `terraform/` | AWS infrastructure as code |
| `sql/` | Analytics and data quality SQL queries |
| `airflow/dags/` | Optional Airflow orchestration |
| `scripts/` | Dataset upload, snapshot management, deployment, and performance comparison |
| `examples/` | Example dataset with 11 records |
| `tests/` | Automated tests |
| `docs/` | Data model, operations, project planning, and tool documentation |
| `.github/workflows/` | CI/CD workflows |

## Tests

Automated tests are executed with:

```bash
pytest -q
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

### Memory Usage

The current validation process loads the relatively small demonstration datasets into memory.

Larger datasets would require distributed validation rules.

### Batch IDs

Batch IDs must not be reused.

### Deduplication

Deduplication is limited to the AWS Glue execution history.

Failed or expired executions require an intentional restart.

### AWS Verification

AWS execution and Amazon MWAA have not yet been verified.

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
| `docs/data-model.md` | Data model, architecture diagram, S3 storage areas, tables, and error codes |
| `docs/operations.md` | Automation, access permissions, monitoring, and MWAA |
| `docs/ihk-plan.md` | Project schedule and presentation plan |
| `docs/tool-map.md` | Tool inventory and verified implementation status |
| `docs/cost-estimate.md` | Cost estimation template |
| `docs/overview-en.md` | Technical project overview in English |

---

**BankGuard is an educational and demonstration project using synthetic data. It is not a production system.**