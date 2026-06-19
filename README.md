# codex-official-api-handoff

`codex-official-api-handoff` 是一个本地工具，用来在 Codex Desktop 的官方 OpenAI 登录模式和 cc-switch/API provider 模式之间交接会话。

它解决的核心问题是：

> 官方账号左侧看到什么，切到 API 后也尽量看到什么；API 里继续聊、重命名、归档后，切回官方账号也能接上。

## 和 cc-switch 的关系

本项目不是 cc-switch 的替代品，而是配合 cc-switch 使用的会话交接工具。

cc-switch 的 Codex 应用增强能力主要解决的是：

- 保留官方 `auth.json` 登录态；
- 把第三方供应商、模型、endpoint、token 等写入 `config.toml`；
- 让 Codex Desktop 仍能识别官方账号，从而尽量保留官方插件、手机远程操作等能力；
- 让模型请求实际走 cc-switch 当前选择的第三方 API provider。

也就是说，cc-switch 解决的是“官方登录态 + 第三方 API 请求路由”的问题。

但在部分 Codex Desktop 环境中，官方账号和 API provider 会被记录为不同的本地会话来源。切换后可能出现：

- 左侧聊天记录不一致；
- 某些官方账号下的会话在 API 侧不可见；
- API 侧继续聊过的内容切回官方后接不上；
- 手动重命名的标题变回旧标题或“你好”；
- 归档状态在两边不一致。

如果你的 cc-switch 配置已经能让官方账号和 API 之间天然共用同一批左侧会话，并且标题、归档、续聊都正常，那么你可能不需要这个工具。

如果你遇到上述断裂，本项目补齐的是“Codex Desktop 本地会话状态镜像”这一层：同步 `.codex` 里的会话索引、JSONL、标题、归档状态和 SQLite 元数据，让官方/API 两侧尽量沿同一条任务主线继续。

## 适合谁

- Codex 官方账号额度经常用完，但仍想继续同一批任务的人。
- 同时使用官方登录和 cc-switch/API provider 的 Codex Desktop 用户。
- 使用 cc-switch 后仍遇到左侧聊天记录、标题、归档状态断裂的人。
- 希望保留官方登录态、官方插件、远程能力，同时在额度不足时切到 API 的人。

## 项目优势

- 不替代 cc-switch，只补齐会话交接能力。
- 不修改 `auth.json`，尽量保留官方登录态。
- 不接管 provider 配置，不覆盖你的 cc-switch 方案。
- 同步的不只是聊天 JSONL，还包括左侧列表、标题、归档状态和 Codex Desktop 的标题索引。
- 每次写入前自动备份，并生成恢复脚本。
- 如果 cc-switch 已经满足你的会话连续需求，可以不使用本工具。
- 日常只需要记住两个命令：

```powershell
codex-handoff api
codex-handoff official
```

## 风险说明

这是一个会修改 Codex Desktop 本地状态文件的实验性工具。它会读写：

- `.codex/state_5.sqlite`
- `.codex/session_index.jsonl`
- `.codex/sessions/`
- `.codex/archived_sessions/`
- `.codex/official-api-handoff/pairs.json`

首次使用前建议：

1. 完全退出 Codex Desktop。
2. 先运行检查命令。
3. 确认工具生成了备份。
4. 小范围测试几轮后再长期使用。

## 它不会做什么

- 不修改 `auth.json`。
- 不接管 cc-switch 的登录和 provider 配置。
- 不覆盖 Codex 官方登录态。
- 不删除会话。
- 不绕过官方额度限制。
- 不把 provider 绑定的 `encrypted_content` 强行复制到另一侧。
- 不跨 provider 复制 Automation 历史运行会话；自动化任务本身仍由 Codex Desktop 管理并正常运行。

## 安装

克隆仓库后进入项目目录：

```powershell
git clone https://github.com/<your-name>/codex-official-api-handoff.git
cd codex-official-api-handoff
```

运行安装脚本：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

安装后会得到两个命令：

```powershell
codex-handoff
codex-official-api-handoff
```

普通用户日常只需要用 `codex-handoff`。

如果 PowerShell 提示“无法识别 codex-handoff”，可以先在项目目录下使用本地命令：

```powershell
.\bin\codex-handoff.cmd api
.\bin\codex-handoff.cmd official
```

也可以把安装脚本输出的 Python `Scripts` 目录加入系统 PATH。

## 日常切换

记住一句话：

> 准备切到哪边，就先运行 `codex-handoff` 哪边。

准备从官方账号切到 API：

```powershell
codex-handoff api
```

完成后，再去 cc-switch 切到 API。

准备从 API 切回官方账号：

```powershell
codex-handoff official
```

完成后，再去 cc-switch 切到官方账号。

这两个命令会先预览，再询问确认。确认后会：

