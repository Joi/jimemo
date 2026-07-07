import hashlib
import subprocess
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import jimemo
from jimemo.cli import main


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    assert jimemo.__version__ in capsys.readouterr().out


def test_doctor_on_clean_repo(capsys):
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "python" in out.lower()
    assert "vendor" in out.lower()
    assert "ok   vendored imports (jinja2, markdown, yaml, tomli)" in out
    assert "ok   charts vendored (chart.js 4.5.1)" in out


def test_no_args_shows_help(capsys):
    assert main([]) == 2
    assert "usage" in capsys.readouterr().err.lower()


def test_list_runs(capsys):
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert ("no templates installed yet" in out) or ("\t" in out)


def test_doctor_skips_vendored_imports_on_tampered_checksums(capsys, monkeypatch, tmp_path):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    f = vendor / "pkg" / "mod.py"
    f.parent.mkdir()
    f.write_text("x = 1\n")
    digest = hashlib.sha256(f.read_bytes()).hexdigest()
    (vendor / "SHA256SUMS").write_text(f"{digest}  ./pkg/mod.py\n")
    f.write_text("x = 2\n")  # tamper after recording the checksum

    monkeypatch.setattr("jimemo.cli.VENDOR_DIR", vendor)

    assert main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "checksum mismatch" in out
    assert "skip vendored imports" in out
    assert "ok   vendored imports" not in out


def test_doctor_reports_tampered_charts_checksums(capsys, monkeypatch, tmp_path):
    charts_vendor = tmp_path / "charts_vendor"
    f = charts_vendor / "chartjs" / "chart.umd.min.js"
    f.parent.mkdir(parents=True)
    f.write_text("/*! Chart.js v4.5.1 */\nvar x = 1;\n")
    digest = hashlib.sha256(f.read_bytes()).hexdigest()
    (charts_vendor / "SHA256SUMS").write_text(f"{digest}  ./chartjs/chart.umd.min.js\n")
    f.write_text("/*! Chart.js v4.5.1 */\nvar x = 2;\n")  # tamper after recording

    monkeypatch.setattr("jimemo.cli.CHARTS_VENDOR_DIR", charts_vendor)

    assert main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "FAIL charts: checksum mismatch" in out
    assert "chartjs/chart.umd.min.js" in out
    # the real (non-tampered) vendor/ should still verify clean independently
    assert "ok   vendor checksums" in out


# The next two checks need a *clean* sys.modules: every other test module in
# this suite imports jimemo.content/render/suggest at its own top (to test
# them directly), and pytest collection imports every test module before any
# test body runs -- so by the time any in-process test executes, jinja2/
# yaml/markdown are already in sys.modules regardless of run order, for
# reasons that have nothing to do with cli.py. Run each check in a fresh
# subprocess instead, where sys.modules starts empty.

SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")


