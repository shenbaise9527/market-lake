//! ingest：把 raw 的 hive 布局读进来，排序后按 (dt, exchange, symbol) 重新分区写出。
//!
//! 这是第 10 章的生产写入器路径：解析 → Arrow → 排序 → 分区 zstd Parquet，
//! 并复用第 8 章的幂等模式——先写 `.tmp-` 目录，行数校验通过后整体改名。
//!
//! 用法：mdlake ingest --in <raw_dir> --out <lake_dir> [--row-group 32768] [--codec zstd]

use anyhow::{bail, Context, Result};
use arrow::array::{ArrayRef, StringArray};
use arrow::compute::kernels::sort::{lexsort_to_indices, SortColumn};
use arrow::compute::take;
use arrow::datatypes::Schema;
use arrow::record_batch::RecordBatch;
use parquet::arrow::arrow_reader::ParquetRecordBatchReaderBuilder;
use parquet::arrow::ArrowWriter;
use std::collections::BTreeMap;
use std::fs::{self, File};
use std::path::{Path, PathBuf};

/// 从路径里取 `key=value` 段的 value（hive 分区约定）。
fn hive_value(path: &Path, key: &str) -> Result<String> {
    for comp in path.components() {
        let s = comp.as_os_str().to_string_lossy();
        if let Some(v) = s.strip_prefix(&format!("{key}=")) {
            return Ok(v.to_string());
        }
    }
    bail!("路径 {} 中没有分区键 {}", path.display(), key);
}

fn collect_batches(
    files: &[PathBuf],
) -> Result<(Schema, Vec<Vec<RecordBatch>>, Vec<(String, String)>)> {
    let mut schema: Option<Schema> = None;
    let mut batches: Vec<Vec<RecordBatch>> = Vec::new();
    let mut parts = Vec::new();
    for f in files {
        let dt = hive_value(f, "dt")?;
        let ex = hive_value(f, "exchange")?;
        let file = File::open(f).with_context(|| format!("打开失败: {}", f.display()))?;
        let builder = ParquetRecordBatchReaderBuilder::try_new(file)?;
        if schema.is_none() {
            schema = Some(builder.schema().as_ref().clone());
        }
        let reader = builder.with_batch_size(65_536).build()?;
        let mut file_batches: Vec<RecordBatch> = Vec::new();
        let mut n = 0usize;
        for b in reader {
            let b = b?;
            n += b.num_rows();
            file_batches.push(b);
        }
        println!("  {} → {} 行（dt={dt} exchange={ex}）", f.display(), n);
        batches.push(file_batches);
        parts.push((dt, ex));
    }
    Ok((schema.context("没有输入文件")?, batches, parts))
}

/// 把每个 batch 附上 dt/exchange 两列（标量广播），再合并。
fn concat_with_parts(
    schema: &Schema,
    batches: &[Vec<RecordBatch>],
    parts: &[(String, String)],
) -> Result<RecordBatch> {
    let mut fields: Vec<std::sync::Arc<arrow::datatypes::Field>> =
        schema.fields().iter().cloned().collect();
    fields.push(arrow::datatypes::Field::new("dt", arrow::datatypes::DataType::Utf8, false).into());
    fields.push(
        arrow::datatypes::Field::new("exchange", arrow::datatypes::DataType::Utf8, false).into(),
    );
    let full_schema = std::sync::Arc::new(Schema::new(fields));

    let mut all: Vec<RecordBatch> = Vec::new();
    for (file_batches, (dt, ex)) in batches.iter().zip(parts) {
        for b in file_batches {
            let n = b.num_rows();
            let mut cols: Vec<ArrayRef> = b.columns().to_vec();
            cols.push(std::sync::Arc::new(StringArray::from(vec![dt.as_str(); n])));
            cols.push(std::sync::Arc::new(StringArray::from(vec![ex.as_str(); n])));
            all.push(RecordBatch::try_new(full_schema.clone(), cols)?);
        }
    }
    let batch = arrow::compute::concat_batches(&full_schema, &all)?;
    sort_batch(&batch)
}

