# codex-official-api-handoff

`codex-official-api-handoff` 是一个本地工具，用来在 Codex Desktop 的官方 OpenAI 登录模式和 cc-switch/API 模式之间交接会话。

它的目标很简单：

> 官方账号左侧看到什么，切到 API 后也尽量看到什么；API 里继续聊、重命名、归档后，切回官方账号也能接上。

## 它不会做什么

- 不修改 `auth.json`。
- 不接管 cc-switch 的登录和 provider 配置。
- 不覆盖 Codex 官方登录态。
- 不删除会话。
- 不绕过官方额度限制。
- 不把 provider 绑定的 `encrypted_content` 强行复制到另一侧。

## 安装

进入项目目录，运行安装脚本：

```powershell
cd "E:\OneDrive\Develop\codex-official-api-handoff"
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

安装后会得到两个命令：

```powershell
codex-handoff
codex-official-api-handoff
```

普通用户日常只需要用 `codex-handoff`。

如果 PowerShell 提示“无法识别 codex-handoff”，可以先使用项目本地命令：

```powershell
.\bin\codex-handoff.cmd api
.\bin\codex-handoff.cmd official
```

也可以把安装脚本输出的 Python `Scripts` 目录加入系统 PATH。

## 日常切换

准备从官方账号切到 API：

```powershell
codex-handoff api
```

看到完成提示后，再去 cc-switch 切到 API。

准备从 API 切回官方账号：

```powershell
codex-handoff official
```

如果还没有配置 PATH，也可以在项目目录下运行：

```powershell
.\bin\codex-handoff.cmd api
.\bin\codex-handoff.cmd official
```

看到完成提示后，再去 cc-switch 切到官方账号。

这两个命令会先预览，再询问确认。确认后会：

- 备份 `.codex`；
- 同步已接入会话的内容；
- 同步左侧会话列表；
- 同步会话标题；
- 同步归档状态；
- 把归档会话的 JSONL 文件移动到 Codex Desktop 期望的位置；
- 修复 SQLite 中残留的旧 JSONL 路径。

## 检查状态

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

## 备份与恢复

备份默认保存到：

```text
D:\codex-backups\codex-official-api-handoff\YYYYMMDD-HHMMSS
```

每个备份目录里都有：

```text
restore-codex-backup.ps1
```

恢复步骤：

1. 完全退出 Codex Desktop。
2. 进入对应备份目录。
3. 运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\restore-codex-backup.ps1 -ConfirmRestore
```

恢复脚本会先把当前 `.codex` 移到一个 `before-restore-*` 目录，再恢复备份。

## 推荐测试计划

本机稳定测试建议持续 2-3 天：

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

## 当前状态

这是早期本地工具，已经在 Windows + Codex Desktop + cc-switch/API provider 场景下完成初步验证。

建议先在私有仓库和个人电脑上持续测试，确认稳定后再公开发布。
