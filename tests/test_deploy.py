import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
PRIVATE_HOME = "".join(("/", "home", "/", "pra", "nesta"))


def test_units_stay_on_paper_scan_without_a_private_home():
    service = (DEPLOY / "polymarket-weather-bot.service").read_text(encoding="utf-8")
    timer = (DEPLOY / "polymarket-weather-bot.timer").read_text(encoding="utf-8")
    script = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    assert "WorkingDirectory=@INSTALL_DIR@" in service
    assert "ExecStart=@INSTALL_DIR@/.venv/bin/weatherbot scan" in service
    assert "User=@SERVICE_USER@" in service
    assert "ReadWritePaths=@INSTALL_DIR@/data" in service
    assert "OnCalendar=*:0/30" in timer
    assert "Persistent=false" in timer
    assert "OnActiveSec" not in timer
    assert "OnBootSec" not in timer
    assert 'INSTALL_DIR="${INSTALL_DIR:-$ROOT}"' in script
    assert "@INSTALL_DIR@" in script
    assert "@SERVICE_USER@" in script
    assert "pull --ff-only" not in script
    for text in (service, timer, script):
        lowered = text.lower()
        assert PRIVATE_HOME not in text
        for banned in ("private_key", "telegram", "wallet", "--live"):
            assert banned not in lowered


def test_published_tree_leaves_out_secrets_and_the_private_home():
    assert not (ROOT / "config" / "telegram.env").exists()
    assert not (ROOT / "config" / "wallet.env").exists()
    assert not list(ROOT.rglob("*.sqlite"))
    assert not list(ROOT.rglob("*.sqlite3"))
    cli = (ROOT / "src" / "weatherbot" / "cli.py").read_text(encoding="utf-8")
    assert '"paper"' in cli and '"live"' in cli
    service = (DEPLOY / "polymarket-weather-bot.service").read_text(encoding="utf-8")
    assert "ExecStart=@INSTALL_DIR@/.venv/bin/weatherbot scan" in service
    assert "--live" not in service
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {".git", ".venv", "__pycache__", ".pytest_cache"} for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8")
        assert PRIVATE_HOME not in text, path
        for secret_name in ("telegram.env", "wallet.env"):
            assert secret_name not in path.name or path.name.endswith(".example")


def test_install_requires_python_311(tmp_path: Path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    python = bindir / "python3"
    python.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    python.chmod(0o755)
    result = subprocess.run(
        ["bash", str(DEPLOY / "install.sh")],
        check=False,
        capture_output=True,
        text=True,
        env={"PATH": f"{bindir}:/usr/bin:/bin"},
    )
    assert result.returncode == 1
    assert "Python 3.11 or newer is required." in result.stderr
    assert PRIVATE_HOME not in result.stderr


def test_install_check_only_stays_inside_the_clone(tmp_path: Path):
    clone = tmp_path / "clone"
    clone.mkdir()
    (clone / "pyproject.toml").write_text("[project]\nname = 'weatherbot'\n", encoding="utf-8")
    result = subprocess.run(
        ["bash", str(DEPLOY / "install.sh")],
        check=False,
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "INSTALL_DIR": str(clone),
            "WEATHERBOT_INSTALL_CHECK_ONLY": "1",
        },
    )
    assert result.returncode == 0, result.stderr
    assert f"Install directory: {clone}" in result.stdout
    assert PRIVATE_HOME not in result.stdout + result.stderr
    assert not (clone / ".venv").exists()
    assert not (clone / ".git").exists()


def test_install_refuses_a_directory_that_is_not_this_project(tmp_path: Path):
    result = subprocess.run(
        ["bash", str(DEPLOY / "install.sh")],
        check=False,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "INSTALL_DIR": str(tmp_path / "empty")},
    )
    assert result.returncode == 1
    assert "No project at" in result.stderr
    assert "Clone the repository" in result.stderr
