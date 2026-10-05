# File: storage.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Where uploaded photos live: a local folder (default) or an S3-compatible bucket.

Set S3_BUCKET (and optionally S3_ENDPOINT_URL for MinIO / Cloudflare R2 / Backblaze, S3_REGION,
S3_PREFIX) to keep photos in object storage; AWS credentials come from the usual AWS_* environment
variables. Nothing else in the app changes: feed.py only calls save / delete / read / response.

Photos stay private in both modes: the browser asks the app (which checks the sign-in), and with S3 the
app answers with a short-lived signed link to the object, so the bucket itself can stay private.
"""
import os

from flask import current_app, redirect, send_from_directory

from .baclog import bac_log

CONTENT_TYPES = {".jpg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}
SIGNED_LINK_SECONDS = 600


def _content_type(name):
    return CONTENT_TYPES.get(os.path.splitext(name)[1].lower(), "application/octet-stream")


class LocalStorage:
    kind = "local"

    def __init__(self, folder):
        self.folder = folder

    def save(self, name, data):
        os.makedirs(self.folder, exist_ok=True)
        with open(os.path.join(self.folder, name), "wb") as fh:
            fh.write(data)

    def delete(self, name):
        try:
            os.remove(os.path.join(self.folder, name))
            return True
        except OSError:
            return False  # already gone

    def read(self, name):
        with open(os.path.join(self.folder, name), "rb") as fh:
            return fh.read()

    def response(self, name):
        # send_from_directory refuses paths that escape the folder (../ tricks)
        return send_from_directory(self.folder, name, max_age=86400)


class S3Storage:
    kind = "s3"

    def __init__(self, bucket, prefix="uploads/", client=None, **client_options):
        self.bucket = bucket
        self.prefix = prefix
        if client is None:
            try:
                import boto3
            except ImportError as exc:
                raise RuntimeError("S3_BUCKET is set but boto3 is not installed: pip install boto3") from exc
            client = boto3.client("s3", **{k: v for k, v in client_options.items() if v})
        self.client = client

    def _key(self, name):
        return self.prefix + os.path.basename(name)  # basename: a file name can never climb out of the prefix

    def save(self, name, data):
        self.client.put_object(Bucket=self.bucket, Key=self._key(name), Body=data,
                               ContentType=_content_type(name))

    def delete(self, name):
        self.client.delete_object(Bucket=self.bucket, Key=self._key(name))
        return True

    def read(self, name):
        return self.client.get_object(Bucket=self.bucket, Key=self._key(name))["Body"].read()

    def response(self, name):
        url = self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": self._key(name)},
            ExpiresIn=SIGNED_LINK_SECONDS)
        return redirect(url)


def init_app(app):
    bucket = app.config.get("S3_BUCKET")
    if bucket:
        store = S3Storage(bucket, app.config["S3_PREFIX"], endpoint_url=app.config["S3_ENDPOINT_URL"],
                          region_name=app.config["S3_REGION"])
        bac_log("storage", "photos are stored in S3 bucket %r" % bucket)
    else:
        store = LocalStorage(app.config["UPLOAD_DIR"])
        bac_log("storage", "photos are stored in %s" % app.config["UPLOAD_DIR"])
    app.extensions["bac_storage"] = store


def get():
    return current_app.extensions["bac_storage"]
