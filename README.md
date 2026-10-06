# 海淀二手房筛选

这个工具处理**已获授权导出或手工整理**的房源数据，按以下条件筛选并输出 Excel：

- 城市：北京
- 行政区：海淀
- 楼龄：10 年以内
- 总价：300 万至 500 万元，包含边界
- 电梯：房源级字段明确为有电梯
- 大产权：`large_property` 字段明确为是或大产权
- 状态：在售

它不会访问链家网站、读取 Chrome 登录态、处理验证码、轮换代理或调用未授权接口。贝壳官方 `beike-ai-platform` CLI 仅限授权用户使用；使用前请确认许可范围，并将允许保存的结果导出为 CSV、XLSX 或 JSON。

## 运行

```powershell
python -m pip install -r requirements.txt
python lianjia_search.py examples/listings-template.csv -o output/haidingian.xlsx
```

精确复现核验口径时，可以显式指定日期：

```powershell
python lianjia_search.py examples/listings-template.csv -o output/haidingian.xlsx --today 2026-10-06
```

## 输入字段

必要字段：

| 字段 | 说明 |
|---|---|
| `listing_id` | 房源编号或本地稳定编号 |
| `city` | 城市，例如 `北京` |
| `district` | 行政区，例如 `海淀区` |
| `community_name` | 小区名称 |
| `total_price_wan` | 总价，单位万元 |
| `has_elevator` | 房源是否有电梯 |
| `large_property` | 是否明确属于大产权，不从房屋性质推断 |
| `status` | `在售` 或 `on_sale` |
| `build_year` 或 `build_date` | 建成年份或建成日期，至少一个 |
| `source` | 数据来源或导出批次 |
| `observed_at` | 数据查询或导出时间 |

建议同时提供：

`community_id`、`layout`、`area_sqm`、`unit_price_yuan_sqm`、`floor_desc`、`orientation`、`house_nature`。

布尔字段可使用 `是/否`、`有/无`、`大产权/小产权`、`true/false` 或 `1/0`。空值不会被当成符合条件。

## 输出

生成的 XLSX 包含两个 Sheet：

- `小区汇总`：每个符合条件的小区及其房源数、价格区间、面积区间。
- `房源明细`：所有符合条件房源，按小区和总价排序。

输出只代表输入来源在查询时可见的数据，不代表未经来源确认的全量房源。

## 验证

```powershell
python -m unittest -v
```

测试覆盖 300/500 万边界、2016 年楼龄边界、未知字段排除、区县/在售过滤和 Excel 双 Sheet 输出。
