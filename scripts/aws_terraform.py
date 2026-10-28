"""Run Terraform with renewable credentials from the current AWS CLI session.

Usage: python scripts/aws_terraform.py plan -input=false
Supports AWS CLI login sessions even when the pinned Terraform provider does not.
Terraform requests fresh credentials through credential_process when needed.
The temporary configuration contains only a command, never access keys.
"""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile


def run_terraform(arguments, environment, root):
    # Explicit environment credentials already work with the provider natively.
    if environment.get("AWS_ACCESS_KEY_ID"):
        return subprocess.call(
            ["terraform", f"-chdir={root / 'terraform'}", *arguments], env=environment,
        )
    source_profile = environment.get("AWS_PROFILE") or environment.get("AWS_DEFAULT_PROFILE") or "default"
    source_config = environment.get("AWS_CONFIG_FILE") or str(Path.home() / ".aws/config")
    # Keep this file alive until Terraform exits so the provider can refresh.
    with tempfile.TemporaryDirectory(prefix="bankguard-terraform-") as directory:
        config = Path(directory) / "config"
        child_environment = environment.copy()
        child_environment.update({"AWS_CONFIG_FILE": str(config), "AWS_PROFILE": "bankguard-process",
                                  "AWS_DEFAULT_PROFILE": "bankguard-process", "AWS_SDK_LOAD_CONFIG": "1"})
        # A credentials file must not override the process profile by coincidence.
        credentials = Path(directory) / "credentials"
        credentials.write_text("")
        child_environment["AWS_SHARED_CREDENTIALS_FILE"] = str(credentials)
        # The CLI subprocess needs the original credentials file, not this empty one.
        original_credentials = environment.get("AWS_SHARED_CREDENTIALS_FILE") or str(Path.home() / ".aws/credentials")
        command = shlex.join([
            shutil.which("env") or "env", f"AWS_CONFIG_FILE={source_config}",
            f"AWS_SHARED_CREDENTIALS_FILE={original_credentials}",
            f"AWS_PROFILE={source_profile}", f"AWS_DEFAULT_PROFILE={source_profile}",
            shutil.which("aws") or "aws", "configure", "export-credentials",
            "--profile", source_profile, "--format", "process",
        ])
        config.write_text(f"[profile bankguard-process]\ncredential_process = {command}\n")
        return subprocess.call(
            ["terraform", f"-chdir={root / 'terraform'}", *arguments], env=child_environment,
        )


def main():
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python scripts/aws_terraform.py <terraform command> [options]")
    root = Path(__file__).resolve().parents[1]
    return run_terraform(sys.argv[1:], os.environ.copy(), root)


if __name__ == "__main__":
    sys.exit(main())
