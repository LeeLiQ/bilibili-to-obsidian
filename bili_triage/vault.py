"""与 vault 的全部交互（Mac vault，结构见设计说明 §6）。

- 写面板到 00-收件箱/
- route：按你在面板里填的 exit 执行出口（跳过 → 90-弃 一行 + 删除；其余 → 20-资料/）
- export：把要去 PC 合成的东西打包，由你手动搬到 PC 的 00 Inbox/From Mac/Bilibili/
- gc：缓存过期清理
"""
from __future__ import annotations

import hashlib
import re
import shutil
import time
from pathlib import Path

from .config import Config
from .util import log, read_json, safe_filename, write_json

_FM = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)
KEY_RE = re.compile(r"BV[0-9A-Za-z]{10}(?:_p\d+)?")
HUMAN_FIELDS = ("decidable", "exit", "reason", "topic", "to_pc")


def frontmatter(text: str) -> dict[str, str]:
    m = _FM.match(text)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        mm = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if mm:
            out[mm.group(1)] = mm.group(2).strip().strip('"')
    return out


def set_field(text: str, key: str, value: str) -> str:
    m = _FM.match(text)
    if not m:
        return text
    fm = m.group(1)
    if re.search(rf"^{key}:.*$", fm, re.M):
        fm = re.sub(rf"^{key}:.*$", lambda _: f"{key}: {value}", fm, flags=re.M)
    else:
        fm = fm + f"\n{key}: {value}"
    return f"---\n{fm}\n---\n" + text[m.end():]


def _panels(cfg: Config, dirs: tuple[Path, ...] | None = None) -> list[Path]:
    out = []
    for d in dirs or (cfg.inbox_dir, cfg.saved_dir):
        if d.is_dir():
            out += [p for p in d.rglob("*.md") if p.name.lower() != "readme.md"]
    return out


def _is_panel(fm: dict) -> bool:
    return fm.get("type") == "分诊" and bool(fm.get("bvid"))


def find_panel(cfg: Config, key: str) -> Path | None:
    for p in _panels(cfg):
        fm = frontmatter(p.read_text(encoding="utf-8", errors="ignore")[:3000])
        if _is_panel(fm) and fm.get("bvid") == key:
            return p
    return None


def write_panel(cfg: Config, key: str, title: str, md: str) -> Path:
    existing = find_panel(cfg, key)
    if existing:
        # 重跑：保留你已经填写的字段
        old = frontmatter(existing.read_text(encoding="utf-8"))
        for k in HUMAN_FIELDS:
            if old.get(k):
                md = set_field(md, k, old[k])
        existing.write_text(md, encoding="utf-8")
        return existing
    cfg.inbox_dir.mkdir(parents=True, exist_ok=True)
    name = safe_filename(title)
    path = cfg.inbox_dir / f"{name}.md"
    if path.exists():
        path = cfg.inbox_dir / f"{name} {key}.md"
    path.write_text(md, encoding="utf-8")
    return path


def worklog(cfg: Config, entry: str) -> None:
    p = cfg.worklog_path
    if not p:
        return
    prefix = ""
    if p.exists() and p.stat().st_size > 0:
        with open(p, "rb") as f:
            f.seek(-1, 2)
            if f.read(1) != b"\n":
                prefix = "\n"
    with open(p, "a", encoding="utf-8") as f:
        f.write(f"{prefix}- {time.strftime('%Y-%m-%d %H:%M')} · {entry}\n")


# ---------------- 状态 ----------------

class State:
    def __init__(self, cfg: Config):
        self.path = cfg.state_path  # 放在缓存目录外，gc 不会清掉
        self.d: dict = read_json(self.path, {}) or {}

    def get(self, key: str) -> dict:
        return self.d.get(key, {})

    def done(self, key: str) -> bool:
        return self.get(key).get("status") == "done"

    def failures(self, key: str) -> int:
        return int(self.get(key).get("failures", 0))

    def mark(self, key: str, **kw) -> None:
        cur = self.d.get(key, {})
        cur.update(kw, ts=time.strftime("%Y-%m-%d %H:%M:%S"))
        self.d[key] = cur
        self.save()

    def save(self) -> None:
        write_json(self.path, self.d)


