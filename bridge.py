"""用虚构事件演练 Agent 的收发。直接运行此文件可查看示例。"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum


class ChatType(Enum):
    FRIEND = "好友"
    GROUP = "群聊"


@dataclass(frozen=True)
class Chat:
    kind: ChatType
    chat_id: str


@dataclass(frozen=True)
class Incoming:
    chat: Chat
    event_id: str
    sender_id: str
    text: str


@dataclass(frozen=True)
class Outgoing:
    chat: Chat
    text: str


class LocalBridge:
    """单账号、单进程的内存模拟器。发送结果只进入本地收件列表。"""

    def __init__(self, account_id: str, agent: Callable[[Incoming], str | None]):
        self.account_id = account_id
        self.agent = agent
        self.sent: list[Outgoing] = []
        self._handled: set[tuple[Chat, str]] = set()

    def send(self, chat: Chat, text: str) -> None:
        self.sent.append(Outgoing(chat, text))

    def receive(self, message: Incoming) -> None:
        key = (message.chat, message.event_id)
        if message.sender_id == self.account_id or key in self._handled:
            return
        reply = self.agent(message)
        if reply is not None:
            self.send(message.chat, reply)
        self._handled.add(key)


def main() -> None:
    def example_agent(message: Incoming) -> str:
        return f"收到「{message.text}」"

    bridge = LocalBridge("agent-demo", example_agent)
    friend = Chat(ChatType.FRIEND, "小林")
    group = Chat(ChatType.GROUP, "周末读书群")
    bridge.receive(Incoming(friend, "1", "小林", "明天一起读书吗？"))
    bridge.receive(Incoming(group, "2", "小周", "大家想读哪一章？"))
    bridge.send(group, "我想从第一章开始。")
    for message in bridge.sent:
        print(f"[模拟{message.chat.kind.value} · {message.chat.chat_id}] {message.text}")


if __name__ == "__main__":
    main()
