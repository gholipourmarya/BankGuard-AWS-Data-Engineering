"""Run Terraform with temporary credentials exported by the current AWS CLI session.

Usage: python scripts/aws_terraform.py plan -input=false
Supports AWS CLI login sessions even when the pinned Terraform provider does not.
Credentials stay in the child environment and are never printed or saved.
"""
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python scripts/aws_terraform.py <terraform command> [options]")
    result = subprocess.run(
        ["aws", "configure", "export-credentials", "--format", "process"],
        check=True, capture_output=True, text=True,
    )
    credentials = json.loads(result.stdout)
    environment = os.environ.copy()
    for field, variable in (
        ("AccessKeyId", "AWS_ACCESS_KEY_ID"),
        ("SecretAccessKey", "AWS_SECRET_ACCESS_KEY"),
        ("SessionToken", "AWS_SESSION_TOKEN"),
    ):
        if credentials.get(field):
            environment[variable] = credentials[field]
        else:
            environment.pop(variable, None)
    root = Path(__file__).resolve().parents[1]
    return subprocess.call(
        ["terraform", f"-chdir={root / 'terraform'}", *sys.argv[1:]], env=environment,
    )


if __name__ == "__main__":
    sys.exit(main())
