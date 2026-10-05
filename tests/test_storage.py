# File: test_storage.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Photo storage: local folder and S3-compatible buckets (tested against a fake client, no network)."""
import importlib.util
import io

import pytest

from helpers import make_app, post_form, register, scalar, token_of
from rockconnect.storage import LocalStorage, S3Storage

PNG = b"\x89PNG\r\n\x1a\n" + b"1" * 64


class FakeS3:
    """Just enough of boto3's S3 client: put/get/delete objects and signed links."""

    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[(Bucket, Key)] = (Body, ContentType)

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)][0])}

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)

    def generate_presigned_url(self, op, Params, ExpiresIn):
        return "https://files.example.com/%s/%s?expires=%d&sig=abc" % (Params["Bucket"], Params["Key"], ExpiresIn)


def test_local_storage_round_trip(tmp_path):
    store = LocalStorage(str(tmp_path / "pics"))
    store.save("a.png", PNG)
    assert store.read("a.png") == PNG
    assert store.delete("a.png") is True and store.delete("a.png") is False       # second time: already gone


def test_s3_storage_round_trip_and_keys():
    fake = FakeS3()
    store = S3Storage("bucket", "uploads/", client=fake)
    store.save("a.png", PNG)
    assert fake.objects[("bucket", "uploads/a.png")] == (PNG, "image/png")
    assert store.read("a.png") == PNG
    store.save("../../etc/passwd.jpg", b"x")                                      # cannot climb out of the prefix
    assert ("bucket", "uploads/passwd.jpg") in fake.objects
    store.delete("a.png")
    assert ("bucket", "uploads/a.png") not in fake.objects


def test_s3_without_boto3_installed_says_what_to_do(tmp_path):
    if importlib.util.find_spec("boto3"):
        pytest.skip("boto3 is installed here")
    with pytest.raises(RuntimeError, match="pip install boto3"):
        make_app(tmp_path, S3_BUCKET="my-bucket")


def app_with_s3(tmp_path):
    app = make_app(tmp_path)
    fake = FakeS3()
    app.extensions["bac_storage"] = S3Storage("bucket", "uploads/", client=fake)
    return app, fake


def upload(client, body="pic"):
    return client.post("/posts", data={"_csrf": token_of(client, "/feed"), "body": body,
                                       "image": (io.BytesIO(PNG), "x.png")}, content_type="multipart/form-data")


def test_photos_go_to_the_bucket_and_are_served_through_signed_links(tmp_path):
    app, fake = app_with_s3(tmp_path)
    client = register(app)
    upload(client)
    name = scalar(app, "SELECT image_filename FROM posts")
    assert ("bucket", "uploads/" + name) in fake.objects
    assert not (tmp_path / "uploads").exists()                                       # nothing on local disk
    r = client.get("/uploads/" + name)
    assert r.status_code == 302 and r.headers["Location"].startswith("https://files.example.com/bucket/uploads/" + name)
    assert "private" in r.headers["Cache-Control"]
    # the bucket link is only handed to signed-in members
    assert app.test_client().get("/uploads/" + name).status_code == 302
    assert "/users/signin" in app.test_client().get("/uploads/" + name).headers["Location"]


def test_deleting_a_post_or_account_removes_the_object(tmp_path):
    app, fake = app_with_s3(tmp_path)
    client = register(app)
    upload(client, "first"), upload(client, "second")
    assert len(fake.objects) == 2
    post_form(client, "/posts/1/delete")
    assert len(fake.objects) == 1
    client.post("/account/delete", data={"password": "S3cret!pw", "confirm": "DELETE",
                                         "_csrf": token_of(client, "/account/")})
    assert fake.objects == {}


def test_csp_allows_signed_links_only_when_s3_is_used(tmp_path):
    local = make_app(tmp_path)
    assert "img-src 'self' data:;" in local.test_client().get("/").headers["Content-Security-Policy"] + ";"
    from rockconnect import _content_security_policy
    assert "img-src 'self' data: https:" in _content_security_policy({"S3_BUCKET": "b"})


def test_path_tricks_are_refused_for_local_files(tmp_path):
    app = make_app(tmp_path)
    client = register(app)
    assert client.get("/uploads/..%2f..%2frockconnect.sqlite").status_code == 404
