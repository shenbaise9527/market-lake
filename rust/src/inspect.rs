//! 打印 Parquet 文件的物理结构。
//!
//! 这个子命令是整个学习计划里最该反复用的工具：它把"行组 / 列块 / 编码 /
//! 压缩 / 统计信息"从抽象概念变成你数据上的真实数字。

use anyhow::Result;
use parquet::basic::{Compression, Encoding};
use parquet::file::reader::{FileReader, SerializedFileReader};
use parquet::file::statistics::Statistics;
use std::fs::File;

pub fn run(path: &str, head: usize) -> Result<()> {
    let file = File::open(path)?;
    let reader = SerializedFileReader::new(file)?;
    let meta = reader.metadata();
    let fm = meta.file_metadata();

    println!("文件: {path}");
    println!("格式版本: {}", fm.version());
    println!("写入器: {}", fm.created_by().unwrap_or("unknown"));
    println!("总行数: {}", fm.num_rows());
    println!("行组数: {}", meta.num_row_groups());
    println!("顶层列数: {}", fm.schema_descr().num_columns());
    println!("文件大小: {} 字节", std::fs::metadata(path)?.len());
    println!();

    let mut total_compressed = 0i64;
    let mut total_uncompressed = 0i64;

    for rg in 0..meta.num_row_groups() {
        let rgm = meta.row_group(rg);
        println!(
            "── 行组 #{rg}  行数={}  压缩后={}  原始={}",
            rgm.num_rows(),
            rgm.compressed_size(),
            rgm.total_byte_size()
        );
        total_compressed += rgm.compressed_size() as i64;
        total_uncompressed += rgm.total_byte_size() as i64;

        if rg >= head {
            println!("   （其余行组省略，用 --head 调整）");
            continue;
        }
        for c in 0..rgm.num_columns() {
            let cc = rgm.column(c);
            println!(
                "  col{:>2} {:<22} {:<12} enc=[{}] dict={} rows={} csize={}",
                c,
                cc.column_path().to_string(),
                compression_name(cc.compression()),
                encodings(cc.encodings()),
                cc.dictionary_page_offset().is_some(),
                cc.num_values(),
                cc.compressed_size(),
            );
            if let Some(stats) = cc.statistics() {
                println!("        stats: {}", fmt_stats(stats));
            }
        }
        println!();
    }

    let ratio = if total_compressed > 0 {
        total_uncompressed as f64 / total_compressed as f64
    } else {
        0.0
    };
    println!(
        "总压缩比: {ratio:.2}x  压缩后={total_compressed} 原始={total_uncompressed}"
    );
    Ok(())
}

fn compression_name(c: Compression) -> &'static str {
    match c {
        Compression::UNCOMPRESSED => "none",
        Compression::SNAPPY => "snappy",
        Compression::GZIP(_) => "gzip",
        Compression::LZO => "lzo",
        Compression::BROTLI(_) => "brotli",
        Compression::LZ4 => "lz4",
        Compression::ZSTD(_) => "zstd",
        _ => "other",
    }
}

fn encodings(v: impl Iterator<Item = Encoding>) -> String {
    v.map(|e| match e {
            Encoding::PLAIN => "PLAIN",
            Encoding::PLAIN_DICTIONARY => "PLAIN_DICT",
            Encoding::RLE => "RLE",
            Encoding::DELTA_BINARY_PACKED => "DELTA_BP",
            Encoding::DELTA_LENGTH_BYTE_ARRAY => "DELTA_LEN",
            Encoding::DELTA_BYTE_ARRAY => "DELTA_BA",
            Encoding::RLE_DICTIONARY => "RLE_DICT",
            Encoding::BYTE_STREAM_SPLIT => "BYTE_STREAM_SPLIT",
            _ => "OTHER",
        })
        .collect::<Vec<_>>()
        .join(",")
}

fn fmt_stats(s: &Statistics) -> String {
    match s {
        Statistics::Boolean(v) => format_opt(v.min_opt(), v.max_opt(), v.null_count_opt()),
        Statistics::Int32(v) => format_opt(v.min_opt(), v.max_opt(), v.null_count_opt()),
        Statistics::Int64(v) => format_opt(v.min_opt(), v.max_opt(), v.null_count_opt()),
        Statistics::Float(v) => format_opt(v.min_opt(), v.max_opt(), v.null_count_opt()),
        Statistics::Double(v) => format_opt(v.min_opt(), v.max_opt(), v.null_count_opt()),
        other => format!("{other:?}"),
    }
}

fn format_opt<T: std::fmt::Debug>(min: Option<&T>, max: Option<&T>, nulls: Option<u64>) -> String {
    format!("min={min:?} max={max:?} nulls={nulls:?}")
}
