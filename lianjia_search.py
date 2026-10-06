#!/usr/bin/env python3
"""Filter authorized housing exports and write a two-sheet Excel report."""

from __future__ import annotations

import argparse
import math
import re
from collections import Counter
from datetime import date
from pathlib import Path

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill


BASE_REQUIRED_COLUMNS = {
    "listing_id",
    "city",
    "district",
    "community_name",
    "total_price_wan",
    "has_elevator",
    "large_property",
    "status",
    "source",
    "observed_at",
}

LISTING_COLUMNS = {
    "listing_id": "房源编号",
    "city": "城市",
    "district": "行政区",
    "community_id": "小区编号",
    "community_name": "小区名称",
    "build_year": "建成年份",
    "build_date": "建成日期",
    "property_age_years": "楼龄(年)",
    "layout": "户型",
    "area_sqm": "建筑面积(㎡)",
    "total_price_wan": "总价(万元)",
    "unit_price_yuan_sqm": "单价(元/㎡)",
    "floor_desc": "楼层",
    "orientation": "朝向",
    "has_elevator": "有电梯",
    "house_nature": "房屋性质",
    "large_property": "大产权标记",
    "status": "状态",
    "source": "数据来源",
    "observed_at": "查询时间",
}

SUMMARY_COLUMNS = {
    "community_id": "小区编号",
    "community_name": "小区名称",
    "district": "行政区",
    "build_year_min": "最早建成年份",
    "build_year_max": "最晚建成年份",
    "matching_listings": "符合条件房源数",
    "min_total_price_wan": "最低总价(万元)",
    "median_total_price_wan": "总价中位数(万元)",
    "max_total_price_wan": "最高总价(万元)",
    "min_area_sqm": "最小面积(㎡)",
    "median_area_sqm": "面积中位数(㎡)",
    "max_area_sqm": "最大面积(㎡)",
}

TRUE_VALUES = {"1", "true", "yes", "y", "是", "有", "有电梯", "大产权"}
FALSE_VALUES = {"0", "false", "no", "n", "否", "无", "无电梯", "小产权"}
ON_SALE_VALUES = {"on_sale", "for_sale", "sale", "在售", "在售中"}


class InputError(ValueError):
    """Raised when the input export cannot be used safely."""


def parse_number(value: object) -> float:
    if value is None:
        return math.nan
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group()) if match else math.nan


def parse_bool(value: object) -> bool | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    return None


def normalize_place(value: object) -> str:
    return re.sub(r"[市区]$", "", str(value).strip())


