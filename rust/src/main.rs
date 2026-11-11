mod ingest;
mod inspect;
mod pages;
mod reader;
mod writer;

use clap::{Parser, Subcommand};

#[derive(Parser)]
#[command(name = "mdlake", about = "行情数据湖工具（Arrow + Parquet）")]
struct Cli {
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// 打印 Parquet 文件的物理结构：行组、列块、编码、压缩、统计信息
    Inspect {
        file: String,
        /// 只打印前 N 个行组的明细
        #[arg(long, default_value_t = 3)]
        head: usize,
    },
    /// 读出、按 (symbol, ts) 排序、按指定参数重写为 Parquet
    Rewrite {
        #[arg(long)]
        r#in: String,
        #[arg(long)]
        out: String,
        #[arg(long, default_value_t = 65536)]
        row_group: usize,
        #[arg(long, default_value = "zstd")]
        codec: String,
        /// 价格列编码：plain | bss（BYTE_STREAM_SPLIT）| delta
        #[arg(long, default_value = "bss")]
        price_encoding: String,
        /// 是否按 (symbol, ts) 排序后再写
        #[arg(long, default_value_t = true)]
        sort: bool,
    },
    /// 页级剪枝探测：读 page index，算命中页与需读字节
    Pages {
        file: String,
        #[arg(long, default_value = "ts")]
        column: String,
        #[arg(long)]
        lo: i64,
        #[arg(long)]
        hi: i64,
    },
    /// 生产写入路径：读 raw 布局 → 排序 → 按 (dt, symbol) 分区写出（幂等）
    Ingest {
        #[arg(long)]
        r#in: String,
        #[arg(long)]
        out: String,
        #[arg(long, default_value_t = 32768)]
        row_group: usize,
        #[arg(long, default_value = "zstd")]
        codec: String,
    },
    /// 扫描统计：按 symbol 过滤后的行数、价格区间、时间区间
    Scan {
        file: String,
        #[arg(long)]
        symbol: Option<String>,
    },
}

fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();
    match cli.cmd {
        Cmd::Inspect { file, head } => inspect::run(&file, head),
        Cmd::Rewrite {
            r#in,
            out,
            row_group,
            codec,
            price_encoding,
            sort,
        } => writer::run(&r#in, &out, row_group, &codec, &price_encoding, sort),
        Cmd::Pages { file, column, lo, hi } => pages::run(&file, &column, lo, hi),
        Cmd::Ingest { r#in, out, row_group, codec } => {
            ingest::run(&r#in, &out, row_group, &codec)
        }
        Cmd::Scan { file, symbol } => reader::run(&file, symbol.as_deref()),
    }
}
