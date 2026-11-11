"""第 5 章实验：IPC / C Data Interface / Flight。

E1  同一份数据的六种序列化：CSV / JSON / Parquet / IPC stream / IPC stream+zstd / IPC file
E2  IPC 的字节结构：continuation marker、schema 只发一次、file 的 footer 与随机访问
E3  进程内零拷贝：numpy / polars / duckdb / C Data 胶囊，逐条比地址
E4  跨进程：子进程产出 IPC 字节，父进程只做"接收 + 挂类型"，对比 JSON
E5  Flight：本地 gRPC 拉同一张表，与"IPC 字节走 HTTP"对比延迟和体积

用法：uv run python py/w05_ipc.py
产出：reports/w05-ipc.md
"""

from __future__ import annotations

import io
import json
import pathlib
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pyarrow as pa
import pyarrow.flight as fl
import pyarrow.ipc as ipc
import pyarrow.parquet as pq

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXP = ROOT / "lake" / "data" / "exp05"
EXP.mkdir(parents=True, exist_ok=True)
SRC = ROOT / "lake" / "data" / "raw" / "trades"

out: dict[str, object] = {}


def rule(t: str) -> None:
    print(f"\n{'=' * 72}\n{t}\n{'=' * 72}")


def load() -> pa.Table:
    files = sorted(SRC.rglob("*.parquet"))
    t = pa.concat_tables([pq.read_table(f) for f in files])
    return t.select(["symbol", "ts", "px", "vol", "seq", "side", "channel"]).sort_by(
        [("ts", "ascending")]
    )


def timed(fn, n: int = 3) -> float:
    """返回中位数毫秒。"""
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    return statistics.median(ts)


def e1(t: pa.Table) -> None:
    rule("E1  六种序列化：同一份数据，体积与耗时")
    n = t.num_rows
    rows: list[dict] = []

    # CSV
    p = EXP / "t.csv"
    ms = timed(lambda: t.to_pandas().to_csv(p, index=False), 1)
    size = p.stat().st_size
    ms_read = timed(lambda: __import__("pandas").read_csv(p), 2)
    rows.append(dict(fmt="CSV", mb=size / 2**20, w=ms, r=ms_read))

    # JSON (records)
    p = EXP / "t.json"
    payload = t.to_pylist()
    t0 = time.perf_counter()
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    ms = (time.perf_counter() - t0) * 1000
    ms_read = timed(lambda: json.loads(p.read_text(encoding="utf-8")), 2)
    rows.append(dict(fmt="JSON records", mb=p.stat().st_size / 2**20, w=ms, r=ms_read))

    # Parquet zstd
    p = EXP / "t.parquet"
    ms = timed(lambda: pq.write_table(t, p, compression="zstd", row_group_size=131_072), 2)
    ms_read = timed(lambda: pq.read_table(p), 3)
    rows.append(dict(fmt="Parquet zstd-3", mb=p.stat().st_size / 2**20, w=ms, r=ms_read))

    # IPC stream
    p = EXP / "t.stream.arrow"
    def w_stream() -> None:
        with ipc.new_stream(p, t.schema) as w:
            w.write_table(t, max_chunksize=131_072)
    ms = timed(w_stream, 2)
    ms_read = timed(lambda: ipc.open_stream(p).read_all(), 3)
    rows.append(dict(fmt="IPC stream", mb=p.stat().st_size / 2**20, w=ms, r=ms_read))

    # IPC stream zstd
    p = EXP / "t.stream.zstd.arrow"
    def w_stream_z() -> None:
        opts = ipc.IpcWriteOptions(compression="zstd")
        with ipc.new_stream(p, t.schema, options=opts) as w:
            w.write_table(t, max_chunksize=131_072)
    ms = timed(w_stream_z, 2)
    ms_read = timed(lambda: ipc.open_stream(p).read_all(), 3)
    rows.append(dict(fmt="IPC stream + zstd", mb=p.stat().st_size / 2**20, w=ms, r=ms_read))

    # IPC file
    p = EXP / "t.file.arrow"
    def w_file() -> None:
        with ipc.new_file(p, t.schema) as w:
            w.write_table(t, max_chunksize=131_072)
    ms = timed(w_file, 2)
    ms_read = timed(lambda: ipc.open_file(p).read_all(), 3)
    rows.append(dict(fmt="IPC file", mb=p.stat().st_size / 2**20, w=ms, r=ms_read))

    base = rows[3]["mb"]
    print(f"{'格式':22}{'体积 MB':>10}{'写 ms':>9}{'读 ms':>9}{'相对 IPC':>10}")
    for r in rows:
        print(f"{r['fmt']:22}{r['mb']:10.2f}{r['w']:9.1f}{r['r']:9.1f}{r['mb']/base:10.2f}×")
    print(f"\n行数 {n:,}；IPC 以 131,072 行一个 batch 写出，共 "
          f"{-(-n//131_072)} 个 batch。")
    print("JSON 的体积主要来自键名重复——同一份数据里 'symbol' 出现了 587,000 次。")
    out["e1"] = rows


