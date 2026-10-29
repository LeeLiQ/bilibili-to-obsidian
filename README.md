# bili-triage

B 站技术视频 → Obsidian 证据面板。系统不下结论，只负责把证据摆全、排好顺序，让你 60 秒内自己判断看不看。
设计见 [B站内容分诊-设计说明.md](B站内容分诊-设计说明.md)（§10 是实现时的改动）。

**在哪台机器上跑**：主力是 Mac（个人设备）。登录 B 站、分诊、看视频都在 Mac 上，面板写进 Mac vault。PC 是工作机，不登个人账号；它只接收 Mac 导出的包，用来合成知识卡。代码本身跨平台。

## 安装（Mac）

```bash
brew install ffmpeg
cd bilibili-to-obsidian
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[mac]'                  # 装 mlx-whisper
cp config.example.toml config.toml       # 改 [vault] path、[llm] 模型、[favorites] 收藏夹 ID
export OPENAI_API_KEY=...                # 环境变量名要和 [llm] api_key_env 一致
bili-triage doctor
```

**B 站登录态**：配置里写 `cookies_from_browser = "safari"`（或 chrome / firefox），yt-dlp 会直接读浏览器的登录状态。
- 用 Safari：要先在「系统设置 → 隐私与安全性 → 完全磁盘访问权限」里勾上你用的终端。
- 用 Chrome：第一次读取时 macOS 会弹钥匙串授权。

不登录的话只能拿到约 480p，录屏里的字读不清。

## 用法

| 命令 | 作用 |
|---|---|
| `bili-triage run <URL\|BV号> [...]` | 生成面板，落到 `00-收件箱/` |
| `bili-triage fav` | 处理收藏夹里还没处理过的视频（每轮最多 5 条，失败最多重试 3 次） |
| `bili-triage frames BV1xx 08:12-09:40` | 抽这一段的单帧原图，追加到面板末尾；PPT 类视频加 `--mode scene` |
| `bili-triage route` | 执行你在面板里填的出口，并统计 D2 够用率 |
| `bili-triage export` | 打包要去 PC 合成的内容 |
| `bili-triage gc [--orphans]` | 清理超过 30 天的缓存；加 `--orphans` 还会删掉全库都没有笔记引用的附件目录 |

收藏夹自动跑：
1. 先把 key 存进钥匙串：`security add-generic-password -a "$USER" -s bili-triage-llm -w '<key>'`
2. 再运行 `bash scripts/install-launchd.sh`，之后每 30 分钟自动跑一次。

手机刷到就收藏，不用再做别的。

## 读完一张卡之后

在面板的 frontmatter 里填这几项：

- `decidable`：能 / 不能（D2：看完这张卡，能不能决定看不看）
- `exit`：跳过 / 存着 / 看片段 / 精读
- `reason`：跳过时可选，写原因关键词
- `topic`：可选，进 `20-资料/` 时的主题子目录

然后跑 `bili-triage route`：
- 跳过：在 `90-弃/分诊拒绝记录.md` 记一行，面板和附件一起删掉。
- 其余三个出口：移到 `20-资料/<topic>/`。

## 同步到 PC（手动）

1. 精读后写的笔记，在 frontmatter 里加 `to_pc: true`。只有标了这个的普通笔记才会被导出。
2. 运行 `bili-triage export`，会生成 `~/bili-to-pc/bili-<时间戳>/`，里面是 `20-资料` 的面板、标了 `to_pc` 的笔记和用到的图片。
3. 把整个文件夹拖进 PC vault 的 `00 Inbox/From Mac/Bilibili/`。

## 数据放在哪

- **vault 内**：
  - 面板 `.md`
  - 联系表和单帧：`attachments/分诊-<bvid>/`，联系表叫 `sheet-<bvid>.jpg`，单帧叫 `<bvid>-t<秒>.png`
- **vault 外**：
  - 缓存：`~/.bili-triage/cache/`，包括视频、音频、转写和 LLM 原始输出，30 天后清理
  - 处理状态：`~/.bili-triage/state.json`

## 其他平台

`[asr] backend = "auto"`：Apple Silicon 上用 mlx-whisper，其他平台用 faster-whisper（`pip install -e '.[local-asr]'`）。也可以设成 `api`，走云端转写。

`scripts/register-fav-task.ps1` 是 Windows 的定时任务脚本，只给个人 Windows 设备用。

## 测试

```bash
python -m tests.test_offline   # 合成视频 + 假 LLM，不联网，覆盖：生成、校验、抽帧、出口、导出
```
