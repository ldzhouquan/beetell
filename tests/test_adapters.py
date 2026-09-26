import asyncio
import json
import unittest
from unittest.mock import patch

import httpx

from btell.adapters import AgentError, LLMAdapter


class AdapterTests(unittest.TestCase):
    def run_stream(self, adapter, response):
        transport = httpx.MockTransport(lambda request: response(request))
        client_factory = httpx.AsyncClient
        with patch("btell.adapters.httpx.AsyncClient", side_effect=lambda **kwargs: client_factory(transport=transport, **kwargs)):
            async def collect():
                return [event async for event in adapter.send("测试文本")]
            return asyncio.run(collect())

    def test_openai_sse(self):
        def response(request):
            self.assertEqual(request.headers["authorization"], "Bearer private-key")
            self.assertEqual(json.loads(request.content)["messages"][0]["content"], "测试文本")
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"你"}}]}\n\ndata: {"choices":[{"delta":{"content":"好"}}]}\n\ndata: [DONE]\n\n')
        events = self.run_stream(LLMAdapter("llm", "private-key", "test-model"), response)
        self.assertEqual([e["type"] for e in events], ["agent.delta", "agent.delta", "agent.done"])
        self.assertEqual(events[-1]["text"], "你好")

    def test_anthropic_sse(self):
        def response(request):
            self.assertEqual(request.headers["x-api-key"], "private-key")
            return httpx.Response(200, text='event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"text":"你好"}}\n\n')
        events = self.run_stream(LLMAdapter("anthropic", "private-key", "test-model"), response)
        self.assertEqual(events[-1]["text"], "你好")

    def test_missing_credentials_do_not_contact_provider(self):
        async def run():
            return [e async for e in LLMAdapter("llm", "", "test-model").send("private")]
        with self.assertRaises(AgentError):
            asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
