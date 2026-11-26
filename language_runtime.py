"""Verified, offline model execution for LetterSmith writing corrections."""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time

MODEL = "Qwen3VL-4B-Instruct-Q4_K_M.gguf"
PROJECTOR = "mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf"
MODEL_HASHES = {
    MODEL: "66358cb18bb6b3b1b6675aa412c7a88ef01d228f481184d13668e5201c730a0a",
    PROJECTOR: "30ba2c7dd3127a4561b6cba9d13d0f711c91bdb38742e2f56d73c8cb596bd06d",
}
MODEL_VERSION = "qwen3-vl-4b-q4-1cd86afb-llama-b11969"


class Cancelled(Exception):
    pass


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def runtime_hashes():
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    manifest = (root / "resources" / "language-runtime" / "language_runtime_hashes.json"
                if getattr(sys, "frozen", False) else root / "language_runtime_hashes.json")
    hashes = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(hashes, dict) or "llama-server.exe" not in hashes or any(
        not isinstance(name, str) or Path(name).name != name or not isinstance(value, str)
        or len(value) != 64 for name, value in hashes.items()
    ):
        raise ValueError("The writing engine manifest is invalid. Repair LetterSmith.")
    return hashes


def verify_pack(root: Path, cancelled=lambda: False) -> Path:
    # Windows packaged-app redirection may resolve files into LocalCache while
    # their apparent parent still resolves to Roaming. Anchor the actual pack.
    root = (root / MODEL).resolve(strict=True).parent
    trusted = runtime_hashes()
    expected = {**MODEL_HASHES, **{f"runtime/{name}": digest for name, digest in trusted.items()}}
    for name, expected_hash in expected.items():
        if cancelled():
            raise Cancelled()
        path = (root / name).resolve(strict=True)
        if not path.is_relative_to(root) or digest_file(path, cancelled) != expected_hash:
            raise ValueError(f"Model pack verification failed: {name}")
    # DLL search must not admit unverified libraries placed beside the executable.
    for path in (root / "runtime").iterdir():
        if path.suffix.lower() in {".exe", ".dll"} and path.name not in trusted:
            raise ValueError(f"Unrecognized runtime file: {path.name}")
    return root


def digest_file(path, cancelled=lambda: False):
    from dictation_models import digest
    return digest(path, cancelled)


