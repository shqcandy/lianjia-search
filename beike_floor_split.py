# -*- coding: utf-8 -*-
"""按楼层拆分，探查剩余未完整价格区间（一居室高价段）。

用法: python beike_floor_split.py [预算次数]
- 合并区间: (319.9375,320.0625) = 原1+2, (339.9375,340.0625) = 原3+4, (349.9375,350)
- 每个合并区间按 低/中/高楼层 各发一次请求
"""
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

import beike_collect as bc
from lianjia_search import run_pipeline

bc.MIN_REQUEST_INTERVAL_SECONDS = 30
bc.rate_limit_pause = lambda request_times, now: None  # 沿用续采脚本口径

CLI = Path('tools/beike/beike.exe')
CACHE = Path('output/beike_cache')
STATE = Path('output/beike_rate_limit.json')
CSV = Path('output/beike_collected.csv')
XLSX = Path('outputs/01a11482-0424-7f30-8597-93f454b83e5b/beike-housing-results-2026-10-07.xlsx')
RESULT = Path('output/beike_floor_split.json')

DISTRICT = '西城区'
MERGED = [
    (319.9375, 320.0625, '一居室'),   # 原 1 + 2
    (339.9375, 340.0625, '一居室'),   # 原 3 + 4
    (349.9375, 350.0, '一居室'),      # 原 5
]
FLOORS = ['低楼层', '中楼层', '高楼层']

BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 9


def build_query(low, high, layout, floor):
    return (
        f'{DISTRICT} 建成时间{bc.MAX_AGE_CUTOFF_TEXT} '
        f'总价在{low:g}万到{high:g}万之间 有电梯 商品房 70年产权 {layout} {floor}'
    )


def conditions_of(text):
    m = re.search(r'【本次实际命中的检索条件】\s*(\[.*?\])\s*\n+【检索概览】', text, re.S)
    return json.loads(m.group(1)) if m else []


def run_query(low, high, layout, floor, limiter):
    query = build_query(low, high, layout, floor)
    cmd = [str(CLI), 'buy', 'search', '-c', '北京', '-q', query,
           '--house-type', 'second', '--json']
    limiter.before_request()
    completed = subprocess.run(cmd, cwd=str(CLI.parents[2]), env=os.environ.copy(),
                               capture_output=True, text=True, encoding='utf-8', errors='replace')
    if completed.returncode:
        raise bc.CollectionError(completed.stderr.strip() or completed.stdout.strip())
    if 'service temporarily unavailable' in completed.stdout.lower():
        raise limiter.record_service_unavailable()
    payload = json.loads(completed.stdout)
    if not payload.get('ok'):
        raise bc.CollectionError(f"ok=false: {payload.get('error') or payload.get('message')}")
    limiter.record_success()
    return payload['data']


limiter = bc.RateLimiter(STATE)
new_records = {}
results = []
query_count = 0
pending = []
pause = None

for low, high, layout in MERGED:
    for floor in FLOORS:
        cache_file = CACHE / f'{DISTRICT}-{low:g}-{high:g}-{layout}-{floor}.json'
        if cache_file.exists():
            text = json.loads(cache_file.read_text(encoding='utf-8'))['data']
            used_cache = True
        else:
            if query_count >= BUDGET:
                pending.append((low, high, layout, floor))
                continue
            try:
                text = run_query(low, high, layout, floor, limiter)
            except bc.RateLimitPause as error:
                pending.append((low, high, layout, floor))
                pause = error
                break
            cache_file.write_text(json.dumps({'data': text}, ensure_ascii=False), encoding='utf-8')
            query_count += 1
            used_cache = False

        total, _al, _ah = bc.parse_exact_conditions(text, DISTRICT, low, high, layout)
        conds = conditions_of(text)
        floor_ok = any(c['检索条件'] == f'房源楼层类型 = {floor}' for c in conds)
        recs = bc.parse_listings(text)
        for r in recs:
            new_records.setdefault(r['listing_id'], r)
        results.append({
            'low': low, 'high': high, 'layout': layout, 'floor': floor,
            'total': total, 'parsed': len(recs), 'floor_condition_ok': floor_ok,
            'cache': used_cache,
        })
        print(f'FLOOR {DISTRICT} [{low:g},{high:g}] {layout} {floor} '
              f'total={total} parsed={len(recs)} cond_ok={floor_ok} cache={used_cache}', flush=True)
    if pause:
        break

RESULT.write_text(json.dumps({
    'observed_at': bc.OBSERVED_DATE,
    'district': DISTRICT,
    'merged_ranges': [{'low': l, 'high': h, 'layout': y} for l, h, y in MERGED],
    'floors': FLOORS,
    'results': results,
    'pending': [{'low': l, 'high': h, 'layout': y, 'floor': f} for l, h, y, f in pending],
    'pause': str(pause) if pause else None,
}, ensure_ascii=False, indent=2), encoding='utf-8')

# merge into collected CSV
base = pd.read_csv(CSV, dtype=str).fillna('') if CSV.exists() else pd.DataFrame(columns=bc.OUTPUT_COLUMNS)
added = pd.DataFrame(new_records.values(), columns=bc.OUTPUT_COLUMNS)
merged = pd.concat([base, added], ignore_index=True).drop_duplicates(subset='listing_id', keep='first')
merged.to_csv(CSV, index=False, encoding='utf-8-sig')

print('NEW_LISTINGS', len(added), 'TOTAL_LISTINGS', len(merged), flush=True)
if pause:
    print('PAUSE', str(pause), flush=True)
for l, h, y, f in pending:
    print('PENDING', DISTRICT, f'{l:g}-{h:g}', y, f, flush=True)

run_pipeline(
    CSV, XLSX, today=date.today(),
    max_age_years=date.today().year - 2000,
    min_total_price=0, max_total_price=349.9999,
    city='北京', district=['东城区', '西城区'],
)
print('WROTE', XLSX, flush=True)
