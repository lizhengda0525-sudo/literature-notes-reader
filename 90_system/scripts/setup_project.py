"""Complete this project's local environment without changing global packages."""
from __future__ import annotations
import argparse
import tempfile
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "90_system/scripts"))
from literature import Workspace, WorkspaceError, dump, now, sha, load, safe_path, skill_package_errors, temporary_path

def run(args, timeout=180, live=False):
    command = [str(a) for a in args]
    if live:
        result = subprocess.run(command, cwd=ROOT, timeout=timeout)
        if result.returncode:
            raise RuntimeError(f"Command failed with exit {result.returncode}; see the progress output above.")
        return ""
    result = subprocess.run(command, cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout)[-1800:].strip())
    return result.stdout


def git_setup(no_commit=False):
    git = shutil.which("git")
    if not git:
        raise RuntimeError("Git is not available on PATH.")
    probe = subprocess.run([git, "-C", str(ROOT), "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    if probe.returncode == 0 and Path(probe.stdout.strip()).resolve() != ROOT:
        raise RuntimeError("The workspace is inside another repository; review that repository before initializing.")
    if probe.returncode:
        run([git, "init", "-b", "main"])
    run([git, "config", "--local", "core.autocrlf", "false"])
    has_head = subprocess.run([git, "rev-parse", "--verify", "HEAD"], cwd=ROOT,
                              capture_output=True).returncode == 0
    committed = False
    if not no_commit and not has_head:
        run([git, "add", "--all"])
        changed = subprocess.run([git, "diff", "--cached", "--quiet"], cwd=ROOT).returncode
        if changed == 1:
            run([git, "-c", "user.name=Codex", "-c", "user.email=codex@local.invalid",
                 "-c", "commit.gpgsign=false", "commit", "-m", "Initialize literature workspace"])
            committed = True
    return {"repository": str(ROOT), "initial_commit_created": committed,
            "remote_configured": False}


def install_primary_skill():
    """Activate a verified project skill, preserving the current copy on failure."""
    config_path = ROOT / "90_system/workspace.json"
    config = load(config_path)
    primary = config["primary_skill"]
    source = safe_path(ROOT, primary["path"])
    target = safe_path(ROOT, primary["native_path"])
    errors = skill_package_errors(source)
    if errors:
        raise RuntimeError("Primary skill is not ready: " + "; ".join(errors))
    if source == target:
        return {"native_skill": str(target), "updated": False}
    if source.is_relative_to(target) or target.is_relative_to(source):
        raise RuntimeError("Skill source and native target must be separate directories.")
    if target.exists():
        errors = skill_package_errors(target)
        if errors:
            raise RuntimeError("Native target contains unverified content; retained: " + "; ".join(errors))
    # Both sources are verified and all move/delete targets stay inside this project.
    cache = temporary_path(ROOT)
    cache.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="skill-update-", dir=cache)).resolve()
    if not temporary.is_relative_to(cache) or temporary == cache:
        raise RuntimeError("Skill update recovery path leaves the project cache.")
    previous = temporary / "previous"
    moved = False
    replaced = False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.replace(previous)
            replaced = True
        source.replace(target)
        moved = True
        errors = skill_package_errors(target)
        if errors:
            raise RuntimeError("; ".join(errors))
        primary["path"] = primary["native_path"]
        dump(config_path, config)
    except BaseException:
        try:
            if moved:
                target.replace(source)
            if replaced:
                previous.replace(target)
        except OSError as error:
            raise RuntimeError(f"Skill update recovery failed; files retained in {temporary}") from error
        temporary.rmdir()
        raise
    shutil.rmtree(temporary)
    if source.parent != ROOT and source.parent.is_dir() and not any(source.parent.iterdir()):
        source.parent.rmdir()
    return {"native_skill": str(target), "updated": True}

