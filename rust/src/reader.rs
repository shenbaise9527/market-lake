//! 扫描统计：演示 Arrow 侧的类型下转与逐 batch 计算。
//!
//! 故意不用 SQL、不用 DataFrame，只用 Arrow kernel 和数组下转，
//! 为的是让你看清"引擎之下到底发生了什么"。

use anyhow::{Context, Result};
use arrow::array::{Array, Float64Array, Int64Array, StringArray};
use parquet::arrow::arrow_reader::ParquetRecordBatchReaderBuilder;
use std::fs::File;

pub fn run(path: &str, symbol: Option<&str>) -> Result<()> {
    let t0 = std::time::Instant::now();
    let f = File::open(path).with_context(|| format!("打开失败: {path}"))?;
    let builder = ParquetRecordBatchReaderBuilder::try_new(f)?;
    let schema = builder.schema().clone();
    let reader = builder.with_batch_size(65536).build()?;

    let sym_idx = schema.index_of("symbol")?;
    let ts_idx = schema.index_of("ts")?;
    let px_idx = schema.index_of("px").or_else(|_| schema.index_of("price"))?;

    let mut rows = 0u64;
    let mut matched = 0u64;
    let mut ts_min = i64::MAX;
    let mut ts_max = i64::MIN;
    let mut px_min = f64::MAX;
    let mut px_max = f64::MIN;
    let mut batches = 0u64;

    for b in reader {
        let batch = b?;
        batches += 1;
        rows += batch.num_rows() as u64;

        let sym = batch
            .column(sym_idx)
            .as_any()
            .downcast_ref::<StringArray>()
            .context("symbol 不是 Utf8")?;
        let ts = batch
            .column(ts_idx)
            .as_any()
            .downcast_ref::<Int64Array>()
            .context("ts 不是 Int64")?;
        let px = batch
            .column(px_idx)
            .as_any()
            .downcast_ref::<Float64Array>()
            .context("px 不是 Float64")?;

        for i in 0..batch.num_rows() {
            if let Some(want) = symbol {
                if sym.value(i) != want {
                    continue;
                }
            }
            matched += 1;
            ts_min = ts_min.min(ts.value(i));
            ts_max = ts_max.max(ts.value(i));
            let p = px.value(i);
            if p.is_finite() {
                px_min = px_min.min(p);
                px_max = px_max.max(p);
            }
        }
    }

    println!("文件: {path}");
    println!("batch 数: {batches}  总行数: {rows}  命中: {matched}");
    if matched > 0 {
        println!("ts 区间: [{ts_min}, {ts_max}]");
        println!("px 区间: [{px_min:.4}, {px_max:.4}]");
    }
    println!("耗时: {:?}", t0.elapsed());
    Ok(())
}
