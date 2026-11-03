import json
import sys
from unittest.mock import Mock

import pytest

from scripts import athena_scan_compare as compare, upload_dataset, verify_aws


def test_uploader_releases_marker_only_after_all_three_files(tmp_path):
    for name in upload_dataset.FILES:
        (tmp_path / name).write_text("header\n")
    s3 = Mock()
    prefix = upload_dataset.upload_dataset(s3, "bucket", tmp_path)
    assert [call[0] for call in s3.method_calls] == ["upload_file"] * 3 + ["put_object"]
    assert [call.args[2] for call in s3.upload_file.call_args_list] == [
        f"{prefix}/{name}" for name in upload_dataset.FILES]
    assert s3.put_object.call_args.kwargs["Key"] == f"{prefix}/ready.json"
    assert upload_dataset.upload_dataset(Mock(), "bucket", tmp_path) != prefix


def test_failed_upload_never_releases_marker(tmp_path):
    for name in upload_dataset.FILES:
        (tmp_path / name).write_text("header\n")
    s3 = Mock()
    s3.upload_file.side_effect = RuntimeError("upload failed")
    with pytest.raises(RuntimeError):
        upload_dataset.upload_dataset(s3, "bucket", tmp_path)
    s3.put_object.assert_not_called()


def test_missing_local_file_prevents_any_upload(tmp_path):
    s3 = Mock()
    with pytest.raises(FileNotFoundError):
        upload_dataset.upload_dataset(s3, "bucket", tmp_path)
    assert s3.method_calls == []


def test_comparison_checks_equal_results(monkeypatch):
    monkeypatch.setattr(compare, "run", Mock(side_effect=[({}, [("currency",), ("EUR",)]),
                                                         ({}, [("currency",), ("USD",)])]))
    with pytest.raises(RuntimeError, match="differ"):
        compare.compare(Mock(), "2026-03-04", "custom", "custom")


def test_empty_comparison_is_not_reported_as_measurement(monkeypatch):
    monkeypatch.setattr(compare, "run", Mock(return_value=({}, [("currency",)])))
    with pytest.raises(RuntimeError, match="No transactions"):
        compare.compare(Mock(), "2026-03-04", "custom", "custom")


def test_queries_reject_date_injection():
    with pytest.raises(ValueError):
        compare.build_queries("2026-03-04' OR TRUE --")


def test_athena_uses_selected_database_and_disables_cache():
    athena = Mock()
    athena.start_query_execution.return_value = {"QueryExecutionId": "q1"}
    athena.get_query_execution.return_value = {"QueryExecution": {
        "Status": {"State": "SUCCEEDED"}, "Statistics": {"DataScannedInBytes": 100}}}
    athena.get_paginator.return_value.paginate.return_value = [{"ResultSet": {"Rows": []}}]
    stats, rows = compare.run(athena, "SELECT 1", "custom-group", "custom-db")
    assert stats["DataScannedInBytes"] == 100
    call = athena.start_query_execution.call_args.kwargs
    assert call["QueryExecutionContext"] == {"Database": "custom-db"}
    assert call["ResultReuseConfiguration"]["ResultReuseByAgeConfiguration"]["Enabled"] is False


def test_athena_timeout_cancels_query(monkeypatch):
    athena = Mock()
    athena.start_query_execution.return_value = {"QueryExecutionId": "q1"}
    athena.get_query_execution.return_value = {"QueryExecution": {"Status": {"State": "RUNNING"}}}
    monkeypatch.setattr(compare.time, "monotonic", Mock(side_effect=[0, 301]))
    with pytest.raises(TimeoutError):
        compare.run(athena, "SELECT 1", "group", "database")
    athena.stop_query_execution.assert_called_once_with(QueryExecutionId="q1")