def environment_setup():
    env = ROOT / ".venv"
    if not env.resolve().is_relative_to(ROOT):
        raise RuntimeError("The project environment points outside this workspace.")
    python = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    cli = env / ("Scripts/zotero-cli.exe" if os.name == "nt" else "bin/zotero-cli")
    ready = env / ".workspace-ready"
    requirements = ROOT / "90_system/environment/requirements.in"
    input_hash = sha(requirements)
    if ready.is_file() and python.is_file() and cli.is_file():
        marker = json.loads(ready.read_text(encoding="utf-8"))
        if marker.get("requirements_sha256") == input_hash:
            run([python, "-B", "-c", "import importlib.metadata as m; import pypdf; assert m.version('zotero-mcp-server') == '0.15.1'; assert pypdf.__version__ == '6.10.2'"])
            run([cli, "--help"], timeout=30)
            return {"python": str(python), "cli": str(cli), "dependency_route": "existing_ready"}
    if ready.exists():
        ready.unlink()
    cache = temporary_path(ROOT)
    cache.mkdir(parents=True, exist_ok=True)
    original_temp = {key: os.environ.get(key) for key in ("TEMP", "TMP")}
    try:
        with tempfile.TemporaryDirectory(prefix="pip-build-", dir=cache) as build:
            os.environ.update({"TEMP": build, "TMP": build})
            if not python.is_file():
                venv.EnvBuilder(with_pip=True).create(env)
            pip_available = subprocess.run([str(python), "-B", "-m", "pip", "--version"],
                                          cwd=ROOT, capture_output=True).returncode == 0
            if not pip_available:
                run([python, "-B", "-m", "ensurepip", "--default-pip"])
            frozen_path = ROOT / "90_system/environment/requirements.freeze.txt"
            input_path = ROOT / "90_system/environment/requirements-input.sha256"
            repeat = frozen_path.is_file() and input_path.is_file() and input_path.read_text().strip() == input_hash
            selected = frozen_path if repeat else requirements
            run([python, "-B", "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
                 "--retries", "1", "--timeout", "20", "--prefer-binary", "--no-cache-dir",
                 "--index-url", "https://pypi.org/simple", "-r", selected], timeout=600, live=True)
            run([python, "-B", "-m", "pip", "check"])
            run([python, "-B", "-c", "import importlib.metadata as m; import pypdf; assert m.version('zotero-mcp-server') == '0.15.1'; assert pypdf.__version__ == '6.10.2'"])
            run([cli, "--help"], timeout=30)
            frozen_path.write_text(run([python, "-B", "-m", "pip", "freeze", "--all"]), encoding="utf-8")
            input_path.write_text(input_hash + "\n", encoding="ascii")
            dump(ready, {"ready_at": now(), "requirements_sha256": input_hash, "dependency_route": "pip_with_freeze"})
    finally:
        for key, value in original_temp.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return {"python": str(python), "cli": str(cli), "dependency_route": "pip_with_freeze"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--skip-env", action="store_true")
    parser.add_argument("--skip-git", action="store_true")
    parser.add_argument("--skip-skill", action="store_true")
    parser.add_argument("--no-commit", action="store_true")
    args = parser.parse_args()
    ws = Workspace(ROOT)
    if args.check:
        result = ws.doctor(probe=False)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    report = {"started_at": now(), "steps": [], "skipped": {"environment": args.skip_env, "git": args.skip_git, "skill": args.skip_skill}}
    steps = [("project_rules", lambda: {"enabled": (ROOT / "AGENTS.md").is_file()})]
    if not args.skip_skill:
        steps.append(("primary_reading_skill", install_primary_skill))
    if not args.skip_env:
        steps.append(("project_environment", environment_setup))
    for name, function in steps:
        print(f"[setup] {name}", flush=True)
        try:
            detail = function()
            success = detail.get("enabled", True)
            report["steps"].append({"name": name, "ok": success, "detail": detail})
        except (RuntimeError, OSError, ValueError, KeyError, TypeError, WorkspaceError, subprocess.TimeoutExpired) as error:
            report["steps"].append({"name": name, "ok": False, "error": str(error)[:2000]})
    ws = Workspace(ROOT)
    try:
        report["workspace"] = ws.check()
        ws.index()
        report["health"] = ws.doctor(probe=not args.skip_env)
    except (WorkspaceError, OSError, ValueError, KeyError) as error:
        report["workspace"] = {"ok": False, "error": str(error)}
    # Commit only after configuration has been checked; existing repositories are never auto-committed.
    if not args.skip_git:
        try:
            detail = git_setup(args.no_commit)
            report["steps"].append({"name": "project_git", "ok": True, "detail": detail})
        except (RuntimeError, OSError, ValueError, KeyError, TypeError, WorkspaceError, subprocess.TimeoutExpired) as error:
            report["steps"].append({"name": "project_git", "ok": False, "error": str(error)[:2000]})
    report["finished_at"] = now()
    report["scope"] = "local_only" if args.skip_env else "full"
    report["health"] = ws.doctor(probe=not args.skip_env)
    report["configuration_ok"] = all(step["ok"] for step in report["steps"]) and report.get("workspace", {}).get("ok", False)
    report["full_ready"] = report["health"]["ok"]
    report["ok"] = report["configuration_ok"] and (args.skip_env or report["full_ready"])
    report["next"] = "Run lit.cmd doctor --probe-zotero; finish any failed environment/connection steps."
    # Report failures in stdout; do not accumulate intermediate setup reports.
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
