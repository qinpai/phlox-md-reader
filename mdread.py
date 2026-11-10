# -*- coding: utf-8 -*-
"""mdread —— 把一份 Markdown 变成"能读"的网页，并在本地/局域网实时预览。

  python mdread.py serve  <文件或目录> [--port 7788] [--lan on|off] [--no-open]
  python mdread.py build  <文件>       [--out x.html]
  python mdread.py export <文件>       [--out x.html]   # 单文件，图片/公式/样式全内嵌
  python mdread.py doctor
  python mdread.py demo

设计取舍（出现改动前先读这段）：
- 不做静态站生成：不做构建产物目录，页面在请求时现渲染（800ms 轮询 + no-store）。
- 版心只有一种（A 宽版心）：字号 clamp(17px,15px+0.22vw,20.5px)，每行 52–56 字。
- 按钮文字永不隐藏：窄屏靠换行、缩字号、横滑，不靠省略号或只留图标。
- 局域网默认关闭；工具栏上的"局域网"按钮 = 真正重绑 socket，而不是软性拒绝。
"""
import argparse
import base64
import hashlib
import io
import json
import os
import re
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
VENDOR = ROOT / "vendor"
WORKDIR = ".mdread"          # 每个被读目录下的缓存目录（化学图、导出物）

CJK = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"


# ---------------------------------------------------------------- 小工具
def log(*a):
    print(*a, flush=True)


def read_text(p):
    return Path(p).read_text(encoding="utf-8-sig")


def human_bytes(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return "%.1f %s" % (n, u) if u != "B" else "%d B" % n
        n /= 1024.0


def count_cjk_and_words(text):
    cjk = len(re.findall("[%s]" % CJK, text))
    words = len(re.findall(r"[A-Za-z][A-Za-z\-']*", text))
    return cjk, words


def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


# ---------------------------------------------------------------- 化学结构
_INDIGO = None


def indigo():
    """懒加载 Indigo；没装就返回 None，页面自动跳过化学块（不报错）。"""
    global _INDIGO
    if _INDIGO is not None:
        return _INDIGO or None
    try:
        if VENDOR.is_dir() and str(VENDOR) not in sys.path:
            sys.path.insert(0, str(VENDOR))
        from indigo import Indigo
        from indigo.renderer import IndigoRenderer
        ind = Indigo()
        _INDIGO = (ind, IndigoRenderer(ind))
    except Exception as e:
        log("[化学] 未启用（%s）" % e)
        _INDIGO = False
    return _INDIGO or None


def _trim_white(path, pad=8):
    from PIL import Image, ImageChops
    im = Image.open(path).convert("RGB")
    bg = Image.new("RGB", im.size, (255, 255, 255))
    box = ImageChops.difference(im, bg).getbbox()
    if box:
        im = im.crop((max(0, box[0] - pad), max(0, box[1] - pad),
                      min(im.width, box[2] + pad), min(im.height, box[3] + pad)))
    im.save(path)
    return im.size


def render_chem(code, kind, out_dir, embed=False):
    """返回 (src_or_datauri, caption_extra, w, h) 或 None。

    code 支持在首行写 `! 英文条件`（Indigo 内置字库没有中文，写中文会变方块）。
    """
    pair = indigo()
    if not pair:
        return None
    ind, renderer = pair
    cond, smi = "", []
    for line in code.strip().splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("!") or s.startswith("#!"):
            cond = s.lstrip("#!").strip()
        else:
            smi.append(s)
    smi = "".join(smi)
    if not smi:
        return None
    try:
        obj = ind.loadReaction(smi) if kind == "rxn" else ind.loadMolecule(smi)
    except Exception as e:
        return ("ERR", str(e).strip().splitlines()[0][:160], 0, 0)
    extra = ""
    if kind == "smiles":
        try:
            extra = "%s · MW %s" % (obj.grossFormula(), ("%.2f" % obj.molecularWeight()))
        except Exception:
            extra = ""
    ind.setOption("render-background-color", "255,255,255")
    ind.setOption("render-output-format", "png")
    ind.setOption("render-comment", cond)
    base_w = 460 if kind == "rxn" else 320
    base_h = 170 if kind == "rxn" else 240
    ind.setOption("render-image-size", "%d,%d" % (base_w * 2, base_h * 2))
    digest = hashlib.md5(("%s|%s|%s" % (kind, smi, cond)).encode()).hexdigest()[:12]
    png = out_dir / ("chem_%s.png" % digest)
    try:
        renderer.renderToFile(obj, str(png))
        w, h = _trim_white(str(png))
    except Exception as e:
        return ("ERR", str(e)[:160], 0, 0)
    if embed:
        b64 = base64.b64encode(png.read_bytes()).decode()
        return ("data:image/png;base64," + b64, extra, w, h)
    return (str(png), extra, w, h)


# ---------------------------------------------------------------- Markdown → HTML
CALLOUTS = {"NOTE": "说明", "TIP": "提示", "IMPORTANT": "要点",
            "WARNING": "注意", "CAUTION": "风险"}
BADGE_COLORS = {"目视比对": "b-visual", "机读": "b-read", "推断": "b-infer",
                "待完善": "b-todo", "实测": "b-test", "结论": "b-concl"}


def md_to_html(text, base_dir, img_prefix, embed=False, chem_dir=None, mode="serve"):
    import markdown
    text = text.replace("\r\n", "\n")
    figs = []          # 化学块占位 → html

    def chem_repl(m):
        kind, body = m.group(1), m.group(2)
        res = render_chem(body, kind, chem_dir, embed=embed)
        i = len(figs)
        if res is None:
            figs.append('<figure class="fx"><div class="chem-fail">化学渲染未启用（缺 Indigo）</div></figure>')
        elif res[0] == "ERR":
            figs.append('<figure class="fx"><div class="chem-fail">SMILES 解析失败：%s</div>'
                        '<pre class="chem-src">%s</pre></figure>' % (html_escape(res[1]), html_escape(body.strip())))
        else:
            src, extra, w, h = res
            if not src.startswith("data:"):
                pp = Path(src)
                if mode == "build":
                    try:
                        src = os.path.relpath(pp, base_dir).replace("\\", "/")
                    except Exception:
                        src = pp.as_posix()
                else:
                    src = img_prefix + "/files/" + pp.as_posix()
            cap = "反应式" if kind == "rxn" else "结构式"
            sub = ("　" + extra) if extra else ""
            figs.append('<figure class="fx chem"><img src="%s" alt="%s" width="%d" height="%d" data-raw="1">'
                        '<figcaption><span class="fignum">__FIGN__</span>%s%s</figcaption></figure>'
                        % (src, cap, w, h, cap, sub))
        return "\n\n@@CHEM%d@@\n\n" % i

    text = re.sub(r"(?ms)^```(smiles|rxn)[ \t]*\n(.*?)^```[ \t]*$", chem_repl, text)

    md = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists", "attr_list"])
    html = md.convert(text)

    for i, f in enumerate(figs):
        html = html.replace("<p>@@CHEM%d@@</p>" % i, f).replace("@@CHEM%d@@" % i, f)

    # 图片 → figure（自动编号 + 题注 + 长图滚动框）
    def _px_size(s, base_dir, img_tag):
        """优先用 img 上的 width/height，其次读本地文件头；远程/data URI 返回 None。"""
        mw = re.search(r'width="(\d+)"', img_tag)
        mh = re.search(r'height="(\d+)"', img_tag)
        if mw and mh:
            return int(mw.group(1)), int(mh.group(1))
        if s.startswith(("data:", "http://", "https://", "//")):
            return None
        p = Path(s)
        if not p.is_absolute():
            p = (base_dir / s) if base_dir else p
        try:
            from PIL import Image
            with Image.open(p) as im:
                return im.size
        except Exception:
            return None

    def decorate(m):
        """把一个 <img> 变成 <figure>：自动编号、题注、长图滚动框。只跑一次，避免嵌套。"""
        img = m.group(0)
        if "data-raw" in img:      # 化学图已经自带 figure/题注，不要再包一层
            return img
        src = re.search(r'src="([^"]+)"', img)
        alt = re.search(r'alt="([^"]*)"', img)
        cap = (alt.group(1) if alt and alt.group(1) else "").strip()
        size = _px_size(src.group(1), base_dir, img) if src else None
        if src:
            s = src.group(1)
            if not s.startswith(("data:", "http://", "https://", "//")):
                new_src = _img_src(s, base_dir, img_prefix, embed, mode=mode)
            else:
                new_src = s
            img = img.replace(s, new_src)
        if "loading=" not in img:
            img = img[:-1] + ' loading="lazy" decoding="async">'
        tall = bool(size and size[1] > 2 * size[0])
        body = '<div class="vscroll">%s</div>' % img if tall else img
        hint = ('<span class="vhint">原图 %d×%d，框内滚动看全图</span>' % size) if tall else ""
        if not cap and not hint:
            return '<figure class="fx">%s</figure>' % body
        return ('<figure class="fx">%s<figcaption><span class="fignum">__FIGN__</span>%s%s</figcaption></figure>'
                % (body, html_escape(cap), hint))

    html = re.sub(r"<img[^>]*>", decorate, html)
    html = re.sub(r"<p>\s*</p>", "", html)
    html = re.sub(r'<p>\s*(<figure class="fx"[^>]*>.*?</figure>)\s*</p>', r"\1", html, flags=re.S)

    # 表格外壳（窄屏横滑）
    html = re.sub(r"<table>", '<div class="tbox"><table>', html)
    html = re.sub(r"</table>", "</table></div>", html)

    # 提示框 [!NOTE]
    # 提示框 [!NOTE]：连续几个 > 引用会被 markdown 合并成一个 <blockquote>，必须先拆
    def one_callout(kind, body):
        kind = kind.upper()
        label = CALLOUTS.get(kind, kind.title())
        body = re.sub(r"</p>\s*$", "", body.strip())
        return ('<div class="callout c-%s"><div class="callout-t">%s</div>'
                '<div class="callout-b">%s</div></div>' % (kind.lower(), label, body))

    def bq_repl(m):
        parts = re.split(r"(?=<p>\[!\w+\])", m.group(1))
        out = []
        for part in parts:
            mm = re.match(r"<p>\[!(\w+)\]\s*(.*)", part, re.S)
            if mm:
                out.append(one_callout(mm.group(1), mm.group(2)))
            elif part.strip():
                out.append("<blockquote>%s</blockquote>" % part)
        return "\n".join(out)

    html = re.sub(r"<blockquote>(.*?)</blockquote>", bq_repl, html, flags=re.S)

    # 徽章【…】
    def badge(m):
        t = m.group(1)
        return '<span class="badge %s">%s</span>' % (BADGE_COLORS.get(t, "b-plain"), t)

    html = re.sub(r"【([^】]{1,8})】", badge, html)

    # ==高亮==
    html = re.sub(r"==([^=<>]{1,200})==", r"<mark>\1</mark>", html)

    # 标题加 id + 抽出编号 + 建目录
    toc = []
    counter = [0]

    def head_repl(m):
        lvl, attrs, inner = int(m.group(1)), m.group(2) or "", m.group(3)
        counter[0] += 1
        sid = "s%d" % counter[0]
        plain = re.sub(r"<[^>]+>", "", inner).strip()
        num = ""
        mm = re.match(r"^(\d+(?:\.\d+)*)[\s、.]+(.*)$", plain)
        if mm and lvl >= 2:
            num, rest = mm.group(1), mm.group(2)
            inner = re.sub(r"^\s*%s[\s、.]+" % re.escape(num), "", inner, count=1)
            plain = rest
        if num:
            inner = '<span class="secnum">%s</span>%s' % (num, inner)
        attrs = re.sub(r'\sid="[^"]*"', "", attrs)
        if lvl in (2, 3):
            toc.append((lvl, sid, plain))
        return '<h%d id="%s"%s>%s</h%d>' % (lvl, sid, attrs, inner, lvl)

    html = re.sub(r"<h([1-6])((?:\s[^>]*)?)>(.*?)</h\1>", head_repl, html, flags=re.S)

    # 图号顺序填充
    n = [0]

    def fig_num(m):
        n[0] += 1
        return str(n[0])

    html = re.sub(r"__FIGN__", fig_num, html)
    return html, toc


def html_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _img_src(src, base_dir, img_prefix, embed, mode="serve"):
    raw = urllib.parse.unquote(src)
    p = Path(raw)
    if not p.is_absolute():
        p = (base_dir / raw)
    try:
        p = p.resolve()
    except Exception:
        pass
    if not p.exists():
        return src
    if embed:
        ext = p.suffix.lower().lstrip(".")
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif",
                "svg": "svg+xml", "webp": "webp"}.get(ext, "png")
        return "data:image/%s;base64,%s" % (mime, base64.b64encode(p.read_bytes()).decode())
    if mode == "build":                     # build 模式：html 与 md 同目录，相对路径，file:// 也能开
        try:
            return os.path.relpath(p, base_dir).replace("\\", "/")
        except Exception:
            return src
    return img_prefix + "/files/" + str(p).replace("\\", "/")


