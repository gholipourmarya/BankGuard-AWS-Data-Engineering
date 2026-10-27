from unittest.mock import Mock

import pytest

from scripts import athena_scan_compare as compare, upload_dataset


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
