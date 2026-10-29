"""离线端到端测试：合成视频 + 伪造转写 + 本地假 LLM 服务器，不碰 B 站和真 LLM。

    python -m tests.test_offline     （项目根目录下运行，需要 ffmpeg）
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bili_triage import panel as panel_mod  # noqa: E402
from bili_triage import pipeline  # noqa: E402
from bili_triage.bili_api import VideoRef, extract_links  # noqa: E402
from bili_triage.config import Config  # noqa: E402
from bili_triage.frames import extract_range  # noqa: E402
from bili_triage.util import write_json  # noqa: E402
from bili_triage.vault import export, frontmatter, route  # noqa: E402

BV = "BV1test00000"
DUR = 300  # 5 分钟

SEGS = [
    {"start": 2, "end": 8, "text": "先说结论，这个方案我用了两个月，中间踩了三个坑，我一个个讲。"},
    {"start": 40, "end": 48, "text": "然后就是那个，我们先打开终端，输入 pip install ragas，版本是 0.2.6。"},
    {"start": 100, "end": 108, "text": "这里可能会有点绕，我换个说法，其实就是把评估拆成三步。"},
    {"start": 160, "end": 170, "text": "context recall 的阈值我设的是 0.8，低于这个就报警，对吧。"},
    {"start": 250, "end": 258, "text": "想系统学的可以看一下我的课，链接在简介里，然后我们下期见。"},
]

LLM_REPLY = {
    "one_liner": "演示用 ragas 给 RAG 流程做评估，设置 context recall 阈值",
    "timeline": [
        {"bucket": 0, "type": "口播", "what": "背景与结论预告"},
        {"bucket": 1, "type": "演示", "what": "安装 ragas，跑评估"},
        {"bucket": 2, "type": "闲聊", "what": "卖课引流，干货满满"},  # 评价词 → 应被剥离
    ],
    "reproducible": [
        {"t": "00:42", "item": "pip install ragas==0.2.6", "evidence": "口述", "quote": "输入 pip install ragas，版本是 0.2.6"},
        {"t": "02:42", "item": "context_recall 阈值 0.8", "evidence": "口述", "quote": "context recall 的阈值我设的是 0.8"},
        {"t": "01:00", "item": "LangChain v0.3", "evidence": "口述", "quote": "我们用的是 LangChain 零点三版本"},  # 编造 → 应删
        {"t": "03:45", "item": "config.yaml 全文", "evidence": "画面", "quote": ""},  # 对不上截图 → 应删
    ],
    "style_quotes": [
        {"t": "00:02", "text": "先说结论，这个方案我用了两个月，中间踩了三个坑，我一个个讲。"},
        {"t": "01:40", "text": "这句话转写里根本没有"},  # 编造 → 应删并由代码补
        {"t": "04:10", "text": "想系统学的可以看一下我的课，链接在简介里"},
    ],
}


class FakeLLM(BaseHTTPRequestHandler):
    calls = 0

    def do_POST(self):
        FakeLLM.calls += 1
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        n_img = sum(1 for p in body["messages"][1]["content"] if p.get("type") == "image_url")
        assert n_img > 0, "没有图片进 LLM"
        out = json.dumps({"choices": [{"message": {"content": json.dumps(LLM_REPLY, ensure_ascii=False)}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class FakeBili:
    def __init__(self, *_):
        pass

    @classmethod
    def from_cfg(cls, cfg):
        return cls()

    def resolve(self, s):
        return VideoRef(s, 1)

    def view(self, bvid):
        return {"title": "测试：用 RAGAS 评估 RAG", "owner": {"name": "测试UP"}, "desc": "代码 https://github.com/x/y 。",
                "aid": 1, "pubdate": 1761400000, "pages": [{"cid": 42, "duration": DUR, "part": ""}]}

    def chapters(self, *a):
        return []

    def pinned_reply(self, *a):
        return "文档：https://docs.example.com/rag"


def main():
    td = Path(tempfile.mkdtemp(prefix="bili-test-"))
    vault, cache = td / "vault", td / "cache"
    vault.mkdir()

    # 合成视频：每 75s 换一种画面，模拟场景切换
    kc = cache / BV
    kc.mkdir(parents=True)
    srcs = ["testsrc=size=1280x720:rate=10", "smptebars=size=1280x720:rate=10",
            "mandelbrot=size=1280x720:rate=10", "rgbtestsrc=size=1280x720:rate=10"]
    parts = []
    for i, s in enumerate(srcs):
        p = td / f"p{i}.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", s, "-t", "75",
                        "-c:v", "libx264", "-g", "20", "-pix_fmt", "yuv420p", str(p)], check=True)
        parts.append(p)
    lst = td / "list.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c", "copy", str(kc / "video.mp4")], check=True)
    (kc / "audio.m4a").write_bytes(b"x")
    write_json(kc / "transcript.json", SEGS)

    srv = HTTPServer(("127.0.0.1", 0), FakeLLM)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    os.environ["FAKE_KEY"] = "k"

    cfg = Config(vault=vault, cache_dir=cache, llm_base_url=f"http://127.0.0.1:{srv.server_port}/v1",
                 llm_api_key_env="FAKE_KEY", llm_model="fake", export_dir=td / "export",
                 state_path=td / "state.json")
    pipeline.Bili = FakeBili
    path = pipeline.run_one(cfg, BV)
    md = path.read_text(encoding="utf-8")
    print("=" * 60)
    print(md)
    print("=" * 60)

    fm = frontmatter(md)
    assert path.parent == vault / "00-收件箱"
    assert fm["bvid"] == BV and fm["exit"] == "" and fm["up"] == "测试UP"
    assert "干货" not in md, "评价词没被剥离"
    assert "LangChain" not in md.split("## 可复现清单")[1].split("## 原文引述")[0], "编造条目没被删"
    assert "config.yaml" not in md.split("## 可复现清单")[1].split("## 原文引述")[0], "对不上截图的画面条目没被删"
    assert "这句话转写里根本没有" not in md.split("## 原文引述")[1].split("## 客观指标")[0]
    assert md.split("## 原文引述")[1].split("## 客观指标")[0].count("「") == 3, "原文引述应恰好 3 条"
    assert "| 卖课引流 |" in md, "含评价词的子句应整句删掉"
    assert "https://github.com/x/y" in md and "https://docs.example.com/rag" in md
    adir = vault / "attachments" / f"分诊-{BV}"
    assert (adir / f"sheet-{BV}.jpg").exists()
    assert not (vault / "WORKLOG.md").exists(), "默认不该写 vault 台账"
    # 假回复里有评价词 → 触发一次纠错重试 → 仍违规 → 由代码剥离
    assert FakeLLM.calls == 2, f"LLM 调用 {FakeLLM.calls} 次（应为 1 次 + 1 次纠错重试）"

    # 重复运行应跳过
    assert pipeline.run_one(cfg, BV) is None

    # L2 区间抽帧：interval + scene
    out = extract_range(kc / "video.mp4", 70, 90, adir, BV, "interval", 5)
    assert [p.name for _, p in out][:2] == [f"{BV}-t0070.png", f"{BV}-t0075.png"], out
    sc = extract_range(kc / "video.mp4", 60, 160, adir, BV, "scene", threshold=0.3)
    names = [p.name for _, p in sc]
    print("scene 帧：", names)
    assert any(abs(int(n.split("-t")[1][:4]) - 75) <= 1 for n in names), "75s 处的场景切换没抓到"
    assert any(abs(int(n.split("-t")[1][:4]) - 150) <= 1 for n in names), "150s 处的场景切换没抓到"

    # 第二条视频，用来测「跳过」
    BV2 = "BV1skip00000"
    shutil.copytree(kc, cache / BV2)
    path2 = pipeline.run_one(cfg, BV2)

    # route：精读 → 20-资料/<topic>/；跳过 → 90-弃 一行 + 删面板和附件
    path.write_text(md.replace("exit: \n", "exit: 精读\n").replace("topic: \n", "topic: RAG\n")
                    .replace("decidable: \n", "decidable: 能\n"), encoding="utf-8")
    t2 = path2.read_text(encoding="utf-8")
    path2.write_text(t2.replace("exit: \n", "exit: 跳过\n").replace("reason: \n", "reason: 新闻复述\n")
                     .replace("decidable: \n", "decidable: 不能\n"), encoding="utf-8")
    route(cfg)
    moved = vault / "20-资料" / "RAG" / path.name
    assert moved.exists() and not path.exists()
    assert not path2.exists() and not (vault / "attachments" / f"分诊-{BV2}").exists()
    rej = (vault / "90-弃" / "分诊拒绝记录.md").read_text(encoding="utf-8")
    assert "| 新闻复述 |" in rej and BV2 in rej, rej
    assert pipeline.run_one(cfg, BV2) is None, "跳过过的视频不该重新分诊"

    # export：20-资料 的面板 + 显式 to_pc 笔记；没标记的私人笔记绝不带出
    (vault / "日记").mkdir()
    (vault / "日记" / "私人.md").write_text(f"---\ntitle: 私人\n---\n今天看了 {BV}\n", encoding="utf-8")
    (vault / "笔记").mkdir()
    (vault / "笔记" / "精读-RAG评估.md").write_text(
        f"---\nto_pc: true\n---\n我的理解……\n![[{BV}-t0075.png]]\n", encoding="utf-8")
    bundle = export(cfg)
    files = sorted(p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file())
    print("导出包：", files)
    assert path.name in files and "精读-RAG评估.md" in files
    assert "私人.md" not in files, "私人笔记被带出去了"
    assert f"_attachments/分诊-{BV}/sheet-{BV}.jpg" in files
    imgs = [f.rsplit("/", 1)[-1] for f in files if f.endswith((".png", ".jpg"))]
    assert len(imgs) == len(set(imgs)), f"包里有重名图片：{imgs}"
    assert f"_attachments/分诊-{BV}/{BV}-t0075.png" in files
    assert "origin: mac-bili-triage" in (bundle / path.name).read_text(encoding="utf-8")
    assert export(cfg) is None, "没改动不该重复导出"

    # 单元：链接抽取、匹配度
    assert extract_links("见 https://a.com/x，和 https://b.com。") == ["https://a.com/x", "https://b.com"]
    assert panel_mod.match_ratio("阈值我设的是0.8", "context recall 的阈值我设的是 0.8，低于") == 1.0
    print("\nALL OK  →", td)


if __name__ == "__main__":
    main()
