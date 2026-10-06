import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

import lianjia_search as search


class PipelineTest(unittest.TestCase):
    def test_boundaries_grouping_and_excel_output(self) -> None:
        rows = [
            self.row("a1", "海淀区", "10", "300", "2016", "有", "是", "在售"),
            self.row("b1", "海淀区", "20", "500", "2020", "是", "大产权", "on_sale"),
            self.row(
                "d1",
                "海淀区",
                "28",
                "400",
                "2016",
                "有",
                "是",
                "在售",
                build_date="2016-10-06",
            ),
            self.row(
                "d2",
                "海淀区",
                "29",
                "400",
                "2016",
                "有",
                "是",
                "在售",
                build_date="2016-10-05",
            ),
            self.row("x1", "海淀区", "21", "299.99", "2020", "有", "是", "在售"),
            self.row("x2", "海淀区", "22", "500.01", "2020", "有", "是", "在售"),
            self.row("x3", "海淀区", "23", "400", "2015", "有", "是", "在售"),
            self.row("x4", "海淀区", "24", "400", "2020", "", "是", "在售"),
            self.row("x5", "海淀区", "25", "400", "2020", "有", "", "在售"),
            self.row("x6", "朝阳区", "26", "400", "2020", "有", "是", "在售"),
            self.row("x7", "海淀区", "27", "400", "2020", "有", "是", "已下架"),
        ]
        source = pd.DataFrame(rows)
        filtered, stats = search.filter_listings(
            source,
            today=date(2026, 10, 6),
            max_age_years=10,
            min_total_price=300,
            max_total_price=500,
            city="北京",
            district="海淀",
        )
        self.assertEqual(["a1", "b1", "d1"], filtered["listing_id"].tolist())
        self.assertEqual(3, stats["matched_rows"])

        summary = search.build_community_summary(filtered)
        self.assertEqual([1, 1, 1], summary["matching_listings"].tolist())

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.xlsx"
            search.run_pipeline(
                self.write_source(Path(directory), source),
                output,
                today=date(2026, 10, 6),
            )
            with pd.ExcelFile(output, engine="openpyxl") as workbook:
                self.assertEqual(["小区汇总", "房源明细"], workbook.sheet_names)
                self.assertEqual(3, len(workbook.parse("房源明细")))
                self.assertEqual(3, len(workbook.parse("小区汇总")))

    @staticmethod
    def row(
        listing_id: str,
        district: str,
        community_id: str,
        total_price: str,
        build_year: str,
        elevator: str,
        large_property: str,
        status: str,
        build_date: str = "",
    ) -> dict[str, str]:
        return {
            "listing_id": listing_id,
            "city": "北京",
            "district": district,
            "community_id": community_id,
            "community_name": f"小区{community_id}",
            "build_year": build_year,
            "build_date": build_date,
            "layout": "2室1厅",
            "area_sqm": "90",
            "total_price_wan": total_price,
            "unit_price_yuan_sqm": "50000",
            "floor_desc": "中楼层",
            "orientation": "南",
            "has_elevator": elevator,
            "house_nature": "商品房",
            "large_property": large_property,
            "status": status,
            "source": "test-fixture",
            "observed_at": "2026-10-06",
        }

    @staticmethod
    def write_source(directory: Path, frame: pd.DataFrame) -> Path:
        path = directory / "source.csv"
        frame.to_csv(path, index=False, encoding="utf-8-sig")
        return path


if __name__ == "__main__":
    unittest.main()
