from __future__ import annotations

import multiprocessing
import os
import time
import unittest

from gaia.context_model_executor import ContextModelExecutorError, execute_context_model_call


def valid_worker(sender, payload):
    sender.send({"kind": "result", "result": {"ok": True, "answer": "{}"}})
    sender.close()


def hanging_worker(sender, payload):
    time.sleep(10)


def crashing_worker(sender, payload):
    os._exit(2)


def malformed_worker(sender, payload):
    sender.send({"kind": "result", "result": "bad"})
    sender.close()


class ContextModelExecutorTests(unittest.TestCase):
    def assert_no_children(self) -> None:
        self.assertEqual(multiprocessing.active_children(), [])

    def test_child_returns_valid_result_and_is_joined(self):
        result = execute_context_model_call({}, 1, worker=valid_worker)
        self.assertEqual(result, {"ok": True, "answer": "{}"})
        self.assert_no_children()

    def test_timeout_terminates_hanging_child(self):
        started = time.monotonic()
        with self.assertRaisesRegex(ContextModelExecutorError, "timeout"):
            execute_context_model_call({}, 0.15, worker=hanging_worker)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assert_no_children()

    def test_cancellation_terminates_child(self):
        event = __import__("threading").Event()
        event.set()
        with self.assertRaisesRegex(ContextModelExecutorError, "cancelled"):
            execute_context_model_call({}, 1, event, worker=hanging_worker)
        self.assert_no_children()

    def test_crash_and_malformed_ipc_are_safe(self):
        for worker, code in ((crashing_worker, "process"), (malformed_worker, "result")):
            with self.subTest(code=code), self.assertRaisesRegex(ContextModelExecutorError, code):
                execute_context_model_call({}, 1, worker=worker)
            self.assert_no_children()
