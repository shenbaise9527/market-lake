# 第 4 周 · Arrow 内存模型

**阶段** S2 ｜ **时长** 9h ｜ **前置** W1（W2 有帮助但不是必需）

## 本周目标

1. 能徒手画出 `Int64Array`、`StringArray`、`StructArray` 的 buffer 布局。
2. 手写一个 tick 二进制解析器，直接产出 `RecordBatch`。
3. 说清「零拷贝」省掉的是哪一步，以及它的代价。

## 阅读（约 3h）

- Arrow Columnar Format：*Physical Memory Layout*、*Validity Bitmaps*、*Variable-size Binary*、
  *Dictionary-encoded*、*Nested Types*
- arrow-rs：`Array`、`ArrayRef`、`RecordBatch`、`Buffer` 的文档（只看类型定义和 `slice`）
- 本仓库 `rust/src/reader.rs`（如何下转数组）

## 动手（约 5h）

1. **手写解析器**：定义一个定长二进制格式（32 字节一条：
   `symbol_id u32 | ts i64 | px f64 | vol i64 | side i8 | pad 3`），
   在 Rust 里逐条解析 → 构造 `RecordBatch` → 用 `ArrowWriter` 落盘。
   要求：不经过 CSV、不经过 `Vec<String>` 中转 symbol（用 dictionary 编码）。
2. **打印布局**：Python 侧手工构造同样内容的 batch，
   打印每个 array 的 `buffers()`、`offset`、`null_count`，把 values buffer 的前 32 字节打成 hex。
3. **validity bitmap 实验**：8 个元素、第 3 个为 null，打印 bitmap 的字节，
   验证「从低位往高位、1 表示有效」。
4. **slice 实验**：对 `StringArray` 做两次 slice，打印 offsets 的实际值，
   解释为什么 `to_numpy()` 会触发拷贝而 `slice` 不会。
5. **dictionary 实验**：`symbol` 列 100 万行 → 普通 `StringArray` vs `DictionaryArray<Int32>`，
   比较内存占用与聚合耗时。

## 验收

- 能手写（纸上）画出 `StringArray(["ab","c"])` 的三个 buffer 及其内容。
- 能解释 `BinaryArray` 的 2GB 硬上限来自哪里，以及 `LargeBinary` 如何绕过。
- 能说出 `slice` 之后哪些操作会退化成拷贝（`to_numpy`、`concat`、部分 kernel）。
- `uv run python py/verify.py --week 4` 全绿（沿用 W1 的数据项）。

## 常见坑

- validity bitmap 的第 0 位对应第 0 个元素，1 = 有效，别记反。
- `offset` 是元素单位，不是字节单位；slice 后的 array 不能直接 `from_raw_parts`。
- 长期持有 slice 会拖住整个 buffer，内存看着不释放——需要周期性 `compact`。
- `DictionaryArray` 的 values 必须在所有 batch 间一致，否则跨 batch 拼接会失真
  （这是流式写入时最常见的坑）。

## 交付物

- `rust/src/parse_fixed.rs`：定长二进制 → RecordBatch
- `notes/week04.md`：三张手绘布局图（可拍照）+ 内存对比表
