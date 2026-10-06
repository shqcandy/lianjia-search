#!/usr/bin/env python3
"""Collect official Beike search results by recursively partitioning price."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import time
from collections import deque
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from lianjia_search import run_pipeline


CLI_PATH = Path(__file__).resolve().parent / "tools" / "beike" / "beike.exe"
OBSERVED_DATE = date.today().isoformat()
MAX_AGE_CUTOFF_TEXT = "2000年1月1日之后"
MAX_AGE_CUTOFF_ISO = "2000-01-01T00:00:00"
PRICE_BINS = (
    (10, 150),
    (150, 200),
    (200, 225),
    (225, 250),
    (250, 275),
    (275, 300),
    (300, 325),
    (325, 350),
)

OUTPUT_COLUMNS = [
    "listing_id",
    "city",
    "district",
    "community_id",
    "community_name",
    "build_year",
    "build_date",
    "layout",
    "area_sqm",
    "total_price_wan",
    "unit_price_yuan_sqm",
    "floor_desc",
    "orientation",
    "has_elevator",
    "house_nature",
    "large_property",
    "status",
    "source",
    "observed_at",
]


class CollectionError(RuntimeError):
    """Raised when official results cannot be collected safely."""


def number(value: str) -> float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
    return float(match.group()) if match else None


def run_search(district: str, low: float, high: float, layout: str = "") -> str:
    key = os.environ.get("BEIKE_MCP_API_KEY")
    if not key:
        raise CollectionError("BEIKE_MCP_API_KEY 未注入当前查询进程")

    query = (
        f"{district} 建成时间{MAX_AGE_CUTOFF_TEXT} "
        f"总价在{low:g}万到{high:g}万之间 有电梯 商品房 70年产权"
    )
    if layout:
        query += f" {layout}"
    command = [
        str(CLI_PATH),
        "buy",
        "search",
        "-c",
        "北京",
        "-q",
        query,
        "--house-type",
        "second",
        "--json",
    ]
    completed = subprocess.run(
        command,
        cwd=CLI_PATH.parents[2],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode:
        raise CollectionError(completed.stderr.strip() or completed.stdout.strip())
    if "service temporarily unavailable" in completed.stdout.lower():
        raise CollectionError("贝壳官方服务暂时不可用，已停止本轮查询")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise CollectionError(
            f"贝壳 CLI 未返回 JSON: {completed.stdout.strip() or completed.stderr.strip()}"
        ) from error
    if not payload.get("ok"):
        detail = payload.get("error") or payload.get("message") or payload
        raise CollectionError(f"贝壳 CLI 返回 ok=false: {detail}")
    return payload["data"]


def parse_exact_conditions(
    text: str,
    district: str,
    low: float,
    high: float,
    layout: str = "",
) -> tuple[int, float, float]:
    match = re.search(
        r"【本次实际命中的检索条件】\s*(\[.*?\])\s*\n+【检索概览】",
        text,
        re.S,
    )
    if not match:
        raise CollectionError("响应中缺少实际检索条件")
    conditions = json.loads(match.group(1))
    actual = {item["查询条件"]: item["检索条件"] for item in conditions}

    required = {
        "district": f"城区 = {district}",
        "date": f"建成时间 > {MAX_AGE_CUTOFF_ISO}",
        "elevator": "是否有电梯 = 是",
        "property": "房产权属 = 商品房",
        "property_years": "产权年限 between [65.0,75.0]",
        "status": "贝壳售卖状态 = 房源在售",
    }
    for name, expected in required.items():
        if expected not in actual.values():
            raise CollectionError(f"{name} 条件没有被精确覆盖: {expected}")
    if layout and not any(
        "户型" in value or "卧室" in value or "居" in value
        for value in actual.values()
    ):
        raise CollectionError(f"户型条件没有被覆盖: {layout}")

    price_condition = next(
        (value for value in actual.values() if value.startswith("卖房总价 between [")),
        "",
    )
    price_match = re.search(r"\[([\d.]+),([\d.]+)\]", price_condition)
    if not price_match:
        raise CollectionError(f"价格条件没有被精确覆盖: {price_condition or '缺失'}")
    actual_low, actual_high = map(float, price_match.groups())
    if not math.isclose(actual_low, low) or not math.isclose(actual_high, high):
        raise CollectionError(
            f"价格条件被改写: 期望 [{low}, {high}]，实际 [{actual_low}, {actual_high}]"
        )

    total_match = re.search(r"总召回结果数：(\d+)", text)
    if not total_match:
        raise CollectionError("响应中缺少总召回结果数")
    return int(total_match.group(1)), actual_low, actual_high


def parse_listings(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    container = re.search(r"<房源>\s*(.*?)\s*</房源>", text, re.S)
    if not container:
        return records
    pattern = re.compile(r"<(\d+)>\s*(\{.*?\})\s*</\1>", re.S)
    for listing_id, raw in pattern.findall(container.group(1)):
        payload = json.loads(raw)
        summary = payload.get("摘要信息", {})
        transaction = str(summary.get("交易信息", ""))
        property_match = re.search(r"(\d+)年产权", transaction)
        if not property_match or int(property_match.group(1)) != 70:
            continue

        price = number(str(summary.get("价格信息", "")))
        if price is None or price >= 350:
            continue

        community_text = str(summary.get("小区信息", ""))
        community_match = re.search(r"^(.*?)\(小区ID:(\d+)\)", community_text)
        build_match = re.search(r"，(\d{4})(?:-(\d{4}))?年建成", community_text)
        location = str(summary.get("区位交通", ""))
        location_match = re.search(r"位于(东城区|西城区)(.*?)板块(.*?)(?:小区|$)", location)
        area_match = re.search(r"建筑面积(\d+(?:\.\d+)?)㎡", str(summary.get("户型信息", "")))
        unit_price_match = re.search(r"单价约(\d+)元/平米", str(summary.get("价格信息", "")))
        orientation_match = re.search(r"朝向([^，]+)", str(summary.get("户型信息", "")))

        records.append(
            {
                "listing_id": listing_id,
                "city": "北京",
                "district": (
                    location_match.group(1) if location_match else ""
                ),
                "community_id": community_match.group(2) if community_match else "",
                "community_name": community_match.group(1) if community_match else "",
                "build_year": build_match.group(1) if build_match else "",
                "build_date": "",
                "layout": str(summary.get("户型信息", "")).split("，", 1)[0],
                "area_sqm": area_match.group(1) if area_match else "",
                "total_price_wan": str(price),
                "unit_price_yuan_sqm": (
                    unit_price_match.group(1) if unit_price_match else ""
                ),
                "floor_desc": str(summary.get("房源所在楼层信息", "")),
                "orientation": (
                    orientation_match.group(1) if orientation_match else ""
                ),
                "has_elevator": "是",
                "house_nature": "商品房",
                "large_property": "是",
                "status": "在售",
                "source": "贝壳官方 CLI buy search",
                "observed_at": OBSERVED_DATE,
            }
        )
    return records


def collect_district(
    district: str,
    *,
    delay_seconds: float,
    max_queries: int,
    cache_dir: Path,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[tuple[float, float]]]:
    queue = deque((low, high, "") for low, high in PRICE_BINS)
    matches: dict[str, dict[str, Any]] = {}
    incomplete: list[dict[str, Any]] = []
    query_count = 0
    pending: list[tuple[float, float, str]] = []
    cache_dir.mkdir(parents=True, exist_ok=True)

    while queue:
        low, high, layout = queue.popleft()
        suffix = f"-{layout}" if layout else ""
        cache_file = cache_dir / f"{district}-{low:g}-{high:g}{suffix}.json"
        if cache_file.exists():
            text = json.loads(cache_file.read_text(encoding="utf-8"))["data"]
        else:
            if query_count >= max_queries:
                queue.appendleft((low, high, layout))
                pending = list(queue)
                break
            text = run_search(district, low, high, layout)
            cache_file.write_text(
                json.dumps({"data": text}, ensure_ascii=False),
                encoding="utf-8",
            )
            query_count += 1
            time.sleep(delay_seconds)

        total, actual_low, actual_high = parse_exact_conditions(
            text, district, low, high, layout
        )
        records = parse_listings(text)
        for record in records:
            matches.setdefault(record["listing_id"], record)

        print(
            f"{district} [{low:g},{high:g}] total={total} "
            f"70-year={len(records)} layout={layout or 'all'}",
            flush=True,
        )

        if total <= 10:
            continue
        if high - low <= 1:
            if not layout:
                for fallback in ("一居室", "二居室", "三居室及以上"):
                    queue.append((low, high, fallback))
                continue
            incomplete.append(
                {
                    "district": district,
                    "low": actual_low,
                    "high": actual_high,
                    "total": total,
                    "layout": layout,
                }
            )
            continue

        midpoint = math.floor((low + high) / 2)
        queue.appendleft((midpoint, high, layout))
        queue.appendleft((low, midpoint, layout))

    return matches, incomplete, pending


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="使用贝壳官方 CLI 按价格区间递归收集精确条件结果。"
    )
    parser.add_argument(
        "--district",
        action="append",
        choices=("东城区", "西城区"),
        default=None,
        help="可重复指定；默认东西城",
    )
    parser.add_argument("--delay", type=float, default=0.8)
    parser.add_argument(
        "--max-queries",
        type=int,
        default=8,
        help="每轮最多发起的官方查询数；缓存命中不计入",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("output/beike_cache"),
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=Path("output/beike_collected.csv"),
    )
    parser.add_argument(
        "--xlsx",
        type=Path,
        default=Path("output/dongxicheng-under-350.xlsx"),
    )
    parser.add_argument(
        "--coverage",
        type=Path,
        default=Path("output/beike_coverage.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    districts = args.district or ["东城区", "西城区"]
    all_records: dict[str, dict[str, Any]] = {}
    incomplete: list[dict[str, Any]] = []
    pending: dict[str, list[tuple[float, float, str]]] = {}

    for district in districts:
        records, unresolved, district_pending = collect_district(
            district,
            delay_seconds=args.delay,
            max_queries=args.max_queries,
            cache_dir=args.cache_dir,
        )
        all_records.update(records)
        incomplete.extend(unresolved)
        if district_pending:
            pending[district] = district_pending
            break

    frame = pd.DataFrame(all_records.values(), columns=OUTPUT_COLUMNS)
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.csv, index=False, encoding="utf-8-sig")
    args.coverage.write_text(
        json.dumps(
            {
                "observed_at": OBSERVED_DATE,
                "districts": districts,
                "collected_listing_count": len(frame),
                "community_count": frame["community_name"].nunique(),
                "incomplete_price_ranges": incomplete,
                "pending_price_ranges": {
                    district: [[low, high, layout] for low, high, layout in ranges]
                    for district, ranges in pending.items()
                },
                "status": "paused" if pending else "complete",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if pending:
        print(
            "本轮达到查询预算，已保存缓存；等待限流窗口后再次运行即可续跑",
            flush=True,
        )
        return

    run_pipeline(
        args.csv,
        args.xlsx,
        today=date.today(),
        max_age_years=date.today().year - 2000,
        min_total_price=0,
        max_total_price=349.9999,
        city="北京",
        district=districts,
    )
    print(
        f"完成: {len(frame)} 套、{frame['community_name'].nunique()} 个小区；"
        f"输出 {args.xlsx}",
        flush=True,
    )
    if incomplete:
        print(f"警告: {len(incomplete)} 个价格区间未能完整细分", flush=True)


if __name__ == "__main__":
    main()
