"""Agent adapter boundary. Only transcribed text is sent to configured providers."""
import asyncio
import json
import shutil
from typing import AsyncIterator

import httpx


class AgentError(Exception):
    pass


class Adapter:
    name = ""
    capabilities = {"streaming": False, "tools": False, "memory": False, "audio_in": False, "long_task": False}

    async def send(self, text: str) -> AsyncIterator[dict]:
        raise NotImplementedError

    async def cancel(self):
        pass

    def ready(self):
        return False


class LLMAdapter(Adapter):
    capabilities = {"streaming": True, "tools": False, "memory": False, "audio_in": False, "long_task": False}

    def __init__(self, name, key, model, url=None):
        self.name, self.key, self.model = name, key, model
        self.url = url or ("https://api.openai.com/v1/chat/completions" if name == "llm" else "https://api.anthropic.com/v1/messages")

    def ready(self):
        return bool(self.key)

    async def send(self, text):
        if not self.ready():
            raise AgentError("provider credentials unavailable")
        openai = self.name == "llm"
        headers = ({"Authorization": "Bearer " + self.key} if openai else
                   {"x-api-key": self.key, "anthropic-version": "2023-06-01"})
        payload = ({"model": self.model, "messages": [{"role": "user", "content": text}], "stream": True} if openai else
                   {"model": self.model, "max_tokens": 1024, "messages": [{"role": "user", "content": text}], "stream": True})
        answer = []
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                async with client.stream("POST", self.url, headers=headers, json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data: ") or line == "data: [DONE]":
                            continue
                        try:
                            obj = json.loads(line[6:])
                            delta = (obj.get("choices", [{}])[0].get("delta", {}).get("content", "") if openai else
                                     obj.get("delta", {}).get("text", "") if obj.get("type") == "content_block_delta" else "")
                        except (ValueError, IndexError, TypeError):
                            continue
                        if delta:
                            answer.append(delta)
                            yield {"type": "agent.delta", "text": delta}
        except (httpx.HTTPError, ValueError) as exc:
            raise AgentError("provider request failed") from exc
        yield {"type": "agent.done", "text": "".join(answer)}


class CodexAdapter(Adapter):
    name = "codex"
    capabilities = {"streaming": False, "tools": True, "memory": False, "audio_in": False, "long_task": True}

    def __init__(self, version=""):
        self.version = version
        self.process = None

    def ready(self):
        return bool(shutil.which("codex") and self.version)

    async def send(self, text):
        if not self.ready():
            raise AgentError("codex unavailable or version not pinned")
        version = await asyncio.create_subprocess_exec("codex", "--version", stdout=asyncio.subprocess.PIPE)
        stdout, _ = await version.communicate()
        if version.returncode or self.version not in stdout.decode():
            raise AgentError("codex version mismatch")
        try:
            self.process = await asyncio.create_subprocess_exec(
                "codex", "exec", "--json", "--skip-git-repo-check", "--", text,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            answer = ""
            async for line in self.process.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") == "item.completed" and event.get("item", {}).get("type") == "agent_message":
                    answer += event["item"].get("text", "")
                if event.get("type") in ("turn.failed", "error"):
                    raise AgentError("codex failed")
            if await self.process.wait():
                raise AgentError("codex exited unsuccessfully")
            yield {"type": "agent.done", "text": answer}
        finally:
            await self.cancel()

    async def cancel(self):
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=2)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()


def adapters(config):
    return {"llm": LLMAdapter("llm", config.openai_key, config.openai_model),
            "anthropic": LLMAdapter("anthropic", config.anthropic_key, config.anthropic_model),
            "codex": CodexAdapter(config.codex_version)}
