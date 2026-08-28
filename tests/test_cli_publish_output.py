"""cmd_publish output formatting: dict entries from the cloudflare
backend's list() must print as readable lines, and gc must report what
it did (a count from backends that own their storage; silence only when
the external command already reported for itself)."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo import cli
from jimemo.publish import Publisher


class _FakePublisher(Publisher):
    def __init__(self, entries=None, gc_result=None):
        self._entries = entries or []
        self._gc_result = gc_result

    def publish(self, html_path, title=None):
        return "https://example.test/hash/"

    def purge(self, hash_or_url):
        pass

    def list(self):
        return self._entries

    def gc(self):
        return self._gc_result


def _run_publish(monkeypatch, target, publisher):
    monkeypatch.setattr("jimemo.publish.get_publisher", lambda config, **kw: publisher)
    monkeypatch.setattr("jimemo.config.load_config", lambda *a, **k: object())
    args = argparse.Namespace(target=target, arg=None, title=None, dry_run=False)
    return cli.cmd_publish(args)


def test_list_formats_dict_entries(monkeypatch, capsys):
    entries = [
        {"hash": "ab" * 12, "status": "live", "tombstoned_at": None,
         "staged_locally": True},
        {"hash": "cd" * 12, "status": "purged",
         "tombstoned_at": "2026-08-28T00:00:00Z", "staged_locally": False},
    ]
    assert _run_publish(monkeypatch, "list", _FakePublisher(entries=entries)) in (0, None)
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"{'ab' * 12}  live"
    assert out[1] == (
        f"{'cd' * 12}  purged  tombstoned 2026-08-28T00:00:00Z"
        "  (not staged locally)"
    )
    assert "{" not in "".join(out)


def test_list_passes_plain_lines_through(monkeypatch, capsys):
    assert _run_publish(monkeypatch, "list", _FakePublisher(entries=["a line"])) in (0, None)
    assert capsys.readouterr().out == "a line\n"


def test_gc_reports_count(monkeypatch, capsys):
    assert _run_publish(monkeypatch, "gc", _FakePublisher(gc_result=2)) in (0, None)
    assert "removed 2 tombstoned page(s)" in capsys.readouterr().out


def test_gc_reports_nothing_to_collect(monkeypatch, capsys):
    assert _run_publish(monkeypatch, "gc", _FakePublisher(gc_result=0)) in (0, None)
    assert "nothing to collect" in capsys.readouterr().out


def test_gc_stays_quiet_for_external_command(monkeypatch, capsys):
    assert _run_publish(monkeypatch, "gc", _FakePublisher(gc_result=None)) in (0, None)
    assert capsys.readouterr().out == ""


def test_setup_assets_only_refreshes_and_deploys(monkeypatch, capsys):
    calls = []

    class _CfLike(_FakePublisher):
        def refresh_assets(self):
            calls.append("refresh")

    monkeypatch.setattr("jimemo.publish.get_publisher", lambda config, **kw: _CfLike())
    monkeypatch.setattr("jimemo.config.load_config", lambda *a, **k: object())
    args = argparse.Namespace(
        target="setup", arg=None, title=None, dry_run=False, assets_only=True
    )
    assert cli.cmd_publish(args) == 0
    assert calls == ["refresh"]
    assert "refreshed and redeployed" in capsys.readouterr().out


def test_setup_assets_only_rejects_command_backend(monkeypatch, capsys):
    monkeypatch.setattr(
        "jimemo.publish.get_publisher", lambda config, **kw: _FakePublisher()
    )
    monkeypatch.setattr("jimemo.config.load_config", lambda *a, **k: object())
    args = argparse.Namespace(
        target="setup", arg=None, title=None, dry_run=False, assets_only=True
    )
    assert cli.cmd_publish(args) == 2
    assert "cloudflare backend" in capsys.readouterr().err
