# 来源与变更记录

本仓库分享 v1.7 的完整源码快照。下文按开发阶段记录功能和测试结果，小漓的上游版本为 v3.5.2。

## 上游基线

- **作者与许可。** 小漓由 GitHub 用户 `wens6515` 发布。导入版本为 `b30c5d29302d5db8d8efdaf27a771b6c4ad1f08c`。原 MIT 许可证及版权声明保存在导入目录内。[上游固定提交](https://github.com/wens6515/weixin-wechat-wxauto-ocr-xiaoli/commit/b30c5d29302d5db8d8efdaf27a771b6c4ad1f08c) · [许可证原文](xiaoli/LICENSE)
- **导入范围。** 上游该提交有 489 个文件。开发期间共导入其中 486 个，包含桌面程序、测试、使用文档、公开素材、OCR 和窗口标定工具。3 个历史调研或探针文件未纳入。导入时逐一核对了 486 个文件的内容，均与固定的上游版本一致。
- **继承的能力。** 上游提供 Windows 窗口定位、未读标记检测、截图、RapidOCR 文字识别和键盘发送，以及独立的小漓聊天引擎和桌面面板。本项目的文字桥直接调用视觉后端，回复由所有者接入的 Agent 决定。[小漓项目说明](xiaoli/README.md) · [本项目文字通道](xiaoli/xiaoli_desktop/agent_channel.py)

## v1.7 · 已有 Agent 的配置与连通检查

新增 `setup` 和 `check`。向导可创建或修改单个会话，保留其他会话的设置。连通检查向已配置的本机接口发送虚构事件，验证 HTTP 响应和动作格式。配置错误会指出文件位置或会话序号。接入流程只配置通信参数和权限，已有 Agent 的身份与记忆由所有者管理。[配置实现](xiaoli/xiaoli_desktop/channel_config.py) · 

验证了首次配置、修改、取消、重复编辑冲突、接口路径错误及复用已运行的 HTTP 服务。配置、文字通道和早期原型共 20 项测试通过；OCR v4 另通过合成图自检。[配置测试](xiaoli/xiaoli_desktop/tests/test_channel_config_cli.py)

## v1.6 · 将小漓接到外部 Agent

| 改动位置 | 具体变化 |
|---|---|
| [新增文字通道](xiaoli/xiaoli_desktop/agent_channel.py) | 把私聊或群聊中的可见文字整理为 HTTP 事件，附上相邻画面的重叠情况；提供回复、静默、主动发送和可选的原私聊结果回传。用 SQLite 保存游标和收发队列，避免重启后盲目重发 |
| [修改上游视觉后端](xiaoli/xiaoli_desktop/wx_backend/visual_backend.py) | 新通道读取时在同一画面核对消息与会话标题；点击、粘贴、按回车前复核窗口。支持选择 OCR v4 或 v5，并可用合成图自检 |
| [修复上游原版引擎](xiaoli/xiaoli_desktop/xiaoli_bot.py) | 待处理附件原先按发送人索引，两个群的同名成员可能互相覆盖。改为同时匹配会话类型、名称和发送人，群里的其他成员提到机器人时也不会领走这份待处理附件 |
| [演示服务](examples/local_agent.py) 与[配置样例](examples/channel.json) | 用虚构好友和群聊演示接口，便于在接入自己的 Agent 前独立排查传输问题 |

该阶段的原型、消息通道、群聊、附件和视觉后端共 184 项测试，183 项通过，1 项因缺少上游截图素材跳过。目标微信版本上的实际送达仍待独立验证。[通道回归测试](xiaoli/xiaoli_desktop/tests/test_agent_channel.py)

## v1.5 · 离线原型

用虚构消息验证好友回复、群聊回复、主动发言和重复事件处理。根目录的 `bridge.py` 是内存演示。连接微信的运行入口及安装步骤见根目录 README。[离线原型](bridge.py) · [启动说明](README.md#在-windows-上启动)
