# 运行手册

本手册中的命令在解压后的工作目录执行。Python 仅使用标准库；MCP依赖由 npm 安装。agent 应先确认 Python、Node 的实际路径，使用正常安装的运行环境。下文的 `python` 可替换为已核实的解释器完整路径。

## 首次初始化

登录目标 Steam 账号后执行：

```powershell
python -X utf8 tools/workflow.py init
python -X utf8 tools/workflow.py setup
python -X utf8 tools/workflow.py status
```

init 从当前用户注册表读取 Steam 路径和 ActiveUser，不根据“最近登录过谁”猜测当前账号。Steam 未运行或无法识别时，显式指定：

```powershell
python -X utf8 tools/workflow.py init --steam-root "<Steam安装目录>" --account <SteamID32> --node "<node.exe完整路径>" --npm-cli "<npm-cli.js完整路径>"
```

SteamID32 是 userdata 下的数字目录名。同一目录绑定一个账号；更换账号使用空白分享包。重复初始化只补齐缺少的模板，保留已有分类及缓存，并更新本机配置。移动目录后重新 init。

setup 安装锁定依赖、编译 MCP、验证协议握手。Python 3.10+、Node.js 22.22.1+；npm-cli.js 通常位于 Node 安装目录的 node_modules/npm/bin 下，使用包管理器安装时按实际位置指定。

默认只读的 MCP 接入配置由 init 写入 mcp.local.json。支持 stdio MCP 的 agent 客户端可采用其中的 command、args、cwd、env；具体注册位置由该客户端决定。本地命令也能完成完整工作流，注册 MCP 不影响批量入口的使用。

## 连接 Steam 与刷新

status 检查调试端口及游戏库是否就绪。若需要开启客户端调试，先告知用户将关闭并重开 Steam，取得对应授权后执行：

```powershell
python -X utf8 tools/workflow.py connect
python -X utf8 tools/workflow.py refresh
```

connect 调用 steam_restart，健康连接会直接复用；默认拒绝中断运行中的游戏。refresh 不重启 Steam，读取实时库存、导入清单、读取现有收藏夹并生成 AI 待处理队列。当前登录账号与本地绑定不符时停止，避免混用数据。

库存以客户端本人及家庭可用的游戏为主，安装状态是单独字段；未安装游戏同样导出。试玩、测试、软件、未知归属及暂时缺席的历史记录分别保留。应用类型为 game 的条目也可能是厂商命名的试玩序章，需要查询版本身份；把具体证据写入 data/inventory_scope.json 的 preview_appids。

## 手动调整与复用

refresh 对照上次实时快照，识别静态收藏夹的手动增减，保存于 state/manual_memberships.json。计划中的成员按这些调整覆盖AI建议；原玩法理由和研究资料仍保留，新增游戏正常判断。原始AI判断与实际收藏夹归属分别可追溯。

手动改名、删除或改变动态规则的集合保留当前状态，并在 output/manual_changes.json 及本地结构记录中列出；AI先解释变化及对自动维护的影响。需要恢复该集合自动维护时，用户决定后移除对应 preserved_names/preserved_ids；需要取消某项手动成员覆盖时，移除对应 include/exclude。批次自身产生的变化在应用后直接更新快照，后续不会当作手动调整。

首次导入没有旧快照时，已有收藏夹作为原始参考；首次整理仍通过具体预览确定迁移和成员。以后继续使用本账号原工作目录，保留这些本地修订记录。

## AI调查和保存判断

AI先读取 output/to_classify.jsonl 与已有 state。公开资料按 AppID 批量缓存，已有资料可复用，更新过的版本按需刷新：

```powershell
python -X utf8 tools/fetch_metadata.py --all-games
python -X utf8 tools/fetch_capabilities.py
python -X utf8 tools/fetch_length_mapping.py
python -X utf8 tools/fetch_lengths.py
```

查询可用 --appids 限定本轮新增游戏。商店标签和模式字段是线索，AI还需核实完整玩法、单人流程、在线合作、DLC条件及当前服务状态。商店失败后查开发者官网、公告、商店介绍及可靠实机记录，并保存来源与查询日期。网络失败应先区分运行环境限制和服务问题。

玩法判断整理成 JSON 列表，通过已有导入器批量保存：

```json
[
  {
    "appid": 620,
    "primary_type": "解谜",
    "secondary_types": [],
    "reason": "以传送门、空间关系与机关解谜推进关卡。",
    "sources": ["https://store.steampowered.com/app/620/"],
    "checked_date": "YYYY-MM-DD",
    "confidence": "high"
  }
]
```

