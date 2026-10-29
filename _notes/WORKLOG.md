# WORKLOG · bili-triage

## 2026-10-26 · 设计审阅 → v0.1 实现（Cowork 会话）

**产出**
- `bili_triage/` Python 包，命令行 `bili-triage run | fav | frames | route | gc | doctor`
- `config.example.toml`、`README.md`、`scripts/register-fav-task.ps1`（计划任务，每 30 分钟拉一次收藏夹）
- `tests/test_offline.py`：合成视频 + 假 LLM 跑完整流程，在沙箱里通过（ffmpeg 4.4）
- 设计说明追加 §10「实现决议」；改之前的原文备份在 `_scratch/B站内容分诊-设计说明.BACKUP-20261026-223625.md`

**关键决策**（Qian 拍板）→ 第 1、3 项已被 10-27 更正，见下
- ASR：先用 CPU 跑 faster-whisper，暂时接受超出 3 分钟目标；之后切云端或 CUDA 只改配置
- LLM：用 OpenAI 兼容接口，端点和模型在配置里填（必须支持图片输入）
- vault：作为第四条管道接入现有结构（Clippings/Bilibili + value 字段 + 根 WORKLOG 台账），不另建 §6 的目录
- 入口：手动 URL 和收藏夹轮询两种都做

**审阅时提出、已落进代码的改动**
- 多模态组卡（把截图喂给 LLM）
- 可复现条目和原文引述都由代码回指核对，核不上就删
- 评价词：先让 LLM 重试一次，仍然出现就整个子句删掉
- 单帧文件名加 bvid 前缀，避免不同视频的单帧重名
- 关键帧扫描算画面变化
- 视频优先选 avc1 编码（抽帧解码更快）
- whisper 幻觉句过滤 + 繁转简

**没验证的**（沙箱连不上 B 站，也没有真 LLM 或中文音频）
- B 站接口和 yt-dlp 下载、faster-whisper 转真实中文、真 LLM 的输出质量、Windows 上的计划任务
- 第一步：在本机跑 `bili-triage doctor`，再拿 3 个不同类型的 URL 对照 §9 验收

**未决**
- D1 收件箱怎么清理
- L2 的 OCR
- 合集 / 多 P 展开
- 评价词黑名单可能误伤（目前给「推荐系统 / 推荐算法」等开了白名单）
- （已处理）`_scratch/test-sheet.jpg` 测试联系表：10-26 复查时已不存在

## 2026-10-27 · 更正：改回 Mac 为主、跨平台（Cowork 会话）

**错在哪**
- 10-26 我因为会话跑在 PC 上，就默认工具要跑在 PC 上，于是把原设计的 Mac 配置、mlx-whisper 和 §6 目录都当成错误改掉了。我还建议在 PC（工作机）上登录 B 站，这违反了「私人内容绝不上 PC」。
- 当时的选择题是在这个错误前提下出的，所以 10-26 那几项「拍板」不能都当成 Qian 的真实决定。

**10-26 决策的去向**
- ASR 先用 CPU：被接替。现在 `backend = auto`，Apple Silicon 上用 mlx-whisper，其他平台用 faster-whisper 或云端。
- LLM 用 OpenAI 兼容接口：仍有效。
- 接入 PC 现有管道：被接替。Mac 上按 §6 结构；PC 只接收导出包（落在 `From Mac/Bilibili/`）。
- 两种入口：仍有效，改在 Mac 上跑（launchd）。

**10-27 新决定（Qian）**
- 同步「存着」的面板和精读笔记（笔记要标 `to_pc: true` 才导出）。
- PC 落点是 `00 Inbox/From Mac/Bilibili/`。
- 跳过的面板在 90-弃 记一行，然后连同附件删除。
- 单帧文件名保留 bvid 前缀（和 §6 唯一的不同，Qian 没有反对）。

**代码改动**
- config：默认值改成 §6 的结构和 `~/.bili-triage/`；新增 `cookies_from_browser`、`export`、`asr_resolved`。
- asr：新增 mlx 后端。
- bili_api / download：可以直接读浏览器的登录态。
- render：frontmatter 改成 §5.3 模板，加上 `decidable / exit / reason / topic`。
- vault：重写 `route`（执行出口），新增 `export`（只认显式标记、内容改过才重新导出、同名图片只放一份）；`state.json` 移到缓存目录外。
- cli：新增 `export` 子命令，`doctor` 加上登录态和模型名检查。
- scripts：新增 `install-launchd.sh`（API key 从钥匙串读）；ps1 脚本注明只给个人 Windows 设备用。
- 文档：README 重写；设计说明 §10 更正 10.2–10.4，新增 10.8。
- 测试：`tests/test_offline.py` 扩展到覆盖出口、跳过后不再分诊、导出不带出私人笔记、导出包里没有重名图片。沙箱里全部通过。

**没验证的**
- mlx-whisper、真实 B 站接口、读浏览器 cookie、真实 LLM、launchd。这些都要在 Mac 上跑一次，对照 §9 验收。

**未决**
- （已完成 10-27）wechat-digest 跳过 `From Mac/Bilibili/`：Qian 手动改了第 21、36 行，核对过。
- PC 端处理 B 站包的合成步骤还没建。
- D1 收件箱怎么清理；L2 的 OCR；合集和多 P 展开。
