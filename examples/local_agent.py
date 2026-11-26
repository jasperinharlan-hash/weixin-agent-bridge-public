"""本机演示 Agent，只处理虚构示例，替换 decide() 即可接入自己的模型。"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


def decide(event):
    message = event["visible"][-1]["text"]
    if message.startswith("私下回传") and "defer" in event["allowed_actions"]:
        return {"action": "defer"}
    if event["conversation"]["kind"] == "group" and "@小漓" not in message:
        return {"action": "silent"}
    return {"action": "reply", "text": "收到，我看到了这条测试消息。"}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/decide":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1024 * 1024:
                raise ValueError
            event = json.loads(self.rfile.read(length))
            result = decide(event)
        except (KeyError, TypeError, ValueError, IndexError):
            self.send_error(400)
            return
        body = json.dumps(result, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    with HTTPServer(("127.0.0.1", 8765), Handler) as server:
        print("演示 Agent 已在 127.0.0.1:8765 就绪，Ctrl+C 可停止。", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