# ---------------------------------------------------------------- 页面模板
CSS = r"""
*,*::before,*::after{box-sizing:border-box}
:root{
  --barh:52px; --side:250px; --fsx:1; --pad:26px;
  --measure:min(54em, 92vw);
  --bg:#f6f4ef; --bg2:#fdfcf9; --ink:#1e232a; --ink2:#5d6672; --line:#e1dcd2;
  --mark:#fbeaa8; --code:#f1eee7; --shadow:0 1px 2px rgba(24,20,12,.06);
}
/* 四套配色。--accent 管边线/编号/色块，--accent2 管链接与强调文字 */
body{--accent:#1f4e79; --accent2:#173c5d}
body[data-accent="navy"]{--accent:#1f4e79; --accent2:#173c5d}
body[data-accent="teal"]{--accent:#0f6157; --accent2:#0b4a42}
body[data-accent="amber"]{--accent:#9a5b12; --accent2:#78450a}
body[data-accent="red"]{--accent:#8e2f2c; --accent2:#6d211f}
body[data-theme="dark"]{
  --bg:#151a1f; --bg2:#1b2128; --ink:#dde3ea; --ink2:#96a2b0; --line:#2b333d;
  --mark:#463d12; --code:#222932; --shadow:0 1px 2px rgba(0,0,0,.45);
  --accent:#7fb2e5; --accent2:#a8cdf0;
}
body[data-theme="dark"][data-accent="teal"]{--accent:#54c3b3; --accent2:#7fdccd}
body[data-theme="dark"][data-accent="amber"]{--accent:#e0a45c; --accent2:#f0bf85}
body[data-theme="dark"][data-accent="red"]{--accent:#e2897f; --accent2:#f0a89f}
html{font-size:clamp(17px, 15px + 0.22vw, 20.5px); -webkit-text-size-adjust:100%}
body{margin:0; background:var(--bg); color:var(--ink);
  font-family:"Noto Serif SC","Source Han Serif SC","Songti SC",Georgia,"Times New Roman",serif;
  font-size:calc(1rem * var(--fsx)); line-height:1.78;
  padding-top:var(--barh); word-break:break-word}
body[data-font="sans"]{font-family:"Noto Sans SC","Microsoft YaHei",system-ui,-apple-system,sans-serif}
::selection{background:var(--mark); color:var(--ink)}

/* ---------- 顶栏 ---------- */
#bar{position:fixed; inset:0 0 auto 0; height:var(--barh); z-index:80;
  display:flex; align-items:center; gap:8px; padding:0 12px;
  background:color-mix(in srgb, var(--bg2) 88%, transparent);
  backdrop-filter:saturate(1.2) blur(8px); border-bottom:1px solid var(--line)}
#bar .sp{flex:1 1 auto}
.btn{appearance:none; border:1px solid var(--line); background:var(--bg2); color:var(--ink);
  font:inherit; font-size:13px; line-height:1.2; padding:7px 11px; border-radius:9px; cursor:pointer;
  transition:transform .11s ease, background .11s ease, border-color .11s ease, box-shadow .11s;
  white-space:nowrap; display:inline-flex; align-items:center; gap:5px;
  flex:0 0 auto; overflow:visible}          /* 控制按钮永不压缩：宁可让文档条横滚 */
/* 文档切换：左上角文件夹按钮 + 向下展开列表（不再是横向长条） */
#docwrap{position:relative; flex:0 1 auto; min-width:0}
#docbtn{max-width:min(46vw,440px)}
#docbtn .dcur{overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:34vw}
#doclist{position:absolute; left:0; top:calc(100% + 10px); z-index:92;
  width:min(520px, 88vw); max-height:min(70vh, 540px); overflow-y:auto; overscroll-behavior:contain;
  background:var(--bg2); border:1px solid var(--line); border-radius:11px;
  box-shadow:0 14px 38px rgba(20,16,8,.18); padding:6px}
#doclist[hidden]{display:none}
#doclist .ditem{display:flex; align-items:baseline; gap:9px; padding:8px 10px; border-radius:8px;
  color:var(--ink); text-decoration:none; font-size:13px; line-height:1.35}
#doclist .ditem:hover{background:color-mix(in srgb, var(--accent) 9%, transparent); color:var(--accent2)}
#doclist .ditem.cur{background:color-mix(in srgb, var(--accent) 15%, transparent);
  color:var(--accent2); font-weight:600}
#doclist .dnum{flex:0 0 auto; min-width:1.6em; font-family:"Cascadia Mono",Consolas,monospace;
  font-size:11.5px; color:var(--ink2); opacity:.85}
#doclist .dname{flex:1 1 auto; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap}
#docbtn .dcaret{margin-left:3px; opacity:.55; font-size:11px}
#docbtn[aria-expanded="true"]{border-color:var(--accent); color:var(--accent2);
  background:color-mix(in srgb, var(--accent) 10%, var(--bg2))}
#doclist .dempty{padding:10px; color:var(--ink2); font-size:12.5px}
.btn:hover{border-color:var(--accent); color:var(--accent2); box-shadow:var(--shadow)}
.btn:active{transform:translateY(1px) scale(.96); background:color-mix(in srgb, var(--accent) 12%, var(--bg2))}
.btn:focus-visible{outline:2px solid var(--accent); outline-offset:2px}
.btn.on{border-color:var(--accent); color:var(--accent2);
  background:color-mix(in srgb, var(--accent) 14%, var(--bg2))}
#menu{display:none}

/* ---------- 目录 ---------- */
#toc{position:fixed; top:var(--barh); left:0; bottom:0; width:var(--side); z-index:60;
  background:var(--bg2); border-right:1px solid var(--line); overflow-y:auto;
  padding:20px 12px 40px 18px; font-family:"Noto Sans SC","Microsoft YaHei",system-ui,sans-serif}
#toc .toc-t{font-size:11.5px; letter-spacing:.32em; color:var(--ink2); margin:0 0 12px 8px}
#toc a{display:block; color:var(--ink2); text-decoration:none; font-size:13px; line-height:1.45;
  padding:5px 8px; border-left:2px solid transparent; border-radius:0 6px 6px 0; margin-bottom:1px}
#toc a.lv3{padding-left:22px; font-size:12.5px; opacity:.86}
#toc a:hover{color:var(--accent2); background:color-mix(in srgb, var(--accent) 8%, transparent)}
#toc a.on{color:var(--accent2); border-left-color:var(--accent); font-weight:600;
  background:color-mix(in srgb, var(--accent) 10%, transparent)}
#toc .dots{display:inline-block; min-width:14px; color:var(--ink2); opacity:.55}

/* ---------- 正文 ---------- */
#main{margin-left:var(--side); padding:34px var(--pad) 160px}
#doc{max-width:var(--measure); margin:0 auto}
.doc-meta{max-width:var(--measure); margin:0 auto 18px; font-size:12.5px; color:var(--ink2);
  display:flex; flex-wrap:wrap; gap:6px 14px; font-family:"Noto Sans SC",system-ui,sans-serif}
.doc-meta code{background:var(--code); padding:1px 6px; border-radius:5px}
#doc h1{font-size:1.72em; line-height:1.32; margin:.2em 0 .9em; letter-spacing:.01em}
#doc h2{font-size:1.3em; line-height:1.4; margin:2.1em 0 .7em; padding-bottom:.3em;
  border-bottom:1px solid var(--line)}
#doc h3{font-size:1.1em; margin:1.7em 0 .55em}
#doc h4{font-size:1em; margin:1.4em 0 .5em}
#doc .secnum{display:inline-block; color:var(--accent); font-variant-numeric:tabular-nums;
  margin-right:.5em; font-weight:600; font-family:"Noto Sans SC",system-ui,sans-serif; font-size:.92em}
#doc p{margin:.78em 0}
#doc ul,#doc ol{margin:.7em 0; padding-left:1.5em}
#doc li{margin:.3em 0}
#doc li>p{margin:.2em 0}
#doc a{color:var(--accent2); text-decoration:none; border-bottom:1px solid color-mix(in srgb,var(--accent) 40%, transparent)}
#doc a:hover{background:color-mix(in srgb, var(--accent) 10%, transparent)}
#doc strong{color:color-mix(in srgb, var(--ink) 88%, #000)}
#doc mark{background:var(--mark); color:inherit; padding:0 .18em; border-radius:3px}
#doc code{font-family:"Cascadia Mono",Consolas,"Courier New",monospace; font-size:.86em;
  background:var(--code); padding:.12em .38em; border-radius:5px; border:1px solid var(--line)}
#doc pre{background:var(--code); border:1px solid var(--line); border-radius:10px; padding:14px 16px;
  overflow-x:auto; font-size:.84em; line-height:1.6}
#doc pre code{background:none; border:none; padding:0; font-size:1em}
#doc hr{border:none; border-top:1px solid var(--line); margin:2.2em 0}
#doc blockquote{margin:1em 0; padding:.55em 1em; border-left:3px solid var(--line);
  color:var(--ink2); background:color-mix(in srgb, var(--bg2) 60%, transparent)}
#doc blockquote p{margin:.25em 0}
#doc table{border-collapse:collapse; width:100%; font-size:.86em; line-height:1.55;
  font-family:"Noto Sans SC",system-ui,sans-serif}
#doc th,#doc td{border:1px solid var(--line); padding:7px 10px; text-align:left; vertical-align:top}
#doc thead th{background:color-mix(in srgb, var(--accent) 8%, var(--bg2)); font-weight:600}
#doc tbody tr:nth-child(even){background:color-mix(in srgb, var(--bg) 55%, transparent)}
#doc .tbox{overflow-x:auto; margin:1.1em 0; border-radius:10px; box-shadow:var(--shadow)}
#doc .tbox table{margin:0; min-width:0}
#doc img{max-width:100%; height:auto}
#doc figure{margin:1.4em 0; text-align:center}
#doc figcaption{margin-top:.6em; font-size:12.5px; color:var(--ink2); line-height:1.55;
  font-family:"Noto Sans SC",system-ui,sans-serif; text-align:center}
#doc .fignum{font-weight:700; color:var(--accent2); margin-right:.5em; letter-spacing:.02em}
#doc .fx img{border-radius:8px; border:1px solid var(--line); background:#fff; padding:4px}
#doc .fx.chem img{max-width:min(100%, 560px)}
#doc .chem-fail{border:1px dashed #d9534f; color:#c0392b; padding:14px; border-radius:8px; font-size:13px}
#doc .chem-src{font-size:11.5px; margin-top:8px; text-align:left}
/* 长图（高 > 2×宽）放进限高滚动框，别让一页被一张图撑到没法读 */
#doc .vscroll{max-height:76vh; overflow-y:auto; overscroll-behavior:contain;
  border:1px solid var(--line); border-radius:10px; padding:6px; background:#fff}
#doc .vscroll img{border:0; border-radius:6px}
#doc .vscroll::-webkit-scrollbar{width:10px}
#doc .vscroll::-webkit-scrollbar-thumb{background:var(--line); border-radius:6px}
#doc .vhint{display:inline-block; margin-left:.55em; padding:1px 6px; border-radius:5px;
  background:var(--chip); color:var(--ink2); font-size:11px; letter-spacing:.01em; white-space:nowrap}

/* 徽章 */
.badge{display:inline-block; font-size:.74em; line-height:1.5; padding:.1em .5em; border-radius:999px;
  border:1px solid; margin:0 .12em; vertical-align:.08em; white-space:nowrap;
  font-family:"Noto Sans SC",system-ui,sans-serif; font-weight:600}
.b-visual{color:#8a4b00; border-color:#e8b34b; background:#fdf2d8}
.b-read{color:#12467f; border-color:#8fb6e0; background:#e9f2fc}
.b-infer{color:#5a2f8a; border-color:#b79ae0; background:#f3ebfd}
.b-todo{color:#8a1c1c; border-color:#e59a9a; background:#fdecec}
.b-test{color:#0b6b5e; border-color:#7cc8bd; background:#e6f6f3}
.b-concl{color:#1f3d2e; border-color:#8fc3a5; background:#eaf6ee}
.b-plain{color:var(--ink2); border-color:var(--line); background:var(--code)}

/* 提示框 */
.callout{margin:1.1em 0; border-radius:10px; border:1px solid var(--line); overflow:hidden;
  background:var(--bg2); box-shadow:var(--shadow)}
.callout-t{font-size:12px; font-weight:700; letter-spacing:.06em; padding:7px 14px;
  font-family:"Noto Sans SC",system-ui,sans-serif; border-bottom:1px solid var(--line)}
.callout-b{padding:2px 14px 6px; font-size:.94em}
.c-note .callout-t{background:#eaf2fb; color:#1d4b7c}
.c-tip .callout-t{background:#e8f6ee; color:#1c6042}
.c-important .callout-t{background:#f0ecfb; color:#4b2d86}
.c-warning .callout-t{background:#fdf3e3; color:#85520a}
.c-caution .callout-t{background:#fdecea; color:#8d2620}

.foot{max-width:var(--measure); margin:56px auto 0; padding-top:14px; border-top:1px solid var(--line);
  font-size:12px; color:var(--ink2); display:flex; flex-wrap:wrap; gap:8px 16px;
  font-family:"Noto Sans SC",system-ui,sans-serif}
#scrim{display:none; position:fixed; inset:0; background:rgba(10,14,20,.42); z-index:70}
body.nav-open #scrim{display:block}
/* 局域网提示条：常驻、可选中、可复制（不用 toast，一闪就没） */
#lanbar{position:fixed; left:0; right:0; top:var(--barh); z-index:78; display:flex; align-items:center;
  flex-wrap:wrap; gap:10px; padding:10px 16px; font-size:13px; line-height:1.5;
  background:color-mix(in srgb, var(--accent) 15%, var(--bg2));
  border-bottom:1px solid var(--line); box-shadow:var(--shadow);
  font-family:"Noto Sans SC","Microsoft YaHei",system-ui,sans-serif;
  user-select:text; -webkit-user-select:text}
#lanbar[hidden]{display:none}
#lanbar .lb-t{font-weight:700; color:var(--accent2)}
#lanbar .lb-u{font-family:"Cascadia Mono",Consolas,monospace; font-size:13px; background:var(--bg2);
  border:1px solid var(--line); border-radius:7px; padding:3px 9px; user-select:all;
  -webkit-user-select:all; word-break:break-all}
#lanbar .sp{flex:1 1 auto}
#lanbar .btn{font-size:12px; padding:5px 10px}
body.has-lanbar #main{padding-top:calc(var(--barh) + var(--lanh, 50px) + 16px)}
#toast{position:fixed; left:50%; bottom:26px; transform:translate(-50%,16px); z-index:90;
  background:var(--ink); color:var(--bg2); font-size:13px; padding:9px 16px; border-radius:999px;
  opacity:0; pointer-events:none; transition:opacity .18s, transform .18s;
  font-family:"Noto Sans SC",system-ui,sans-serif}
#toast.show{opacity:1; transform:translate(-50%,0)}
/* 手机上不要任何浮层提示：toast 直接不显示（桌面端仍然给点击反馈） */
@media (max-width:1080px){ #toast{display:none !important} }

/* ---------- 窄屏 ---------- */
@media (max-width:1080px){
  :root{--barh:104px; --pad:18px}
  #bar{flex-wrap:wrap; align-content:center; gap:6px 8px; padding:8px 10px}
  #bar .sp{display:none}
  #docwrap{order:3; flex:1 1 100%; width:100%}
  #docbtn{max-width:none; width:100%; justify-content:space-between}
  #docbtn .dcur{max-width:calc(100vw - 150px)}
  #doclist{width:min(92vw, 520px)}
  #menu{display:inline-flex}
  #lan{display:none}          /* 局域网开关只在电脑上出现：手机上点它会把手机自己踢下线 */
  #lanbar{display:none !important}
  body.has-lanbar #main{padding-top:20px}
  .btn{font-size:12px; padding:8px 9px}
  #toc{transform:translateX(-102%); transition:transform .22s ease; z-index:75;
    box-shadow:8px 0 24px rgba(10,14,20,.14)}
  body.nav-open #toc{transform:none}
  #main{margin-left:0; padding-top:20px}
  #doc{max-width:min(100%, 46em)}
  #doc .tbox table{min-width:560px}
  h1,h2,h3,figure,[id]{scroll-margin-top:calc(var(--barh) + 10px)}
}
@media (max-width:430px){
  :root{--pad:14px}
  .btn{font-size:11.5px; padding:8px 6px}
  #doclist .ditem{font-size:12px; padding:8px 8px}
}
@media (orientation:landscape) and (max-height:560px){
  :root{--barh:52px; --pad:16px}
  #bar{flex-wrap:nowrap; padding:0 8px; gap:6px}
  #bar .sp{display:block}
  #docwrap{order:0; flex:1 1 auto; width:auto; min-width:0}
  #docbtn{width:auto; justify-content:flex-start}
  #menu{display:inline-flex}
  .btn{font-size:11.5px; padding:5px 8px}
  #toc{transform:none}
  #main{margin-left:0}
}
@media print{
  :root{--barh:0px}
  #bar,#toc,#scrim,#toast,.doc-meta .no-print{display:none!important}
  body{background:#fff; color:#000; padding:0; font-size:10.5pt; line-height:1.6}
  #main{margin:0; padding:0; max-width:none}
  #doc{max-width:none}
  #doc a{color:#000; border:none; text-decoration:underline}
  #doc pre,#doc figure,#doc table,.callout{break-inside:avoid; page-break-inside:avoid}
  #doc h1,#doc h2,#doc h3{break-after:avoid; page-break-after:avoid}
  .foot{display:none}
  @page{size:A4; margin:16mm 14mm}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important; animation:none!important}}
"""