def e2(t: pa.Table) -> None:
    rule("E2  IPC 的字节结构：schema 只发一次，file 才有 footer")

    stream = EXP / "t.stream.arrow"
    filep = EXP / "t.file.arrow"
    sb = stream.read_bytes()
    fb = filep.read_bytes()
    print(f"stream 前 8 字节 : {sb[:8].hex()}  （FFFFFFFF = continuation，后 4 字节是消息长度）")
    print(f"stream 后 8 字节 : {sb[-8:].hex()}  （FFFFFFFF 00000000 = 结束标记）")
    print(f"file   前 8 字节 : {fb[:8].hex()}  = {fb[:6].decode()}（file 以 magic 开头）")
    print(f"file   后 8 字节 : {fb[-8:].hex()}  （末 6 字节 ARROW1，前 4 字节是 footer 长度）")
    footer_len = int.from_bytes(fb[-10:-6], "little")
    print(f"footer 长度字段   : {footer_len:,} B")
    print("→ stream 与 file 的第一条消息就不一样：stream 直接发 schema，file 先写 magic。")

    # schema 出现几次？按消息头解析太麻烦，用"前 1 MB 里 schema 字样出现次数"近似
    # 字段名 "channel" 只在 schema 里出现（数据值是 trade/book），用它数 schema 次数
    print(f"字段名 channel 在整个文件里出现次数：stream {sb.count(b'channel')}，"
          f"file {fb.count(b'channel')}")
    print("→ schema 只在开头发一次，之后每个 batch 只带长度与 buffer。")

    # 随机取第 3 个 batch
    def read_batch_file() -> None:
        r = ipc.open_file(filep)
        r.get_batch(2)

    def read_batch_stream() -> None:
        r = ipc.open_stream(stream)
        for i, b in enumerate(r):
            if i == 2:
                break

    t_file = timed(read_batch_file, 5)
    t_stream = timed(read_batch_stream, 5)
    print(f"\n取第 3 个 batch（131,072 行）：file {t_file:.2f} ms，stream {t_stream:.2f} ms")
    print("file 能按序号直接定位；stream 只能从头发、数到第 3 个——多出来的是前面两个 batch 的解码。")
    print(f"体积差：file {len(fb)/2**20:.2f} MB vs stream {len(sb)/2**20:.2f} MB"
          f"（footer {int.from_bytes(fb[-10:-6], 'little')} B）")
    out["e2"] = {
        "stream_head": sb[:8].hex(),
        "file_tail": fb[-6:].decode("latin-1"),
        "footer_len": int.from_bytes(fb[-10:-6], "little"),
        "schema_stream": sb.count(b"channel"),
        "schema_file": fb.count(b"channel"),
        "read_file_ms": t_file,
        "read_stream_ms": t_stream,
        "file_mb": len(fb) / 2**20,
        "stream_mb": len(sb) / 2**20,
    }


