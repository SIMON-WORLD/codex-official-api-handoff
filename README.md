# codex-official-api-handoff

`codex-official-api-handoff` 是一个保守的本地工具，用于在 Codex Desktop 的官方 OpenAI 登录模式和 cc-switch/custom API 模式之间交接会话，让同一个任务尽量沿一条主线继续。

## 设计目标

- 不修改 `auth.json`。
- 不覆盖 Codex 内置的 `openai` provider。
- 不接管或破坏 cc-switch 的 `config.toml` 配置。
- 只同步线性会话历史；如果 official/API 两边都新增了不同内容，会停止并提示冲突。
- 跨 provider 同步时会忽略或过滤 `encrypted_content`，避免把 provider 绑定的加密片段带到另一侧。
- 每次 `--apply` 写入前都会完整备份 `.codex`。

## 日常命令

准备从官方 OpenAI 登录模式切到 API 模式：

```powershell
codex-official-api-handoff to api
codex-official-api-handoff to api --apply
```

准备从 API 模式切回官方 OpenAI 登录模式：

```powershell
codex-official-api-handoff to official
codex-official-api-handoff to official --apply --api-provider openai-chat-completions
```

不带 `--apply` 时，命令默认是 dry-run，只报告将会做什么，不写入任何文件。

## 命令说明

```powershell
codex-official-api-handoff doctor
```

只读检查当前 `.codex` 状态，包括当前配置的 provider、`auth.json` 是否存在、已登记 pair 数量、以及 `state_5.sqlite` 中各 provider 的会话数量。

```powershell
codex-official-api-handoff pair add NAME --official OFFICIAL_ID --api API_ID --api-provider PROVIDER
codex-official-api-handoff pair list
```

登记和查看 official/API 会话对。工具只会自动同步已经登记的 pair。

```powershell
codex-official-api-handoff to api [--apply] [--api-provider PROVIDER]
```

同步所有已登记 pair，并准备从 official `openai` 模式交接到 API provider。默认不会复制未配对的新会话；如需复制，需要显式加 `--copy-new`。
如需在 dry-run 中列出未配对候选明细，可加 `--show-new`。

```powershell
codex-official-api-handoff to official [--apply] [--api-provider PROVIDER]
```

同步所有已登记 pair，并准备从 API provider 交接回 official `openai`。默认不会复制未配对的新会话；如需复制，需要显式加 `--copy-new`。
如需在 dry-run 中列出未配对候选明细，可加 `--show-new`。

## 备份与回滚

写入前的完整备份默认保存到：

```text
D:\codex-backups\codex-official-api-handoff\YYYYMMDD-HHMMSS
```

每个备份目录都会生成：

```text
restore-codex-backup.ps1
```

如果需要回滚，请先完全退出 Codex，再运行该脚本并加上 `-ConfirmRestore`。

## 当前状态

这是早期本地原型。建议始终先 dry-run，确认报告干净后再使用 `--apply`。