JS = r"""
(function(){
  var body=document.body, D=document.documentElement;
  var LS={theme:'mdread.theme', fs:'mdread.fs', nav:null};
  function $(s,r){return (r||document).querySelector(s)}
  function $$(s,r){return Array.prototype.slice.call((r||document).querySelectorAll(s))}
  function toast(t){var el=$('#toast'); el.textContent=t; el.classList.add('show');
    clearTimeout(el._t); el._t=setTimeout(function(){el.classList.remove('show')},1400)}
  function barH(){return parseFloat(getComputedStyle(D).getPropertyValue('--barh'))||52}

  /* 主题 */
  try{var th=localStorage.getItem(LS.theme); if(th) body.dataset.theme=th;}catch(e){}
  function themeLabel(){var b=$('#theme');
    b.textContent = body.dataset.theme==='dark' ? '☀ 浅色' : '◐ 深色';
    b.classList.toggle('on', body.dataset.theme==='dark')}
  themeLabel();
  $('#theme') && $('#theme').addEventListener('click',function(){
    body.dataset.theme = body.dataset.theme==='dark' ? 'light' : 'dark';
    try{localStorage.setItem(LS.theme, body.dataset.theme)}catch(e){}
    themeLabel(); toast(body.dataset.theme==='dark'?'深色':'浅色');
  });

  /* 配色（四个色相，深浅各自一对变量） */
  var ACCENTS=[['navy','深蓝'],['teal','青绿'],['amber','橙'],['red','红']];
  try{var ac=localStorage.getItem('mdread.accent'); if(ac) body.dataset.accent=ac;}catch(e){}
  if(!body.dataset.accent) body.dataset.accent='navy';
  function accLabel(){var b=$('#accent'), n=body.dataset.accent;
    for(var i=0;i<ACCENTS.length;i++) if(ACCENTS[i][0]===n){b.textContent='🎨 '+ACCENTS[i][1]; break}}
  accLabel();
  $('#accent') && $('#accent').addEventListener('click',function(){
    var n=body.dataset.accent, idx=0;
    ACCENTS.forEach(function(a,i){ if(a[0]===n) idx=i; });
    var nx=ACCENTS[(idx+1)%ACCENTS.length];
    body.dataset.accent=nx[0];
    try{localStorage.setItem('mdread.accent', nx[0])}catch(e){}
    accLabel(); toast('配色：'+nx[1]);
  });

  /* 字号 */
  var fsx=1; try{fsx=parseFloat(localStorage.getItem(LS.fs))||1}catch(e){}
  function applyFs(){D.style.setProperty('--fsx', fsx.toFixed(2));
    try{localStorage.setItem(LS.fs, fsx)}catch(e){} measure()}
  function step(d){fsx=Math.min(1.34, Math.max(.84, fsx+d)); applyFs();}
  $('#fsup') && $('#fsup').addEventListener('click',function(){step(.07)});
  $('#fsdown') && $('#fsdown').addEventListener('click',function(){step(-.07)});
  applyFs();

  /* 每行字数读数 */
  function measure(){
    var out=$('#readout'); if(!out) return;
    var p=$('#doc > p')||$('#doc p')||$('#doc');
    var w=p.getBoundingClientRect().width;
    var sp=document.createElement('span'); sp.textContent='测';
    sp.style.cssText='position:absolute;visibility:hidden;white-space:nowrap;font:inherit';
    p.appendChild(sp); var cw=sp.getBoundingClientRect().width; p.removeChild(sp);
    var fs=parseFloat(getComputedStyle(p).fontSize);
    out.textContent='每行约 '+Math.round(w/Math.max(cw,1))+' 字 · '+Math.round(w)+'px · '+fs.toFixed(1)+'px';
  }
  measure(); window.addEventListener('resize',measure);

  /* 目录抽屉 */
  function navOpen(on){body.classList.toggle('nav-open', on);}
  $('#menu') && $('#menu').addEventListener('click',function(){navOpen(!body.classList.contains('nav-open'))});
  $('#scrim') && $('#scrim').addEventListener('click',function(){navOpen(false)});
  $$('#toc a').forEach(function(a){a.addEventListener('click',function(){navOpen(false)})});
  document.addEventListener('keydown',function(e){
    if(e.key==='Escape') navOpen(false);
    if(e.key==='d'||e.key==='D'){ if(!e.metaKey&&!e.ctrlKey) $('#theme').click(); }
  });
  window.addEventListener('resize',function(){ if(window.innerWidth>1080) navOpen(false); });

  /* 目录高亮 */
  var links=$$('#toc a'), ticks=[];
  function retick(){ ticks=links.map(function(a){var t=document.getElementById(a.dataset.id);
      return t?t.getBoundingClientRect().top+window.scrollY:1e9}) }
  retick();
  function spy(){
    var y=window.scrollY+barH()+70, idx=-1;
    for(var i=0;i<ticks.length;i++){ if(ticks[i]<=y) idx=i; }
    links.forEach(function(a,i){a.classList.toggle('on', i===idx)});
  }
  window.addEventListener('scroll',spy,{passive:true});
  window.addEventListener('resize',function(){retick();spy()}); spy();

  /* 打印 */
  $('#print') && $('#print').addEventListener('click',function(){window.print()});

  /* 局域网开关 + 常驻提示条 */
  var lan=$('#lan'), lanbar=$('#lanbar'), lanurl=$('#lanurl');
  function showLan(urls){
    if(!urls || !urls.length) return;
    lanurl.textContent = urls[0];
    lanbar.hidden=false; body.classList.add('has-lanbar');
    D.style.setProperty('--lanh', Math.round(lanbar.offsetHeight)+'px');
  }
  function hideLan(){ lanbar.hidden=true; body.classList.remove('has-lanbar');
    D.style.setProperty('--lanh','0px'); }
  window.addEventListener('resize',function(){
    if(!lanbar.hidden) D.style.setProperty('--lanh', Math.round(lanbar.offsetHeight)+'px');
  });
  function setLanBtn(on){
    if(!lan) return;
    lan.dataset.on = on?'1':'0';
    lan.textContent = on ? '🌐 局域网 开' : '🌐 局域网 关';
    lan.classList.toggle('on', !!on);
  }
  $('#lanclose') && $('#lanclose').addEventListener('click',function(){
    hideLan(); try{sessionStorage.setItem('mdread.lanbar','off')}catch(e){}
  });
  $('#lancopy') && $('#lancopy').addEventListener('click',function(){
    var t=lanurl.textContent, btn=this;
    function done(ok){ btn.textContent = ok?'已复制 ✓':'请手动选中复制'; 
      setTimeout(function(){btn.textContent='复制地址'},1500); }
    if(navigator.clipboard && navigator.clipboard.writeText){
      navigator.clipboard.writeText(t).then(function(){done(true)},function(){done(false)});
    }else{
      try{ var ta=document.createElement('textarea'); ta.value=t; document.body.appendChild(ta);
        ta.select(); var ok=document.execCommand('copy'); document.body.removeChild(ta); done(ok);
      }catch(e){ done(false); }
    }
  });
  if(lan){
    fetch('/api/lan',{cache:'no-store'}).then(function(r){return r.json()}).then(function(j){
      setLanBtn(!!j.lan);
      var dismissed=false; try{dismissed=sessionStorage.getItem('mdread.lanbar')==='off'}catch(e){}
      if(j.lan && !dismissed) showLan(j.urls);
    }).catch(function(){});
    lan.addEventListener('click',function(){
      var want = lan.dataset.on!=='1';
      lan.disabled=true;
      fetch('/api/lan',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({on:want})})
      .then(function(r){return r.json()})
      .then(function(j){
        setLanBtn(!!j.lan);
        if(j.lan){ try{sessionStorage.removeItem('mdread.lanbar')}catch(e){} showLan(j.urls); }
        else { hideLan(); }
        toast(j.lan ? '局域网已开，地址见顶部提示条' : '局域网已关，仅本机可看');
        lan.disabled=false;
      }).catch(function(){ lan.disabled=false; toast('切换失败'); });
    });
  }

  /* 保存即刷新（保留滚动位置） */
  var docKey = body.dataset.doc || '', ver = body.dataset.ver || '';
  function scrollKey(){ return 'mdread.scroll:' + (document.body.dataset.doc||''); }
  function saveScroll(){ try{sessionStorage.setItem(scrollKey(), String(window.scrollY))}catch(e){} }
  function poll(){
    fetch('/api/watch?doc='+encodeURIComponent(docKey)+'&v='+encodeURIComponent(ver),
          {cache:'no-store'})
      .then(function(r){return r.json()})
      .then(function(j){
        if(j.v!==ver){ saveScroll(); location.reload(); return; }
        if(j.docs && j.docs.length!==$$('#doclist .ditem').length){
          location.reload();
        }
      }).catch(function(){});
  }
  setInterval(poll, 800);
  window.addEventListener('beforeunload', saveScroll);
  function restore(){
    try{ var y=parseInt(sessionStorage.getItem('mdread.scroll:' + (document.body.dataset.doc||''))||'0',10);
      if(y>0){ window.scrollTo(0,y);
        setTimeout(function(){window.scrollTo(0,y)},60);
        setTimeout(function(){window.scrollTo(0,y)},260); }
    }catch(e){}
  }
  restore();

  /* 文档切换：左上角文件夹 → 向下展开列表（记住滚动位置，不再弹回最左/最上） */
  var docbtn=$('#docbtn'), doclist=$('#doclist');
  if(docbtn && doclist){
    var docStash=function(){
      try{ sessionStorage.setItem('mdread.doclist', String(doclist.scrollTop)) }catch(e){}
    };
    var docOpen=function(open){
      if(!open){ docStash(); }               /* 必须先存：hidden 会把 scrollTop 归零 */
      doclist.hidden=!open;
      docbtn.setAttribute('aria-expanded', open?'true':'false');
      if(!open){ return; }
      var st=0; try{ st=parseInt(sessionStorage.getItem('mdread.doclist')||'0',10) }catch(e){}
      if(st>0){ doclist.scrollTop=st; }
      else {
        var cur=doclist.querySelector('.ditem.cur');
        if(cur){ doclist.scrollTop=Math.max(0, cur.offsetTop - doclist.clientHeight/2 + cur.offsetHeight/2); }
      }
    };
    docbtn.addEventListener('click', function(ev){ ev.stopPropagation(); docOpen(doclist.hidden); });
    doclist.addEventListener('scroll', docStash, {passive:true});
    document.addEventListener('click', function(ev){
      if(doclist.hidden) return;
      if(doclist.contains(ev.target) || ev.target===docbtn || docbtn.contains(ev.target)) return;
      docOpen(false);
    });
    document.addEventListener('keydown', function(ev){ if(ev.key==='Escape' && !doclist.hidden) docOpen(false); });
  }

  /* 目录点击平滑 */
  $$('#toc a').forEach(function(a){
    a.addEventListener('click',function(ev){
      var t=document.getElementById(a.dataset.id); if(!t) return;
      ev.preventDefault();
      var y=t.getBoundingClientRect().top+window.scrollY-barH()-14;
      window.scrollTo({top:y, behavior:'smooth'});
      history.replaceState(null,'','#'+a.dataset.id);
    });
  });
})();
"""

