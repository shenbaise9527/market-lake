//! 页级剪枝探测：读 page index，算出"命中页 / 总页"和"命中页字节 / 列块字节"。
//!
//! 这是行组级 min/max 之下的第二级剪枝：行组内部的每一页都有自己的 min/max。
//! 注意：这里的数字是"读端若实现了页级剪枝，最多能省多少"，不是实际 IO。

use std::fs::File;

use anyhow::{bail, Result};
use parquet::file::metadata::{PageIndexPolicy, ParquetMetaDataReader};
use parquet::file::page_index::column_index::ColumnIndexMetaData;

pub fn run(path: &str, column: &str, lo: i64, hi: i64) -> Result<()> {
    let file = File::open(path)?;
    // 默认的元数据读取不加载 page index，必须显式要求
    let md = ParquetMetaDataReader::new()
        .with_page_index_policy(PageIndexPolicy::Required)
        .parse_and_finish(&file)?;

    println!(
        "{}  行组={}  页级统计列={}  区间=[{}, {})",
        path,
        md.num_row_groups(),
        column,
        lo,
        hi
    );

    let (mut tot_pages, mut hit_pages, mut tot_bytes, mut hit_bytes) = (0usize, 0usize, 0i64, 0i64);
    for rg in 0..md.num_row_groups() {
        let group = md.row_group(rg);
        let Some(col) = (0..group.num_columns()).find(|&c| group.column(c).column_path().string() == column)
        else {
            bail!("找不到列 {column}");
        };
        let idx = md.page_index_for_row_group(rg);
        let Some(ci) = idx.column_index(col) else {
            bail!("行组 #{rg} 的 {column} 没有 column index（写文件时没开 page index？）");
        };
        let ColumnIndexMetaData::INT64(ci) = ci else {
            bail!("这个示例只处理 INT64 列，{column} 不是 INT64");
        };
        let Some(locs) = idx.page_locations(col) else {
            bail!("行组 #{rg} 的 {column} 没有 offset index");
        };

        println!("\n── 行组 #{rg}  行数={}  页数={}", group.num_rows(), locs.len());
        println!("  页  行区间起       min                 max                 字节   命中");
        let mut rg_hit = 0usize;
        for (i, loc) in locs.iter().enumerate() {
            let (mn, mx) = (ci.min_value(i), ci.max_value(i));
            let hit = match (mn, mx) {
                (Some(a), Some(b)) => !(*b < lo || *a >= hi),
                _ => true,
            };
            if i < 6 || i + 2 >= locs.len() {
                println!(
                    "  {:>3} {:>10}  {:>17}  {:>17}  {:>7}  {}",
                    i,
                    loc.first_row_index,
                    mn.map(|v| v.to_string()).unwrap_or_default(),
                    mx.map(|v| v.to_string()).unwrap_or_default(),
                    loc.compressed_page_size,
                    if hit { "✓" } else { "×" }
                );
                if i == 6 && locs.len() > 8 {
                    println!("  ...");
                }
            }
            tot_pages += 1;
            tot_bytes += loc.compressed_page_size as i64;
            if hit {
                hit_pages += 1;
                hit_bytes += loc.compressed_page_size as i64;
                rg_hit += 1;
            }
        }
        println!("  行组内命中 {rg_hit}/{} 页", locs.len());
    }

    println!(
        "\n合计：命中 {hit_pages}/{tot_pages} 页，需读 {:.2} MB / 列块 {:.2} MB（{:.1}%）",
        hit_bytes as f64 / 1e6,
        tot_bytes as f64 / 1e6,
        hit_bytes as f64 * 100.0 / tot_bytes.max(1) as f64
    );
    Ok(())
}
