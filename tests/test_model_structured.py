import json, threading, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from agent.model import Message, ModelResponse, OpenAICompatProvider, ProviderError, RuleModel, ScriptedModel, ToolCall, estimate_tokens
from agent.structured import extract_json, generate_structured, validate

SCHEMA = {"type": "object", "required": ["city", "temp"], "properties": {"city": {"type": "string"}, "temp": {"type": "number", "minimum": -90}}}


class StructuredTests(unittest.TestCase):
    def test_validate_ok_and_errors(self):
        self.assertEqual(validate({"city": "A", "temp": 3}, SCHEMA), [])
        self.assertIn("missing required 'temp'", validate({"city": "A"}, SCHEMA)[0])
        self.assertTrue(validate({"city": 1, "temp": 3}, SCHEMA))
        self.assertTrue(validate({"city": "A", "temp": -100}, SCHEMA))
        self.assertTrue(validate({"city": "A", "temp": True}, SCHEMA), "bool is not a number")

    def test_extract_json_from_prose_and_fences(self):
        self.assertEqual(extract_json('Sure! {"a": {"b": "}"}} hope that helps'), {"a": {"b": "}"}})
        self.assertEqual(extract_json('```json\n{"x": 1}\n```'), {"x": 1})
        with self.assertRaises(ValueError):
            extract_json("no json here")

    def test_generate_structured_repairs_then_succeeds(self):
        m = ScriptedModel(["not json", '{"city": "Austin"}', '{"city": "Austin", "temp": 31}'])
        data, usage, repairs = generate_structured(m, [Message("user", "weather")], SCHEMA, max_repairs=2)
        self.assertEqual(data["temp"], 31)
        self.assertEqual(repairs, 2)
        self.assertGreater(usage.total, 0)
        # the repair prompt told the model what was wrong
        self.assertIn("missing required 'temp'", m.calls[2][-1].content)

    def test_generate_structured_gives_up(self):
        m = ScriptedModel(["x", "y", "z"])
        with self.assertRaises(ProviderError) as cm:
            generate_structured(m, [], SCHEMA, max_repairs=2)
        self.assertEqual(cm.exception.kind, "structured")


class ModelTests(unittest.TestCase):
    def test_rule_model_calls_tool_then_answers(self):
        tools = [{"name": "calculator"}]
        r = RuleModel().complete([Message("user", "what is 6 x 7")], tools)
        self.assertEqual(r.tool_calls[0].args, {"expression": "6 * 7"})
        r2 = RuleModel().complete([Message("user", "q"), Message("tool", "42")], tools)
        self.assertIn("42", r2.content)

    def test_scripted_model_raises_scripted_errors_and_counts_tokens(self):
        m = ScriptedModel([ProviderError("boom", retryable=True), "hello there"])
        with self.assertRaises(ProviderError):
            m.complete([Message("user", "hi")])
        r = m.complete([Message("user", "hi")])
        self.assertGreater(r.usage.total, 0)
        self.assertEqual(estimate_tokens(""), 0)


class _H(BaseHTTPRequestHandler):
    mode = "ok"

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _H.last = (self.headers.get("Authorization"), body)
        if _H.mode == "500":
            self.send_response(503); self.end_headers(); return
        if _H.mode == "garbage":
            self.send_response(200); self.end_headers(); self.wfile.write(b"<html>"); return
        out = {"choices": [{"message": {"content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "calculator", "arguments": "{\"expression\": \"1+1\"}"}}]}}],
               "usage": {"prompt_tokens": 11, "completion_tokens": 5}}
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(json.dumps(out).encode())

    def log_message(self, *a): pass


class ProviderHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_port}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_parses_tool_calls_usage_and_sends_auth(self):
        _H.mode = "ok"
        p = OpenAICompatProvider(self.url, "m", api_key="k123")
        r = p.complete([Message("user", "x")], [{"name": "calculator", "description": "d", "parameters": {"type": "object"}}])
        self.assertEqual(r.tool_calls[0].name, "calculator")
        self.assertEqual(r.tool_calls[0].args, {"expression": "1+1"})
        self.assertEqual((r.usage.prompt_tokens, r.usage.completion_tokens), (11, 5))
        self.assertEqual(_H.last[0], "Bearer k123")
        self.assertEqual(_H.last[1]["tools"][0]["function"]["name"], "calculator")

    def test_http_503_is_retryable_and_garbage_is_classified(self):
        p = OpenAICompatProvider(self.url, "m")
        _H.mode = "500"
        with self.assertRaises(ProviderError) as c1:
            p.complete([Message("user", "x")])
        self.assertTrue(c1.exception.retryable)
        _H.mode = "garbage"
        with self.assertRaises(ProviderError) as c2:
            p.complete([Message("user", "x")])
        self.assertEqual(c2.exception.kind, "malformed")

    def test_connection_refused_is_network_error(self):
        with self.assertRaises(ProviderError) as c:
            OpenAICompatProvider("http://127.0.0.1:1/v1", "m", timeout=1).complete([Message("user", "x")])
        self.assertEqual(c.exception.kind, "network")
        self.assertTrue(c.exception.retryable)


if __name__ == "__main__":
    unittest.main()
