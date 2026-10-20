from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FaultDiagnosticsTests(unittest.TestCase):
    def _run_child(self, source: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "faulthandler",
                    "-c",
                    textwrap.dedent(source),
                    directory,
                ],
                cwd=PROJECT_ROOT,
                env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=20,
            )
        self.assertEqual(
            result.returncode,
            0,
            f"Child diagnostics check failed:\n{result.stdout}\n{result.stderr}",
        )
        return result

    def test_log_retains_descriptor_and_captures_all_threads(self) -> None:
        self._run_child(
            """
            import faulthandler
            import gc
            import logging
            import os
            from pathlib import Path
            import sys
            import threading
            from unittest import mock
            import fault_diagnostics as diagnostics

            directory = Path(sys.argv[1])
            with mock.patch.object(
                faulthandler, "enable", wraps=faulthandler.enable
            ) as enabled:
                path = diagnostics.enable_fault_logging(directory)
            assert path is not None and path.is_file()
            descriptor = diagnostics._fault_fd
            assert isinstance(descriptor, int)
            assert enabled.call_args.kwargs["all_threads"] is True
            assert enabled.call_args.kwargs["file"] == descriptor
            assert diagnostics.enable_fault_logging(directory) == path
            assert diagnostics._fault_fd == descriptor
            assert not any(name.startswith("PySide6") for name in sys.modules)

            diagnostics.record_fault_context("test checkpoint before shutdown")
            logging.shutdown()
            gc.collect()
            os.fstat(descriptor)
            diagnostics.record_fault_context("test checkpoint after shutdown")

            ready = threading.Event()
            release = threading.Event()
            def sentinel_fault_worker():
                ready.set()
                release.wait(5)
            worker = threading.Thread(target=sentinel_fault_worker)
            worker.start()
            assert ready.wait(5)
            try:
                faulthandler.dump_traceback(file=descriptor, all_threads=True)
            finally:
                release.set()
                worker.join(5)
            assert not worker.is_alive()
            content = path.read_text(encoding="utf-8")
            assert "test checkpoint before shutdown" in content
            assert "test checkpoint after shutdown" in content
            assert "sentinel_fault_worker" in content
            assert sys.version.split()[0] in content
            assert faulthandler.is_enabled()
            """
        )

    def test_unwritable_log_directory_preserves_existing_handler(self) -> None:
        result = self._run_child(
            """
            import faulthandler
            from pathlib import Path
            import sys
            import fault_diagnostics as diagnostics

            blocked = Path(sys.argv[1]) / "ordinary-file"
            blocked.write_text("not a directory", encoding="utf-8")
            assert faulthandler.is_enabled()
            assert diagnostics.enable_fault_logging(blocked / "logs") is None
            assert faulthandler.is_enabled()
            diagnostics.record_fault_context("unavailable log checkpoint")
            faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
            """
        )
        self.assertIn("Current thread", result.stderr)

    def test_context_stays_bounded_on_one_line(self) -> None:
        self._run_child(
            """
            from pathlib import Path
            import sys
            import fault_diagnostics as diagnostics

            path = diagnostics.enable_fault_logging(Path(sys.argv[1]))
            diagnostics.record_fault_context(
                "bounded checkpoint" + chr(13) + chr(10) + "x" * 2000
            )
            lines = path.read_text(encoding="utf-8").splitlines()
            assert len(lines) == 2
            assert "bounded checkpoint  " in lines[-1]
            assert len(lines[-1].split(" ", 1)[1]) <= 400
            """
        )

    def test_unwritable_log_directory_enables_stderr_when_needed(self) -> None:
        self._run_child(
            """
            import faulthandler
            from pathlib import Path
            import sys
            import fault_diagnostics as diagnostics

            blocked = Path(sys.argv[1]) / "ordinary-file"
            blocked.write_text("not a directory", encoding="utf-8")
            faulthandler.disable()
            assert diagnostics.enable_fault_logging(blocked / "logs") is None
            assert faulthandler.is_enabled()
            diagnostics.record_fault_context("stderr fallback checkpoint")
            """
        )

    def test_handler_configuration_failure_does_not_abort_startup(self) -> None:
        self._run_child(
            """
            import faulthandler
            from pathlib import Path
            import sys
            from unittest import mock
            import fault_diagnostics as diagnostics

            assert faulthandler.is_enabled()
            with mock.patch.object(
                faulthandler, "enable", side_effect=OSError("descriptor unavailable")
            ):
                assert diagnostics.enable_fault_logging(Path(sys.argv[1])) is None
            assert faulthandler.is_enabled()
            diagnostics.record_fault_context("failed handler checkpoint")
            """
        )


if __name__ == "__main__":
    unittest.main()
