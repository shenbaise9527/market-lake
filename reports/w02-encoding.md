# 第 2 章实验：编码组合对体积的影响

源文件：`part-600267.SH.parquet` · 73,375 行 · 7 列 · zstd(3) · 单个行组

| 方案 | 说明 | 文件体积 | 相对 A | ts | seq | px | vol |
|---|---|---|---|---|---|---|---|
| a-default | 全默认（每列都开字典） | 996.7 KB | 1.00x | 614.5 | 210.8 | 97.4 | 42.2 |
| b-delta | ts/seq 换 DELTA_BINARY_PACKED | 454.2 KB | 0.46x | 279.1 | 3.8 | 97.4 | 42.2 |
| c-delta-bss | 再加 px 换 BYTE_STREAM_SPLIT | 683.3 KB | 0.69x | 279.1 | 3.8 | 326.5 | 42.2 |
| d-plain | 再关全部字典（全 PLAIN+DICT 对比） | 756.3 KB | 0.76x | 279.1 | 3.8 | 326.5 | 73.6 |
| e-pageindex | C 的配置 + page index | 683.4 KB | 0.69x | 278.9 | 3.7 | 326.3 | 42.0 |
| f-shuffled-delta | **行序打乱** + B 的编码 | 753.5 KB | 0.76x | 414.4 | 160.5 | 102.8 | 44.0 |
| g-shuffled-dict | **行序打乱** + 全默认字典 | 1147.2 KB | 1.15x | 631.9 | 336.7 | 102.8 | 44.0 |

## 各方案的列编码

**a-default**：`symbol`=PLAIN+RLE+RLE_DICTIONARY；`ts`=PLAIN+RLE+RLE_DICTIONARY；`seq`=PLAIN+RLE+RLE_DICTIONARY；`px`=PLAIN+RLE+RLE_DICTIONARY

**b-delta**：`symbol`=PLAIN+RLE+RLE_DICTIONARY；`ts`=DELTA_BINARY_PACKED+RLE；`seq`=DELTA_BINARY_PACKED+RLE；`px`=PLAIN+RLE+RLE_DICTIONARY

**c-delta-bss**：`symbol`=PLAIN+RLE+RLE_DICTIONARY；`ts`=DELTA_BINARY_PACKED+RLE；`seq`=DELTA_BINARY_PACKED+RLE；`px`=BYTE_STREAM_SPLIT+RLE

**d-plain**：`symbol`=PLAIN+RLE；`ts`=DELTA_BINARY_PACKED+RLE；`seq`=DELTA_BINARY_PACKED+RLE；`px`=BYTE_STREAM_SPLIT+RLE

**e-pageindex**：`symbol`=PLAIN+RLE+RLE_DICTIONARY；`ts`=DELTA_BINARY_PACKED+RLE；`seq`=DELTA_BINARY_PACKED+RLE；`px`=BYTE_STREAM_SPLIT+RLE

**f-shuffled-delta**：`symbol`=PLAIN+RLE+RLE_DICTIONARY；`ts`=DELTA_BINARY_PACKED+RLE；`seq`=DELTA_BINARY_PACKED+RLE；`px`=PLAIN+RLE+RLE_DICTIONARY

**g-shuffled-dict**：`symbol`=PLAIN+RLE+RLE_DICTIONARY；`ts`=PLAIN+RLE+RLE_DICTIONARY；`seq`=PLAIN+RLE+RLE_DICTIONARY；`px`=PLAIN+RLE+RLE_DICTIONARY

