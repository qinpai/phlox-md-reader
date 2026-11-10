# Phlox-md-reader

把 Markdown 渲染成「能长时间读下去」的中文阅读页：左侧目录、自动编号插图、离线公式、化学结构与反应式、深色与四套配色、手机自适应；本地起一个端口边改边看，也能导出一份单文件 HTML 发给别人。

*A single-file Markdown reader for Chinese long-form reading — TOC, auto-numbered figures, offline KaTeX, chemical structures from SMILES, dark mode, mobile-friendly, live reload, one-file HTML export.*

![桌面端：左上角文件夹下拉切换文档](docs/shot-menu.png)

| 手机端 | 四套配色 |
|---|---|
| ![手机端](docs/shot-phone.png) | ![配色](docs/shot-themes.png) |

## 为什么写它

编辑器内置预览、GitHub、docsify 都只做「把标记翻译成 HTML」。真正的长时间阅读是另一回事：

| 问题 | 这里的做法 |
|---|---|
| 一屏 120 字的长行，读三分钟就累 | 版心固定 `min(54em, 92vw)`，每行 52–54 个汉字，字号 `clamp()` 随窗口流体变化 |
| 长文档找不到位置 | h2/h3 自动加锚点，宽屏固定左栏目录、窄屏 ☰ 抽屉，滚动时高亮当前节 |
| 插图没有编号，长截图撑爆一页 | 自动编号 + 题注；高 > 2× 宽的长图进限高 76vh 的滚动框 |
| 化学式只能贴 ChemDraw 图片 | ` ```smiles ` 直接出结构式，题注自动补分子式与分子量 |
| 改一个字就要手动刷新 | 800ms 轮询，保存即刷新，且**保留滚动位置** |
| 想发给同学，对方不一定有环境 | `export` 出一份单文件 HTML，图片/公式/字体/样式全内嵌 |

## 快速开始

需要 Python 3.9+（开发与验证环境：Windows 11 + Python 3.13.15）。

```bash
pip install -r requirements.txt          # markdown + pillow
python mdread.py demo                    # 生成一份功能样张并打开，先看看效果

python mdread.py serve "D:\notes"        # 整个目录，左上角 📁 下拉切换文档
python mdread.py serve "D:\notes\a.md" --port 7800
```

浏览器自动打开 `http://127.0.0.1:7788/`。改 md 保存 → 页面自动刷新。

想用化学结构式（可选，另需约 22 MB）：

```bash
pip install --target vendor epam.indigo
python mdread.py doctor                  # 确认 Indigo 可用
```

## 五个命令

| 命令 | 用途 |
|---|---|
| `serve <文件或目录>` | 起服务实时预览。选项：`--port`、`--lan on\|off`、`--no-open` |
| `build <文件>` | 渲染成同目录 `xxx.html`，图片走相对路径，双击可看 |
| `export <文件>` | 单文件 HTML，图片/KaTeX 字体/样式全部 base64 内嵌，可发给别人 |
| `doctor` | 自检 KaTeX、Indigo、markdown 库、本机地址、防火墙规则 |
| `demo [目录]` | 生成含图、公式、化学式、提示框的功能样张并起服务 |

端口默认 7788，被占用会自动往后找（+1 … +19），日志里会写明实际端口。

## 局域网：默认关闭

服务默认只绑 `127.0.0.1`，外部机器连不上。要手机看，点工具栏的「🌐 局域网 开」——它会**真的重新绑 socket** 到 `0.0.0.0`，然后顶部出现常驻地址条：

```
📶 局域网已开　手机连同一个 Wi-Fi，打开：http://192.168.1.42:7800/　[复制地址] [× 关闭这条]
```

地址条可选中、可一键复制；手机端不显示任何浮层。开关状态存在服务端进程里，**保存 md、切换文档都不会改动它**。

Windows 首次使用若手机连不上，通常是防火墙：需要给 python 放行入站（端口段 7788–7807）。校园网/公共 Wi-Fi 常做接入层隔离，此时换手机热点最稳。

## 页面里有什么