def prepare_snapshot_verification(monkeypatch, accepted, rejected, day):
    receipt = {"database": "template_jr_test", "run_id": "jr_test", "counts_consistent": True,
               "input_prefix": "raw", "data_prefix": "snapshots/jr_test/",
               "input_count": accepted + rejected, "accepted_count": accepted,
               "rejected_count": rejected, "by_source_system": {"CARDS": {
                   "input_count": accepted + rejected, "accepted_count": accepted,
                   "rejected_count": rejected, "counts_consistent": True}}}
    monkeypatch.setattr(verify_aws, "read_published_snapshot", lambda *_: receipt)
    glue = Mock()
    glue.get_job_run.return_value = {"JobRun": {"Id": "jr_test", "JobName": "job",
                                              "JobRunState": "SUCCEEDED", "Arguments": {}}}
    glue.get_job.return_value = {"Job": {"DefaultArguments": {
        "--bucket": "bucket", "--database": "template", "--input_prefix": "raw"}}}
    monkeypatch.setattr(verify_aws, "build_queries", lambda _: {"CSV": "FORMAT_CSV", "Parquet": "FORMAT_PARQUET"})

    def query(athena, sql, workgroup, database):
        if "AS accepted" in sql:
            return {}, [("accepted", "rejected"), (str(accepted), str(rejected))]
        if "MIN(transaction_date)" in sql:
            return {}, [("day",), (day,)]
        if sql.startswith("FORMAT_"):
            return {"DataScannedInBytes": 100}, [("currency", "count", "amount"), ("EUR", "1", "1.00")]
        if "SELECT source_system, COUNT(*) FROM curated_transactions" in sql:
            return {}, [("source_system", "count")] + ([("CARDS", str(accepted))] if accepted else [])
        if "SELECT source_system, COUNT(*) FROM rejected_transactions" in sql:
            return {}, [("source_system", "count")] + ([("CARDS", str(rejected))] if rejected else [])
        return {}, []

    monkeypatch.setattr(verify_aws, "run", query)
    return glue


@pytest.mark.parametrize("accepted,rejected", [(0, 1), (0, 0), (1, 0)])
def test_verification_preserves_valid_snapshots_without_fabricating_empty_measurements(monkeypatch, accepted, rejected):
    day = "2026-01-01" if accepted else None
    glue = prepare_snapshot_verification(monkeypatch, accepted, rejected, day)
    report = verify_aws.verify(Mock(), glue, Mock(), "bucket", "job", "group")
    assert report["counts_verified"] is True
    assert report["comparison_day"] == day
    if accepted:
        assert report["comparison_status"] == "completed"
        assert {"CSV", "Parquet"} <= report["queries"].keys()
    else:
        assert report["comparison_status"] == "skipped"
        assert report["comparison_skip_reason"]
        assert not {"CSV", "Parquet"} & report["queries"].keys()


def test_verification_still_rejects_missing_dates_for_accepted_transactions(monkeypatch):
    glue = prepare_snapshot_verification(monkeypatch, 1, 0, None)
    with pytest.raises(ValueError, match="no comparison date"):
        verify_aws.verify(Mock(), glue, Mock(), "bucket", "job", "group")


def test_verification_cli_saves_zero_accepted_snapshot_successfully(monkeypatch, tmp_path, capsys):
    import boto3
    glue = prepare_snapshot_verification(monkeypatch, 0, 1, None)
    monkeypatch.setattr(boto3, "client", lambda service: glue if service == "glue" else Mock())
    target = tmp_path / "evidence.json"
    monkeypatch.setattr(sys, "argv", ["verify_aws.py", "bucket", "--job", "job", "--out", str(target)])
    assert verify_aws.main() == 0
    report = json.loads(target.read_text())
    assert report["counts_verified"] is True
    assert report["comparison_status"] == "skipped"
    assert "Format comparison skipped" in capsys.readouterr().out


def test_verification_rejects_wrong_rejected_source_distribution(monkeypatch):
    glue = prepare_snapshot_verification(monkeypatch, 0, 1, None)
    original_run = verify_aws.run

    def run(athena, sql, *args):
        if "SELECT source_system, COUNT(*) FROM rejected_transactions" in sql:
            return {}, [("source_system", "count"), ("ONLINE_BANKING", "1")]
        return original_run(athena, sql, *args)

    monkeypatch.setattr(verify_aws, "run", run)
    with pytest.raises(ValueError, match="rejected source-system counts differ"):
        verify_aws.verify(Mock(), glue, Mock(), "bucket", "job", "group")


@pytest.mark.parametrize("missing", [False, True])
def test_verification_normalizes_rejected_source_labels_like_pipeline_metrics(monkeypatch, missing):
    glue = prepare_snapshot_verification(monkeypatch, 0, 2, None)
    receipt = verify_aws.read_published_snapshot(None, None)
    if missing:
        receipt["by_source_system"]["(missing)"] = receipt["by_source_system"].pop("CARDS")
    original_run = verify_aws.run

    def run(athena, sql, *args):
        if "SELECT source_system, COUNT(*) FROM rejected_transactions" in sql:
            groups = [(None, "1"), (" \t ", "1")] if missing else [(" CARDS ", "1"), ("CARDS", "1")]
            return {}, [("source_system", "count"), *groups]
        return original_run(athena, sql, *args)

    monkeypatch.setattr(verify_aws, "run", run)
    assert verify_aws.verify(Mock(), glue, Mock(), "bucket", "job", "group")["counts_verified"] is True


