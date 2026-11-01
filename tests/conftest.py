import os

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path, request):
    for k in [k for k in os.environ if k.startswith("OBSERVE_LENS_")
              or k == "XDG_CONFIG_HOME"]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OBSERVE_LENS_CONFIG_DIR", str(tmp_path))
    if request.instance is not None:
        request.instance._mp = monkeypatch
        request.instance.tmp = tmp_path
