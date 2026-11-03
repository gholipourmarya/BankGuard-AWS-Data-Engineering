"""Poll terminal Glue states that have no documented State Change event."""
import json
import os

FAILURE_STATES = {"ERROR", "EXPIRED"}


def check_runs(glue, s3, sns, job_name, bucket, topic):
    notified = []
    for page in glue.get_paginator("get_job_runs").paginate(JobName=job_name, PaginationConfig={"PageSize": 200}):
        for run in page.get("JobRuns", []):
            state = run["JobRunState"]
            if state not in FAILURE_STATES:
                continue
            key = f"monitor/notified/{run['Id']}-{state}.json"
            try:
                with s3.get_object(Bucket=bucket, Key=key)["Body"] as body:
                    body.read()
            except s3.exceptions.NoSuchKey:
                # Notify before recording delivery. A retry may repeat an alert,
                # but a failed publish must never suppress the notification.
                message = {"job": job_name, "run_id": run["Id"], "state": state,
                           "error": run.get("ErrorMessage", "")}
                text = json.dumps(message, sort_keys=True)
                sns.publish(TopicArn=topic, Subject="BankGuard Glue run failed", Message=text)
                s3.put_object(Bucket=bucket, Key=key, Body=text.encode("utf-8"), ContentType="application/json")
                notified.append(run["Id"])
    return {"notified": notified}


def handler(event, context=None, glue=None, s3=None, sns=None):
    if glue is None or s3 is None or sns is None:
        import boto3
        glue = glue or boto3.client("glue")
        s3 = s3 or boto3.client("s3")
        sns = sns or boto3.client("sns")
    return check_runs(glue, s3, sns, os.environ["GLUE_JOB_NAME"], os.environ["BUCKET"], os.environ["ALERT_TOPIC"])
