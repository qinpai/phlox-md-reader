# 设计令牌（Phlox-md-reader）

改样式时只动 `mdread.py` 里 `CSS` 常量的这些变量，不要各处硬写数值——手机端和暗色模式都靠它们联动。

## 字号与版心

| 用途 | 值 | 说明 |
|---|---|---|
| 根字号 | `clamp(17px, 15px + 0.22vw, 20.5px)` | 1366 屏约 17.5px，2560 屏封顶 20.5px |
| 正文行高 | `1.75` | 中文低于 1.7 会显挤 |
| 正文版心 | `min(54em, 92vw)` | 1440 屏约 962px ≈ 每行 54 汉字 |
| 用户微调 | `--fsx: 0.86 – 1.30`，步进 0.08 | `A− / A+`，记 localStorage |
| 段落间距 | `--gap: 1.05em` | 段落之间用 margin-top，不用空 `<p>` |

判据：一行 40–45 个汉字偏窄（停顿多），60 以上偏宽（回扫累）。52–56 是中文长文的舒适区。

## 断点

| 断点 | 变化 |
|---|---|
| `> 1080px` | 左栏目录固定（`--tocw: 250px`），工具栏单行 |
| `≤ 1080px` | 目录变抽屉 + 遮罩；工具栏两行 `--barh: 104px`；隐藏局域网按钮与所有浮层；表格允许横滑（`min-width: 560px`） |
| `≤ 430px` | `--barh: 98px`；按钮字号 11px、左右 padding 2px |
| 横屏 `(orientation: landscape) and (max-height: 560px)` | `--barh: 48px` 单行，按钮 `flex: 0 0 auto` |

按钮文字**永不隐藏**（用户明确要求）。窄屏靠缩字号与 padding 收纳，不靠藏文字。实测 390 宽 8 个按钮全部常显、零裁切。

## 配色

四套色相 × 明暗两档，`body[data-accent]` 与 `body[data-theme]` 同时作用。

| accent | 亮色主色 | 暗色主色 | 语气 |
|---|---|---|---|
| `navy`（默认） | `#1f4e79` | `#7fb0d6` | 正式、报告、审阅 |
| `teal` | `#0f6157` | `#5cc0b0` | 生物、药化、实验记录 |
| `amber` | `#9a5b12` | `#e0a45c` | 提醒、待办、清单 |
| `red` | `#9c2b2b` | `#e58c8c` | 风险、批注、问题清单 |

底色不用纯白：亮色 `--bg:#f6f4ef`（暖纸）、`--bg2:#fdfcf9`；暗色 `--bg:#171a1d`、`--bg2:#1e2226`；分割线 `--line:#e1dcd2`（暗色 `#2e343a`）。纯白背景加高对比正文在长文里会"发白光"，这是被用户明确否掉的一版。

## 间距

| 元素 | 值 |
|---|---|
| 工具栏高 | `--barh: 52px`（桌面）/ 104px（两行）/ 48px（横屏） |
| 正文上内边距 | `calc(var(--barh) + 30px)`；有地址条时再加 `--lanh` |
| 锚点偏移 | `h1,h2,h3,figure,[id]{scroll-margin-top:calc(var(--barh) + 10px)}` |
| 长图框限高 | `76vh`，`overscroll-behavior: contain` |
| 化学图最大宽 | `min(100%, 560px)` |

## 交互反馈

`.btn:active{transform:translateY(1px) scale(.96)}`，`transition .11s`，`:focus-visible` 保留 2px 描边；`@media (prefers-reduced-motion: reduce)` 下关闭过渡。浮层（toast / 局域网地址条）**只在桌面端出现**。