PAGE = """<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>__TITLE__</title>
__KATEX_CSS__
<style>__CSS__</style>
</head>
<body data-theme="light" data-accent="navy" data-doc="__DOCKEY__" data-ver="__VER__">
<div id="bar">
  <button id="menu" class="btn" type="button" title="目录">☰ 目录</button>
  <div id="docwrap">
    <button id="docbtn" class="btn" type="button" aria-expanded="false" aria-controls="doclist"
            title="切换文档：__DOCCOUNT__ 份"><span aria-hidden="true">📁</span><span class="dcur">__DOCCUR__</span><span aria-hidden="true" class="dcaret">▾</span></button>
    <div id="doclist" hidden>__DOCS__</div>
  </div>
  <div class="sp"></div>
  <button class="btn" id="fsdown" type="button" title="字号减小">A−</button>
  <button class="btn" id="fsup" type="button" title="字号增大">A+</button>
  <button class="btn" id="accent" type="button" title="切换配色">🎨 深蓝</button>
  <button class="btn" id="theme" type="button">◐ 深色</button>
  __LANBTN__
  <button class="btn" id="print" type="button" title="打印或另存 PDF">⎙ 打印</button>
</div>
<aside id="toc"><div class="toc-t">目 次</div><nav>__TOC__</nav></aside>
<div id="scrim"></div>
<div id="lanbar" hidden>
  <span class="lb-t">📶 局域网已开</span>
  <span>手机连同一个 Wi-Fi，打开：</span>
  <span class="lb-u" id="lanurl">http://</span>
  <span class="sp"></span>
  <button class="btn" id="lancopy" type="button">复制地址</button>
  <button class="btn" id="lanclose" type="button">× 关闭这条</button>
</div>
<main id="main">
  <div class="doc-meta">__META__</div>
  <article id="doc">__BODY__</article>
  <footer class="foot">__FOOT__</footer>
</main>
<div id="toast"></div>
__KATEX_JS__
<script>__JS__</script>
</body></html>
"""