def test_importing_cli_does_not_import_vendored_libs():
    # cli.py's own top-level imports must stay vendor-free (see cli.py's
    # module docstring comment): doctor, --version, and list all need to
    # run before the checksum gate can matter, which is moot if merely
    # `import jimemo.cli` already pulled in jinja2/yaml/markdown.
    script = (
        "import sys\n"
        f"sys.path.insert(0, {SRC_DIR!r})\n"
        "import jimemo.cli\n"
        "assert 'jinja2' not in sys.modules, sorted(sys.modules)\n"
        "assert 'yaml' not in sys.modules, sorted(sys.modules)\n"
        "assert 'markdown' not in sys.modules, sorted(sys.modules)\n"
        "assert 'tomli' not in sys.modules, sorted(sys.modules)\n"
        "print('OK')\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_doctor_tampered_checksums_never_imports_vendored_libs(tmp_path):
    # Extends test_doctor_skips_vendored_imports_on_tampered_checksums:
    # on a tampered vendor/, doctor must not just print "skip vendored
    # imports" -- no vendored module may actually land in sys.modules,
    # including via the unconditional stale-suitability-label scan, which
    # pulls in suggest.py (is_stale_labels).
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    f = vendor / "pkg" / "mod.py"
    f.parent.mkdir()
    f.write_text("x = 1\n")
    digest = hashlib.sha256(f.read_bytes()).hexdigest()
    (vendor / "SHA256SUMS").write_text(f"{digest}  ./pkg/mod.py\n")
    f.write_text("x = 2\n")  # tamper after recording the checksum

    script = (
        "import sys\n"
        f"sys.path.insert(0, {SRC_DIR!r})\n"
        "from pathlib import Path\n"
        "import jimemo.cli as cli\n"
        f"cli.VENDOR_DIR = Path({str(vendor)!r})\n"
        "rc = cli.main(['doctor'])\n"
        "assert rc == 1, rc\n"
        "assert 'jinja2' not in sys.modules, sorted(sys.modules)\n"
        "assert 'yaml' not in sys.modules, sorted(sys.modules)\n"
        "assert 'markdown' not in sys.modules, sorted(sys.modules)\n"
        "assert 'tomli' not in sys.modules, sorted(sys.modules)\n"
        "print('OK')\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "checksum mismatch" in result.stdout
    assert "skip vendored imports" in result.stdout
    assert "OK" in result.stdout


# ---------------------------------------------------------------------------
# publish dispatch (Phase 5 Task 2): a fake command config + a monkeypatched
# subprocess.run stand in for a real notes-publish invocation. Patching
# subprocess.run (rather than CommandPublisher's injectable `runner`) mirrors
# what actually happens end to end -- the CLI's own construction of
# CommandPublisher via get_publisher() never sees a test-injected runner.
# ---------------------------------------------------------------------------

def _write_command_config(tmp_path, command="fake-publish-cli"):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(f'[publish]\nbackend = "command"\ncommand = "{command}"\n')
    return cfg_file


def test_publish_file_prints_url(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(_write_command_config(tmp_path)))
    html = tmp_path / "page.html"
    html.write_text("<html></html>")

    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return CompletedProcess(argv, 0, stdout="https://notes.ito.com/abc123/\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert main(["publish", str(html)]) == 0
    assert capsys.readouterr().out.strip() == "https://notes.ito.com/abc123/"
    assert calls == [["fake-publish-cli", str(html)]]


def test_publish_file_with_title(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(_write_command_config(tmp_path)))
    html = tmp_path / "page.html"
    html.write_text("<html></html>")

    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return CompletedProcess(argv, 0, stdout="https://notes.ito.com/abc123/\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert main(["publish", str(html), "--title", "Q3 Briefing"]) == 0
    assert calls == [["fake-publish-cli", str(html), "--title", "Q3 Briefing"]]


def test_publish_missing_file_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(_write_command_config(tmp_path)))
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: (_ for _ in ()).throw(
        AssertionError("subprocess.run should not be called for a missing file")
    ))

    missing = tmp_path / "nope.html"
    assert main(["publish", str(missing)]) == 1
    assert "file not found" in capsys.readouterr().err


def test_publish_purge_dispatches(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(_write_command_config(tmp_path)))

    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return CompletedProcess(argv, 0, stdout="purged: abc123\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert main(["publish", "purge", "abc123"]) == 0
    assert calls == [["fake-publish-cli", "purge", "abc123"]]
    assert "abc123" in capsys.readouterr().out


def test_publish_purge_missing_arg_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(_write_command_config(tmp_path)))
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: (_ for _ in ()).throw(
        AssertionError("subprocess.run should not be called without a purge target")
    ))

    assert main(["publish", "purge"]) == 2
    assert "missing hash or URL" in capsys.readouterr().err


def test_publish_list_dispatches(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(_write_command_config(tmp_path)))

    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return CompletedProcess(argv, 0, stdout="HASH  TITLE\nabc123  Note\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert main(["publish", "list"]) == 0
    assert calls == [["fake-publish-cli", "list"]]
    out = capsys.readouterr().out
    assert "HASH  TITLE" in out
    assert "abc123  Note" in out


def test_publish_gc_dispatches(tmp_path, monkeypatch):
    monkeypatch.setenv("JIMEMO_CONFIG", str(_write_command_config(tmp_path)))

    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert main(["publish", "gc"]) == 0
    assert calls == [["fake-publish-cli", "gc"]]


def test_publish_no_target_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(_write_command_config(tmp_path)))

    assert main(["publish"]) == 2
    assert "provide a file" in capsys.readouterr().err


def test_publish_missing_config_errors_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "does-not-exist.toml"))
    html = tmp_path / "page.html"
    html.write_text("<html></html>")

    assert main(["publish", str(html)]) == 1
    assert "jimemo publish setup" in capsys.readouterr().err