- 备份 Codex 本地状态；
- 同步已接入会话的内容；
- 同步左侧会话列表；
- 同步会话标题；
- 同步归档状态；
- 同步置顶状态，把当前侧置顶会话映射到目标侧对应副本；
- 默认排除 Automation 历史运行会话，避免不同 provider 的展示规则污染左侧列表；
- 把归档会话的 JSONL 文件移动到 Codex Desktop 期望的位置；
- 修复 SQLite 中残留的旧 JSONL 路径。

如果同一条会话曾在官方和 API 两侧分别继续，工具会识别为“双边分叉”。确认交接后，以你当前正在离开的源侧为主线重建目标副本；目标侧旧副本已经包含在本次完整备份中，不会把两条互相矛盾的尾部强行拼接。

切换命令运行期间不要继续在 Codex Desktop 发送消息。否则活跃会话会在备份过程中继续增长，命令完成后可能再次显示少量“待交接”内容。

置顶会话由 Codex Desktop 单独管理。会话置顶后通常只显示在“置顶”区域，不再在项目分组中重复显示；这不代表会话被归档或丢失。本工具会把当前侧置顶状态映射到目标侧对应 pair，但不会修改官方登录态或 provider 配置。

## 备份与恢复

默认备份目录是：

```text
%USERPROFILE%\codex-backups\codex-official-api-handoff\YYYYMMDD-HHMMSS
```

也可以用环境变量或参数自定义。比如想固定备份到其他磁盘：

```powershell
$env:CODEX_HANDOFF_BACKUP_BASE="$HOME\codex-backups-custom\codex-official-api-handoff"
codex-handoff api
```

或：

```powershell
codex-handoff api --backup-base "$HOME\codex-backups-custom\codex-official-api-handoff"
```

如果希望长期生效，可以设置用户环境变量后重开 PowerShell：

```powershell
[Environment]::SetEnvironmentVariable(
  "CODEX_HANDOFF_BACKUP_BASE",
  "$HOME\codex-backups-custom\codex-official-api-handoff",
  "User"
)
```

每个备份目录里都有：

```text
restore-codex-backup.ps1
```

如果切换后发现异常，恢复步骤是：

1. 完全退出 Codex Desktop。
2. 进入对应备份目录。
3. 运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\restore-codex-backup.ps1 -ConfirmRestore
```

恢复脚本会先把当前 `.codex` 移到一个 `before-restore-*` 目录，再恢复备份。

## 检查状态

一键体检：

```powershell
codex-handoff doctor
```

只检查某个切换方向：

```powershell
codex-handoff doctor api
codex-handoff doctor official
```

`doctor` 会检查可见会话、标题、归档、排序、更新时间、置顶状态和已接入会话 JSONL 内容是否一致。

检查“切到 API 前是否已经准备好”：

```powershell
codex-official-api-handoff check api
```

检查“切到官方账号前是否已经准备好”：

```powershell
codex-official-api-handoff check official
```

常见结论：

```text
结论：目标侧左侧列表一致；已接入会话的归档状态一致。
```

表示可以切换。

```text
结论：这是正常的待交接状态。
```

表示当前侧有新内容还没带到目标侧。按提示运行对应的 `codex-handoff api` 或 `codex-handoff official` 即可。

## 接入更多会话

如果有某条会话还没有进入 handoff 管理，可以运行：

```powershell
codex-handoff connect api
codex-handoff connect official
```

工具会列出候选会话。输入编号后，只会复制选中的会话。

选择示例：

```text
1,3,5     选择第 1、3、5 条
1-5       选择第 1 到第 5 条
all       选择当前显示的全部候选
直接回车  跳过
```

## 推荐测试计划

建议首次使用时测试 2-3 轮：

1. API -> 官方：运行 `codex-handoff official`，再用 cc-switch 切官方。
2. 在官方账号里新增会话、继续旧会话、重命名、归档。
3. 官方 -> API：运行 `codex-handoff api`，再用 cc-switch 切 API。
4. 检查左侧列表、标题、归档状态是否一致。
5. 在 API 里重复新增、继续、重命名、归档。
6. 再切回官方账号验证。

如果检查命令显示“正常的待交接状态”，这是预期行为，说明当前侧刚产生了新内容，切换前运行对应 handoff 即可。

## 高级命令

完整 CLI：

```powershell
codex-official-api-handoff doctor
codex-official-api-handoff check api
codex-official-api-handoff check official
codex-official-api-handoff pair list
codex-official-api-handoff refresh-index
```

兼容旧式单条主线同步：

```powershell
codex-handoff sync api
codex-handoff sync official
```

一般不建议日常使用 `sync`，普通切换直接用：

```powershell
codex-handoff api
codex-handoff official
```


## 参考

- [cc-switch：使用第三方 API 时保留 Codex 远程操作和官方插件](https://github.com/farion1231/cc-switch/blob/main/docs/guides/codex-official-auth-preservation-guide-zh.md)