KATEX_RENDER = r"""
<script>
(function(){
  if(!window.renderMathInElement) return;
  renderMathInElement(document.getElementById('doc'), {
    delimiters:[
      {left:'$$',right:'$$',display:true},
      {left:'$',right:'$',display:false},
      {left:'\\(',right:'\\)',display:false},
      {left:'\\[',right:'\\]',display:true}
    ],
    throwOnError:false, ignoredTags:['script','noscript','style','textarea','pre','code']
  });
})();
</script>
"""


def build_toc_html(toc):
    out = []
    for lvl, sid, text in toc:
        cls = "lv2" if lvl == 2 else "lv3"
        out.append('<a class="%s" href="#%s" data-id="%s">%s</a>'
                   % (cls, sid, sid, html_escape(text)))
    return "\n".join(out) or '<div style="color:#999;font-size:12px">（无标题）</div>'


def katex_tags(single_file=False, prefix=""):
    if not (ASSETS / "katex" / "katex.min.js").exists():
        return "", "", ""
    if single_file:
        css = (ASSETS / "katex" / "katex.min.css").read_text(encoding="utf-8")
        fdir = ASSETS / "katex" / "fonts"

        def repl(m):
            name = m.group(1)
            p = fdir / name
            if not p.exists():
                return m.group(0)
            b = base64.b64encode(p.read_bytes()).decode()
            return "url(data:font/woff2;base64,%s) format('woff2')" % b

        # KaTeX 的 @font-face 里 woff2 后面还跟 woff/ttf 兜底；单文件里只留 woff2，
        # 否则那两个相对路径会 404。
        css = re.sub(r'url\(fonts/[^)]+\.(?:woff|ttf)\)\s*format\("(?:woff|truetype)"\)\s*,?', "", css)
        css = re.sub(r'url\(fonts/([^)]+\.woff2)\)\s*format\("woff2"\)', repl, css)
        css = re.sub(r",\s*;", ";", css)
        js = (ASSETS / "katex" / "katex.min.js").read_text(encoding="utf-8")
        auto = (ASSETS / "katex" / "auto-render.min.js").read_text(encoding="utf-8")
        return ("<style>%s</style>" % css,
                "<script>%s</script>\n<script>%s</script>\n%s" % (js, auto, KATEX_RENDER),
                "")
    css = '<link rel="stylesheet" href="%s/assets/katex/katex.min.css">' % prefix
    js = ('<script src="%s/assets/katex/katex.min.js"></script>\n'
          '<script src="%s/assets/katex/auto-render.min.js"></script>\n%s'
          % (prefix, prefix, KATEX_RENDER))
    return css, js, ""