def test_publish_command_failure_surfaces_stderr(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(_write_command_config(tmp_path)))
    html = tmp_path / "page.html"
    html.write_text("<html></html>")

    def fake_run(argv, **kwargs):
        return CompletedProcess(argv, 1, stdout="", stderr="wrangler deploy failed")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert main(["publish", str(html)]) == 1
    assert "wrangler deploy failed" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# publish setup (Phase 5 Task 5): only --dry-run is exercised through the
# real CLI entry point (a non-dry-run run needs interactive stdin and a
# real/mock Wrangler -- that path is covered directly against run_setup()
# in tests/test_setup.py instead). The key thing to prove here is wiring:
# `jimemo publish setup --dry-run` reaches jimemo.publish.setup.run_setup
# and never touches config loading, even with no config present at all.
# ---------------------------------------------------------------------------

def test_publish_setup_dry_run_dispatches_and_needs_no_config(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "does-not-exist.toml"))
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)

    assert main(["publish", "setup", "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "jimemo publish setup" in out
    assert "[dry-run]" in out
    assert "TOMBSTONES" in out
    assert not (tmp_path / "does-not-exist.toml").exists()


def test_publish_setup_without_dry_run_flag_defaults_false(monkeypatch):
    # Sanity check on argparse wiring: --dry-run must default to False so
    # a bare `jimemo publish setup` attempts the real (non-dry-run) path
    # rather than silently behaving like --dry-run.
    seen_dry_run = None

    def fake_run_setup(dry_run, wrangler, config_path, io):
        nonlocal seen_dry_run
        seen_dry_run = dry_run

    monkeypatch.setattr("jimemo.publish.setup.run_setup", fake_run_setup)

    assert main(["publish", "setup"]) == 0
    assert seen_dry_run is False


# A minimal page that satisfies lint_standalone, and one that violates
# it (remote image). Reused by the pdf/publish gate tests below.
GOOD_PAGE = (
    "<!doctype html><html><head><style>body{color:#111}</style></head>"
    "<body><p>hi</p></body></html>"
)
BAD_PAGE = '<html><body><img src="https://cdn.example/x.png"></body></html>'


def test_check_passes_clean_file(tmp_path, capsys):
    f = tmp_path / "draft.html"
    f.write_text(GOOD_PAGE)
    assert main(["check", str(f)]) == 0
    assert "ok" in capsys.readouterr().out


def test_check_fails_file_with_remote_reference(tmp_path, capsys):
    f = tmp_path / "draft.html"
    f.write_text(BAD_PAGE)
    assert main(["check", str(f)]) == 1
    assert "cdn.example" in capsys.readouterr().err


def test_check_missing_file(tmp_path, capsys):
    assert main(["check", str(tmp_path / "nope.html")]) == 1
    assert "not found" in capsys.readouterr().err


def _fake_pdf_seam(monkeypatch, browser="/usr/bin/chromium"):
    """Stub jimemo.pdf's two entry points (cli imports them lazily
    inside each handler, so patching the module attributes works) and
    record render_pdf calls. No test launches a real browser."""
    calls = []
    monkeypatch.setattr(
        "jimemo.pdf.find_browser", lambda configured=None, **kw: browser
    )

    def fake_render_pdf(html_path, pdf_path, browser_, launcher=None):
        calls.append((Path(html_path), Path(pdf_path), browser_))
        Path(pdf_path).parent.mkdir(parents=True, exist_ok=True)
        Path(pdf_path).write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr("jimemo.pdf.render_pdf", fake_render_pdf)
    return calls


def test_pdf_default_output_path(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    calls = _fake_pdf_seam(monkeypatch)
    f = tmp_path / "draft.html"
    f.write_text(GOOD_PAGE)

    assert main(["pdf", str(f)]) == 0

    assert calls == [(f, tmp_path / "draft.pdf", "/usr/bin/chromium")]
    assert f"wrote {tmp_path / 'draft.pdf'}" in capsys.readouterr().out


def test_pdf_explicit_output_path(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    calls = _fake_pdf_seam(monkeypatch)
    f = tmp_path / "draft.html"
    f.write_text(GOOD_PAGE)
    out = tmp_path / "final" / "brief.pdf"

    assert main(["pdf", str(f), "-o", str(out)]) == 0
    assert calls[0][1] == out


def test_pdf_refuses_unverified_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    calls = _fake_pdf_seam(monkeypatch)
    f = tmp_path / "draft.html"
    f.write_text(BAD_PAGE)

    assert main(["pdf", str(f)]) == 1

    err = capsys.readouterr().err
    assert "cdn.example" in err
    assert "--no-verify" in err
    assert calls == []


def test_pdf_no_verify_skips_the_check(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    calls = _fake_pdf_seam(monkeypatch)
    f = tmp_path / "draft.html"
    f.write_text(BAD_PAGE)

    assert main(["pdf", str(f), "--no-verify"]) == 0
    assert len(calls) == 1


def test_pdf_without_browser_fails_with_remedy(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setattr(
        "jimemo.pdf.find_browser", lambda configured=None, **kw: None
    )
    f = tmp_path / "draft.html"
    f.write_text(GOOD_PAGE)

    assert main(["pdf", str(f)]) == 1
    assert "[pdf]" in capsys.readouterr().err


def test_pdf_missing_input_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    assert main(["pdf", str(tmp_path / "nope.html")]) == 1
    assert "not found" in capsys.readouterr().err


REPO = Path(__file__).resolve().parents[1]
BRIEFING_SAMPLE = REPO / "templates" / "briefing" / "sample" / "content.md"


def test_render_pdf_flag_writes_both(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    calls = _fake_pdf_seam(monkeypatch)
    out = tmp_path / "brief.html"

    assert main(["render", "briefing", str(BRIEFING_SAMPLE), "-o", str(out), "--pdf"]) == 0

    assert out.is_file()
    assert calls == [(out, tmp_path / "brief.pdf", "/usr/bin/chromium")]
    stdout = capsys.readouterr().out
    assert f"wrote {out}" in stdout
    assert f"wrote {tmp_path / 'brief.pdf'}" in stdout


def test_render_pdf_flag_with_explicit_path(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    calls = _fake_pdf_seam(monkeypatch)
    out = tmp_path / "brief.html"
    pdf_out = tmp_path / "elsewhere" / "final.pdf"

    assert main([
        "render", "briefing", str(BRIEFING_SAMPLE),
        "-o", str(out), "--pdf", str(pdf_out),
    ]) == 0
    assert calls[0][1] == pdf_out


def test_render_pdf_only_via_out_extension(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    calls = _fake_pdf_seam(monkeypatch)
    out = tmp_path / "brief.pdf"

    assert main(["render", "briefing", str(BRIEFING_SAMPLE), "-o", str(out)]) == 0

    assert out.is_file()
    assert not (tmp_path / "brief.html").exists()
    # The intermediate HTML lived in a temp dir, not next to the PDF.
    intermediate_html = calls[0][0]
    assert intermediate_html.parent != tmp_path
    assert not intermediate_html.exists()


def test_render_pdf_only_conflicts_with_pdf_flag(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    _fake_pdf_seam(monkeypatch)
    out = tmp_path / "brief.pdf"

    assert main([
        "render", "briefing", str(BRIEFING_SAMPLE), "-o", str(out), "--pdf",
    ]) == 2
    assert "--pdf" in capsys.readouterr().err


def test_render_pdf_fails_closed_before_rendering_without_browser(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setattr(
        "jimemo.pdf.find_browser", lambda configured=None, **kw: None
    )
    out = tmp_path / "brief.html"

    assert main(["render", "briefing", str(BRIEFING_SAMPLE), "-o", str(out), "--pdf"]) == 1

    assert not out.exists()  # refused whole invocation, wrote nothing
    assert "[pdf]" in capsys.readouterr().err


def test_render_without_pdf_never_touches_the_browser_seam(tmp_path, monkeypatch):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))

    def boom(*a, **kw):
        raise AssertionError("find_browser must not be called")

    monkeypatch.setattr("jimemo.pdf.find_browser", boom)
    out = tmp_path / "brief.html"
    assert main(["render", "briefing", str(BRIEFING_SAMPLE), "-o", str(out)]) == 0
    assert out.is_file()


# ---------------------------------------------------------------------------
# publish verify gate (Phase 5 Task 8): a fake publisher + _publish_env helper
# stand in for a real jimemo.publish backend. The monkeypatch of
# jimemo.publish.get_publisher mirrors what actually happens end to end --
# the CLI's own lazy import of get_publisher at call time sees the monkeypatched
# version.
# ---------------------------------------------------------------------------

class FakePublisher:
    def __init__(self):
        self.published = []

    def publish(self, path, title=None):
        self.published.append(Path(path))
        return "https://pages.example/abc123/"


def _publish_env(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[publish]\nbackend = "command"\ncommand = "true"\n')
    monkeypatch.setenv("JIMEMO_CONFIG", str(cfg))
    fake = FakePublisher()
    monkeypatch.setattr("jimemo.publish.get_publisher", lambda cfg: fake)
    return fake


def test_publish_refuses_unverified_html(tmp_path, monkeypatch, capsys):
    fake = _publish_env(tmp_path, monkeypatch)
    f = tmp_path / "draft.html"
    f.write_text(BAD_PAGE)

    assert main(["publish", str(f)]) == 1

    err = capsys.readouterr().err
    assert "cdn.example" in err
    assert "--no-verify" in err
    assert fake.published == []


def test_publish_verifies_then_publishes_clean_html(tmp_path, monkeypatch, capsys):
    fake = _publish_env(tmp_path, monkeypatch)
    f = tmp_path / "draft.html"
    f.write_text(GOOD_PAGE)

    assert main(["publish", str(f)]) == 0
    assert fake.published == [f]
    assert "https://pages.example/abc123/" in capsys.readouterr().out


def test_publish_no_verify_skips_the_check(tmp_path, monkeypatch, capsys):
    fake = _publish_env(tmp_path, monkeypatch)
    f = tmp_path / "draft.html"
    f.write_text(BAD_PAGE)

    assert main(["publish", str(f), "--no-verify"]) == 0
    assert fake.published == [f]


def test_publish_non_html_passes_through_unverified(tmp_path, monkeypatch, capsys):
    fake = _publish_env(tmp_path, monkeypatch)
    f = tmp_path / "brief.pdf"
    f.write_bytes(b"%PDF-1.4 fake")

    assert main(["publish", str(f)]) == 0
    assert fake.published == [f]


def test_doctor_reports_pdf_browser_found(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setattr(
        "jimemo.pdf.find_browser", lambda configured=None, **kw: "/usr/bin/chromium"
    )
    assert main(["doctor"]) == 0
    assert "ok   pdf browser (/usr/bin/chromium)" in capsys.readouterr().out


def test_doctor_reports_pdf_browser_missing_without_failing(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setattr(
        "jimemo.pdf.find_browser", lambda configured=None, **kw: None
    )
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "info pdf browser not found" in out
    assert "jimemo pdf unavailable" in out