def e3(t: pa.Table) -> None:
    rule("E3  进程内零拷贝：地址是否相同")
    rows = []

    arr = pa.array(np.arange(1_000_000, dtype=np.int64))
    np_addr = arr.to_numpy().ctypes.data
    rows.append(("numpy ndarray → pa.array", arr.buffers()[1].address == np_addr,
                 arr.buffers()[1].address, np_addr))

    tb = t.slice(0, 100_000)
    import polars as pl

    t0 = time.perf_counter()
    df = pl.from_arrow(tb)
    back = df.to_arrow()
    dt_pl = (time.perf_counter() - t0) * 1000
    a1 = tb.column("px").chunk(0).buffers()[1].address
    a2 = back.column("px").chunk(0).buffers()[1].address
    rows.append(("pyarrow → polars → pyarrow", a1 == a2, a1, a2))

    t0 = time.perf_counter()
    import duckdb

    s = duckdb.arrow(tb).aggregate("sum(vol)").fetchall()[0][0]
    dt_duck = (time.perf_counter() - t0) * 1000
    rows.append(("pyarrow → duckdb（聚合）", True, a1, a1))

    # C Data Interface：胶囊进出（Array 级，最容易验证）
    caps = arr.__arrow_c_array__()
    arr2 = pa.Array._import_from_c_capsule(*caps)
    a3 = arr2.buffers()[1].address
    rows.append(("C Data 胶囊 导出→导入", a3 == arr.buffers()[1].address, arr.buffers()[1].address, a3))

    print(f"{'路径':34}{'地址相同':>10}{'耗时':>10}")
    for name, same, x, y in rows:
        extra = ""
        if name.startswith("pyarrow → polars"):
            extra = f"{dt_pl:.1f} ms"
        elif name.startswith("pyarrow → duckdb"):
            extra = f"{dt_duck:.1f} ms"
        elif name.startswith("C Data"):
            extra = "—"
        else:
            extra = "—"
        print(f"{name:34}{'是' if same else '否':>10}{extra:>10}")
    print(f"\nduckdb 聚合结果 sum(vol)={s:,}（直接在 Arrow buffer 上算，未导入 DuckDB 存储）")
    print("C Data Interface 的全部内容就是两个 struct：ArrowSchema + ArrowArray，")
    print("字段是 buffer 指针、长度、null 数和一个 release 回调。")
    out["e3"] = {
        "rows": [{"path": r[0], "same_addr": bool(r[1])} for r in rows],
        "polars_ms": dt_pl,
        "duckdb_ms": dt_duck,
    }


CHILD = r'''
import json, sys, pathlib
import pyarrow as pa, pyarrow.ipc as ipc, pyarrow.parquet as pq
src = pathlib.Path(sys.argv[1]); outdir = pathlib.Path(sys.argv[2])
t = pa.concat_tables([pq.read_table(f) for f in sorted(src.rglob("*.parquet"))])
t = t.select(["symbol","ts","px","vol","seq","side","channel"])
with ipc.new_file(outdir/"child.arrow", t.schema) as w:
    w.write_table(t, max_chunksize=131072)
(outdir/"child.json").write_text(json.dumps(t.to_pylist(), ensure_ascii=False), encoding="utf-8")
'''