# ---------------------------------------------------------------- 文档集
class Docs(object):
    def __init__(self, target):
        self.target = Path(target).resolve()
        if self.target.is_dir():
            self.root = self.target
            self.files = self._scan()
            self.single = False
        else:
            self.root = self.target.parent
            self.files = [self.target]
            self.single = True
        self.chem_dir = (self.root / WORKDIR / "chem")
        self.chem_dir.mkdir(parents=True, exist_ok=True)

    def _scan(self):
        # 默认只扫第一层：一个工作目录里常混着 _pat/、旧版本、思维导图草稿，
        # 递归会把切换条撑成几百个按钮（实测 207 份）。
        fs = [p for p in self.root.glob("*.md")
              if WORKDIR not in p.parts and not p.name.startswith(".")
              and p.name.lower() != "readme.md"]
        if not fs:
            fs = [p for p in self.root.rglob("*.md")
                  if WORKDIR not in p.parts and not p.name.startswith(".")]
        return sorted(fs, key=lambda p: natural_key(str(p.relative_to(self.root))))

    def key(self, p):
        try:
            return str(Path(p).resolve().relative_to(self.root)).replace("\\", "/")
        except Exception:
            return Path(p).name

    def by_key(self, k):
        for p in self.files:
            if self.key(p) == k:
                return p
        return None

    def version(self):
        v = 0.0
        for p in self.files:
            try:
                v = max(v, p.stat().st_mtime)
            except Exception:
                pass
        return "%.3f" % v


