"""Offline integration tests: real SDK, mocked HTTP, real CSV output."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import pandas as pd
from openai import OpenAI

import geo_monitor as monitor


def completion(text, finish="stop"):
    return {
        "id": "offline-test", "object": "chat.completion", "created": 0,
        "model": monitor.DEFAULT_MODEL,
        "choices": [{"index": 0, "finish_reason": finish,
                     "message": {"role": "assistant", "content": text}}],
    }


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.output = Path(self.folder.name) / "result.csv"
        self.requests = []

    def client(self, responses):
        pending = iter(responses)

        def handle(request):
            self.assertEqual(str(request.url), "https://api.deepseek.com/chat/completions")
            self.requests.append(json.loads(request.content))
            response = next(pending)
            if isinstance(response, BaseException):
                raise response
            status, body = response
            return httpx.Response(status, json=body)

        client = OpenAI(
            api_key="offline-test-key", base_url=monitor.BASE_URL, max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(handle)),
        )
        self.addCleanup(client.close)
        return client

    def invoke(self, responses, questions=None):
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            results = monitor.run_monitor(self.client(responses), monitor.DEFAULT_MODEL,
                                          self.output, questions=questions)
        return results, log.getvalue()

    def test_three_questions_real_sdk_and_literal_mentions(self):
        long_answer = "维情\n" + "说明" * 35
        rows, log = self.invoke([
            (200, completion(long_answer)),
            (200, completion("没有具体品牌。")),
            (200, completion("不推荐维情。")),
        ])
        self.assertEqual([row["状态"] for row in rows], ["Positive", "Missed", "Positive"])
        self.assertEqual(rows[0]["回答摘要"], (" ".join(long_answer.split()))[:50] + "…")
        self.assertIn("2/3 (66.7%)", log)
        self.assertEqual(len(self.requests), 3)
        for body, question in zip(self.requests, monitor.QUESTIONS):
            self.assertEqual(body["model"], monitor.DEFAULT_MODEL)
            self.assertEqual(body["messages"], [{"role": "user", "content": question}])
            self.assertNotIn(monitor.BRAND_NAME, question)
            self.assertEqual(body["thinking"], {"type": "disabled"})
            self.assertEqual(body["temperature"], 0)
            self.assertEqual(body["max_tokens"], 1024)
        frame = pd.read_csv(self.output)
        self.assertEqual(list(frame.columns), monitor.COLUMNS)
        self.assertEqual(len(frame), 3)
        self.assertTrue(self.output.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertFalse(self.output.with_name("result.csv.tmp").exists())

    def test_api_error_preserved_and_prompted_brand_excluded(self):
        rows, log = self.invoke([
            (200, completion("维情")),
            (401, {"error": {"message": "PRIVATE ERROR BODY", "type": "invalid_api_key"}}),
            (200, completion("未出现目标名称。")),
        ], ["维情靠谱吗？", "普通问题？", "另一个问题？"])
        self.assertTrue(rows[0]["问题含品牌"])
        self.assertEqual(rows[1]["状态"], "Error")
        self.assertIsNone(rows[1]["是否提及品牌"])
        self.assertEqual(rows[1]["错误类型"], "AuthenticationError:HTTP401")
        self.assertIn("成功 2/3，失败 1", log)
        self.assertIn("0/1 (0.0%)", log)
        exported = self.output.read_text(encoding="utf-8-sig")
        self.assertNotIn("PRIVATE ERROR BODY", exported + log)
        self.assertNotIn("offline-test-key", exported + log)
        self.assertEqual(len(pd.read_csv(self.output)), 3)

    def test_empty_and_incomplete_answers_are_errors(self):
        responses = [
            (200, {**completion(""), "choices": []}),
            (200, completion(None)),
            (200, completion(" \n ")),
            (200, completion("维情", "length")),
            (200, completion(None, "content_filter")),
        ]
        rows, log = self.invoke(responses, ["问题"] * len(responses))
        self.assertTrue(all(row["状态"] == "Error" for row in rows))
        self.assertTrue(all(row["是否提及品牌"] is None for row in rows))
        self.assertEqual(rows[3]["回答摘要"], "维情")
        self.assertIn("N/A", log)

    def test_csv_formula_text_is_escaped(self):
        rows, _ = self.invoke([(200, completion("\n=HYPERLINK(\"test\")"))], ["=1+1"])
        frame = pd.read_csv(self.output, keep_default_na=False)
        self.assertEqual(frame.iloc[0]["问题"], "'=1+1")
        self.assertTrue(frame.iloc[0]["回答摘要"].startswith("'="))
        self.assertEqual(rows[0]["状态"], "Missed")
        for text in ("+1", "-1", "@SUM(1)", "  =1", "\ttext"):
            self.assertTrue(monitor.csv_safe(text).startswith("'"))
        self.assertEqual(monitor.csv_safe(""), "")
        self.assertEqual(monitor.csv_safe("普通文本"), "普通文本")

    def test_interrupt_keeps_completed_rows(self):
        client = self.client([(200, completion("没有目标名称")), KeyboardInterrupt()])
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                monitor.run_monitor(client, monitor.DEFAULT_MODEL, self.output, questions=["问题1", "问题2"])
        self.assertEqual(len(pd.read_csv(self.output)), 1)

    def test_network_error_does_not_export_raw_message(self):
        rows, log = self.invoke([httpx.ReadTimeout("PRIVATE NETWORK DETAIL")], ["问题"])
        self.assertEqual(rows[0]["状态"], "Error")
        self.assertEqual(rows[0]["错误类型"], "APITimeoutError")
        self.assertNotIn("PRIVATE NETWORK DETAIL", log + self.output.read_text(encoding="utf-8-sig"))

    def test_missing_key_fails_before_network_or_output(self):
        with patch.dict(os.environ, {}, clear=True), patch("sys.argv", ["geo_monitor.py"]):
            with patch.object(monitor, "OpenAI") as sdk, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(monitor.main(), 2)
                sdk.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_invalid_input_fails_before_network(self):
        client = self.client([])
        with self.assertRaises(ValueError):
            monitor.run_monitor(client, monitor.DEFAULT_MODEL, self.output, questions=[])
        with self.assertRaises(ValueError):
            monitor.run_monitor(client, monitor.DEFAULT_MODEL, self.output, brand=" ")
        self.assertEqual(self.requests, [])


if __name__ == "__main__":
    unittest.main()