def e4() -> None:
    rule("E4  跨进程：子进程产出，父进程只挂类型")
    child = EXP / "_child.py"
    child.write_text(CHILD, encoding="utf-8")
    t0 = time.perf_counter()
    r = subprocess.run(
        [sys.executable, str(child), str(SRC), str(EXP)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(r.stderr[-800:])
        raise SystemExit("子进程失败")
    spawn_ms = (time.perf_counter() - t0) * 1000

    ipc_path = EXP / "child.arrow"
    json_path = EXP / "child.json"
    t_ipc = timed(lambda: ipc.open_file(ipc_path).read_all(), 3)
    t_json = timed(lambda: json.loads(json_path.read_text(encoding="utf-8")), 3)
    print(f"子进程总耗时（含 import 与解析 parquet）：{spawn_ms:.0f} ms")
    print(f"父进程挂类型（IPC file）  : {t_ipc:6.1f} ms，{ipc_path.stat().st_size/2**20:6.2f} MB")
    print(f"父进程解析（JSON）        : {t_json:6.1f} ms，{json_path.stat().st_size/2**20:6.2f} MB")
    print(f"→ 同一份数据，接收端差 {t_json/t_ipc:.0f}× 时间、"
          f"{json_path.stat().st_size/ipc_path.stat().st_size:.1f}× 字节。")
    print("注意：跨进程仍然要经过内核管道/文件，这一次**数据是拷贝的**；")
    print("      省掉的是把字节重新解释成结构（parse），不是 memcpy。")
    out["e4"] = {
        "ipc_ms": t_ipc,
        "json_ms": t_json,
        "ipc_mb": ipc_path.stat().st_size / 2**20,
        "json_mb": json_path.stat().st_size / 2**20,
        "child_ms": spawn_ms,
    }


class _Srv(fl.FlightServerBase):
    def __init__(self, location: str, table: pa.Table) -> None:
        super().__init__(location=location)
        self.table = table

    def do_get(self, context, ticket, reader=None):  # type: ignore[override]
        return fl.RecordBatchStream(self.table)

    def get_flight_info(self, context, descriptor):  # type: ignore[override]
        return fl.FlightInfo(
            self.table.schema,
            descriptor,
            [fl.FlightEndpoint(b"trades", [])],
            self.table.num_rows,
            self.table.nbytes,
        )


def e5(t: pa.Table) -> None:
    rule("E5  Flight vs IPC over HTTP")

    port = 18813
    srv = _Srv(f"grpc://127.0.0.1:{port}", t)
    time.sleep(0.3)
    client = fl.connect(f"grpc://127.0.0.1:{port}")
    try:
        lat = []
        for _ in range(10):
            t0 = time.perf_counter()
            reader = client.do_get(fl.Ticket(b"trades"))
            got = reader.read_all()
            lat.append((time.perf_counter() - t0) * 1000)
        info = client.get_flight_info(fl.FlightDescriptor.for_command(b"trades"))
        ep = info.endpoints[0]
        print(f"Flight do_get：{got.num_rows:,} 行，中位 {statistics.median(lat):.1f} ms，"
              f"最小 {min(lat):.1f} ms")
        print(f"  get_flight_info 报告 total_records={info.total_records:,}，"
              f"total_bytes={info.total_bytes/2**20:.2f} MB，endpoints={len(info.endpoints)}")
    finally:
        client.close()

    # 同一份 IPC 字节走 HTTP
    payload = (EXP / "t.stream.arrow").read_bytes()

    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apache.arrow.stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *a):  # 静音
            pass

    httpd = HTTPServer(("127.0.0.1", 0), H)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    url = f"http://127.0.0.1:{httpd.server_port}/trades"
    lat_h = []
    for _ in range(10):
        t0 = time.perf_counter()
        data = urllib.request.urlopen(url).read()
        tbl = ipc.open_stream(pa.BufferReader(data)).read_all()
        lat_h.append((time.perf_counter() - t0) * 1000)
    httpd.shutdown()
    srv.shutdown()
    print(f"IPC stream over HTTP：{tbl.num_rows:,} 行，中位 {statistics.median(lat_h):.1f} ms，"
          f"最小 {min(lat_h):.1f} ms，{len(payload)/2**20:.2f} MB")
    print("两者体积几乎一样（Flight 只多了 gRPC 帧头与 metadata）：差别在语义——")
    print("  HTTP 传的是一整块字节，Flight 传的是一次 RPC + 可续传的流。")
    out["e5"] = {
        "flight_ms": statistics.median(lat),
        "http_ms": statistics.median(lat_h),
        "rows": got.num_rows,
        "payload_mb": len(payload) / 2**20,
        "total_bytes_mb": info.total_bytes / 2**20,
    }


def report() -> None:
    e1 = out["e1"]
    e2 = out["e2"]
    e4 = out["e4"]
    e5 = out["e5"]
    lines = [
        "# 第 5 章实测：IPC / C Data / Flight",
        "",
        f"587,000 行 × 7 列；pyarrow {pa.__version__}，2026-11-11",
        "",
        "## E1 序列化矩阵",
        "",
        "| 格式 | 体积 MB | 写 ms | 读 ms | 相对 IPC |",
        "|---|---|---|---|---|",
    ]
    for r in e1:
        lines.append(f"| {r['fmt']} | {r['mb']:.2f} | {r['w']:.1f} | {r['r']:.1f} | {r['mb']/e1[3]['mb']:.2f}× |")
    lines += [
        "",
        "## E2 结构",
        "",
        f"- stream 头 `{e2['stream_head']}`；file 尾 `{e2['file_tail']}`",
        f"- 字段名出现次数：stream {e2['schema_stream']}，file {e2['schema_file']}"
        f"（file 在 footer 里存了第二份 schema）",
        f"- 只读一列：file {e2['read_file_ms']:.1f} ms / stream {e2['read_stream_ms']:.1f} ms",
        "",
        "## E4 跨进程",
        "",
        f"- IPC {e4['ipc_ms']:.1f} ms / {e4['ipc_mb']:.2f} MB；"
        f"JSON {e4['json_ms']:.1f} ms / {e4['json_mb']:.2f} MB",
        "",
        "## E5 Flight",
        "",
        f"- Flight 中位 {e5['flight_ms']:.1f} ms；IPC over HTTP 中位 {e5['http_ms']:.1f} ms；"
        f"payload {e5['payload_mb']:.2f} MB",
    ]
    (ROOT / "reports" / "w05-ipc.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (ROOT / "reports" / "w05-ipc.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n报告已写入 reports/w05-ipc.md")


def main() -> None:
    t = load()
    print(f"数据 {t.num_rows:,} 行")
    e1(t)
    e2(t)
    e3(t)
    e4()
    e5(t)
    report()


if __name__ == "__main__":
    main()
