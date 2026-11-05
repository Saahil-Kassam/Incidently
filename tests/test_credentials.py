"""Where the API key comes from, and saving and forgetting it."""

import sys

import pytest

from incidently.credentials import ApiKeys, CredentialsError, KeyStatus


@pytest.fixture
def path(tmp_path):
    return tmp_path / "data" / "api-key"


def keys(path, environ_key=None, offline=False):
    applied = []
    store = ApiKeys(path=path, environ_key=environ_key, apply=applied.append, offline=offline)
    return store, applied


def test_no_key_anywhere(path):
    store, applied = keys(path)
    assert not store.configured
    assert store.status() == KeyStatus(False, None, None)
    assert applied == []


def test_environment_key(path):
    store, applied = keys(path, environ_key=" sk-ant-env-1234 ")
    assert store.configured
    assert store.status() == KeyStatus(True, "environment", "…1234")
    assert applied == ["sk-ant-env-1234"]


def test_offline_needs_no_key(path):
    store, _ = keys(path, offline=True)
    assert store.configured
    assert store.status() == KeyStatus(True, "offline", None)


def test_remembered_key_is_saved_and_loaded_next_time(path):
    store, applied = keys(path, environ_key="sk-ant-env-1234")
    store.use("  sk-ant-mine-abcd\n", remember=True)

    assert path.read_text(encoding="utf-8") == "sk-ant-mine-abcd\n"
    assert store.status() == KeyStatus(True, "saved", "…abcd")
    assert applied[-1] == "sk-ant-mine-abcd"

    # A saved key wins over the environment's on the next start.
    restarted, applied = keys(path, environ_key="sk-ant-env-1234")
    assert restarted.status() == KeyStatus(True, "saved", "…abcd")
    assert applied == ["sk-ant-mine-abcd"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_saved_key_is_private(path):
    store, _ = keys(path)
    store.use("sk-ant-mine-abcd", remember=True)
    assert path.stat().st_mode & 0o777 == 0o600


def test_unremembered_key_is_not_saved_and_removes_an_old_one(path):
    store, _ = keys(path)
    store.use("sk-ant-first-1111", remember=True)

    store.use("sk-ant-second-2222", remember=False)

    assert not path.exists()
    assert store.status() == KeyStatus(True, "entered", "…2222")
    assert not keys(path)[0].configured  # gone after a restart


def test_forget_falls_back_to_the_environment(path):
    store, applied = keys(path, environ_key="sk-ant-env-1234")
    store.use("sk-ant-mine-abcd", remember=True)

    store.forget()

    assert not path.exists()
    assert store.status() == KeyStatus(True, "environment", "…1234")
    assert applied[-1] == "sk-ant-env-1234"


def test_forget_without_an_environment_key(path):
    store, applied = keys(path)
    store.use("sk-ant-mine-abcd", remember=False)
    store.forget()
    assert not store.configured
    assert applied[-1] is None


def test_remember_needs_a_folder():
    store, _ = keys(None)
    with pytest.raises(CredentialsError, match="no data folder"):
        store.use("sk-ant-mine-abcd", remember=True)
    store.use("sk-ant-mine-abcd", remember=False)  # memory only is fine
    assert store.configured


def test_an_unreadable_saved_key_is_ignored(path):
    path.mkdir(parents=True)  # a folder where the file should be
    store, _ = keys(path, environ_key="sk-ant-env-1234")
    assert store.status().source == "environment"
