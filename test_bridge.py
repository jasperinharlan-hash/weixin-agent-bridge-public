import unittest

from bridge import Chat, ChatType, Incoming, LocalBridge, Outgoing


class LocalBridgeTest(unittest.TestCase):
    def test_private_and_group_events_do_not_collide_or_cross_chats(self):
        friend = Chat(ChatType.FRIEND, "42")
        group = Chat(ChatType.GROUP, "42")
        bridge = LocalBridge("agent", lambda msg: f"答：{msg.text}")
        direct = Incoming(friend, "1", "friend", "私聊")
        bridge.receive(direct)
        bridge.receive(Incoming(group, "1", "member", "群聊"))
        bridge.receive(direct)
        bridge.receive(Incoming(group, "2", "agent", "自己刚发的消息"))
        self.assertEqual(bridge.sent, [Outgoing(friend, "答：私聊"), Outgoing(group, "答：群聊")])

    def test_agent_can_stay_quiet_and_send_without_an_incoming_event(self):
        group = Chat(ChatType.GROUP, "demo")
        bridge = LocalBridge("agent", lambda msg: None)
        bridge.send(group, "我先开个头。")
        bridge.receive(Incoming(group, "3", "member", "知道了"))
        self.assertEqual(bridge.sent, [Outgoing(group, "我先开个头。")])


if __name__ == "__main__":
    unittest.main()