# ---------------- route：执行出口 ----------------

EXIT_SKIP = "跳过"
EXIT_KEEP = ("存着", "看片段", "精读")


def _reject_line(cfg: Config, fm: dict) -> None:
    p = cfg.rejected_path
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text("# 分诊拒绝记录\n\n> 一条一行，防止重复评估，不是归档。\n\n", encoding="utf-8")
    reason = fm.get("reason") or "-"
    line = f"- [{fm.get('title', '?')}]({fm.get('source', '')}) | {reason} | {time.strftime('%Y-%m-%d')}\n"
    with open(p, "a", encoding="utf-8") as f:
        f.write(line)


def route(cfg: Config) -> None:
    state = State(cfg)
    skipped, kept, pending, bad = [], [], [], []
    for p in _panels(cfg, (cfg.inbox_dir,)):
        text = p.read_text(encoding="utf-8")
        fm = frontmatter(text)
        if not _is_panel(fm):
            continue
        ex = fm.get("exit", "")
        if ex == EXIT_SKIP:
            _reject_line(cfg, fm)
            shutil.rmtree(cfg.attach_dir(fm["bvid"]), ignore_errors=True)
            p.unlink()
            state.mark(fm["bvid"], exit=EXIT_SKIP)
            skipped.append(p.stem)
        elif ex in EXIT_KEEP:
            dst_dir = cfg.saved_dir / safe_filename(fm["topic"]) if fm.get("topic") else cfg.saved_dir
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = dst_dir / p.name
            shutil.move(str(p), str(dst))
            state.mark(fm["bvid"], exit=ex)
            kept.append(f"[{ex}] {dst.relative_to(cfg.vault)}")
        elif ex:
            bad.append(f"{p.stem}（exit: {ex}）")
        else:
            pending.append(p.stem)
    for s in skipped:
        log(f"跳过：{s} → 已记入 {cfg.rejected}/，面板和附件已删")
    for k in kept:
        log(f"移入 {k}")
    for b in bad:
        log(f"exit 值不认识，没动：{b}（只认 跳过 / 存着 / 看片段 / 精读）")
    log(f"收件箱里还没定出口：{len(pending)} 条")
    _d2(cfg)


def _d2(cfg: Config) -> None:
    yes = no = 0
    for p in _panels(cfg):
        dec = frontmatter(p.read_text(encoding="utf-8", errors="ignore")[:3000]).get("decidable", "").lower()
        if dec in ("能", "yes", "y", "true"):
            yes += 1
        elif dec in ("不能", "no", "n", "false"):
            no += 1
    if yes + no:
        log(f"D2 面板够用率：能决定 {yes} / 还得点开 {no}（{round(100 * yes / (yes + no))}%）")


# ---------------- export：打包去 PC ----------------

_EMBED = re.compile(r"!\[\[([^\]|#]+?\.(?:png|jpe?g|gif|webp))(?:\|[^\]]*)?\]\]", re.I)


