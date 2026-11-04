import io
from unittest.mock import Mock

import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber

from scripts import verify_aws
from src import snapshot, transform, validate
from tests.test_aws_scripts import prepare_snapshot_verification
from tests.test_customer_contacts import reference_error_result
from tests.test_glue_output import load_function
from tests.test_sdk_contract import client


def publish_metrics(metrics, cloudwatch=None):
    cloudwatch = cloudwatch if cloudwatch is not None else Mock()
    put = load_function("glue/banking_etl.py", "put_metrics", {
        "boto3": Mock(client=Mock(return_value=cloudwatch)), "JOB_NAME": "job", "log": Mock(),
    })
    put(metrics)
    return cloudwatch.put_metric_data.call_args.kwargs["MetricData"] if isinstance(cloudwatch, Mock) else None


def metric_values(data, metric_name):
    return {dict((item["Name"], item["Value"]) for item in datum["Dimensions"])["Dataset"]: datum["Value"]
            for datum in data if datum["MetricName"] == metric_name}


def test_bad_transactions_cannot_be_hidden_by_valid_reference_data():
    customers = [{"customer_id": f"C{i}", "name": "Alice", "country": "DE", "_line": i+2}
                 for i in range(100)]
    accounts = [{"account_id": f"A{i}", "customer_id": f"C{i}", "status": "Active", "_line": i+2}
                for i in range(100)]
    transactions = [{"transaction_id": "T1", "account_id": "A0", "amount": "bad", "currency": "EUR",
                     "transaction_type": "PAYMENT", "transaction_date": "2026-01-01",
                     "source_system": "CARDS", "_line": 2}]
    metrics = transform.run_pipeline(customers, accounts, transactions, "review", "now")["metrics"]
    data = publish_metrics(metrics)
    rates = metric_values(data, "PassRatePercent")
    assert rates == {"overall": 99.5, "customers": 100, "accounts": 100, "transactions": 0}
    assert rates["transactions"] < 90 < rates["overall"]
    assert all({item["Name"]: item["Value"] for item in datum["Dimensions"]}["JobName"] == "job" for datum in data)


def test_bad_reference_data_has_its_own_quality_metrics():
    rates = metric_values(publish_metrics(reference_error_result()["metrics"]), "PassRatePercent")
    assert rates == {"overall": 60, "customers": 50, "accounts": 50, "transactions": 100}


def test_empty_files_emit_zero_counts_without_false_quality_failures():
    metrics = transform.run_pipeline([], [], [], "review", "now")["metrics"]
    data = publish_metrics(metrics)
    assert not metric_values(data, "PassRatePercent")
    assert metric_values(data, "InputCount") == dict.fromkeys(("overall", "customers", "accounts", "transactions"), 0)


def test_old_metrics_without_rates_can_still_be_published():
    metrics = {"input_count": 2, "accepted_count": 1, "rejected_count": 1}
    assert metric_values(publish_metrics(metrics), "PassRatePercent") == {"overall": 50, "transactions": 50}


@pytest.mark.parametrize("location", ["total", "source", "customers", "accounts", "transactions", "overall"])
@pytest.mark.parametrize("rate", [-1, 999, 0, float("nan"), float("inf"), "100", True, None])
def test_inconsistent_or_invalid_quality_rates_cannot_be_published(location, rate):
    metrics = reference_error_result()["metrics"]
    target = (metrics if location == "total" else metrics["by_source_system"]["CARDS"] if location == "source"
              else metrics["overall"] if location == "overall" else metrics["by_dataset"][location])
    target["pass_rate_percent"] = rate
    s3 = Mock()
    with pytest.raises(ValueError, match="pass_rate_percent"):
        snapshot.publish(s3, "bucket", "jr_new", "db", "raw", metrics)
    s3.put_object.assert_not_called()


def test_correct_rounded_rate_is_accepted_and_wrong_in_range_rate_is_rejected():
    metrics = {"input_count": 3, "accepted_count": 2, "rejected_count": 1, "counts_consistent": True,
               "pass_rate_percent": 66.67}
    metrics["by_source_system"] = {"CARDS": dict(metrics)}
    validate.validate_metrics(metrics)
    metrics["pass_rate_percent"] = 66.66
    with pytest.raises(ValueError, match="pass_rate_percent"):
        validate.validate_metrics(metrics)


def test_cloudwatch_requests_match_sdk_with_scoped_dimensions():
    metrics = reference_error_result()["metrics"]
    expected = publish_metrics(metrics)
    cloudwatch = client("cloudwatch")
    with Stubber(cloudwatch) as stub:
        stub.add_response("put_metric_data", {}, {"Namespace": "BankGuard", "MetricData": expected})
        publish_metrics(metrics, cloudwatch)
        stub.assert_no_pending_responses()


def streaming_body(data):
    return StreamingBody(io.BytesIO(data), len(data))


