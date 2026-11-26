# 个人微信号 Agent 消息桥

把微信接到你已经在用的 Agent，让它通过专属个人微信号与好友私聊，也能在加入的微信群中收发文字。模型、记忆和身份由所有者在原 Agent 中管理。微信侧只需配置接口、会话和发送权限。产品名称待定。

## 项目来源与主要改进

**微信桌面端来自 wens6515 的开源项目「小漓 · 蓝色大肥鱼」。** 本仓库以小漓 v3.5.2 的提交 `b30c5d2` 为基线，沿用它在 Windows 上定位微信窗口、识别未读标记、通过截图和 OCR 读取可见消息、在输入框发送文字的实现。上游采用 MIT 许可证，原版权声明为 `Copyright (c) 2026 wens6515`。[小漓原仓库与固定版本](https://github.com/wens6515/weixin-wechat-wxauto-ocr-xiaoli/tree/b30c5d29302d5db8d8efdaf27a771b6c4ad1f08c) · [随源码保留的许可证](xiaoli/LICENSE)

**SnowLuma（QQ）提供了产品思路。** 它将聊天平台的收发能力开放给外部程序。本项目的微信实现基于小漓，已有 Agent 通过本机 HTTP 接入。[SnowLuma 项目说明](https://github.com/XingYunStar/snowluma/blob/main/README.md)

| 我们在小漓基础上的改动 | 对使用者的作用 |
|---|---|
| 新增已有 Agent 的文字接口与配置向导 | `setup` 指定会话和地址，`check` 用虚构消息验证接口。可以沿用已有 Agent 的设定，无需再配置一套模型或角色 |
| 增加会话核对与发送队列 | 读取时比对标题和聊天类型，粘贴和按回车前再次核对目标。进程重启后可继续处理已入队的消息；结果不确定的发送留给人工核对 |
| 增加群聊上下文提示与私聊结果回传 | 告知 Agent 相邻画面是否重叠。私聊开启回传后，本机程序可按委托编号提交结果，好友在原对话中收到 |
| 修复原版引擎的待处理附件关联 | 按会话类型、名称和发送人匹配附件，避免不同群的同名成员取到对方的待处理文件 |

首次使用请从下方的 Windows 启动步骤开始。各版导入的文件、改动和验证结果见变更记录。[变更记录](CHANGELOG.md)

## 消息如何流转

好友或群成员发来文字后，消息桥把当前会话、发送人和可见内容交给已有 Agent。Agent 可以回复，也可以保持安静。群里有人说“就按第二个方案”而画面里缺少对应选项时，桥会先请对方补充。即使没有新来信，本机程序也可以通过 `post` 向已开启主动发送的会话发言。上述外部接口目前支持文字；小漓原版面板的语音、图片等功能见上游手册。[原版使用文档](xiaoli/docs/使用文档/README.md)

**验证状态。** 已用模拟窗口和本机 HTTP 服务跑通收发流程，OCR v4 已通过合成图片自检。Windows 微信 4.2.0.53 上的真实收发、自动加好友和入群仍待验证，目标使用方式的平台授权也待核实。OCR 依靠可见标题，两位同名好友或两个同名群无法可靠区分。真机测试时应使用唯一可见标题、专属账号和空白输入框，并让一个进程独占微信窗口。

## 在 Windows 上启动

在仓库根目录打开 PowerShell。配置向导和接口检查只需要 Windows 和 Python，可以在登录微信前完成。

```powershell
$channel = "xiaoli/xiaoli_desktop/agent_channel.py"
py -3.13 $channel setup
```

1. 在 `setup` 里选私聊或群聊，填微信窗口显示的会话名称和已有 Agent 的本机 HTTP 接口地址（含端口和路径）。重复运行可以添加会话；输入原类型和名称可以修改，直接回车沿用显示的值。
2. 确认自己的 Agent 服务已启动，然后运行 `py -3.13 $channel check`。检查向每条配置的接口提交一条虚构消息，校验实际返回的动作。测试使用「微信消息桥配置自检」作为会话名，事件编号以 `probe-` 开头，适配器可据此略过记忆写入。检查不会操作微信或写入发件队列。
3. 支持下节消息格式的本机 HTTP 接口可以直连。只有命令行、其他 API 或云端服务的 Agent，需要本机适配器把事件转交原 Agent，再将回复转成下节格式。模型、记忆和身份由原 Agent 管理。可选的 `py -3.13 examples/local_agent.py` 供独立验证消息格式时使用。

默认配置在仓库的 `local-data/channel.json`。每个微信账号单独使用一个配置目录；要指定已有文件，把 `--config 配置文件路径` 放在子命令前。修改配置或切换微信账号后重新启动监听。`friend` 和 `group` 在配置里分别表示微信私聊和群聊。

连接检查通过后安装桌面依赖，运行 OCR 自检。向导新建配置使用本机已验证的 v4。已有配置未指定 `ocr` 时沿用 v5，`--ocr v4` 或 `--ocr v5` 可临时覆盖。首次加载可能需要下载权重。

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -r .\requirements-channel.txt
.\.venv\Scripts\python $channel check-ocr
```

具备账号和平台测试条件、专属号已由本人登录后，用 `.\.venv\Scripts\python $channel listen` 启动微信监听，按 Ctrl+C 停止。

如需使用已有的私聊委托功能，在对应好友配置中启用 `"private_reports": true`。以下命令可查看委托、完成文字回传，也可向启用了 `proactive` 的测试群发言。将“委托编号”换成 `status` 显示的编号。修改权限后重启监听使配置生效。

```powershell
$channel = "xiaoli/xiaoli_desktop/agent_channel.py"
.\.venv\Scripts\python $channel status
.\.venv\Scripts\python $channel request 委托编号
.\.venv\Scripts\python $channel complete 委托编号 "你委托整理的结果已完成。"
.\.venv\Scripts\python $channel post group "测试讨论组" "大家好，这是一次主动发言测试。"
```

`queued` 表示排队，目标不在可定位列表时稍后再试。`entered` 表示程序已按回车，真实送达以对端看到消息为准。`unknown` 表示键盘阶段中断，后续同会话消息会等待。人工检查原会话并清理未发送草稿后，用 `resolve 消息编号 confirmed` 或 `resolve 消息编号 discarded` 记录核对结果。两种操作均不会重发该条消息。

## 自有 Agent 接口

桥向配置的本机 HTTP 地址发送 JSON。请求中的 `id` 在同一事件重试时保持一致，Agent 如有内部状态应凭此去重。`visible` 是当前会话的一段可见文字，`new_from` 是本次新增内容的起始下标。`context` 取值为 `visible_only`（首次读取）、`overlap`（相邻画面重叠）或 `possible_gap`（两次画面未匹配）。OCR 无消息流水号，连发完全相同文字、识别波动和屏幕外历史都可能影响判断。首次无未读标志的前台画面只建立基线。

```json
{"version":1,"id":"demo-event","conversation":{"kind":"friend","title":"测试好友小陈"},"context":"visible_only","visible":[{"sender":"测试好友小陈","text":"你好"}],"new_from":0,"allowed_actions":["silent","reply","defer"]}
```

服务返回 HTTP 200 和以下一种 JSON。回复最多 2000 字符，`defer` 仅对启用 `private_reports` 的好友开放。接入的 Agent 应按会话隔离自己的记忆与工具权限。示例服务无长期记忆。

```json
{"action":"reply","text":"你好，我在。"}
```

```json
{"action":"silent"}
```

```json
{"action":"defer"}
```

## 离线验证

在已安装上述依赖的环境中，从仓库根目录执行。以下命令分组验证旧原型和本次优化，不会向微信发消息。群聊原版回归还会导入上游桌面依赖（例如 requests）。若只想查看最初的内存演示，运行 `python bridge.py`。

```powershell
python -m unittest -v test_bridge
Push-Location xiaoli/xiaoli_desktop
python -m unittest discover -s tests -p test_agent_channel.py
python -m unittest discover -s tests -p test_channel_config_cli.py
python -m unittest discover -s tests -p test_group_full_mode.py
Pop-Location
```

[上游桌面使用文档](xiaoli/docs/使用文档/README.md) 适用于小漓原版面板。自有 Agent 通道从本页的 `listen` 命令启动。
