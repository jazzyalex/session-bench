#!/usr/bin/env python3
"""Run a loopback-only, content-minimizing OTel logs receiver for one run."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[1]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from session_bench.claude_desktop_otel_usage import (  # noqa: E402
    ClaudeDesktopOtelUsageCapture,
    ClaudeDesktopOtelUsageError,
    PrivateSessionIdAllowlist,
    read_otlp_http_body,
)


class _Server(ThreadingHTTPServer):
    # server_close() must wait for any request handler before the final receipt
    # is written; otherwise an in-flight rejected export could be missed.
    daemon_threads = False
    block_on_close = True
    allow_reuse_address = False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--workload-instance", required=True, type=Path)
    parser.add_argument("--session-id-file", required=True, type=Path)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        workload = json.loads(args.workload_instance.read_text(encoding="utf-8"))
        if not isinstance(workload, dict) or workload.get("run_id") != args.run_id:
            raise ClaudeDesktopOtelUsageError("workload instance run identity mismatch")
        turns = workload.get("turns")
        if not isinstance(turns, list) or len(turns) != 2:
            raise ClaudeDesktopOtelUsageError("workload instance must contain two turns")
        canaries = tuple(turn.get("response_canary") for turn in turns if isinstance(turn, dict))
        if len(canaries) != 2:
            raise ClaudeDesktopOtelUsageError("workload response canaries are malformed")
        if not 1 <= args.port <= 65535:
            raise ClaudeDesktopOtelUsageError("port must be between 1 and 65535")
        session_allowlist = PrivateSessionIdAllowlist(args.session_id_file)
        expected_session_id = session_allowlist.refresh()
        capture = ClaudeDesktopOtelUsageCapture(
            run_id=args.run_id, response_canaries=canaries,  # type: ignore[arg-type]
            expected_session_id=expected_session_id,
        )
        output_input = args.output.expanduser()
        if not output_input.is_absolute():
            raise ClaudeDesktopOtelUsageError("output must be a new absolute path")
        output = output_input.resolve()
        if output.exists() or output.is_symlink():
            raise ClaudeDesktopOtelUsageError("output must be a new absolute path")

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _format: str, *_args: Any) -> None:
                # Request bodies can contain response content; never log request metadata.
                return

            def do_POST(self) -> None:
                if self.path != "/v1/logs":
                    capture.reject_export("unexpected_http_path")
                    self.send_error(404)
                    return
                try:
                    expected_session_id = session_allowlist.refresh()
                    if expected_session_id is None:
                        # Close without reading the export body. Before the
                        # exact-session file is valid, no event content enters
                        # the application or receipt lifecycle.
                        self._send_empty_success()
                        return
                    capture.arm_session_id(expected_session_id)
                    raw = read_otlp_http_body(
                        self.rfile,
                        content_length=self.headers.get("Content-Length"),
                        transfer_encoding=self.headers.get("Transfer-Encoding"),
                    )
                    capture.ingest(raw)
                except Exception:
                    # Framing errors happen before ingest() can invalidate the
                    # capture. Fail closed so a later Ctrl-C cannot save a
                    # seemingly complete receipt after this rejected export.
                    capture.reject_export("invalid_http_framing_or_payload")
                    self.send_error(400)
                    return
                self._send_empty_success()

            def _send_empty_success(self) -> None:
                body = b"{}"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True

        server = _Server(("127.0.0.1", args.port), Handler)
        port = server.server_address[1]
        print(json.dumps({
            "status": "ready",
            "run_id": args.run_id,
            "listener": "127.0.0.1",
            "port": port,
            "logs_endpoint": f"http://127.0.0.1:{port}/v1/logs",
            "shutdown": "Ctrl-C writes the minimized private receipt",
        }, sort_keys=True), flush=True)
        try:
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        final_session_id = session_allowlist.refresh()
        if final_session_id is None:
            raise ClaudeDesktopOtelUsageError(
                "exact-session allowlist was never armed"
            )
        capture.arm_session_id(final_session_id)
        raw_receipt = capture.write_receipt(output)
        print(json.dumps({
            "status": "saved",
            "run_id": args.run_id,
            "output": str(output),
            "event_count": len(capture.events),
            "sha256": __import__("hashlib").sha256(raw_receipt).hexdigest(),
        }, sort_keys=True), flush=True)
        return 0
    except Exception as exc:
        category = getattr(locals().get("capture"), "invalid_category", None)
        reason_code = getattr(locals().get("capture"), "invalid_reason_code", None)
        safe_category = category if category in {
            "unexpected_http_path", "invalid_http_framing_or_payload",
            "http_export_rejected", "invalid_otlp_payload",
        } else "capture_setup_or_shutdown_error"
        safe_reason_code = reason_code if isinstance(reason_code, str) and reason_code.replace("_", "").isalnum() else "unclassified"
        print(
            f"OTel capture invalid: {type(exc).__name__}: {safe_category}/{safe_reason_code}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
