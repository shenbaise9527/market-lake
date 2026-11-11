"""量每张 SVG 的真实包围盒，用来收紧 viewBox（消除右侧/底部空白）。

做法：把 md 里的每个 <svg> 单独放进一个 HTML，用 Chrome headless 跑 getBBox()。
用法：
    uv run python tools/measure_svg.py site/docs/ch03-encodings.md

浏览器按 CHROME 环境变量 -> 平台常见安装位置 -> PATH 的顺序找，
Windows 找不到 Chrome 时会退回 Edge（同样支持 --headless=new）。
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

HTML = """<!doctype html><meta charset="utf-8">
<body style="margin:0">
<div id="host" style="width:680px">{svgs}</div>
<pre id="out">pending</pre>
<script>
const res = [];
document.querySelectorAll('#host svg').forEach((s, i) => {{
  const b = s.getBBox();
  res.push({{i, x: +b.x.toFixed(1), y: +b.y.toFixed(1),
            w: +b.width.toFixed(1), h: +b.height.toFixed(1)}});
}});
document.getElementById('out').textContent = JSON.stringify(res);
</script>
"""


def find_chrome() -> str:
    """返回可用的 Chromium 系浏览器路径，找不到就报清楚的错。"""
    env = os.environ.get("CHROME")
    if env and pathlib.Path(env).exists():
        return env

    candidates: list[pathlib.Path] = []
    if sys.platform == "win32":
        bases = [
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("LOCALAPPDATA"),
        ]
        for base in filter(None, bases):
            candidates += [
                pathlib.Path(base) / "Google/Chrome/Application/chrome.exe",
                pathlib.Path(base) / "Microsoft/Edge/Application/msedge.exe",
            ]
    elif sys.platform == "darwin":
        candidates += [
            pathlib.Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            pathlib.Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        ]

    for name in ("google-chrome", "google-chrome-stable", "chromium",
                 "chromium-browser", "microsoft-edge"):
        found = shutil.which(name)
        if found:
            candidates.append(pathlib.Path(found))

    for c in candidates:
        if c.exists():
            return str(c)

    raise SystemExit(
        "找不到 Chrome / Edge。装一个，或用环境变量指定：\n"
        "    CHROME=/path/to/chrome uv run python tools/measure_svg.py <file.md>"
    )


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__.strip().splitlines()[-1].strip())

    md = pathlib.Path(sys.argv[1])
    text = md.read_text(encoding="utf-8")
    svgs = re.findall(r"<svg[^>]*>.*?</svg>", text, re.S)
    if not svgs:
        raise SystemExit("这个文件里没有 svg")

    chrome = find_chrome()
    # 临时文件放系统临时目录：不往仓库里丢 html 与 chrome 配置目录
    with tempfile.TemporaryDirectory(prefix="measure-svg-") as td:
        tmp = pathlib.Path(td) / "measure.html"
        tmp.write_text(HTML.format(svgs="\n".join(svgs)), encoding="utf-8")
        out = subprocess.run(
            [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
             "--user-data-dir=" + str(pathlib.Path(td) / "profile"),
             "--dump-dom", tmp.resolve().as_uri()],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )

    m = re.search(r'<pre id="out">(.*?)</pre>', out.stdout, re.S)
    if not m:
        print(out.stdout[-800:], out.stderr[-800:])
        raise SystemExit("没拿到测量结果")
    data = json.loads(m.group(1))
    for d, s in zip(data, svgs):
        vb = re.search(r'viewBox="([^"]+)"', s).group(1)
        print(f"#{d['i']}  viewBox={vb:16}  bbox x={d['x']:6} y={d['y']:6} "
              f"w={d['w']:7} h={d['h']:7}")


if __name__ == "__main__":
    main()
