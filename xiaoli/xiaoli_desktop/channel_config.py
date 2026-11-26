"""Local conversation routing and first-run setup for the text channel."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "local-data" / "channel.json"
DEFAULT_ENDPOINT = "http://127.0.0.1:8765/decide"


@dataclass(frozen=True)
class Policy:
    url: str
    private_reports: bool = False
    proactive: bool = False


def endpoint(url):
    hint = "请输入完整的本机 HTTP 地址和端口，例如 " + DEFAULT_ENDPOINT
    if not isinstance(url, str) or not url.isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in url):
        raise ValueError(hint)
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise ValueError(hint) from exc
    if (parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "::1", "localhost")
            or parts.username is not None or parts.password is not None
            or "?" in url or "#" in url or not port):
        raise ValueError(hint)
    return parts


def title(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 2000
            or any(ord(c) < 32 or 127 <= ord(c) <= 159 or c in "\u2028\u2029" for c in value)):
        raise ValueError("title 请填写微信窗口里显示的单行会话名称")
    return value.strip()


def parse_routes(config):
    if not isinstance(config, dict) or not isinstance(config.get("conversations"), list):
        raise ValueError("最外层需要含 conversations 数组的 JSON 对象")
    if config.get("ocr", "v5") not in ("v4", "v5"):
        raise ValueError("ocr 可填写 v4 或 v5")
    routes = {}
    for index, row in enumerate(config["conversations"], 1):
        try:
            if not isinstance(row, dict) or row.get("kind") not in ("friend", "group"):
                raise ValueError("kind 请填写 friend（私聊）或 group（群聊）")
            key = row["kind"], title(row.get("title"))
            if key in routes:
                raise ValueError(f"与已有会话重复：{key[1]}")
            endpoint(row.get("endpoint"))
            policy = Policy(row["endpoint"], row.get("private_reports", False), row.get("proactive", False))
            for field in ("private_reports", "proactive"):
                if type(getattr(policy, field)) is not bool:
                    raise ValueError(f"{field} 请填写 true 或 false")
            if key[0] == "group" and policy.private_reports:
                raise ValueError("群聊的 private_reports 应为 false")
        except ValueError as exc:
            raise ValueError(f"第 {index} 个会话：{exc}") from exc
        routes[key] = policy
    return routes


def read_config(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"没有找到配置文件 {path}。请先运行 setup。") from exc
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path} 需要保存为 UTF-8。") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} 第 {exc.lineno} 行第 {exc.colno} 列 JSON 格式有误。") from exc


def load_routes(path):
    return parse_routes(read_config(path))


def ask(label, validate, default=None):
    while True:
        value = input(label + (f" [{default}]" if default is not None else "") + "：").strip()
        try:
            return validate(value or default)
        except ValueError as exc:
            print(exc)


def kind_name(value):
    aliases = {"1": "friend", "私聊": "friend", "friend": "friend",
               "2": "group", "群聊": "group", "group": "group"}
    if value not in aliases:
        raise ValueError("请输入 1（私聊）或 2（群聊）。")
    return aliases[value]


def yes_or_no(value):
    if str(value).lower() in ("y", "yes", "是"):
        return True
    if str(value).lower() in ("n", "no", "否"):
        return False
    raise ValueError("请输入 y 或 n。")


def save_route(path, kind, name, new_fields, original):
    # Only serialize the final read/merge/write. An operator may have left
    # the interactive prompt open while another setup updated a different row.
    import msvcrt
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(path.name + ".lock").open("a+b") as lock:
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        try:
            config = read_config(path) if path.exists() else {"ocr": "v4", "conversations": []}
            routes = parse_routes(config)
            if routes.get((kind, name)) != original:
                raise ValueError("该会话的配置刚刚发生变化，请重新运行 setup 查看新值。")
            rows = config["conversations"]
            row = next((item for item in rows if item["kind"] == kind and title(item["title"]) == name), None)
            if row is None:
                rows.append({"kind": kind, "title": name, **new_fields})
            else:
                row.update(new_fields)
            parse_routes(config)
            tmp = None
            try:
                with NamedTemporaryFile("w", dir=path.parent, encoding="utf-8",
                                        delete=False) as f:
                    tmp = Path(f.name)
                    json.dump(config, f, indent=2, ensure_ascii=False)
                    f.write("\n")
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, path)
            finally:
                if tmp is not None:
                    tmp.unlink(missing_ok=True)
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def setup(path):
    routes = load_routes(path) if path.exists() else {}
    print(f"配置文件 {path}")
    for kind, name in routes:
        print("已配置", "私聊" if kind == "friend" else "群聊", name)
    kind = ask("会话类型，1 私聊 / 2 群聊", kind_name, "1")
    name = ask("微信窗口显示的会话名称（修改时输入原名）", title)
    old = routes.get((kind, name))
    url = ask("本机 Agent 的消息接口地址", endpoint, old.url if old else DEFAULT_ENDPOINT).geturl()
    proactive = ask("允许主动向这个会话发送文字吗？y/n", yes_or_no, "y" if old and old.proactive else "n")
    save_route(path, kind, name, {"endpoint": url, "proactive": proactive}, old)
    print("已保存。再次运行 setup 可添加或修改会话。启动本机 Agent 后运行 check 检查连接。")
