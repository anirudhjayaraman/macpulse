"""Keep every test away from the real Mac: no real home folder, data folder or launchd."""

import pathlib

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(pathlib.Path, "home", lambda: home)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("MACVERDICT_HOME", str(tmp_path / "data"))
    from macverdict import scheduler
    real = scheduler._launchctl

    def guarded(*args):
        # Reading state is harmless; anything that changes launchd must be mocked by the test.
        if args and args[0] in ("bootstrap", "bootout", "load", "unload", "kickstart"):
            raise AssertionError(f"test tried to change real launchd: launchctl {' '.join(args)}")
        return real(*args)

    monkeypatch.setattr(scheduler, "_launchctl", guarded)
    yield home
