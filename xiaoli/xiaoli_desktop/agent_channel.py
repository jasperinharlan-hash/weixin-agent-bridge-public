"""Text-only Agent channel for Xiaoli's Windows visual backend."""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from hashlib import sha256
from http.client import HTTPConnection, HTTPException
import json
import logging
from pathlib import Path
import re
import sqlite3
import time
from uuid import uuid4

from channel_config import DEFAULT_CONFIG, Policy, endpoint, load_routes, parse_routes, read_config, setup


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def checked_text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise ValueError("文本必须为 1 至 2000 个字符")
    if any(ord(ch) < 32 and ch not in "\n\t" for ch in value):
        raise ValueError("文本含有控制字符")
    return value.strip()


class InvalidDecision(ValueError):
    """A rejected Agent action is terminal for this event."""


def call_agent(url, event):
    parts = endpoint(url)
    host = "127.0.0.1" if parts.hostname == "localhost" else parts.hostname
    with closing(HTTPConnection(host, parts.port, timeout=15)) as conn:
        conn.request("POST", parts.path or "/", encode(event).encode("utf-8"),
                     {"Content-Type": "application/json; charset=utf-8"})
        response = conn.getresponse()
        if response.status != 200:
            raise ValueError(f"Agent HTTP {response.status}")
        raw = response.read(16385)
        if len(raw) > 16384:
            raise ValueError("Agent 响应过长")
        return json.loads(raw.decode("utf-8"))