def prepare_reference_outputs(monkeypatch):
    glue = prepare_snapshot_verification(monkeypatch, 1, 0, "2026-01-01")
    receipt = verify_aws.read_published_snapshot(None, None)
    receipt["by_dataset"] = {
        "customers": transform.compute_metrics(2, 1, [{"error_reasons": ["INVALID_EMAIL"]}]),
        "accounts": transform.compute_metrics(1, 1, []),
        "transactions": transform.compute_metrics(1, 1, []),
    }
    receipt["overall"] = transform.compute_metrics(4, 3, [{"error_reasons": ["INVALID_EMAIL"]}])
    validate.validate_metrics(receipt)
    original = verify_aws.run
    actual = {"customers": {"accepted": 1, "rejected": 1}, "accounts": {"accepted": 1, "rejected": 0}}

    def query(athena, sql, *args):
        if "AS customers_accepted" in sql:
            return {}, [("customers_accepted", "accounts_accepted"),
                        tuple(str(actual[name]["accepted"]) for name in ("customers", "accounts"))]
        return original(athena, sql, *args)

    monkeypatch.setattr(verify_aws, "run", query)
    s3 = Mock()

    def list_rows(**kwargs):
        name = kwargs["Prefix"].rstrip("/").split("/")[-1]
        # Put each rejected row in a separate page/file to exercise pagination.
        return [{"Contents": [{"Key": f"{kwargs['Prefix']}part-{index}.json"}]}
                for index in range(actual[name]["rejected"])] or [{}]

    s3.get_paginator.return_value.paginate.side_effect = list_rows
    s3.get_object.side_effect = lambda **kwargs: {"Body": streaming_body(b'{"error_reasons":["INVALID_EMAIL"]}\n')}
    return glue, s3, actual


def test_aws_verification_confirms_all_three_datasets(monkeypatch):
    glue, s3, _ = prepare_reference_outputs(monkeypatch)
    report = verify_aws.verify(s3, glue, Mock(), "bucket", "job", "group")
    assert report["counts_verified"] is True
    assert set(report["verified_datasets"]) == {"customers", "accounts", "transactions"}
    assert s3.get_paginator.call_args_list


@pytest.mark.parametrize("dataset", ["customers", "accounts"])
@pytest.mark.parametrize("kind", ["accepted", "rejected"])
def test_missing_or_extra_reference_outputs_fail_verification(monkeypatch, dataset, kind):
    glue, s3, actual = prepare_reference_outputs(monkeypatch)
    actual[dataset][kind] = 0 if actual[dataset][kind] else 1
    with pytest.raises(ValueError, match=f"{dataset} counts differ"):
        verify_aws.verify(s3, glue, Mock(), "bucket", "job", "group")


def test_old_receipts_explicitly_report_only_the_verified_transaction_scope(monkeypatch):
    glue = prepare_snapshot_verification(monkeypatch, 0, 1, None)
    s3 = Mock()
    report = verify_aws.verify(s3, glue, Mock(), "bucket", "job", "group")
    assert report["verified_datasets"] == ["transactions"]
    s3.get_paginator.assert_not_called()


@pytest.mark.parametrize("data", [b'not-json\n', b'{}\n', b'[]\n', b'{"error_reasons":[]}\n'])
def test_invalid_rejected_output_fails_and_closes_stream(data):
    s3 = Mock()
    s3.get_paginator.return_value.paginate.return_value = [{"Contents": [{"Key": "rejected/part-0.json"}]}]
    body = streaming_body(data)
    s3.get_object.return_value = {"Body": body}
    with pytest.raises(ValueError):
        verify_aws.count_rejected_rows(s3, "bucket", "rejected/")
    assert body._raw_stream.closed


def test_rejected_row_counter_ignores_markers_and_counts_all_pages_with_sdk():
    s3 = client("s3")
    first = b'{"error_reasons":["INVALID_EMAIL"]}\n\n'
    second = b'{"error_reasons":["INVALID_TEL"]}\n'
    with Stubber(s3) as stub:
        stub.add_response("list_objects_v2", {"IsTruncated": True, "NextContinuationToken": "next", "Contents": [
            {"Key": "rejected/_SUCCESS"}, {"Key": "rejected/.metadata.json"}, {"Key": "rejected/part-0.json"}]},
            {"Bucket": "bucket", "Prefix": "rejected/"})
        stub.add_response("get_object", {"Body": streaming_body(first)},
                          {"Bucket": "bucket", "Key": "rejected/part-0.json"})
        stub.add_response("list_objects_v2", {"IsTruncated": False, "Contents": [{"Key": "rejected/part-1.json"}]},
                          {"Bucket": "bucket", "Prefix": "rejected/", "ContinuationToken": "next"})
        stub.add_response("get_object", {"Body": streaming_body(second)},
                          {"Bucket": "bucket", "Key": "rejected/part-1.json"})
        assert verify_aws.count_rejected_rows(s3, "bucket", "rejected/") == 2
        stub.assert_no_pending_responses()
