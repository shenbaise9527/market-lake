"""量每张 SVG 的真实包围盒，用来收紧 viewBox（消除右侧/底部空白）。

做法：把 md 里的每个 <svg> 单独放进一个 HTML，用 Chrome headless 跑 getBBox()。
用法：
    uv run python tools/measure_svg.py site/docs/ch03-encodings.md
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
import tempfile

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

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


def main() -> None:
    md = pathlib.Path(sys.argv[1])
    text = md.read_text(encoding="utf-8")
    svgs = re.findall(r"<svg[^>]*>.*?</svg>", text, re.S)
    if not svgs:
        raise SystemExit("这个文件里没有 svg")

    tmp = pathlib.Path("tools") / "_measure.html"
    tmp.write_text(HTML.format(svgs="\n".join(svgs)), encoding="utf-8")
    out = subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--user-data-dir=" + str(pathlib.Path("tools/_chrome").resolve()),
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
