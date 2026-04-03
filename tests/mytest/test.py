"""
Minimal runnable example: write one string into Milvus.

Before running:
    PowerShell:
        $env:DASHSCOPE_API_KEY="your-key"

Run:
    D:\develop\miniconda3\envs\nanobot\python.exe D:\workspace-my\nanobot\tests\mytest\test.py
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from openai import OpenAI
from pymilvus import MilvusClient


MILVUS_URI = "http://127.0.0.1:19530"
COLLECTION_NAME = "nanobot_string_test"
VECTOR_FIELD = "vector"
EMBEDDING_MODEL = "text-embedding-v4"
EMBEDDING_API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
EMBEDDING_API_KEY = "sk-cad066c22d5b48aaa0958a31d3809627"

def main() -> None:
    text = "电脑必须配置并启用防火墙，如果未开启则判为不合格。"
    # Generate one embedding vector from the test string.
    embedding_client = OpenAI(
        api_key=EMBEDDING_API_KEY,
        base_url=EMBEDDING_API_BASE,
    )
    embedding_response = embedding_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text,
    )
    vector = list(embedding_response.data[0].embedding)

    # Connect to Milvus.
    client = MilvusClient(uri=MILVUS_URI)

    # Create the collection only when it does not exist yet.
    if not client.has_collection(COLLECTION_NAME):
        client.create_collection(
            collection_name=COLLECTION_NAME,
            dimension=len(vector),
            metric_type="COSINE",
            consistency_level="Strong",
        )

    # Your current collection uses INT64 primary key, so write an integer id.
    row_id = int(time.time() * 1000)
    client.insert(
        collection_name=COLLECTION_NAME,
        data=[{
            "id": row_id,
            VECTOR_FIELD: vector,
            "text": text,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }],
    )

    print("Insert success")
    print(f"id={row_id}")
    print(f"collection={COLLECTION_NAME}")


if __name__ == "__main__":
    main()