@pytest.mark.parametrize("sql,expected", [
    ("-- Verified on Athena; see evidence.\nSELECT 1;", ["SELECT 1"]),
    ("SELECT 1; -- trailing comment;\n", ["SELECT 1"]),
    ("SELECT 1 -- inline; comment\n; SELECT 2;", ["SELECT 1", "SELECT 2"]),
    ("SELECT 'a;b--c'; SELECT 'it''s;fine';", ["SELECT 'a;b--c'", "SELECT 'it''s;fine'"]),
    ('SELECT "a;b" FROM "table";', ['SELECT "a;b" FROM "table"']),
    ("SELECT /* comment; /* nested; */ end */ 1;", ["SELECT   1"]),
    ("-- only a comment;\n/* another; */", []),
])
def test_sql_report_splitter_preserves_quoted_semicolons_and_ignores_comments(sql, expected):
    assert verify_aws.sql_statements(sql) == expected


@pytest.mark.parametrize("sql", ["SELECT 'unterminated", "SELECT /* unterminated"])
def test_sql_report_splitter_rejects_unterminated_quotes_and_comments(sql):
    with pytest.raises(ValueError, match="Unterminated SQL"):
        verify_aws.sql_statements(sql)


def test_explicit_empty_run_id_does_not_fall_back_to_current_snapshot():
    s3 = Mock()
    with pytest.raises(ValueError, match="Glue run ID"):
        verify_aws.verify(s3, Mock(), Mock(), "bucket", "job", "group", run_id="")
    s3.get_object.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("database", "template_jr_other"),
    ("database", "another_template_jr_test"),
    ("input_prefix", "raw/batches/claimed"),
    ("data_prefix", "snapshots/jr_other/"),
    ("data_prefix", None),
])
def test_verification_rejects_wrong_snapshot_identity_before_athena(monkeypatch, field, value):
    glue = prepare_snapshot_verification(monkeypatch, 0, 0, None)
    receipt = verify_aws.read_published_snapshot(None, None)
    receipt[field] = value
    query = Mock()
    monkeypatch.setattr(verify_aws, "run", query)
    # Even --expected-prefix cannot make a forged receipt outrank Glue's input.
    with pytest.raises(ValueError):
        verify_aws.verify(Mock(), glue, Mock(), "bucket", "job", "group",
                          expected_prefix=receipt["input_prefix"])
    query.assert_not_called()


@pytest.mark.parametrize("field,value", [("Id", "jr_other"), ("JobName", "other-job"),
                                         ("JobRunState", "FAILED")])
def test_verification_rejects_wrong_execution_identity(monkeypatch, field, value):
    glue = prepare_snapshot_verification(monkeypatch, 0, 0, None)
    glue.get_job_run.return_value["JobRun"][field] = value
    query = Mock()
    monkeypatch.setattr(verify_aws, "run", query)
    with pytest.raises(ValueError):
        verify_aws.verify(Mock(), glue, Mock(), "bucket", "job", "group")
    query.assert_not_called()


def test_verification_resolves_run_arguments_and_fixed_arguments_in_glue_order(monkeypatch):
    glue = prepare_snapshot_verification(monkeypatch, 0, 0, None)
    receipt = verify_aws.read_published_snapshot(None, None)
    receipt.update(input_prefix="raw/batches/run", database="fixed_jr_test")
    glue.get_job_run.return_value["JobRun"]["Arguments"] = {
        "--input_prefix": "raw/batches/run/", "--database": "ignored"}
    glue.get_job.return_value["Job"]["NonOverridableArguments"] = {"--database": "fixed"}
    assert verify_aws.verify(Mock(), glue, Mock(), "bucket", "job", "group")["counts_verified"]


@pytest.mark.parametrize("key,value", [("--bucket", "other-bucket"), ("--database", "other"),
                                      ("--input_prefix", "raw/batches/actual"), ("--bucket", None)])
def test_verification_rejects_mismatched_or_missing_glue_arguments(monkeypatch, key, value):
    glue = prepare_snapshot_verification(monkeypatch, 0, 0, None)
    glue.get_job_run.return_value["JobRun"]["Arguments"] = {key: value}
    query = Mock()
    monkeypatch.setattr(verify_aws, "run", query)
    with pytest.raises(ValueError):
        verify_aws.verify(Mock(), glue, Mock(), "bucket", "job", "group")
    query.assert_not_called()