class LocalRuntime:
    allowed_endpoints = frozenset({"/health", "/v1/chat/completions"})
    memory_limit = 16 * 1024**3

    def __init__(self, pack: str | Path, *, device="auto", cancelled=lambda: False):
        if device not in {"auto", "cpu", "Vulkan0", "Vulkan1", "Vulkan2"}:
            raise ValueError("Unsupported inference device")
        self.pack, self.device, self.cancelled = Path(pack), device, cancelled
        self.process = self.job = None
        self.port = 0
        self.token = secrets.token_hex(32)
        self.stats = {"requests": 0, "failed_requests": 0, "starts": 0, "inference_seconds": 0.0, "startup_seconds": 0.0}

    def _verify_pack(self):
        return verify_pack(self.pack, self.cancelled)

    def _runtime_directory(self, root):
        return root / "runtime"

    def _command(self, root):
        command = [str(root / "runtime" / "llama-server.exe"), "--model", str(root / MODEL),
                   "--mmproj", str(root / PROJECTOR), "--offline", "--host", "127.0.0.1", "--port", str(self.port),
                   "--api-key", self.token, "--no-webui", "--no-agent", "--no-webui-mcp-proxy",
                   "--cors-origins", "http://127.0.0.1", "--no-cors-credentials",
                   "--ctx-size", "8192", "--parallel", "1", "--image-max-tokens", "1536",
                   "--no-warmup", "--no-context-shift", "--log-disable", "--threads", "8"]
        if self.device == "cpu":
            command += ["--device", "none", "--gpu-layers", "0", "--no-mmproj-offload"]
        elif self.device != "auto":
            command += ["--device", self.device, "--gpu-layers", "all"]
        return command

    def __enter__(self):
        from dictation_speech import ProcessJob, external_library_search
        started = time.monotonic()
        root = self._verify_pack()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        runtime = self._runtime_directory(root)
        command = self._command(root)
        # Do not inherit LLAMA_ARG_TOOLS, model URLs, proxies, MCP configuration,
        # loader overrides or remote-provider credentials from the host environment.
        environment = {key: value for key, value in os.environ.items()
                       if key.upper() in {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "TEMP", "TMP"}}
        environment["PATH"] = str(runtime) + os.pathsep + str(Path(os.environ.get("SYSTEMROOT", "C:/Windows")) / "System32")
        try:
            with external_library_search():
                self.process = subprocess.Popen(command, cwd=runtime, env=environment,
                                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.job = ProcessJob(self.process, memory_limit=self.memory_limit)
            while time.monotonic() - started < 120:
                if self.cancelled():
                    raise Cancelled()
                if self.process.poll() is not None:
                    raise RuntimeError("The local model could not start. Verify the model pack or select CPU mode.")
                try:
                    self._request("GET", "/health", None, 1)
                    self.stats["startup_seconds"] += round(time.monotonic() - started, 3)
                    self.stats["starts"] += 1
                    return self
                except (OSError, RuntimeError, ValueError, http.client.HTTPException):
                    time.sleep(.1)
            raise TimeoutError("Local model startup exceeded 120 seconds")
        except BaseException:
            self.close()
            raise

    def _request(self, method, endpoint, payload, timeout=120):
        if endpoint not in self.allowed_endpoints:
            raise ValueError("Unallowlisted local endpoint")
        if self.cancelled():
            raise Cancelled()
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            data = json.dumps(payload).encode() if payload is not None else None
            connection.request(method, endpoint, body=data,
                               headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
            response = connection.getresponse()
            body = response.read(2 * 1024**2 + 1)
            if response.status != 200 or len(body) > 2 * 1024**2:
                raise RuntimeError(f"Local inference request failed ({response.status})")
            return json.loads(body)
        finally:
            connection.close()

    def generate(self, system: str, text: str, *, images=(), schema=None, max_tokens=1400):
        if schema:
            # Grammar constrains syntax but does not tell the model the schema's
            # semantics. Supply the contract explicitly as trusted instructions.
            system += "\nYour response must match this JSON schema:\n" + json.dumps(schema)
        content = [{"type": "text", "text": text}]
        for image in images:
            raw = Path(image).read_bytes()
            if len(raw) > 8 * 1024**2:
                raise ValueError("Image exceeds inference limit")
            content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(raw).decode()}})
        payload = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
                   "temperature": 0, "seed": 42, "max_tokens": max_tokens, "stream": False,
                   "chat_template_kwargs": {"enable_thinking": False}}
        if schema:
            payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "holo_file", "strict": True, "schema": schema}}
        if self.process is None:
            self.__enter__()
        started = time.monotonic()
        try:
            result = self._request("POST", "/v1/chat/completions", payload)
        except (OSError, http.client.HTTPException, RuntimeError):
            # A timed-out server may still be generating. Retire it before the
            # next file so its work cannot queue or contaminate later requests.
            self.stats["failed_requests"] += 1
            self.stats["inference_seconds"] += time.monotonic() - started
            self.close()
            raise
        self.stats["requests"] += 1
        self.stats["inference_seconds"] += time.monotonic() - started
        choice = result["choices"][0]
        if choice.get("message", {}).get("tool_calls"):
            raise ValueError("Tool calls are not supported by file analysis")
        if choice.get("finish_reason") == "length":
            raise ValueError("Analysis exceeded the output limit; review this file separately")
        value = choice["message"].get("content", "")
        return json.loads(value) if schema else value.strip()

    def close(self):
        if self.job:
            self.job.close()
            self.job = None
        if self.process:
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=10)
            self.process = None

    def __exit__(self, *_):
        self.close()