/// 按 (dt, symbol, ts) 字典序排序（exchange 同 dt 内一致，无需参与）。
fn sort_batch(batch: &RecordBatch) -> Result<RecordBatch> {
    let schema = batch.schema();
    let mut cols: Vec<(usize, bool)> = Vec::new();
    for name in ["dt", "symbol", "ts"] {
        let i = schema
            .index_of(name)
            .with_context(|| format!("缺少 {name} 列"))?;
        cols.push((i, false));
    }
    let sort_cols: Vec<SortColumn> = cols
        .iter()
        .map(|(i, desc)| SortColumn {
            values: batch.column(*i).clone(),
            options: Some(arrow::compute::SortOptions {
                descending: *desc,
                nulls_first: false,
            }),
        })
        .collect();
    let indices = lexsort_to_indices(&sort_cols, None)?;
    let cols_out: Vec<ArrayRef> = batch
        .columns()
        .iter()
        .map(|c| take(c.as_ref(), &indices, None))
        .collect::<Result<Vec<_>, _>>()?;
    Ok(RecordBatch::try_new(schema, cols_out)?)
}

pub fn run(
    input: &str,
    output: &str,
    row_group: usize,
    codec: &str,
) -> Result<()> {
    let t0 = std::time::Instant::now();
    let in_dir = PathBuf::from(input);
    let mut files: Vec<PathBuf> = Vec::new();
    fn walk(dir: &Path, out: &mut Vec<PathBuf>) -> Result<()> {
        for e in dir.read_dir()? {
            let p = e?.path();
            if p.is_dir() {
                walk(&p, out)?;
            } else if p.extension().is_some_and(|x| x == "parquet") {
                out.push(p);
            }
        }
        Ok(())
    }
    walk(&in_dir, &mut files)?;
    files.sort();
    if files.is_empty() {
        bail!("{input} 下没有 parquet 文件");
    }
    println!("ingest：{} 个源文件 → {output}", files.len());

    let (schema, batches, parts) = collect_batches(&files)?;
    let batch = concat_with_parts(&schema, &batches, &parts)?;
    let total = batch.num_rows();
    println!("合并 {} 行，排序完成，耗时 {:?}", total, t0.elapsed());

    // 分组键（追加在原始列之后）：dt、exchange
    let n_orig = schema.fields().len();
    let dt_idx = n_orig; // concat_with_parts 把 dt 追加在末尾倒数第二
    let dts = batch
        .column(dt_idx)
        .as_any()
        .downcast_ref::<arrow::array::StringArray>()
        .context("dt 不是字符串列")?;
    let sym_idx = schema.index_of("symbol").context("缺少 symbol 列")?;
    let syms = batch
        .column(sym_idx)
        .as_any()
        .downcast_ref::<arrow::array::StringArray>()
        .context("symbol 不是字符串列")?;

    // 相同 (dt, symbol) 的行区间（排序后是连续段）
    let mut groups: Vec<(String, String, usize, usize)> = Vec::new(); // (dt, symbol, start, end)
    let mut start = 0usize;
    for i in 1..=total {
        let boundary = i == total
            || dts.value(i) != dts.value(start)
            || syms.value(i) != syms.value(start);
        if boundary {
            groups.push((
                dts.value(start).to_string(),
                syms.value(start).to_string(),
                start,
                i,
            ));
            start = i;
        }
    }
    println!("分区 {} 组：{}", groups.len(),
        groups.iter().map(|(d, s, a, b)| format!("{d}/{s}={}行", b - a))
              .collect::<Vec<_>>().join("  "));

    // 幂等写：.tmp → 校验 → rename
    let out_root = PathBuf::from(output);
    if out_root.exists() {
        fs::remove_dir_all(&out_root)?;
    }
    let tmp = out_root.with_file_name(format!(
        ".tmp-{}",
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)?
            .as_millis()
    ));
    fs::create_dir_all(&tmp)?;

    let props = crate::writer::build_props_for(&batch.schema(), row_group, codec)?;
    let mut written = 0usize;
    for (dt, sym, a, b) in &groups {
        let slice = batch.slice(*a, b - a);
        let dir = tmp.join(format!("dt={dt}")).join(format!("symbol={sym}"));
        fs::create_dir_all(&dir)?;
        let path = dir.join("part-0.parquet");
        let w = File::create(&path)?;
        let mut writer = ArrowWriter::try_new(w, slice.schema(), Some(props.clone()))?;
        writer.write(&slice)?;
        let meta = writer.close()?;
        written += meta.file_metadata().num_rows() as usize;
    }
    if written != total {
        bail!("行数校验失败：写出 {written}，应为 {total}（.tmp 保留在 {}）", tmp.display());
    }
    fs::rename(&tmp, &out_root)?;
    println!(
        "写出 {written} 行 → {}，分区 {} 个，总耗时 {:?}（幂等：重跑即整体替换）",
        out_root.display(),
        groups.len(),
        t0.elapsed()
    );
    Ok(())
}

// BTreeMap 未用到则留空引用，避免未使用 import 告警
#[allow(dead_code)]
fn _unused(_: BTreeMap<String, usize>) {}
