"""Run every discovered test module serially in isolated Qt/runtime processes."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def normalized_path(value) -> Path:
    resolved = str(Path(os.fsdecode(value)).resolve())
    if resolved.startswith("\\\\?\\UNC\\"):
        resolved = "\\\\" + resolved[8:]
    elif resolved.startswith("\\\\?\\"):
        resolved = resolved[4:]
    return Path(resolved)


def test_environment(profile: Path) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        LETTER_SMITH_PROFILE_ROOT=str(profile.resolve()),
        TEMP=str(profile.resolve()), TMP=str(profile.resolve()),
        PYTHONDONTWRITEBYTECODE="1", QT_QPA_PLATFORM="offscreen",
        QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu --host-resolver-rules="MAP * ~NOTFOUND , EXCLUDE localhost"',
    )
    return environment


def run_worker(module: str, output: Path) -> int:
    import faulthandler
    import unittest

    faulthandler.enable()
    faulthandler.dump_traceback_later(60, repeat=True)
    sys.path[:0] = [str(PROJECT_ROOT), str(PROJECT_ROOT / "test")]
    from project_paths import ApplicationPaths, configure_application_paths
    paths = configure_application_paths(ApplicationPaths.for_runtime(PROJECT_ROOT))
    if paths.profile_root is None:
        raise RuntimeError("Verification requires an isolated runtime profile.")
    paths.initialize()
    os.chdir(paths.workspace_root)
    from PySide6 import QtCore
    QtCore.QSettings.setDefaultFormat(QtCore.QSettings.IniFormat)
    QtCore.QSettings.setPath(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope,
                           str(paths.settings_root))

    writable_root = normalized_path(output.parent)

    def isolation_guard(event, arguments):
        if event == "socket.connect" and isinstance(arguments[1], tuple):
            if arguments[1][0] not in ("127.0.0.1", "::1", "localhost"):
                raise RuntimeError("External networking is disabled during verification.")
        candidates = ()
        if event == "open":
            path, mode, flags = arguments
            if (mode and any(char in mode for char in "wax+")) or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
                candidates = (path,)
        elif event in ("os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.truncate"):
            candidates = arguments[:1]
        elif event in ("os.rename", "os.link"):
            candidates = arguments[:2]
        elif event == "os.symlink":
            candidates = arguments[1:2]
        for path in candidates:
            if isinstance(path, (str, bytes, os.PathLike)):
                if os.fsdecode(path).casefold() in (os.devnull.casefold(), "\\\\.\\nul"):
                    continue
                resolved = normalized_path(path)
                if not resolved.is_relative_to(writable_root):
                    raise PermissionError(f"Verification write outside isolated evidence: {resolved}")

    sys.addaudithook(isolation_guard)
    suite = unittest.defaultTestLoader.loadTestsFromName(module)
    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {
        "module": module, "tests": result.testsRun,
        "seconds": round(time.monotonic() - started, 3),
        "failures": [test.id() for test, _ in result.failures],
        "errors": [test.id() for test, _ in result.errors],
        "skipped": [(test.id(), reason) for test, reason in result.skipped],
        "expected_failures": [test.id() for test, _ in result.expectedFailures],
        "unexpected_successes": [test.id() for test in result.unexpectedSuccesses],
        "passed": result.wasSuccessful(),
    }
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    faulthandler.cancel_dump_traceback_later()
    return 0 if result.wasSuccessful() else 1


def verify(output: Path, modules: list[str], timeout: int) -> int:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    discovered = sorted(path.stem for path in (PROJECT_ROOT / "test").glob("test_*.py"))
    selected = modules or discovered
    if any(name.split(".")[0] not in discovered for name in selected):
        raise ValueError("Select an existing test module or test within it.")
    report = {"source_root": str(PROJECT_ROOT), "discovered_modules": discovered,
              "selected": selected, "python": sys.version, "runs": []}
    try:
        report["revision"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True).strip()
        report["working_tree"] = subprocess.check_output(
            ["git", "status", "--short"], cwd=PROJECT_ROOT, text=True)
    except (OSError, subprocess.CalledProcessError):
        report["revision"] = "unavailable"
    failed = False
    for index, module in enumerate(selected):
        profile = output / f"p{index:02}"
        profile.mkdir()
        result_path = output / f"{module}.json"
        log_path = output / f"{module}.log"
        print(f"[{index + 1}/{len(selected)}] {module}", flush=True)
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--worker", module,
                 "--output", str(result_path)], cwd=PROJECT_ROOT,
                env=test_environment(profile), stdout=log, stderr=subprocess.STDOUT,
            )
            timed_out = False
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                # Only the process tree started above belongs to this check.
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=log, stderr=subprocess.STDOUT, check=False)
                else:
                    process.kill()
                code = process.wait()
        run = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {"module": module}
        run.update(exit_code=code, timed_out=timed_out, log=str(log_path))
        report["runs"].append(run)
        failed |= code != 0 or timed_out
        (output / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"  {'FAILED' if code or timed_out else 'passed'}: {run.get('tests', 'no completed count')} tests", flush=True)
    return int(failed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path,
                        help="New evidence directory; retains profiles, logs and JSON results.")
    parser.add_argument("--module", action="append", default=[])
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        return run_worker(args.worker, args.output)
    return verify(args.output, args.module, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
