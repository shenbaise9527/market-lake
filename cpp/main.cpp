// 单遍顺序回放：Parquet 流式读取 → 分钟 bar → 双均线信号。
//
// 这是第 10 章的 C++ 交付：回测引擎最苛刻的访问模式——
// 只前进、不回头（零随机 IO），内存占用恒定（与数据量无关）。
//
// 用法: mdlake_cpp <file.parquet> [fast=20] [slow=60]
//
// 三个 Arrow C++ 惯用法（见 cpp/README.md）：
//   1. Status / Result<T> 的错误处理（没有异常）
//   2. 所有对象都是 shared_ptr，内存由 MemoryPool 管
//   3. 逐 batch 流式读取，只投影需要的列（ts / px）

#include <arrow/api.h>
#include <arrow/io/api.h>
#include <parquet/arrow/reader.h>

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <deque>
#include <string>
#include <vector>

#define CHECK_OK(expr)                                                   \
  do {                                                                   \
    auto& _st = (expr);                                                   \
    if (!_st.ok()) {                                                     \
      std::fprintf(stderr, "%s:%d %s\n", __FILE__, __LINE__,             \
                   _st.status().ToString().c_str());                     \
      return 1;                                                          \
    }                                                                    \
  } while (0)

int main(int argc, char** argv) {
  if (argc < 2) {
    std::fprintf(stderr, "用法: mdlake_cpp <file.parquet> [fast=20] [slow=60]\n");
    return 2;
  }
  const std::string path = argv[1];
  const int fast_n = argc > 2 ? std::atoi(argv[2]) : 20;
  const int slow_n = argc > 3 ? std::atoi(argv[3]) : 60;
  if (fast_n <= 0 || slow_n <= fast_n) {
    std::fprintf(stderr, "需要 0 < fast < slow\n");
    return 2;
  }

  const auto t0 = std::chrono::steady_clock::now();

  auto infile = arrow::io::ReadableFile::Open(path);
  CHECK_OK(infile);

  auto maybe_reader = parquet::arrow::OpenFile(infile.ValueOrDie(),
                                               arrow::default_memory_pool());
  CHECK_OK(maybe_reader);
  auto reader = std::move(maybe_reader).ValueOrDie();

  // 只投影 2 列：ts 与 px。lake 文件按 (symbol, ts) 排序、单合约单文件。
  int ts_idx = -1, px_idx = -1;
  std::shared_ptr<arrow::Schema> schema;
  auto st_schema = reader->GetSchema(&schema);
  if (!st_schema.ok()) {
    std::fprintf(stderr, "GetSchema 失败: %s\n", st_schema.ToString().c_str());
    return 1;
  }
  for (int i = 0; i < schema->num_fields(); ++i) {
    if (schema->field(i)->name() == "ts") ts_idx = i;
    if (schema->field(i)->name() == "px") px_idx = i;
  }
  if (ts_idx < 0 || px_idx < 0) {
    std::fprintf(stderr, "文件缺少 ts/px 列\n");
    return 1;
  }
  const int num_rg = reader->num_row_groups();
  std::vector<int> rgs(num_rg);
  for (int i = 0; i < num_rg; ++i) rgs[i] = i;

  auto maybe_batches = reader->GetRecordBatchReader(rgs, {ts_idx, px_idx});
  CHECK_OK(maybe_batches);
  auto batches = std::move(*maybe_batches);

  // ---- 单遍状态机：tick → 分钟 bar → SMA(fast) / SMA(slow) → 交叉 ----
  int64_t cur_minute = -1;
  double cur_close = 0.0;
  bool started = false;
  int64_t bars = 0, ticks = 0;
  std::deque<double> fq, sq;
  double fsum = 0.0, ssum = 0.0;
  bool prev_above = false, have_prev = false;
  int pos = 0, entries = 0;
  double entry = 0.0, pnl = 0.0, last_close = 0.0;

  auto on_bar = [&](double c) {
    bars++;
    last_close = c;
    fq.push_back(c); fsum += c;
    if (static_cast<int>(fq.size()) > fast_n) { fsum -= fq.front(); fq.pop_front(); }
    sq.push_back(c); ssum += c;
    if (static_cast<int>(sq.size()) > slow_n) { ssum -= sq.front(); sq.pop_front(); }
    if (static_cast<int>(sq.size()) == slow_n) {
      const double f = fsum / fast_n, s = ssum / slow_n;
      const bool above = f > s;
      if (have_prev && above != prev_above) {
        if (above && pos == 0) { pos = 1; entry = c; entries++; }
        else if (!above && pos == 1) { pnl += c - entry; pos = 0; }
      }
      prev_above = above;
      have_prev = true;
    }
  };

  while (true) {
    auto maybe_batch = batches->Next();
    CHECK_OK(maybe_batch);
    auto batch = *maybe_batch;
    if (batch == nullptr) break;  // 流结束
    auto ts = std::static_pointer_cast<arrow::Int64Array>(batch->GetColumnByName("ts"));
    auto px = std::static_pointer_cast<arrow::DoubleArray>(batch->GetColumnByName("px"));
    if (ts == nullptr || px == nullptr) {
      std::fprintf(stderr, "列类型不符\n");
      return 1;
    }
    for (int64_t r = 0; r < batch->num_rows(); ++r) {
      if (ts->IsNull(r) || px->IsNull(r)) continue;
      const int64_t minute = ts->Value(r) / 60000000000LL;
      const double p = px->Value(r);
      ticks++;
      if (minute != cur_minute) {
        if (started) on_bar(cur_close);  // 上一根 bar 在这里收盘
        cur_minute = minute;
        started = true;
      }
      cur_close = p;  // 分钟内最后一笔为 close
    }
  }
  if (started) on_bar(cur_close);
  if (pos == 1) pnl += last_close - entry;  // 期末平仓

  const double ms =
      std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();

  std::printf("文件          : %s\n", path.c_str());
  std::printf("行组          : %d（逐组流式，只读 ts/px 两列）\n", num_rg);
  std::printf("tick          : %lld\n", static_cast<long long>(ticks));
  std::printf("分钟 bar      : %lld\n", static_cast<long long>(bars));
  std::printf("SMA 参数      : fast=%d slow=%d\n", fast_n, slow_n);
  std::printf("开仓次数      : %d（金叉开多、死叉平仓）\n", entries);
  std::printf("点数收益      : %.2f（close-to-close，期末自动平仓）\n", pnl);
  std::printf("回放耗时      : %.2f ms\n", ms);
  std::printf("内存          : 恒定 —— 状态只有两个 SMA 滑窗与当前 bar，与 %lld tick 无关\n",
              static_cast<long long>(ticks));
  return 0;
}
