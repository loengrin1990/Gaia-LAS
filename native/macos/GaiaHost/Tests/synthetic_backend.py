#!/usr/bin/env python3
"""Synthetic loopback Gaia identity for the native host launch smoke."""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/api/runtime":
            body = json.dumps({"ready": True, "runtime_id": "smoke-runtime", "api_contract_version": 1}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if "--file-picker-harness" in sys.argv:
            body = b'''<!doctype html><meta charset="utf-8"><title>Gaia file picker harness</title><label for="picker">Choose synthetic file</label><input id="picker" type="file"><p id="result">Waiting</p><script>const input=document.getElementById('picker');const send=(event,fields={})=>window.webkit?.messageHandlers?.gaiaNativeDiagnostics?.postMessage(Object.assign({event},fields));input.addEventListener('input',e=>send('file_input_input_event_received',{file_count:e.target.files.length,event_is_trusted:e.isTrusted,input_connected:e.target.isConnected,input_disabled:e.target.disabled}));input.addEventListener('change',e=>{send('file_input_change_event_received',{file_count:e.target.files.length,event_is_trusted:e.isTrusted,input_connected:e.target.isConnected,input_disabled:e.target.disabled});document.getElementById('result').textContent='FileList count: '+e.target.files.length;});</script>'''
        else:
            body = b"<!doctype html><title>Gaia smoke</title>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler)
    print("ready", flush=True)
    server.serve_forever()