def open_store(path):
    db = sqlite3.connect(path, timeout=5)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS cursors (
            kind TEXT, title TEXT, snapshot TEXT NOT NULL, PRIMARY KEY(kind,title));
        CREATE TABLE IF NOT EXISTS inbox (
            id TEXT PRIMARY KEY, kind TEXT, title TEXT, payload TEXT, due REAL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS reports (
            id TEXT PRIMARY KEY, kind TEXT, title TEXT, request TEXT, status TEXT DEFAULT 'open');
        CREATE TABLE IF NOT EXISTS outbox (
            id TEXT PRIMARY KEY, kind TEXT, title TEXT, purpose TEXT, text TEXT,
            status TEXT DEFAULT 'queued', due REAL DEFAULT 0);
    """)
    return db


# These common follow-ups have no self-contained object. A group member can
# resolve the ambiguity by including the option or instruction in their message.
REFERENCE = re.compile(r"(?:就|那|请|我们|直接|还是|照|按|选|用|执行|采用|照着)\s*"
                       r"(?:按|选|用|执行|采用)?\s*(?:第(?P<number>[一二三四五六七八九十\d]+)(?:个|种|套|项)?"
                       r"(?:方案|办法|建议|选项|版本)?|(?:刚才|上面|之前|前面|刚说的)(?:说的)?那个)")
CLARIFY = "我这边只看到一部分群消息。能把你指的方案或原话再发一下吗？"


def capture(db, route, messages, *, baseline=False):
    from wx_backend.models import MessageType
    visible = [{"sender": m.sender, "text": m.content.strip()} for m in messages
               if m.chat == route[1] and m.type == MessageType.TEXT
               and m.sender and m.sender != "self" and m.content.strip()][-64:]
    signatures = [sha256(encode(m).encode()).hexdigest() for m in visible]
    row = db.execute("SELECT snapshot FROM cursors WHERE kind=? AND title=?", route).fetchone()
    if baseline and row is None:
        with db:
            db.execute("INSERT INTO cursors VALUES(?,?,?)", (*route, encode(signatures)))
        return
    if not visible:
        return
    old = json.loads(row[0]) if row else []
    overlap = min(len(old), len(signatures))
    while overlap and old[-overlap:] != signatures[:overlap]:
        overlap -= 1
    if overlap == len(visible):
        return
    state = "overlap" if overlap else ("possible_gap" if old else "visible_only")
    event = {"version": 1, "id": uuid4().hex, "conversation": {"kind": route[0], "title": route[1]},
             "context": state, "visible": visible, "new_from": overlap}
    with db:
        db.execute("INSERT OR REPLACE INTO cursors VALUES(?,?,?)", (*route, encode(signatures)))
        db.execute("INSERT INTO inbox(id,kind,title,payload) VALUES(?,?,?,?)",
                   (event["id"], *route, encode(event)))


def enqueue(db, route, purpose, text, item_id=None):
    item_id = item_id or uuid4().hex
    db.execute("INSERT INTO outbox(id,kind,title,purpose,text) VALUES(?,?,?,?,?)",
               (item_id, *route, purpose, checked_text(text)))
    return item_id


def decision_for(event, policy, agent):
    actions = ["silent", "reply"]
    if policy.private_reports and event["conversation"]["kind"] == "friend":
        actions.append("defer")
    if event["conversation"]["kind"] == "group":
        for index in range(event["new_from"], len(event["visible"])):
            line = event["visible"][index]["text"]
            ref = REFERENCE.search(line)
            if not ref:
                continue
            number = ref.group("number")
            if not number:
                if event["context"] != "overlap":
                    return "reply", CLARIFY
                continue
            digits = "零一二三四五六七八九"
            alt = str(digits.index(number)) if len(number) == 1 and number in digits else number
            if len(number) == 1 and number in "0123456789":
                alt = digits[int(number)]
            label = f"(?:{number}|{alt})"
            option = re.compile(rf"(?:方案|选项|办法|建议)\s*{label}\s*[:：、]\s*\S|"
                                rf"(?:^|[\s；;]){label}[.、）)]\s*\S|"
                                rf"第{label}(?:个|项)?方案(?:是|为|[:：])\s*\S")
            if not any(option.search(m["text"]) for m in event["visible"][:index + 1]):
                return "reply", CLARIFY
    result = agent(policy.url, dict(event, allowed_actions=actions))
    if not isinstance(result, dict) or result.get("action") not in actions:
        raise InvalidDecision("Agent 返回的动作未获准")
    action = result["action"]
    if set(result) != ({"action", "text"} if action == "reply" else {"action"}):
        raise InvalidDecision("Agent 动作含有意外字段")
    try:
        return action, checked_text(result["text"]) if action == "reply" else None
    except ValueError as exc:
        raise InvalidDecision(str(exc)) from exc


def check_connections(routes):
    errors = []
    for (kind, name), policy in routes.items():
        event = {"version": 1, "id": "probe-" + uuid4().hex, "conversation": {"kind": kind, "title": "微信消息桥配置自检"},
                 "context": "visible_only", "new_from": 0,
                 "visible": [{"sender": "连接测试", "text": "这是一条配置测试消息。"}]}
        try:
            decision_for(event, policy, call_agent)
            print(f"{name}：接口连接和返回格式正常")
        except (OSError, HTTPException, ValueError) as exc:
            errors.append(f"{name}（{policy.url}）：{exc}")
    if errors:
        raise ValueError("请核对 Agent 是否已启动、地址和返回格式是否正确。\n" + "\n".join(errors))


def decide_one(db, routes, agent=call_agent):
    # Keep decisions in order within one conversation; an offline Agent for
    # another conversation must not block this one.
    row = db.execute("""SELECT * FROM inbox AS i WHERE due<=?
        AND NOT EXISTS(SELECT 1 FROM inbox AS older WHERE older.kind=i.kind
          AND older.title=i.title AND older.rowid<i.rowid) ORDER BY rowid LIMIT 1""",
                     (time.time(),)).fetchone()
    if row is None:
        return
    route = row["kind"], row["title"]
    if route not in routes:
        with db:
            db.execute("DELETE FROM inbox WHERE id=?", (row["id"],))
        return
    try:
        event = json.loads(row["payload"])
        action, text = decision_for(event, routes[route], agent)
    except InvalidDecision as exc:
        logging.warning("已拒绝事件 %s 的动作 (%s)", row["id"], exc)
        with db:
            db.execute("DELETE FROM inbox WHERE id=?", (row["id"],))
        return
    except (OSError, HTTPException, ValueError, TypeError) as exc:
        logging.warning("Agent 决策延后，事件 %s (%s)", row["id"], type(exc).__name__)
        with db:
            db.execute("UPDATE inbox SET due=? WHERE id=?", (time.time() + 30, row["id"]))
        return
    with db:
        if action == "reply":
            enqueue(db, route, "reply", text, row["id"])
        elif action == "defer":
            request = "\n".join(m["text"] for m in event["visible"][event["new_from"]:])
            db.execute("INSERT INTO reports(id,kind,title,request) VALUES(?,?,?,?)",
                       (row["id"], *route, request))
        db.execute("DELETE FROM inbox WHERE id=?", (row["id"],))


def complete_report(db, routes, item_id, text):
    with db:
        row = db.execute("SELECT * FROM reports WHERE id=? AND status='open'", (item_id,)).fetchone()
        if row is None:
            raise ValueError("委托不存在或已经完成")
        route = row["kind"], row["title"]
        if route not in routes or not routes[route].private_reports or route[0] != "friend":
            raise ValueError("该好友的回传权限已关闭")
        enqueue(db, route, "report", text, item_id)
        db.execute("UPDATE reports SET status='queued',request=NULL WHERE id=?", (item_id,))


def deliver_one(db, routes, wx):
    row = db.execute("""SELECT * FROM outbox AS o WHERE status='queued' AND due<=?
        AND NOT EXISTS(SELECT 1 FROM outbox AS older WHERE older.kind=o.kind
        AND older.title=o.title AND older.status IN ('queued','unknown') AND older.rowid<o.rowid)
        ORDER BY rowid LIMIT 1""", (time.time(),)).fetchone()
    if row is None:
        return
    route = row["kind"], row["title"]
    policy = routes.get(route)
    if (not policy or (row["purpose"] == "report" and not policy.private_reports)
            or (row["purpose"] == "post" and not policy.proactive)):
        with db:
            db.execute("UPDATE outbox SET status='cancelled',text=NULL WHERE id=?", (row["id"],))
        return
    if not wx.select_conversation(route):
        with db:
            db.execute("UPDATE outbox SET due=? WHERE id=?", (time.time() + 30, row["id"]))
        return
    # Persist uncertainty before touching the keyboard. A crash at any point
    # after this commit must never automatically repeat a possibly sent message.
    with db:
        db.execute("UPDATE outbox SET status='unknown' WHERE id=?", (row["id"],))
    if wx.send_text(route[1], row["text"], expected_kind=route[0]):
        with db:
            db.execute("UPDATE outbox SET status='entered',text=NULL WHERE id=?", (row["id"],))


def resolve_unknown(db, item_id, outcome):
    if outcome not in ("confirmed", "discarded"):
        raise ValueError("核对结果无效")
    with db:
        result = db.execute("UPDATE outbox SET status=?,text=NULL WHERE id=? AND status='unknown'",
                            (outcome, item_id))
        if result.rowcount != 1:
            raise ValueError("该记录不存在或无需核对")


def poll_once(db, routes, wx, agent=call_agent):
    # Each yield has already selected its unread row. Materializing the
    # iterator upfront would read every batch from the last selected chat.
    def read_selected(mark_read):
        route = wx.current_conversation()
        if route not in routes:
            return
        messages = wx.get_messages(None, assume_switched=True, expected_route=route)
        if wx.current_conversation() == route:
            capture(db, route, messages, baseline=not mark_read)
            if mark_read and messages:
                wx.mark_session_read()

    # Badge rows come first. Without a badge, the first foreground frame only
    # establishes a baseline so a new installation won't reply to old history.
    saw_badge = False
    for _ in wx.iter_unread_sessions():
        saw_badge = True
        read_selected(True)
    if not saw_badge and wx.is_foreground():
        read_selected(False)
    decide_one(db, routes, agent)
    deliver_one(db, routes, wx)


@contextmanager
def single_listener():
    import win32api
    import win32event
    handle = win32event.CreateMutex(None, False, "Local\\WeixinAgentBridgeDesktop")
    try:
        if win32api.GetLastError() == 183:
            raise ValueError("本桌面已有微信消息桥在运行")
        yield
    finally:
        handle.Close()


def main():
    parser = argparse.ArgumentParser(description="小漓桌面端 · 自有 Agent 消息桥")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--ocr", choices=["v4", "v5"])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup", help="添加或修改本地会话配置")
    commands.add_parser("check", help="向配置的本机 Agent 发送测试事件，检查接口")
    commands.add_parser("check-ocr")
    commands.add_parser("listen")
    commands.add_parser("status")
    request = commands.add_parser("request")
    request.add_argument("id")
    post = commands.add_parser("post")
    post.add_argument("kind", choices=["friend", "group"])
    post.add_argument("title")
    post.add_argument("text")
    complete = commands.add_parser("complete")
    complete.add_argument("id")
    complete.add_argument("text")
    resolve = commands.add_parser("resolve")
    resolve.add_argument("id")
    resolve.add_argument("outcome", choices=["confirmed", "discarded"])
    args = parser.parse_args()
    try:
        if args.command == "setup":
            setup(args.config)
            return
        if args.command == "check-ocr" and (args.ocr or not args.config.exists()):
            config = {}
            routes = {}
        else:
            config = read_config(args.config)
            routes = parse_routes(config)
        if args.command in ("check", "listen") and not routes:
            raise ValueError("尚未配置任何会话，请先运行 setup。")
        if args.command == "check":
            check_connections(routes)
            return
        if args.command in ("check-ocr", "listen"):
            args.ocr = args.ocr or config.get("ocr", "v5")
            from wx_backend.visual_backend import _get_ocr_engine
            if not _get_ocr_engine(args.ocr):
                raise ValueError(f"OCR {args.ocr} 模型加载失败，当前无法读取微信。")
        if args.command == "check-ocr":
            from PIL import Image, ImageDraw, ImageFont
            from wx_backend.visual_backend import ocr_image
            sample = Image.new("RGB", (420, 80), "white")
            ImageDraw.Draw(sample).text((16, 8), "测试群聊", fill="black",
                                        font=ImageFont.truetype("msyh.ttc", 40))
            text = "".join(item["text"] for item in ocr_image(sample))
            if "测试群聊" not in text:
                raise ValueError(f"OCR {args.ocr} 未正确识别合成测试图")
            print(f"OCR {args.ocr} 已读出合成测试图中的「测试群聊」。")
            return
        with closing(open_store(args.config.with_name("channel.sqlite3"))) as db:
            if args.command == "status":
                print(f"已加载 {len(routes)} 个会话。配置文件 {args.config}")
                for table, query in [("待决策", "SELECT id,kind,title FROM inbox"),
                                     ("待完成", "SELECT id,kind,title FROM reports WHERE status='open'"),
                                     ("需人工核对", "SELECT id,kind,title,status FROM outbox WHERE status='unknown'"),
                                     ("发件", "SELECT id,kind,title,status FROM outbox WHERE status!='unknown' ORDER BY rowid DESC LIMIT 50")]:
                    for row in db.execute(query):
                        print(table, *row)
            elif args.command == "request":
                row = db.execute("SELECT kind,title,request FROM reports WHERE id=? AND status='open'",
                                 (args.id,)).fetchone()
                if row is None:
                    raise ValueError("没有对应的待完成委托")
                print(*row, sep="\n")
            elif args.command == "complete":
                complete_report(db, routes, args.id, args.text)
            elif args.command == "resolve":
                resolve_unknown(db, args.id, args.outcome)
            elif args.command == "post":
                route = args.kind, args.title
                if route not in routes or not routes[route].proactive:
                    raise ValueError("该会话未启用主动发送")
                with db:
                    print(enqueue(db, route, "post", args.text))
            else:
                from wx_backend import create_backend
                with single_listener(), closing(create_backend("visual")) as wx:
                    while True:
                        poll_once(db, routes, wx)
                        time.sleep(1)
    except KeyboardInterrupt:
        if args.command == "setup":
            parser.exit(130, "配置向导已中止。\n")
    except EOFError:
        parser.exit(1, "配置输入未完成，请在终端重新运行 setup。\n")
    except ModuleNotFoundError as exc:
        parser.exit(1, f"缺少依赖 {exc.name}。请用当前 Python 安装 requirements-channel.txt。\n")
    except (OSError, ValueError, KeyError, RuntimeError, sqlite3.Error) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