def _sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def export(cfg: Config, to: Path | None = None) -> Path | None:
    """打包：20-资料/ 里的面板（存着/看片段/精读），加上你明确标了 to_pc: true 的笔记。

    只认显式标记，不按内容扫描——Mac vault 里有私人内容，宁可漏也不能带出去。
    同一篇改过（比如精读后补了笔记）会再导出一次。
    """
    state = State(cfg)
    sent: dict = state.d.setdefault("_exported", {})
    picks: list[tuple[Path, str, dict]] = []

    for p in _panels(cfg, (cfg.saved_dir,)):
        text = p.read_text(encoding="utf-8")
        fm = frontmatter(text)
        if _is_panel(fm):
            picks.append((p, text, fm))
    for p in cfg.vault.rglob("*.md"):
        if p.is_relative_to(cfg.saved_dir) or p.is_relative_to(cfg.inbox_dir):
            continue
        try:
            head = p.read_text(encoding="utf-8", errors="ignore")[:3000]
        except OSError:
            continue
        fm = frontmatter(head)
        if fm.get("to_pc", "").lower() in ("true", "yes", "是"):
            picks.append((p, p.read_text(encoding="utf-8"), fm))

    rel = lambda p: p.relative_to(cfg.vault).as_posix()  # noqa: E731
    todo = [(p, t, fm) for p, t, fm in picks if sent.get(rel(p)) != _sha(t)]
    if not todo:
        log("没有新的或改过的内容要导出")
        return None

    bundle = (to or cfg.export_dir) / f"bili-{time.strftime('%Y%m%d-%H%M')}"
    att = bundle / "_attachments"
    att.mkdir(parents=True, exist_ok=True)
    names: set[str] = set()
    copied: set[str] = set()  # 已进包的图片文件名；同名只放一份，免得 PC 上 ![[...]] 解析歧义
    for p, text, fm in todo:
        name = p.name if p.name not in names else f"{p.stem} {fm.get('bvid', '')}.md".strip()
        names.add(name)
        out = set_field(set_field(text, "origin", "mac-bili-triage"), "exported", time.strftime("%Y-%m-%d"))
        (bundle / name).write_text(out, encoding="utf-8")
        if _is_panel(fm):
            src = cfg.attach_dir(fm["bvid"])
            if src.is_dir():
                shutil.copytree(src, att / src.name, dirs_exist_ok=True)
                copied.update(f.name for f in src.iterdir() if f.is_file())
        for img in _EMBED.findall(text):
            base = Path(img).name
            if base in copied:
                continue
            hit = next(cfg.vault.rglob(base), None)
            if hit and hit.is_file():
                shutil.copy2(hit, att / hit.name)
                copied.add(base)
        sent[rel(p)] = _sha(text)
        log(f"导出 {rel(p)}")

    (bundle / "README.txt").write_text(
        "把整个文件夹拖进 PC vault 的 00 Inbox/From Mac/Bilibili/。\n"
        "图片在 _attachments/ 里；Obsidian 按文件名解析 ![[...]]，放在哪都能显示。\n",
        encoding="utf-8",
    )
    state.save()
    log(f"共 {len(todo)} 篇 → {bundle}")
    return bundle


# ---------------- gc ----------------

def gc(cfg: Config, days: int | None = None, orphans: bool = False) -> None:
    days = cfg.cache_days if days is None else days
    cutoff = time.time() - days * 86400
    if cfg.cache_dir.is_dir():
        for d in cfg.cache_dir.iterdir():
            if not d.is_dir():
                continue
            newest = max((f.stat().st_mtime for f in d.rglob("*") if f.is_file()), default=d.stat().st_mtime)
            if newest < cutoff:
                shutil.rmtree(d, ignore_errors=True)
                log(f"清理缓存 {d.name}")
    if orphans and cfg.attach_root.is_dir():
        # 全库扫描：还有任何笔记引用这个 bvid（面板本身，或别的笔记里嵌了单帧）就保留
        referenced: set[str] = set()
        for md in cfg.vault.rglob("*.md"):
            try:
                referenced.update(KEY_RE.findall(md.read_text(encoding="utf-8", errors="ignore")))
            except OSError:
                continue
        for d in cfg.attach_root.glob(f"{cfg.attach_prefix}*"):
            key = d.name[len(cfg.attach_prefix):]
            if d.is_dir() and key not in referenced:
                log(f"删除孤儿附件目录（全库已无笔记引用）：{d.name}")
                shutil.rmtree(d, ignore_errors=True)
