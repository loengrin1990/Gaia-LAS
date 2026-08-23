#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from d3_live_hybrid_package import build_package
from gaia.hybrid_reasoning import run_hybrid_reasoning
def main() -> None:
 p=argparse.ArgumentParser(); p.add_argument('--workspace-id',required=True); p.add_argument('--query',required=True); p.add_argument('--output',required=True,type=Path); p.add_argument('--storage-root',type=Path,default=ROOT/'storage'); a=p.parse_args(); out=a.output.resolve(); storage=a.storage_root.resolve()
 if out.is_relative_to(storage) or not out.parent.is_dir(): raise ValueError('Output must be outside storage in an existing directory.')
 package=build_package(storage,a.workspace_id,a.query); result=run_hybrid_reasoning(package); out.write_text(json.dumps({'schema':'gaia.d3.hybrid-reasoning-run.v1','workspace_id':a.workspace_id,'package':package,'reasoning':result},ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
 if not result['ok']: raise SystemExit(2)
if __name__=='__main__': main()
