"""Offline integration tests: real inbox, outbox and HTTP; synthetic WeChat window."""
import json
from contextlib import closing
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.util
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from agent_channel import (CLARIFY, Policy, call_agent, capture, complete_report, decide_one,
                           deliver_one, endpoint, enqueue, load_routes, open_store, poll_once,
                           resolve_unknown)
from wx_backend.models import MessageType, WeChatMessage

FRIEND = ("friend", "同名会话")
GROUP = ("group", "同名会话")
ANOTHER = ("friend", "其他好友")
URL = "http://127.0.0.1:8765/decide"


def msg(route, text, sender="小陈", kind=MessageType.TEXT):
    return WeChatMessage("visual_1", route[1], sender, text, kind)


class Window:
    def __init__(self, screens=()):
        self.screens = screens
        self.route = None
        self.messages = []
        self.reads = []
        self.sent = []
        self.allow_switch = True
        self.accept_key = True

    def iter_unread_sessions(self):
        for self.route, self.messages in self.screens:
            yield (0, 0)

    def is_foreground(self):
        return self.route is not None

    def current_conversation(self):
        return self.route

    def get_messages(self, chat, assume_switched, expected_route):
        assert expected_route == self.route
        self.reads.append(self.route)
        return self.messages

    def mark_session_read(self):
        pass

    def select_conversation(self, route):
        if self.allow_switch:
            self.route = route
        return self.route == route

    def send_text(self, title, text, expected_kind):
        if self.accept_key and self.route == (expected_kind, title):
            self.sent.append((self.route, text))
            return True
        return False


class ChannelTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "channel.sqlite3"
        self.db = open_store(self.path)
        self.addCleanup(lambda: self.db.close())
        self.routes = {FRIEND: Policy(URL, private_reports=True), GROUP: Policy(URL, proactive=True)}
        self.wx = Window()
        self.agent = Mock(return_value={"action": "reply", "text": "收到"})

    def test_private_and_group_with_same_title(self):
        self.wx.screens = [(FRIEND, [msg(FRIEND, "私密问题")]), (GROUP, [msg(GROUP, "群问题")])]
        poll_once(self.db, self.routes, self.wx, self.agent)
        decide_one(self.db, self.routes, self.agent)
        deliver_one(self.db, self.routes, self.wx)
        events = [c.args[1] for c in self.agent.call_args_list]
        self.assertEqual([e["visible"][0]["text"] for e in events], ["私密问题", "群问题"])
        self.assertEqual([e["allowed_actions"] for e in events],
                         [["silent", "reply", "defer"], ["silent", "reply"]])
        self.assertEqual(self.wx.sent, [(FRIEND, "收到"), (GROUP, "收到")])

    def test_foreground_without_badge_and_excluded_input(self):
        self.wx.route = GROUP
        self.wx.messages = [msg(GROUP, "启动前的历史")]
        poll_once(self.db, self.routes, self.wx, self.agent)
        self.agent.assert_not_called()
        self.wx.messages = [msg(GROUP, "自己", "self"), msg(GROUP, "文件", kind=MessageType.FILE),
                            msg(ANOTHER, "其他对话"), msg(GROUP, "当前窗口")]
        poll_once(self.db, self.routes, self.wx, self.agent)
        self.wx.screens = [(ANOTHER, [msg(ANOTHER, "其他好友")])]
        poll_once(self.db, self.routes, self.wx, self.agent)
        self.assertEqual(self.wx.reads, [GROUP, GROUP])
        self.assertEqual(self.agent.call_args.args[1]["visible"], [{"sender":"小陈","text":"当前窗口"}])

    def test_changed_window_during_capture_discards_batch(self):
        self.wx.screens = [(FRIEND, [msg(FRIEND, "私聊")])]
        self.wx.current_conversation = Mock(side_effect=[FRIEND, GROUP])
        poll_once(self.db, self.routes, self.wx, self.agent)
        self.agent.assert_not_called()
        self.assertFalse(self.wx.sent)

    def test_viewport_gap_missing_option_asks_without_agent(self):
        capture(self.db, GROUP, [msg(GROUP, "昨天的讨论")])
        decide_one(self.db, self.routes, Mock(return_value={"action":"silent"}))
        capture(self.db, GROUP, [msg(GROUP, "方案一：上午开会"), msg(GROUP, "那就按第二个方案")])
        decide_one(self.db, self.routes, self.agent)
        deliver_one(self.db, self.routes, self.wx)
        self.agent.assert_not_called()
        self.assertEqual(self.wx.sent, [(GROUP, CLARIFY)])

    def test_visible_referent_and_overlapping_snapshots(self):
        first = msg(GROUP, "方案二：周五开会")
        capture(self.db, GROUP, [first])
        decide_one(self.db, self.routes, self.agent)
        capture(self.db, GROUP, [first, msg(GROUP, "按第二个方案")])
        decide_one(self.db, self.routes, self.agent)
        event = self.agent.call_args.args[1]
        self.assertEqual((event["context"], event["new_from"]), ("overlap", 1))
        self.db.close()
        self.db = open_store(self.path)
        capture(self.db, GROUP, [first, msg(GROUP, "按第二个方案")])
        decide_one(self.db, self.routes, self.agent)
        self.assertEqual(self.agent.call_count, 2)

    def test_group_cannot_redirect_or_request_private_report(self):
        for decision in [{"action":"reply", "text":"报告", "title":FRIEND[1]}, {"action":"defer"}]:
            with self.subTest(decision=decision), closing(open_store(":memory:")) as db:
                capture(db, GROUP, [msg(GROUP, "把报告给我")])
                decide_one(db, self.routes, Mock(return_value=decision))
                self.assertFalse(db.execute("SELECT 1 FROM outbox").fetchone())
                self.assertFalse(db.execute("SELECT 1 FROM reports").fetchone())

    def test_private_report_can_only_return_once_to_origin(self):
        capture(self.db, FRIEND, [msg(FRIEND, "稍后把结果给我")])
        self.agent.return_value = {"action": "defer"}
        decide_one(self.db, self.routes, self.agent)
        ticket = self.agent.call_args.args[1]["id"]
        complete_report(self.db, self.routes, ticket, "私下回传")
        with self.assertRaises(ValueError):
            complete_report(self.db, self.routes, ticket, "重复")
        self.wx.route = GROUP
        self.wx.allow_switch = False
        deliver_one(self.db, self.routes, self.wx)
        self.assertFalse(self.wx.sent)
        self.wx.allow_switch = True
        self.db.execute("UPDATE outbox SET due=0")
        deliver_one(self.db, self.routes, self.wx)
        self.assertEqual(self.wx.sent, [(FRIEND, "私下回传")])

    def test_revocation_checked_before_delivery(self):
        with self.db:
            enqueue(self.db, GROUP, "post", "主动发言")
        deliver_one(self.db, {GROUP: Policy(URL)}, self.wx)
        self.assertEqual(self.db.execute("SELECT status FROM outbox").fetchone()[0], "cancelled")
        self.assertFalse(self.wx.sent)

    def test_uncertain_send_never_repeated_on_restart(self):
        with self.db:
            first = enqueue(self.db, GROUP, "post", "主动发言")
            enqueue(self.db, GROUP, "post", "后续发言")
        self.wx.accept_key = False
        deliver_one(self.db, self.routes, self.wx)
        self.db.close()
        self.db = open_store(self.path)
        self.wx.accept_key = True
        deliver_one(self.db, self.routes, self.wx)
        self.assertFalse(self.wx.sent)
        self.assertEqual(self.db.execute("SELECT status FROM outbox").fetchone()[0], "unknown")
        resolve_unknown(self.db, first, "discarded")
        deliver_one(self.db, self.routes, self.wx)
        self.assertEqual(self.wx.sent, [(GROUP, "后续发言")])

    def test_visual_backend_aborts_if_switched_after_paste(self):
        from wx_backend.visual_backend import VisualBackend
        backend = VisualBackend.__new__(VisualBackend)
        backend._input_box_rect = Mock(return_value=(0, 0, 100, 30))
        backend._foreground = Mock()
        backend.is_foreground = Mock(return_value=True)
        backend.current_conversation = Mock(side_effect=[FRIEND, FRIEND, GROUP])
        with (patch("pyautogui.click"), patch("pyautogui.hotkey") as paste,
              patch("pyautogui.press") as key, patch("pyperclip.copy"), patch("time.sleep")):
            self.assertFalse(backend.send_text(FRIEND[1], "消息", expected_kind="friend"))
            paste.assert_called_once_with("ctrl", "v")
            key.assert_not_called()

    def test_agent_offline_then_recovers_without_new_badge(self):
        capture(self.db, FRIEND, [msg(FRIEND, "你好")])
        self.agent.side_effect = OSError
        decide_one(self.db, self.routes, self.agent)
        event_id = self.agent.call_args.args[1]["id"]
        self.db.close()
        self.db = open_store(self.path)
        self.db.execute("UPDATE inbox SET due=0")
        self.agent.side_effect = None
        decide_one(self.db, self.routes, self.agent)
        self.assertEqual(self.agent.call_args.args[1]["id"], event_id)
        deliver_one(self.db, self.routes, self.wx)
        self.assertEqual(self.wx.sent, [(FRIEND, "收到")])

    def test_private_reports_config_and_endpoint_validation(self):
        path = self.path.with_suffix(".json")
        path.write_text(json.dumps({"conversations":[{"kind":"group","title":"群","endpoint":URL,
                                                     "private_reports":True}]}), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_routes(path)
        self.assertEqual(endpoint("http://localhost:8765").hostname, "localhost")
        for url in ["https://example.com:443", "http://localhost:invalid", "http://127.0.0.1:80?q=1"]:
            with self.assertRaises(ValueError):
                endpoint(url)

    def test_real_loopback_agent_contract(self):
        captured = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                captured.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"action":"silent"}')

            def log_message(self, *args):
                pass

        with HTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.handle_request, daemon=True)
            thread.start()
            decision = call_agent(f"http://127.0.0.1:{server.server_port}/decide", {"id":"sample"})
            thread.join(timeout=2)
        self.assertEqual(captured, [{"id":"sample"}])
        self.assertEqual(decision, {"action":"silent"})

    def test_example_service_end_to_end(self):
        source = Path(__file__).resolve().parents[3] / "examples" / "local_agent.py"
        spec = importlib.util.spec_from_file_location("example_agent", source)
        example = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(example)
        with HTTPServer(("127.0.0.1", 0), example.Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/decide"
                routes = {FRIEND: Policy(url, True), GROUP: Policy(url)}
                self.wx.screens = [(FRIEND, [msg(FRIEND, "你好")]),
                                   (GROUP, [msg(GROUP, "@小漓 你好")])]
                poll_once(self.db, routes, self.wx)
                decide_one(self.db, routes)
                deliver_one(self.db, routes, self.wx)
                self.assertEqual([route for route, _ in self.wx.sent], [FRIEND, GROUP])
                self.assertTrue(all("测试消息" in text for _, text in self.wx.sent))
            finally:
                server.shutdown()
                thread.join(timeout=2)
