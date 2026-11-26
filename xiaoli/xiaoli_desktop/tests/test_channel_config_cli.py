"""Configuration-only CLI checks; neither WeChat nor OCR is started."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from channel_config import DEFAULT_CONFIG, parse_routes, save_route


SCRIPT = Path(__file__).resolve().parents[1] / "agent_channel.py"


class ConfigCLI(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.workdir = Path(workspace.name)
        self.config = self.workdir / "new" / "channel.json"

    def cli(self, command, answers=""):
        return subprocess.run([sys.executable, "-S", "-X", "utf8", str(SCRIPT),
                               "--config", str(self.config), command], input=answers,
                              cwd=self.workdir, capture_output=True, encoding="utf-8", timeout=12)

    def test_first_run_edit_and_existing_agent_check(self):
        events = []
        class ExistingAgent(BaseHTTPRequestHandler):
            def do_POST(self):
                events.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"action":"silent"}')
            def log_message(self, *args):
                pass

        with HTTPServer(("127.0.0.1", 0), ExistingAgent) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                url = f"http://localhost:{server.server_port}/decide"
                self.assertEqual(self.cli("setup", f"1\n好友甲\n{url}\nn\nn\n").returncode, 0)
                self.assertEqual(self.cli("setup", f"2\n群聊乙\n{url}\nn\n").returncode, 0)
                self.assertEqual(self.cli("setup", "1\n好友甲\n\ny\n\n").returncode, 0)
                doc = json.loads(self.config.read_text(encoding="utf-8"))
                self.assertEqual(len(doc["conversations"]), 2)
                self.assertTrue(doc["conversations"][0]["proactive"])
                self.assertEqual(doc["ocr"], "v4")
                result = self.cli("check")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(events), 2)
                self.assertTrue(all(e["id"].startswith("probe-") for e in events))
                self.assertTrue(all(e["conversation"]["title"] not in ("好友甲", "群聊乙") for e in events))
                self.assertEqual(set(events[0]), {"version", "id", "conversation", "context",
                                                   "visible", "new_from", "allowed_actions"})
                self.assertFalse(self.config.with_name("channel.sqlite3").exists())
            finally:
                server.shutdown()
                worker.join(timeout=2)

    def test_errors_point_to_field_and_preserve_original(self):
        result = self.cli("setup", "1\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.config.exists())
        result = self.cli("listen")
        self.assertIn("先运行 setup", result.stderr)
        self.config.parent.mkdir()
        self.config.write_text('{\n broken', encoding="utf-8")
        result = self.cli("setup")
        self.assertIn("第 2 行", result.stderr)
        self.assertEqual(self.config.read_text(encoding="utf-8"), '{\n broken')
        with self.assertRaisesRegex(ValueError, "第 1 个会话.*本机 HTTP"):
            parse_routes({"conversations": [{"kind": "friend", "title": "你好", "endpoint": 55}]})

    def test_check_reports_nonworking_agent_path(self):
        class MissingPath(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_error(404)

            def log_message(self, *args):
                pass

        with HTTPServer(("127.0.0.1", 0), MissingPath) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/wrong"
                result = self.cli("setup", f"2\n测试群\n{url}\nn\n")
                self.assertEqual(result.returncode, 0, result.stderr)
                result = self.cli("check")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(url, result.stderr)
                self.assertIn("HTTP 404", result.stderr)
                self.assertFalse(self.config.with_name("channel.sqlite3").exists())
            finally:
                server.shutdown()
                worker.join(timeout=2)

    def test_edit_preserves_other_routes_and_detects_conflicting_change(self):
        url = "http://127.0.0.1:8765/decide"
        self.assertEqual(self.cli("setup", f"1\n甲乙\n{url}\nn\nn\n").returncode, 0)
        with self.assertRaisesRegex(ValueError, "发生变化"):
            save_route(self.config, "friend", "甲乙", {"endpoint": url}, None)
        policy = parse_routes(json.loads(self.config.read_text(encoding="utf-8")))[("friend", "甲乙")]
        self.assertFalse(policy.proactive)
        self.assertEqual(DEFAULT_CONFIG, SCRIPT.parents[2] / "local-data" / "channel.json")