def subtract_years(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def read_source(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    if suffix in {".xlsx", ".xlsm"}:
        return pd.read_excel(path, dtype=str, keep_default_na=False, engine="openpyxl")
    if suffix == ".json":
        return pd.read_json(path, dtype=str, convert_dates=False)
    raise InputError(f"不支持的输入格式: {suffix or '(无扩展名)'}；请使用 CSV、XLSX 或 JSON")


def validate_columns(frame: pd.DataFrame) -> None:
    missing = sorted(BASE_REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        raise InputError(f"缺少必要字段: {', '.join(missing)}")
    if "build_year" not in frame.columns and "build_date" not in frame.columns:
        raise InputError("缺少必要字段: build_year 或 build_date 至少需要一个")


def filter_listings(
    frame: pd.DataFrame,
    *,
    today: date,
    max_age_years: int,
    min_total_price: float,
    max_total_price: float,
    city: str,
    district: str,
) -> tuple[pd.DataFrame, Counter[str]]:
    validate_columns(frame)
    result = frame.copy()

    result["_total_price"] = result["total_price_wan"].map(parse_number)
    result["_area_sqm"] = result.get("area_sqm", pd.Series("", index=result.index)).map(
        parse_number
    )
    result["_build_year"] = result.get(
        "build_year", pd.Series("", index=result.index)
    ).map(parse_number)
    if "build_date" in result:
        result["_build_date"] = pd.to_datetime(result["build_date"], errors="coerce")
    else:
        result["_build_date"] = pd.NaT
    result["_has_elevator"] = result["has_elevator"].map(parse_bool)
    result["_large_property"] = result["large_property"].map(parse_bool)

    cutoff_date = subtract_years(today, max_age_years)
    known_build_date = result["_build_date"].notna() & (
        result["_build_date"] >= pd.Timestamp(cutoff_date)
    )
    known_year = (
        result["_build_date"].isna()
        & result["_build_year"].notna()
        & (result["_build_year"] >= cutoff_date.year)
    )

    checks = {
        "city": result["city"].map(normalize_place).eq(normalize_place(city)),
        "district": result["district"].map(normalize_place).eq(normalize_place(district)),
        "age": known_build_date | known_year,
        "price": result["_total_price"].between(
            min_total_price, max_total_price, inclusive="both"
        ),
        "elevator": result["_has_elevator"].eq(True),
        "large_property": result["_large_property"].eq(True),
        "on_sale": result["status"]
        .astype(str)
        .str.strip()
        .str.lower()
        .isin(ON_SALE_VALUES),
    }

    matched = pd.Series(True, index=result.index)
    stats: Counter[str] = Counter(input_rows=len(result))
    for name, check in checks.items():
        matched &= check
        stats[f"excluded_{name}"] = int((~check).sum())

    result["property_age_years"] = result.apply(
        lambda row: (
            today.year - int(row["_build_year"])
            if pd.notna(row["_build_year"])
            else today.year - row["_build_date"].year
            if pd.notna(row["_build_date"])
            else None
        ),
        axis=1,
    )
    stats["matched_rows"] = int(matched.sum())
    return result.loc[matched].copy(), stats


def build_community_summary(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)

    work = frame.copy()
    work["_community_key"] = work.get(
        "community_id", pd.Series("", index=work.index)
    ).astype(str).str.strip()
    work.loc[work["_community_key"].eq(""), "_community_key"] = work["community_name"]
    work["_community_id_output"] = work.get(
        "community_id", pd.Series("", index=work.index)
    ).astype(str).str.strip()

    summary = (
        work.groupby("_community_key", as_index=False)
        .agg(
            community_id=("_community_id_output", "first"),
            community_name=("community_name", "first"),
            district=("district", "first"),
            build_year_min=("_build_year", "min"),
            build_year_max=("_build_year", "max"),
            matching_listings=("listing_id", "count"),
            min_total_price_wan=("_total_price", "min"),
            median_total_price_wan=("_total_price", "median"),
            max_total_price_wan=("_total_price", "max"),
            min_area_sqm=("_area_sqm", "min"),
            median_area_sqm=("_area_sqm", "median"),
            max_area_sqm=("_area_sqm", "max"),
        )
        .drop(columns="_community_key")
    )
    summary["build_year_min"] = summary["build_year_min"].astype("Int64")
    summary["build_year_max"] = summary["build_year_max"].astype("Int64")
    return summary.sort_values("community_name", kind="stable").reset_index(drop=True)


def prepare_listing_output(frame: pd.DataFrame, today: date) -> pd.DataFrame:
    columns = [column for column in LISTING_COLUMNS if column in frame.columns]
    output = frame[columns].copy()
    output["has_elevator"] = output["has_elevator"].map(
        lambda value: "是" if parse_bool(value) is True else "否"
    )
    output["large_property"] = output["large_property"].map(
        lambda value: "是" if parse_bool(value) is True else "否"
    )
    output = output.rename(columns=LISTING_COLUMNS)
    sort_columns = [name for name in ("小区名称", "总价(万元)") if name in output]
    return output.sort_values(sort_columns, kind="stable").reset_index(drop=True)


def style_workbook(writer: pd.ExcelWriter) -> None:
    header_fill = PatternFill("solid", fgColor="D9EAF7")
    for sheet in writer.book.worksheets:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.font = Font(bold=True)
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
        for column in sheet.columns:
            width = min(
                40,
                max(len(str(cell.value or "")) for cell in column) + 2,
            )
            sheet.column_dimensions[column[0].column_letter].width = max(10, width)


def write_excel(
    listings: pd.DataFrame,
    communities: pd.DataFrame,
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        communities.to_excel(writer, sheet_name="小区汇总", index=False)
        listings.to_excel(writer, sheet_name="房源明细", index=False)
        writer.book.properties.title = "北京海淀二手房筛选结果"
        writer.book.properties.description = (
            "基于授权数据或手工整理数据的筛选结果；不代表未经来源确认的全量房源。"
        )
        style_workbook(writer)


def run_pipeline(
    input_path: Path,
    output_path: Path,
    *,
    today: date,
    max_age_years: int = 10,
    min_total_price: float = 300,
    max_total_price: float = 500,
    city: str = "北京",
    district: str = "海淀",
) -> Counter[str]:
    source = read_source(input_path)
    filtered, stats = filter_listings(
        source,
        today=today,
        max_age_years=max_age_years,
        min_total_price=min_total_price,
        max_total_price=max_total_price,
        city=city,
        district=district,
    )
    summary = build_community_summary(filtered)
    listing_output = prepare_listing_output(filtered, today)
    write_excel(listing_output, summary.rename(columns=SUMMARY_COLUMNS), output_path)
    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从授权导出或手工整理的数据中筛选北京海淀二手房并输出 Excel。"
    )
    parser.add_argument("input", type=Path, help="CSV、XLSX 或 JSON 输入文件")
    parser.add_argument("-o", "--output", type=Path, required=True, help="输出 XLSX 路径")
    parser.add_argument("--today", type=date.fromisoformat, default=date.today())
    parser.add_argument("--max-age-years", type=int, default=10)
    parser.add_argument("--min-total-price", type=float, default=300)
    parser.add_argument("--max-total-price", type=float, default=500)
    parser.add_argument("--city", default="北京")
    parser.add_argument("--district", default="海淀")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stats = run_pipeline(
        args.input,
        args.output,
        today=args.today,
        max_age_years=args.max_age_years,
        min_total_price=args.min_total_price,
        max_total_price=args.max_total_price,
        city=args.city,
        district=args.district,
    )
    print(
        "完成: "
        f"输入 {stats['input_rows']} 条，符合 {stats['matched_rows']} 条；"
        f"输出 {args.output}"
    )
    unknown_fields = (
        "age",
        "price",
        "elevator",
        "large_property",
        "on_sale",
    )
    for field in unknown_fields:
        count = stats[f"excluded_{field}"]
        if count:
            print(f"未通过 {field}: {count} 条")


if __name__ == "__main__":
    main()
