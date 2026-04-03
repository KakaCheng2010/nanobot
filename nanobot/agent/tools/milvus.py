"""Milvus knowledge search tool."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from loguru import logger
from openai import AsyncOpenAI

from nanobot.agent.tools.base import Tool


class MilvusSearchTool(Tool):
    """使用 OpenAI 兼容 embedding + Milvus 做知识库检索。"""

    name = "milvus_search"
    description = "Search the Milvus knowledge base for relevant passages."
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The question or search query to look up in the Milvus knowledge base.",
                "minLength": 1,
            },
            "top_k": {
                "type": "integer",
                "description": "How many results to return. Defaults to configured top_k.",
                "minimum": 1,
                "maximum": 20,
            },
        },
        "required": ["query"],
    }

    def __init__(self, config: Any):
        self.config = config

    async def execute(self, query: str, top_k: int | None = None, **kwargs: Any) -> str:
        if not self.config.collection_name.strip():
            return "Error: Milvus tool is enabled but collection_name is empty."

        embedding_api_key = self.config.embedding_api_key.strip()
        if not embedding_api_key:
            return "Error: Milvus tool requires embedding_api_key in tools.milvus."

        try:
            vector = await self._embed_query(query)
            hits = await asyncio.to_thread(self._search_sync, vector, top_k or self.config.top_k)
            return self._format_hits(query, hits)
        except ImportError:
            return (
                "Error: pymilvus is not installed. Run `pip install pymilvus` in the current Python environment."
            )
        except Exception as exc:
            logger.exception("Milvus search failed")
            return f"Error: Milvus search failed: {type(exc).__name__}: {exc}"

    async def _embed_query(self, query: str) -> list[float]:
        """先把自然语言问题转成向量，再交给 Milvus 检索。"""
        client = AsyncOpenAI(
            api_key=self.config.embedding_api_key,
            base_url=self.config.embedding_api_base or None,
        )
        kwargs: dict[str, Any] = {
            "model": self.config.embedding_model,
            "input": query,
        }
        if self.config.embedding_dimensions > 0:
            kwargs["dimensions"] = self.config.embedding_dimensions

        response = await client.embeddings.create(**kwargs)
        if not response.data:
            raise RuntimeError("Embedding API returned no vectors")
        return list(response.data[0].embedding)

    def _search_sync(self, vector: list[float], top_k: int) -> list[Any]:
        """pymilvus 目前以同步客户端为主，这里放到线程里执行，避免阻塞事件循环。"""
        from pymilvus import MilvusClient

        client_kwargs: dict[str, Any] = {"uri": self.config.uri}
        if self.config.token:
            client_kwargs["token"] = self.config.token
        if self.config.db_name:
            client_kwargs["db_name"] = self.config.db_name

        client = MilvusClient(**client_kwargs)
        search_kwargs: dict[str, Any] = {
            "collection_name": self.config.collection_name,
            "data": [vector],
            "limit": top_k,
            "anns_field": self.config.vector_field,
            "search_params": self._build_search_params(),
        }

        output_fields = self._build_output_fields()
        if output_fields:
            search_kwargs["output_fields"] = output_fields

        result = client.search(**search_kwargs)
        if isinstance(result, list) and result and isinstance(result[0], list):
            return result[0]
        if isinstance(result, list):
            return result
        return []

    def _build_search_params(self) -> dict[str, Any]:
        """给 Milvus 组装检索参数，允许配置覆盖默认 metric。"""
        params = dict(self.config.search_params or {})
        params.setdefault("metric_type", self.config.metric_type)
        return params

    def _build_output_fields(self) -> list[str]:
        fields = list(self.config.output_fields or [])
        if self.config.text_field not in fields:
            fields.append(self.config.text_field)
        return [field for field in fields if str(field).strip()]

    def _format_hits(self, query: str, hits: list[Any]) -> str:
        if not hits:
            return f"No Milvus results for: {query}"

        lines = [f"Milvus results for: {query}", ""]
        for index, hit in enumerate(hits, start=1):
            item = self._normalize_hit(hit)
            score = item.get("score")
            text = str(item.get(self.config.text_field, "") or "").strip()
            entity_id = item.get("id") or item.get("pk") or item.get("entity_id") or ""

            title = f"{index}. score={score:.4f}" if isinstance(score, (int, float)) else f"{index}."
            if entity_id:
                title += f" id={entity_id}"
            lines.append(title)
            if text:
                lines.append(text[:1200])

            # 额外字段以 JSON 形式展示，方便模型按来源、标签继续推理。
            extra = {
                key: value
                for key, value in item.items()
                if key not in {"score", "distance", "id", "pk", "entity_id", self.config.text_field}
            }
            if extra:
                lines.append(f"metadata: {json.dumps(extra, ensure_ascii=False)}")
            lines.append("")

        return "\n".join(lines).strip()

    @staticmethod
    def _normalize_hit(hit: Any) -> dict[str, Any]:
        """兼容不同 pymilvus 返回结构，统一拍平成普通 dict。"""
        if isinstance(hit, dict):
            entity = hit.get("entity")
            if isinstance(entity, dict):
                return {
                    **entity,
                    **{k: v for k, v in hit.items() if k != "entity"},
                }
            return hit

        if hasattr(hit, "entity") and isinstance(hit.entity, dict):
            payload = dict(hit.entity)
            for attr in ("id", "score", "distance"):
                if hasattr(hit, attr):
                    payload[attr] = getattr(hit, attr)
            return payload

        return {"raw": str(hit)}
