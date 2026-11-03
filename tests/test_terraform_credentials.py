"""Long Terraform operations must be able to request fresh CLI credentials."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from scripts import aws_terraform


@pytest.mark.parametrize("profile", ["default", "workshop"])
def test_process_can_refresh_without_recursing_into_temporary_profile(tmp_path, monkeypatch, profile):
    source_config = tmp_path / "original config"
    source_config.write_text("original profile settings")
    source_credentials = tmp_path / "original credentials"
    source_credentials.write_text("original credentials settings")
    counter = tmp_path / "calls"
    fake_aws = tmp_path / "fake aws"
    fake_aws.write_text(f"#!{sys.executable}\n" + '''import json, os, pathlib, sys
assert os.environ["AWS_CONFIG_FILE"] == os.environ["EXPECTED_CONFIG"]
assert os.environ["AWS_SHARED_CREDENTIALS_FILE"] == os.environ["EXPECTED_CREDENTIALS"]
assert os.environ["AWS_PROFILE"] == os.environ["EXPECTED_PROFILE"]
assert sys.argv[1:] == ["configure", "export-credentials", "--profile", os.environ["EXPECTED_PROFILE"], "--format", "process"]
p = pathlib.Path(os.environ["COUNTER"])
n = int(p.read_text()) + 1 if p.exists() else 1
p.write_text(str(n))
print(json.dumps({"Version": 1, "AccessKeyId": f"test-{n}", "SecretAccessKey": "fake", "SessionToken": "fake", "Expiration": "2099-01-01T00:00:00Z"}))
''')
    fake_aws.chmod(0o700)
    which = aws_terraform.shutil.which
    monkeypatch.setattr(aws_terraform.shutil, "which", lambda name: str(fake_aws) if name == "aws" else which(name))
    environment = {**os.environ, "AWS_PROFILE": profile, "AWS_CONFIG_FILE": str(source_config),
                   "AWS_SHARED_CREDENTIALS_FILE": str(source_credentials),
                   "EXPECTED_CONFIG": str(source_config), "EXPECTED_CREDENTIALS": str(source_credentials),
                   "EXPECTED_PROFILE": profile, "COUNTER": str(counter)}
    environment.pop("AWS_ACCESS_KEY_ID", None)
    temporary_paths = []

    def terraform(command, env):
        assert command[-1] == "plan"
        config = Path(env["AWS_CONFIG_FILE"])
        temporary_paths.append(config)
        process = config.read_text().split("credential_process = ", 1)[1].strip()
        # The SDK can invoke this again after its first token expires.
        results = [json.loads(subprocess.check_output(shlex.split(process), env=env, text=True))
                   for _ in range(2)]
        assert [result["AccessKeyId"] for result in results] == ["test-1", "test-2"]
        assert "SecretAccessKey" not in config.read_text()
        assert Path(env["AWS_SHARED_CREDENTIALS_FILE"]).read_text() == ""
        return 0

    monkeypatch.setattr(aws_terraform.subprocess, "call", terraform)
    assert aws_terraform.run_terraform(["plan"], environment, tmp_path) == 0
    assert source_config.read_text() == "original profile settings"
    assert source_credentials.read_text() == "original credentials settings"
    assert all(not path.exists() for path in temporary_paths)


def test_explicit_environment_credentials_keep_native_provider_auth(tmp_path, monkeypatch):
    environment = {"AWS_ACCESS_KEY_ID": "fake", "AWS_SECRET_ACCESS_KEY": "fake"}
    calls = []
    monkeypatch.setattr(aws_terraform.subprocess, "call", lambda command, env: calls.append((command, env)) or 7)
    assert aws_terraform.run_terraform(["plan"], environment, tmp_path) == 7
    assert calls[0][1] == environment
