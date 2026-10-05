# File: conftest.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Fixtures shared by every test file (pytest finds this file by itself: nothing needs to import it)."""
import pytest

from rockconnect import create_app


@pytest.fixture
def client(tmp_path):
    """A test client on its own temporary database and photo folder (never the real instance folder)."""
    app = create_app({"TESTING": True,
                      "INSTANCE_PATH": str(tmp_path / "instance"),
                      "DATABASE_URL": "sqlite:///" + str(tmp_path / "t.sqlite"),
                      "UPLOAD_DIR": str(tmp_path / "uploads")})
    return app.test_client()
