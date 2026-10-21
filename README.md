# Steam 游戏库整理工作流

AI依据实际玩法和用户偏好判断分类，本地 Steam MCP 负责读取库存和批量操作收藏夹。分类结果、资料来源、聊天修订及识别到的手动收藏夹调整保存在本机，供以后增量整理。

这是可复用的 AI 工作环境。使用者把此文件夹交给能运行本地程序的 agent，由 agent 按[整理方法](steam_collection_agent_generic.md)完成调查、讨论和操作；支持 MCP 的客户端还可直接接入 Steam 工具。

## 开始使用

需要 Windows、Steam、Python 3.10+、Node.js 22.22.1+ 和 npm。先登录要整理的 Steam 账号，再让 agent：

> 阅读 README.md、AGENTS.md、分类规则和运行手册。初始化本机环境，刷新本人及家庭可用的完整游戏，核实新增游戏的玩法、联机和时长。向我解释分类建议并生成预览，我确认后再批量应用。

[运行手册](README_incremental.md)列出了完整命令。初始化会识别当前账号与 Steam 路径，生成本地配置；无法识别时提供显式参数。一份工作目录对应一个 Steam 账号，新账号使用重新解压的分享包。

## 整理流程

1. 初始化、安装依赖并检查 MCP 连接。
2. 从运行中的 Steam 读取本人及家庭可用的完整游戏，包括未安装游戏；试玩和测试单列。
3. 复用已有判断，AI批量查证新增游戏，按[分类模板](classification_categories.md)提出归属。
4. 在聊天说明类别用途、代表游戏和具体增减，用户决定方案。
5. 批量预演，保存计划、输入及原始存储快照，再应用、重新连接核对磁盘，复算差额。

默认模板包含43个玩法入口。通常每款游戏一个主类型，额外类型须有独立挑选价值；游玩状态、想玩、流程时长及多人用途分别记录。新用户可调整模板，收藏夹只按实际成员生成。

## 本地数据与分享

| 内容 | 用途 |
| --- | --- |
| classification_categories.md、profiles/default.json | 共享分类模板 |
| steam_collection_agent_generic.md | AI查询、判断、讨论和增量处理方法 |
| tools/、tests/、steam-mcp/ | 工作流程序、检查及 Steam MCP |
| .steam-workflow.local.json、mcp.local.json、steam-mcp/.env | 本机路径、账号和 MCP 接入配置 |
| state/ | 逐游戏判断、用户修订和最近应用结果 |
| data/、output/、.steam-mcp-cache/ | 查询缓存、私人库存、计划、回执和备份 |

分享时运行 `python -X utf8 tools/workflow.py package`，交付生成的 `release/steam-library-workflow.zip`。打包程序按明确文件清单选取通用内容，并检查个人账号和路径；个人数据继续保存在原工作目录。直接压缩整个工作目录会包含个人数据。

Steam 读写基于客户端调试接口，需要本地 Steam 正常登录。写操作默认关闭，授权批次在自己的进程内开启。Steam 更新后应先检查读取与预演结果。

Steam MCP 基于 [sjmatta/steam-mcp](https://github.com/sjmatta/steam-mcp)，包含 Windows 适配和批量分类脚本；上游署名及许可证保存在 steam-mcp/LICENSE。

## 许可证

Copyright (C) 2026 BW1145.

本项目的原创代码与文档采用 GNU General Public License v3.0 only（SPDX：`GPL-3.0-only`）许可，完整条款见 [LICENSE](LICENSE)。

第三方代码、依赖及资源继续适用各自的许可证；其原有版权声明和许可条款保持有效。

`steam-mcp/` 中源自 [sjmatta/steam-mcp](https://github.com/sjmatta/steam-mcp) 的上游代码保留 MIT 许可，版权声明与完整条款见 [steam-mcp/LICENSE](steam-mcp/LICENSE)。本仓库由 BW1145 编写的工作流、脚本及修改部分适用上述 GPL-3.0-only；此声明不改变上游代码原有的 MIT 授权。