- **文档切换**：工具栏最左 `📁 当前文档名 ▾`，点开向下列表（编号 + 文件名），当前项高亮；列表滚动位置会被记住。
- **版心与字号**：`html{font-size:clamp(17px,15px+0.22vw,20.5px)}`，工具栏右侧实时显示「每行约 N 字 · 正文宽 X px · 字号 Y px」。
- **配色**：`🎨` 循环四套（深蓝 / 青绿 / 琥珀 / 砖红）× `◐` 明暗，记在 localStorage。
- **提示框**：`> [!NOTE]`、`[!TIP]`、`[!IMPORTANT]`、`[!WARNING]`、`[!CAUTION]`，渲染成「说明 / 提示 / 要点 / 注意 / 风险」。
- **徽章**：`【机读】【目视比对】【推断】【待完善】【实测】【结论】` 自动上色。
- **高亮**：`==文字==`。
- **公式**：离线 KaTeX（`$…$`、`$$…$$`），断网可用；导出时字体内嵌。
- **表格**：宽表格横向滑动，不撑破版心。
- **打印**：`⎙` 隐藏工具栏与目录，Ctrl+P 直接存 PDF。

![化学结构与反应式](docs/shot-chem.png)

## 写 md 的三条规矩

1. **反应箭头上的条件只能写英文或符号。** Indigo 内置字库没有 CJK，`! 吡啶, 0 °C` 会渲染成 `□□, 0 °C`：

   ````
   ```rxn
   ! pyridine, 0 °C
   CC(=O)Cl.O=C([O-])c1ccccc1[O-].[Na+].[Na+]>>CC(=O)Oc1ccccc1C(=O)O
   ```
   ````

   中文条件写进正文或题注。多步反应（`A>>B>>C`）也不支持，逐步写多个块。

2. **图片放 `figs/`，用相对路径引用。** 题注就是 alt 文本，写清楚"这是什么"而不是"图片1"。缺图会在日志里以 `[缺图]` 报出。

3. **标题编号自己写**（`## 1 背景`、`### 1.1 现状`）。渲染器会把编号抽成 `<span class="secnum">` 排成小号强调色，目录里也按编号排序。

## 已知限制

- **没有全文搜索**。文档多的时候靠编号前缀 + 目录定位。
- 文档列表默认只扫**第一层** `*.md`，不递归（递归会把草稿目录全塞进来）。
- 代码块**没有语法高亮**（不做 Pygments 依赖）。
- 多步反应式、Mermaid 图不支持。
- KaTeX 的 `$…$` 行内公式可能和正文里的美元符号冲突，必要时用 `$$…$$`。
- 只在 Windows 11 + Python 3.13 上完整验证过；Linux/macOS 需自测（防火墙相关的排错说明仅针对 Windows）。

## 依赖与许可

| 组件 | 许可 | 是否随仓库分发 |
|---|---|---|
| 本项目代码 | MIT（见 `LICENSE`） | 是 |
| [KaTeX](https://github.com/KaTeX/KaTeX) 0.16.11 | MIT | 是（`assets/katex/`，附 `LICENSE`） |
| [markdown](https://github.com/Python-Markdown/markdown) | BSD-3-Clause | 否，pip 安装 |
| [Pillow](https://python-pillow.org/) | HPND | 否，pip 安装 |
| [epam.indigo](https://github.com/epam/Indigo) 1.46.0 | Apache-2.0 | **否**，`pip install --target vendor epam.indigo` 按需安装 |

化学渲染是可选能力：`vendor/` 不存在时，化学块会被跳过，其余功能不受影响。

## 目录结构

```
phlox-md-reader/
├── mdread.py              主程序（serve / build / export / doctor / demo）
├── SKILL.md               面向 AI agent 的操作说明（DSH 技能格式）
├── assets/katex/          离线 KaTeX 0.16.11
├── docs/                  README 截图
├── references/style.md    设计令牌：配色、字号、断点、间距
└── vendor/                可选依赖（.gitignore 掉，不随仓库分发）
```

---

如果这份东西帮你少花了几小时调行宽和目录，欢迎 star；有想加的排版元素也欢迎开 issue。