def render_doc(docs, path, single_file=False, lan=None, port=None, prefix="", mode="serve"):
    text = read_text(path)
    html, toc = md_to_html(text, path.parent,
                           img_prefix="" if single_file else prefix,
                           embed=single_file, chem_dir=docs.chem_dir, mode=mode)
    cjk, words = count_cjk_and_words(text)
    title = Path(path).stem
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    if m:
        title = re.sub(r"<[^>]+>", "", m.group(1)).strip() or title
    items, cur_label = [], Path(path).stem
    for i, p in enumerate(docs.files, 1):
        k = docs.key(p)
        stem = Path(k).stem
        mnum = re.match(r"^(\d{1,3})[_\-\.．、 ]+(.+)$", stem)
        num, name = (mnum.group(1), mnum.group(2)) if mnum else ("", stem)
        cls = "ditem cur" if p == path else "ditem"
        short = name if len(name) <= 42 else name[:41] + "…"
        items.append('<a class="%s" href="%s/d/%s" title="%s">'
                     '<span class="dnum">%s</span><span class="dname">%s</span></a>'
                     % (cls, prefix, urllib.parse.quote(k), html_escape(stem),
                        html_escape(num or "·"), html_escape(short)))
        if p == path:
            cur_label = ("%s %s" % (num, name)).strip() if num else name
    if len(cur_label) > 34:
        cur_label = cur_label[:33] + "…"
    lanbtn = ""
    if lan is not None:
        lanbtn = ('<button class="btn%s" id="lan" data-on="%d" type="button" '
                  'title="是否允许局域网/手机访问">🌐 局域网 %s</button>'
                  % (" on" if lan else "", 1 if lan else 0, "开" if lan else "关"))
    meta = ('<span class="no-print">文件 <code>%s</code></span><span>%d 汉字 · %d 英文词 · %.1f KB</span>'
            '<span>更新 %s</span><span id="readout" class="no-print"></span>'
            % (html_escape(docs.key(path)), cjk, words,
               len(text.encode("utf-8")) / 1024.0,
               time.strftime("%m-%d %H:%M", time.localtime(path.stat().st_mtime))))
    foot = ("渲染自 %s · mdread · <span class='no-print'>保存 md 后本页 0.8 秒内自动刷新</span>"
            % html_escape(str(path)))
    kcss, kjs, _ = katex_tags(single_file=single_file, prefix=prefix)
    out = (PAGE
           .replace("__TITLE__", html_escape(title))
           .replace("__KATEX_CSS__", kcss)
           .replace("__CSS__", CSS)
           .replace("__DOCKEY__", html_escape(docs.key(path)))
           .replace("__VER__", docs.version())
           .replace("__DOCS__", "\n".join(items))
           .replace("__DOCCUR__", html_escape(cur_label))
           .replace("__DOCCOUNT__", str(len(docs.files)))
           .replace("__LANBTN__", lanbtn)
           .replace("__TOC__", build_toc_html(toc))
           .replace("__META__", meta)
           .replace("__BODY__", html)
           .replace("__FOOT__", foot)
           .replace("__KATEX_JS__", kjs)
           .replace("__JS__", JS))
    return out


# ---------------------------------------------------------------- HTTP 服务
class Handler(BaseHTTPRequestHandler):
    server_version = "mdread/1.0"
    docs = None
    state = None
    port = 7788

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, must-revalidate")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path, q = u.path, urllib.parse.parse_qs(u.query)
        if path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")
        if path == "/api/watch":
            docs = self.docs
            return self._json({"v": docs.version(),
                               "docs": [docs.key(p) for p in docs.files]})
        if path == "/api/lan":
            on = bool(self.state["lan"])
            return self._json({"lan": on, "urls": lan_urls(self.port, on)})
        if path.startswith("/assets/"):
            return self._static(ASSETS, path[len("/assets/"):])
        if path.startswith("/files/"):
            rel = urllib.parse.unquote(path[len("/files/"):])
            return self._static_files(rel)
        if path.startswith("/d/"):
            k = urllib.parse.unquote(path[3:])
            p = self.docs.by_key(k)
            if not p:
                return self._send(404, "<h1>找不到这份文档</h1>")
            return self._send(200, render_doc(self.docs, p, lan=self.state["lan"], port=self.port))
        if path in ("/", "/index.html"):
            p = self.docs.files[0] if self.docs.files else None
            if not p:
                return self._send(404, "<h1>这个目录里没有 .md</h1>")
            return self._send(200, render_doc(self.docs, p, lan=self.state["lan"], port=self.port))
        return self._send(404, "<h1>404</h1>")

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path != "/api/lan":
            return self._json({"error": "not found"}, 404)
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            body = {}
        want = bool(body.get("on", True))
        self.state["lan"] = want
        self._json({"lan": want, "urls": lan_urls(self.port, want)})
        threading.Timer(0.45, lambda: rebind(want)).start()

    def _static(self, base, rel):
        p = (base / urllib.parse.unquote(rel)).resolve()
        try:
            p.relative_to(base)
        except Exception:
            return self._send(403, "forbidden")
        return self._file(p)

    def _static_files(self, rel):
        p = Path(rel)
        if not p.is_absolute():
            p = (self.docs.root / rel).resolve()
        try:
            p.resolve().relative_to(self.docs.root)
        except Exception:
            return self._send(403, "forbidden")
        return self._file(p)

    def _file(self, p):
        if not p.exists() or not p.is_file():
            return self._send(404, "not found")
        mime = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                ".js": "application/javascript; charset=utf-8", ".json": "application/json",
                ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp",
                ".woff2": "font/woff2", ".woff": "font/woff", ".md": "text/plain; charset=utf-8"}
        return self._send(200, p.read_bytes(), mime.get(p.suffix.lower(), "application/octet-stream"))


SRV = {"obj": None, "port": 7788, "docs": None, "state": {"lan": False}}


def make_server(host, port):
    class S(ThreadingHTTPServer):
        # Windows 上 SO_REUSEADDR 会允许两个进程绑同一端口（静默抢流量），必须关掉；
        # Linux/macOS 打开它只是为了跳过 TIME_WAIT。
        allow_reuse_address = (os.name != "nt")
        daemon_threads = True

    return S((host, port), Handler)


def rebind(lan):
    old = SRV.get("obj")
    host = "0.0.0.0" if lan else "127.0.0.1"
    if old:
        try:
            old.shutdown()
        except Exception:
            pass
        try:
            old.server_close()
        except Exception:
            pass
    ok = False
    for _ in range(12):
        try:
            srv = make_server(host, SRV["port"])
            ok = True
            break
        except OSError:
            time.sleep(0.25)
    if not ok:
        log("[网络] 重绑端口失败，仍按上一次监听继续")
        return
    SRV["obj"] = srv
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log("[网络] 监听 %s:%d —— %s" % (host, SRV["port"],
        "手机可访问 " + (lan_urls(SRV["port"], True)[0] if lan_urls(SRV["port"], True) else "")
        if lan else "仅本机"))


def lan_urls(port, lan):
    if not lan:
        return ["http://127.0.0.1:%d/" % port]
    ips = local_ips()
    return ["http://%s:%d/" % (ip, port) for ip in ips] or ["http://127.0.0.1:%d/" % port]


def local_ips():
    out = []
    try:
        ps = ("Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -notlike '127.*' "
              "-and $_.IPAddress -notlike '169.254.*'} | Select-Object -ExpandProperty IPAddress")
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=12)
        out = [x.strip() for x in r.stdout.splitlines() if x.strip()]
    except Exception:
        pass
    if not out:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            out = [s.getsockname()[0]]
            s.close()
        except Exception:
            pass
    return sorted(set(out), key=lambda x: (not x.startswith("192.168."), x))


