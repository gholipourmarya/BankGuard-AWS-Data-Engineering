"""Upload one immutable ETL release, then switch script and library in one Glue update."""
import argparse
import copy
import hashlib
import io
from pathlib import Path
import zipfile

RELEASE_FILES = ("src/validate.py", "src/transform.py", "src/snapshot.py", "glue/banking_etl.py")


def release_id(root):
    hashes = [hashlib.sha256((Path(root) / name).read_bytes()).hexdigest() for name in RELEASE_FILES]
    return hashlib.sha256("\n".join(hashes).encode("ascii")).hexdigest()


def library_zip(root):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        for name in RELEASE_FILES[:-1]:
            info = zipfile.ZipInfo(Path(name).name, date_time=(1980, 1, 1, 0, 0, 0))
            archive.writestr(info, (Path(root) / name).read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
    data.seek(0)
    return data


def deploy_release(s3, glue, bucket, job_name, root):
    prefix = f"releases/{release_id(root)}"
    s3.upload_file(str(Path(root) / "glue/banking_etl.py"), bucket, f"{prefix}/banking_etl.py")
    s3.upload_fileobj(library_zip(root), bucket, f"{prefix}/src.zip")
    job = glue.get_job(JobName=job_name)["Job"]
    # Preserve the complete writable job configuration; UpdateJob replaces fields.
    shape = glue.meta.service_model.operation_model("UpdateJob").input_shape.members["JobUpdate"]
    update = {key: copy.deepcopy(job[key]) for key in shape.members if key in job}
    if update.get("WorkerType") and update.get("NumberOfWorkers"):
        # GetJob may return deprecated capacity fields alongside worker settings;
        # UpdateJob rejects that combination.
        update.pop("AllocatedCapacity", None)
        update.pop("MaxCapacity", None)
    update["Command"]["ScriptLocation"] = f"s3://{bucket}/{prefix}/banking_etl.py"
    update.setdefault("DefaultArguments", {})["--extra-py-files"] = f"s3://{bucket}/{prefix}/src.zip"
    glue.update_job(JobName=job_name, JobUpdate=update)
    return prefix


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bucket")
    parser.add_argument("job_name")
    args = parser.parse_args()
    import boto3
    print(deploy_release(boto3.client("s3"), boto3.client("glue"), args.bucket, args.job_name,
                         Path(__file__).resolve().parents[1]))


if __name__ == "__main__":
    main()