```powershell
python -X utf8 tools/prepare_review.py --decisions output/decisions.json
```

这是格式示例，只导入当前库存中的 AppID。用户明确修订时追加 --user-correction；普通 AI 更新不会覆盖 confirmed 或 user_override。用户整体同意一套明确方案可确认该方案列出的具体归属，不能把未展示的全库判断自动标为已确认。

联机记录写入 state/multiplayer.json 的 games，键为 AppID：
- group 为 多人对战、好友合作、可选合作，或 null。
- solo_supported、minimum_players、coop_minimum_players、public_lobby_or_matchmaking 保存事实。
- mode_note、reason、sources、checked_date、review_status 保存依据。
- eligibility 为 included 或 excluded；需补查时用 pending/conditional。
- 主要PvP射击使用 collection_policy=multiplayer_only；其余用 gameplay_and_multiplayer。
- 明确没有合格联机玩法的游戏也可保存 group=null、eligibility=excluded 与核实依据，避免重复调查。

流程时长优先核对 AppID一致的主线统计。官方或实机估计保存于 data/duration_decisions.json 的 official_estimates/public_estimates；字段为 minimum_hours、maximum_hours、scope=main_story、reason、source。长期常驻保存于 ongoing，包含 bucket=时长-长期常驻、reason、sources。多路线、版本和合集需说明覆盖范围；统计跨越边界时保留区间。

查询无结果仍需继续调查。确实没有可靠主线样本时，在 data/completion_times.json 对应记录中保存 research_note、research_sources、research_checked_date，明确已查询的资料和缺口。程序允许有证据的未定时长，不允许用未查询或查询失败代替完成工作。

## 计划、预演与应用

```powershell
python -X utf8 tools/workflow.py plan
python -X utf8 tools/workflow.py preview
```

plan 生成 collection_plan.json/md、review_ready.md 及交叉检查；仍在调查时可反复生成。preview 检查玩法覆盖、类别名称、合作条件和时长调查，再批量 dry_run。AI在聊天解释本轮具体变化、动态集合转换及迁移；不要只甩出完整表格。

新环境的迁移映射默认为空。现有收藏夹作为参考，原名、个人集合、收藏与隐藏先保留。改名、删除或动态转静态需要纳入本轮具体预览；用户授权后更新 data/classification_discussion.json 的 collection_policy，或 state/multiplayer.json 的 replace_collection。填写的是当前账号读到的 ID。

用户认可具体方案后执行：

```powershell
python -X utf8 tools/workflow.py apply
```

apply 的检查与记录包括：
1. 比对账号、计划输入哈希和当前收藏夹原始存储。
2. 保存本轮计划与全部输入；复制 Steam 原始存储，记录 MCP 备份。
3. 逐收藏夹批量处理；先创建、改名和调整目的集合，最后删除迁移集合。
4. 保留现有静态集合 ID 和范围外历史成员；获准的动态集合使用 Steam 原生 FreezeToStatic/Save。
5. 重新连接读取实际结果，核对磁盘静态成员及保留项，再生成计划；差额为0后写入 state/last_application.json。

批次回执使用时间戳文件名；计划和输入保存在 data/snapshots/application-*，原始存储另存 steam-storage-*。没有差额时直接结束。应用阶段不要并行发出收藏夹写操作；批次内部按集合和最多500项一组处理。

## 失败与恢复

失败回执记录已完成步骤和错误。先重新读取实际 Steam 状态，对照本轮 before、after、原始存储和输入快照；不要盲目重跑过期计划。

静态成员与名称可通过 MCP 按写前快照恢复，再重新读取验证。被删除的收藏夹重新创建会获得新ID。动态规则保存于原始备份，但通用入口没有自动回滚动态规则的命令；需要 agent 根据当前客户端能力准备具体恢复方案。不要在 Steam 运行时直接覆盖云存储文件。

## 检查与分享

```powershell
python -X utf8 -m unittest discover -s tests
python -X utf8 tools/workflow.py package
```

分享 release/steam-library-workflow.zip。包只包含公共文档、空白规则模板、程序、测试和 MCP 源码；不包含本机配置、库存、分类记录、缓存、快照、依赖目录、Git历史、开发日志或生成的插件路径。新机器解压后重新 init/setup，首次导入不需要旧库存。
