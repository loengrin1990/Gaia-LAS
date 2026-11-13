"""One frozen synthetic qualification on existing saved local routes; no tuning."""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import tempfile
import time
import urllib.request
from unittest.mock import patch

CORPUS_SHA256 = "6da2d0d7cea162c98b3c33a203659dc985218fae16d0cbb0d74b09ff9492a7d0"


def main() -> int:
    parser=argparse.ArgumentParser(description="Frozen synthetic holdout on saved local Gaia routes")
    parser.add_argument("--run-local",action="store_true",help="Explicitly run six synthetic inputs on already installed local models")
    args=parser.parse_args()
    if not args.run_local:parser.error("Use --run-local only for an explicitly authorised local qualification")
    if "gaia.config" in __import__("sys").modules:parser.error("Run in a fresh process before importing Gaia")
    repo=Path(__file__).resolve().parents[1]
    corpus_path=repo/"tests/fixtures/core_local_holdout_20261113.json";raw=corpus_path.read_bytes()
    assert hashlib.sha256(raw).hexdigest()==CORPUS_SHA256
    corpus=json.loads(raw);assert len(corpus["cases"])==6
    source=Path("/Users/ilia/Documents/Gaia/config.json");source_raw=source.read_bytes();saved=json.loads(source_raw)
    local=saved["local_llm"]
    for provider in local["providers"].values():
        url=urllib.parse.urlsplit(provider["endpoint"])
        assert url.scheme=="http" and ipaddress.ip_address(url.hostname).is_loopback and not url.username and not url.password
    assert not any(key in json.dumps(local).lower() for key in ['"api_key"','"secret"','"auth_token"'])
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def api(path,body=None):
        request=urllib.request.Request("http://127.0.0.1:11434"+path,None if body is None else json.dumps(body).encode(),headers={"Content-Type":"application/json"})
        with opener.open(request,timeout=5) as response:return json.load(response)
    assert not api("/api/ps").get("models"),"An existing loaded model may belong to another task; do not unload it"
    report={"corpus_sha256":CORPUS_SHA256,"source_config_sha256":hashlib.sha256(source_raw).hexdigest(),"ollama_version":api("/api/version"),"models":{},"cases":[],"accepted_context_confirmation":False}
    for task in ["veil_review","context_compiler"]:
        route=local["routes"][task];model=route.get("model") or local["providers"][route["provider"]]["model"]
        report["models"][task]={"route":route,"details":api("/api/show",{"model":model})["details"],"model":model}
    output=repo/"tmp/local-model-qualification/results.json";output.parent.mkdir(parents=True,exist_ok=True)
    assert not output.exists(), "Preserve the captured batch; do not overwrite or implicitly repeat it"
    def save():output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    with tempfile.TemporaryDirectory(prefix="gaia-real-local-holdout-") as temporary:
        root=Path(temporary)
        paths={key:str(root/key) for key in ["vault","projects","service_docs","runs","exports","storage","obsidian_work","transcriber","lm_studio_launcher","transcriber_launcher"]}
        for key in ["vault","projects","service_docs","runs","exports","storage","obsidian_work"]:Path(paths[key]).mkdir()
        config={"config_version":1,"paths":paths,"server":{"host":"127.0.0.1","port":8887},"endpoints":saved["endpoints"],"local_llm":local}
        config_path=root/"config.json";config_path.write_text(json.dumps(config));os.environ["GAIA_CONFIG"]=str(config_path);os.environ["NO_PROXY"]="127.0.0.1,::1"
        from gaia.config import ensure_dirs
        ensure_dirs()
        from gaia.controlled_intake import ControlledIntake
        from gaia.review import ReviewService
        from gaia.context_compiler import ContextCompiler,ContextService,ContextCompileError
        from gaia.operational_context import OperationalContextStore
        from gaia.operational_context_review import OperationalContextCandidateStore
        import gaia.context_compiler as compiler_module
        import gaia.module_assist as module_assist
        intake=ControlledIntake();prepared=[];current=None
        original_review=module_assist.call_lm_studio_with_deadline
        def observe_review(*a,**kw):
            started=time.monotonic();result=original_review(*a,**kw)
            current["veil_response"]={k:result[k] for k in ["ok","status","answer","model","provider"] if k in result}
            current["veil_response"]["latency_seconds"]=round(time.monotonic()-started,3)
            return result
        # Real Veil calls, followed by explicit test-only review of this known synthetic corpus.
        with patch.object(module_assist,"call_lm_studio_with_deadline",observe_review):
            for case in corpus["cases"]:
                current={"id":case["id"],"compiler_calls":[]};report["cases"].append(current)
                print(case["id"],"Veil starting",flush=True);started=time.monotonic()
                admitted=intake.admit("holdout-"+case["id"],[(case["id"]+".txt",case["text"].encode())])
                workspace=admitted["workspace_id"];artifact=admitted["materials"][0]["sanitized_id"]
                review=ReviewService(intake.store,workspace);state=review.start(artifact)
                current["veil_state"]=state["state"];current["veil_findings"]=state["findings"]
                current["veil_error_code"]=state.get("error_code","")
                if not current.get("veil_response",{}).get("ok"):
                    current["status"]="blocked_veil_transport";save();return 2
                # All frozen source text is artificial, locally inspected, and never disclosed.
                for finding in state["findings"]:review.decide(artifact,finding["finding_id"],"keep")
                cleaned=review.confirm(artifact);current["sanitized_text"]=cleaned
                current["sanitized_confirmation_method"]=review.get(artifact).get("confirmation_method")
                current["veil_total_seconds"]=round(time.monotonic()-started,3);prepared.append((current,workspace,artifact));save()
                print(case["id"],"Veil done",state["state"],flush=True)
        original_execute=compiler_module.execute_context_model_call
        def observe_execute(payload,*a,**kw):
            started=time.monotonic();entry={"operation":payload.get("operation","generate")}
            try:
                result=original_execute(payload,*a,**kw)
                entry.update({k:result[k] for k in ["ok","status","answer","model","provider","done_reason","prompt_eval_count","eval_count","load_duration","eval_duration","num_ctx","num_predict"] if k in result})
                return result
            except Exception as exc:
                entry["exception_type"]=type(exc).__name__;entry["error_code"]=getattr(exc,"code","");raise
            finally:
                entry["latency_seconds"]=round(time.monotonic()-started,3);current["compiler_calls"].append(entry);save()
        with patch.object(compiler_module,"execute_context_model_call",observe_execute):
            for current,workspace,artifact in prepared:
                print(current["id"],"Compiler starting",flush=True);started=time.monotonic();compiler=ContextCompiler(intake.store,workspace)
                assert compiler._uses_local_provider
                try:
                    items=compiler.compile(artifact)
                    current["candidates"]=[{k:item.get(k) for k in ["item_type","title","statement","actor_ref","deadline","explicit_status","priority","status","current"]} for item in items]
                    text=current["sanitized_text"]
                    current["evidence"]=[[{"start":block["start"],"end":block["end"],"quote":text[block["start"]:block["end"]]} for block in item["block_links"]] for item in items]
                    current["native_source_links_valid"]=all(artifact in item["source_links"] and item["workspace_id"]==workspace for item in items)
                    current["confirmed_context_count"]=sum(len(v) for v in ContextService(intake.store,workspace).summary().values())
                    receipt=compiler._receipt(artifact)
                    pending=OperationalContextCandidateStore(intake.store.root).list_scope(scope="project",scope_ref=workspace,state="pending")
                    current["oc_pending_values"]=[item.value for item in pending]
                    current["oc_pending_count"]=len(pending)
                    current["oc_confirmed_count"]=sum(item["confirmation"]=="confirmed" for item in OperationalContextStore(intake.store.root).list_scope(scope="project",scope_ref=workspace))
                    current["producer_diagnostics"]=receipt["producer_diagnostics"]
                    current["receipt_status"]=receipt["status"];current["status"]="complete" if items or pending else "complete_empty"
                    assert current["oc_confirmed_count"]==0
                    assert current["confirmed_context_count"]==0 and all(x["status"]=="requires_review" for x in items)
                except ContextCompileError as exc:
                    current.update(status="compiler_rejected",error_code=exc.code,diagnostic_code=exc.diagnostic_code,candidates=[])
                    if exc.code=="local_model_unavailable":save();return 2
                finally:
                    current["compiler_total_seconds"]=round(time.monotonic()-started,3);save()
                print(current["id"],current["status"],"seconds",current["compiler_total_seconds"],flush=True)
        report["saved_config_unchanged"]=source.read_bytes()==source_raw
        assert report["saved_config_unchanged"]
        save()
    print("Frozen local batch finished; output:",output,flush=True)
    return 0


if __name__=="__main__":raise SystemExit(main())
