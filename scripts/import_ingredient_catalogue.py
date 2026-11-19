#!/usr/bin/env python3
"""Run the source-backed, local-first ingredient seed import."""
from __future__ import annotations
import argparse
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import engine
import ingredient_store
import catalogue_import
import sources


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database",default=str(ROOT/"smartnano.db"),help="Working v5 SQLite database (not the v4 backup).")
    parser.add_argument("--targets",default=str(ROOT/"seed"/"ingredient_targets.csv"))
    parser.add_argument("--report",default=str(ROOT/"seed"/"ingredient_import_report.json"))
    parser.add_argument("--cache-db",default=str(Path(tempfile.gettempdir())/"smartnano-v5-source-cache.db"))
    parser.add_argument("--timeout",type=float,default=20.0)
    parser.add_argument("--workers",type=int,default=8,help="Concurrent provider fetches; clamped to 1..12 and still rate-limited per source.")
    parser.add_argument("--max-items",type=int,default=0,help="Optional preflight cap; zero processes every target.")
    parser.add_argument("--minimum-ingredient-records",type=int,default=300)
    args=parser.parse_args()
    db_path=Path(args.database).resolve()
    if not db_path.exists():
        parser.error(f"Database not found: {db_path}")
    target_path=Path(args.targets).resolve()
    if not target_path.exists():
        parser.error(f"Target CSV not found: {target_path}")
    con=engine.connect(str(db_path))
    cache_con=None
    try:
        ingredient_store.initialize(con)
        targets=catalogue_import.load_targets(con,target_path)
        if args.max_items>0:
            targets=targets[:args.max_items]
        cache_path=Path(args.cache_db).resolve()
        cache_path.parent.mkdir(parents=True,exist_ok=True)
        cache_con=sqlite3.connect(str(cache_path),timeout=20,check_same_thread=False)
        cache_con.row_factory=sqlite3.Row
        sources.initialize(cache_con)
        adapters=sources.build_adapters(cache_con,timeout=args.timeout)
        last=[time.monotonic()]
        def prefetch_progress(provider,index,total,name):
            now=time.monotonic()
            elapsed=now-last[0]
            last[0]=now
            print(f"[{provider} {index}/{total}] {name[:60]} ({elapsed:.1f}s since checkpoint)",flush=True)
        print(f"Prefetching source results for {len(targets)} deduplicated resolver targets with at most {args.workers} workers.",flush=True)
        pubchem_results,rxnorm_results=catalogue_import.prefetch_source_results(
            con,targets,adapters,workers=args.workers,progress=prefetch_progress)
        print(f"Writing {len(targets)} targets serially to the v5 database.",flush=True)
        def progress(index,total,target):
            print(f"[DB {index}/{total}] {target['category']}: {target['name'][:70]}",flush=True)
        report=catalogue_import.run_import(con,targets,adapters,progress=progress,
                                           prefetched_pubchem=pubchem_results,prefetched_rxnorm=rxnorm_results)
        report["database_seed"]=str(db_path)
        report["target_csv"]="seed/ingredient_targets.csv"
        report["target_csv_sha256"]=__import__("hashlib").sha256(target_path.read_bytes()).hexdigest()
        report["preflight_limited"]=bool(args.max_items)
        report_path=Path(args.report).resolve()
        report_path.parent.mkdir(parents=True,exist_ok=True)
        report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
        print(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True))
        if not args.max_items and report["ingredient_records_total"]<args.minimum_ingredient_records:
            print(f"ERROR: {report['ingredient_records_total']} ingredient records; required minimum is {args.minimum_ingredient_records}.",file=sys.stderr)
            return 2
        return 0
    finally:
        if cache_con is not None:
            cache_con.close()
        con.close()

if __name__=="__main__":
    raise SystemExit(main())
