# 站点写作约定（图解部分）

写第 2–10 章时照这份来，否则图会不显示或主题切换后看不见。

## 1. 图解必须包在 `<figure class="diagram">` 里

```markdown
<figure class="diagram">
<svg width="100%" viewBox="0 0 680 250" role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui, sans-serif">
...
</svg>
</figure>
```

三个原因，缺一个就出问题：

- **不要省略 `<figure>`**：Python-Markdown 不把 `<svg>` 当块级标签，会把它塞进 `<p>`，
  而 `<p>` 里的行内 SVG 没有确定高度，浏览器可能按 0 高度渲染——页面看着就是"图没显示"。
- **`viewBox` 之外必须写 `width="100%"`**：让浏览器按 viewBox 的宽高比算高度。
- **`<figure>` 内不能有空行**：raw HTML 块遇空行即中断，后面的内容会被当成普通段落重新解析。

## 2. 颜色只用 `currentColor`，配合 `fill-opacity` / `stroke-opacity` 做层次

站点有深色（slate）和浅色（default）两套主题，写死颜色必然在其中一套下看不清。
强调色（橙 `#EF9F27`、蓝 `#378ADD`、绿 `#1D9E75`）可以写死——它们在两套主题下都够对比度。

## 3. 画布宽度统一 `0 0 680 *`

与正文最大宽度对齐，`max-width: 680px` 已写进 `assets/css/extra.css`。

## 4. 自检

```bash
cd E:/work/learning/market-lake
uv run zensical build -f site/mkdocs.yml
python -c "
import pathlib,re
for p in pathlib.Path('site/_build').rglob('index.html'):
    s=p.read_text(encoding='utf-8',errors='ignore')
    if re.search(r'<p>\s*<svg', s): print('未包裹 figure:', p)
print('检查完成')
"
```

出现 `未包裹 figure` 就说明图会显示异常，回到第 1 条。

## 5. 放大能力（已全局生效，写图时不用管）

`assets/js/diagram-zoom.js` 会给页面上每个 `figure.diagram` 绑点击事件：弹出全屏浮层，
滚轮缩放（50%–400%）、双击复位、Esc / 点击遮罩 / 右上角 × 关闭。
样式在 `extra.css` 的 `.dz-*` 段，通过 `extra_javascript` 全局加载，新增章节无需任何额外标记。

写图时只需保证两点：图包在 `figure.diagram` 里、`<svg>` 带 `viewBox`——浮层靠 viewBox 等比放大。

## 6. 显示尺寸由 CSS 统一控制，图里不要写死宽高

`extra.css` 顶部把布局改成随视口连续自适应（`.md-grid{max-width:min(calc(100vw - 3rem),1680px)}`），
图解容器上限 1000px，小屏（<720px）按 40rem 渲染并允许横向滚动。
所以 SVG 里只写 `viewBox` + `width="100%"` 即可，**不要写死 `width="900"` 这类数值**，
否则会覆盖自适应逻辑。

## 7. Zensical classic 的 figure 陷阱（已由 extra.css 兜底）

主题给 `.md-typeset figure` 设了 `width: fit-content`。若图容器靠它，容器宽度会依赖内容，
而 svg 的 `width: 100%` 又依赖容器 —— 循环依赖，浏览器把百分比当 auto，
**svg 回落到默认 300x150**（这就是"图怎么都只有 300px"的根因）。
`extra.css` 已对 `figure.diagram` 显式设 `width: auto` 兜底；今后若图仍莫名变小，先查这条。