def port_busy(port):
    """Windows 上 0.0.0.0 与 127.0.0.1 可以同时绑同一端口，bind 不算数，得真的连一下。"""
    s = socket.socket()
    s.settimeout(0.25)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def cmd_serve(args):
    docs = Docs(args.target)
    if not docs.files:
        log("目录里没有 .md 文件：%s" % docs.root)
        return 2
    Handler.docs = docs
    Handler.state = SRV["state"]
    SRV["docs"] = docs
    SRV["port"] = args.port
    Handler.port = args.port
    SRV["state"]["lan"] = (args.lan == "on")
    host = "0.0.0.0" if SRV["state"]["lan"] else "127.0.0.1"
    # 端口被占就往后找：换完必须同步 SRV/Handler，否则局域网开关会绑回旧端口
    srv, port = None, args.port
    for cand in range(args.port, args.port + 20):
        if port_busy(cand):
            continue
        try:
            srv = make_server(host, cand)
            port = cand
            break
        except OSError:
            continue
    if srv is None:
        log("端口 %d–%d 全被占用，换一个 --port" % (args.port, args.port + 19))
        return 2
    if port != args.port:
        log("[端口] %d 被占用，改用 %d" % (args.port, port))
    SRV["port"] = port
    Handler.port = port
    args.port = port
    SRV["obj"] = srv
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log("")
    log("  mdread 已启动 · %d 份文档 · 根目录 %s" % (len(docs.files), docs.root))
    log("  本机   http://127.0.0.1:%d/" % args.port)
    if SRV["state"]["lan"]:
        for u in lan_urls(args.port, True):
            log("  手机   %s" % u)
    else:
        log("  局域网 已关闭（页面上点「🌐 局域网」可开，或加 --lan on）")
    log("  停止   Ctrl+C")
    log("")
    if not args.no_open:
        try:
            os.startfile("http://127.0.0.1:%d/" % args.port)
        except Exception:
            pass
    try:
        while True:
            time.sleep(0.6)
    except KeyboardInterrupt:
        log("\n  bye")
    return 0


def cmd_build(args):
    docs = Docs(args.target)
    p = docs.files[0] if docs.files else None
    if not p:
        log("没有 md")
        return 2
    html = render_doc(docs, p, lan=None, mode="build")
    out = Path(args.out) if args.out else (p.parent / (p.stem + ".html"))
    out.write_text(html, encoding="utf-8")
    log("已写出 %s（%s）" % (out, human_bytes(out.stat().st_size)))
    return 0


def cmd_export(args):
    docs = Docs(args.target)
    p = docs.files[0] if docs.files else None
    if not p:
        log("没有 md")
        return 2
    html = render_doc(docs, p, single_file=True, lan=None)
    out = Path(args.out) if args.out else (p.parent / (p.stem + "_单文件.html"))
    out.write_text(html, encoding="utf-8")
    log("已写出 %s（%s，单文件，可双击/发给同学）" % (out, human_bytes(out.stat().st_size)))
    return 0


def cmd_doctor(args):
    log("mdread doctor")
    log("  脚本位置     %s" % ROOT)
    log("  KaTeX 组件   %s" % ("✓ %d 个文件" %
        len(list((ASSETS / 'katex').rglob('*'))) if (ASSETS / 'katex').exists() else "✗ 缺，公式不会渲染"))
    log("  Indigo 化学  %s" % ("✓ 可用" if indigo() else "✗ 缺，smiles/rxn 块显示为错误提示"))
    log("  python       %s" % sys.version.split()[0])
    try:
        import markdown
        log("  markdown 库  ✓ %s" % markdown.__version__)
    except Exception:
        log("  markdown 库  ✗ 缺，先 pip install markdown")
    log("  本机地址     %s" % ", ".join(local_ips() or ["(取不到)"]))
    if os.name == "nt":
        try:
            ps = ("Get-NetFirewallRule -DisplayName 'mdread*' -ErrorAction SilentlyContinue | "
                  "Select-Object -ExpandProperty DisplayName")
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                               capture_output=True, text=True, timeout=12)
            names = [x.strip() for x in r.stdout.splitlines() if x.strip()]
            log("  防火墙规则   %s" % ("、".join(names) if names else "无（手机连不上多半是这个）"))
            if not names:
                log("               管理员 PowerShell 执行一次：")
                log("               New-NetFirewallRule -DisplayName 'mdread 7788-7805' "
                    "-Direction Inbound -Protocol TCP -LocalPort 7788-7805 -Action Allow -Profile Any")
        except Exception:
            pass
    return 0


DEMO_MD = """# mdread 功能样张

> 这一页由 `mdread demo` 生成，用来快速确认：目录、插图、公式、化学、提示框、徽章是否正常。

## 1 正文与强调

普通段落。**加粗**、*斜体*、`行内代码`、==高亮==、[链接](https://example.com)。

【机读】来自脚本的硬数据 · 【目视比对】人眼看图 · 【推断】我的推测。

> [!NOTE]
> 普通提示：这条会变成"说明"框。

> [!CAUTION]
> 风险提示：这条会变成红色"风险"框。

## 2 表格

| 项目 | 值 | 说明 |
|---|---|---|
| IC50 | 0.6 nM | 细胞水平 |
| 半衰期 | 9.2 h | 大鼠 |

## 3 公式

行内 $IC_{50} = 0.6\\ \\mathrm{nM}$ 与独立公式：

$$\\Delta G^{\\circ} = -RT\\ln K_d$$

## 4 化学

```smiles
CC(=O)Oc1ccccc1C(=O)O
```

```rxn
! pyridine, 0 °C
CC(=O)Cl.O=C([O-])c1ccccc1[O-].[Na+].[Na+]>>CC(=O)Oc1ccccc1C(=O)O
```

## 5 代码

```python
def hello(name):
    return "hi " + name
```

## 6 插图

![示例插图：最小柱状图](figs/demo.png)
"""


def cmd_demo(args):
    d = Path(args.dir or (Path.cwd() / "mdread-demo"))
    (d / "figs").mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image, ImageDraw
        im = Image.new("RGB", (900, 420), "#ffffff")
        dr = ImageDraw.Draw(im)
        dr.line([(70, 360), (860, 360)], fill="#889", width=2)
        dr.line([(70, 40), (70, 360)], fill="#889", width=2)
        for i, h in enumerate([120, 210, 90, 250]):
            x = 130 + i * 180
            dr.rectangle([x, 360 - h, x + 90, 360], fill="#0b6b5e")
        im.save(d / "figs" / "demo.png")
    except Exception as e:
        log("（跳过示例图：%s）" % e)
    (d / "demo.md").write_text(DEMO_MD, encoding="utf-8")
    log("样张已写到 %s" % (d / "demo.md"))
    return cmd_serve(argparse.Namespace(target=str(d), port=args.port,
                                        lan="off", no_open=args.no_open))


def main():
    ap = argparse.ArgumentParser(prog="mdread", description="Markdown 阅读页 + 本地实时预览")
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("serve", help="启动阅读服务（默认只监听本机）")
    s.add_argument("target")
    s.add_argument("--port", type=int, default=7788)
    s.add_argument("--lan", choices=["on", "off"], default="off")
    s.add_argument("--no-open", action="store_true")
    s.set_defaults(func=cmd_serve)

    b = sub.add_parser("build", help="渲染成单个 html")
    b.add_argument("target")
    b.add_argument("--out")
    b.set_defaults(func=cmd_build)

    e = sub.add_parser("export", help="导出单文件 html（图片/公式/样式全内嵌）")
    e.add_argument("target")
    e.add_argument("--out")
    e.set_defaults(func=cmd_export)

    d = sub.add_parser("doctor", help="自检依赖、端口与防火墙")
    d.set_defaults(func=cmd_doctor)

    m = sub.add_parser("demo", help="生成并打开功能样张")
    m.add_argument("dir", nargs="?")
    m.add_argument("--port", type=int, default=7789)
    m.add_argument("--no-open", action="store_true")
    m.set_defaults(func=cmd_demo)

    a = ap.parse_args()
    if not getattr(a, "func", None):
        ap.print_help()
        return 1
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())