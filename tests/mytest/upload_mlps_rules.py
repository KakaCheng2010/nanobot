r"""
Upload the first batch of MLPS-style Linux baseline rules into Milvus.

Before running:
    PowerShell:
        $env:DASHSCOPE_API_KEY="your-key"

Run:
    D:\develop\miniconda3\envs\nanobot\python.exe D:\workspace-my\nanobot\tests\mytest\upload_mlps_rules.py
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI
from pymilvus import MilvusClient


MILVUS_URI = "http://127.0.0.1:19530"
COLLECTION_NAME = "nanobot_string_test"
VECTOR_FIELD = "vector"
EMBEDDING_MODEL = "text-embedding-v4"
EMBEDDING_API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
RULES_FILE = Path(__file__).with_name("mlps_linux_rules.json")
EMBEDDING_API_KEY = "sk-cad066c22d5b48aaa0958a31d3809627"


def build_embedding_text(rule: dict) -> str:
    # 只拼接最影响向量检索的字段，避免把文档变得过长过散。
    keywords = ", ".join(rule["keywords"])
    parts = [
        f"rule_id: {rule['rule_id']}",
        f"title: {rule['title']}",
        f"category: {rule['category']}",
        f"scope: {rule['scope']}",
        f"summary: {rule['summary']}",
        f"requirement: {rule['requirement']}",
        f"pass_condition: {rule['pass_condition']}",
        f"fail_condition: {rule['fail_condition']}",
        f"keywords: {keywords}",
    ]
    return "\n".join(parts)


def main() -> None:

    rules = json.loads(RULES_FILE.read_text(encoding="utf-8"))
    embedding_client = OpenAI(
        api_key=EMBEDDING_API_KEY,
        base_url=EMBEDDING_API_BASE,
    )
    client = MilvusClient(uri=MILVUS_URI)

    # 先对第一条规则生成向量，用它的维度来创建 collection。
    first_vector = list(
        embedding_client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=build_embedding_text(rules[0]),
        ).data[0].embedding
    )

    if not client.has_collection(COLLECTION_NAME):
        client.create_collection(
            collection_name=COLLECTION_NAME,
            dimension=len(first_vector),
            metric_type="COSINE",
            consistency_level="Strong",
        )

    rows = []
    base_id = int(time.time() * 1000)

    for index, rule in enumerate(rules):
        text = build_embedding_text(rule)
        vector = list(
            embedding_client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=text,
            ).data[0].embedding
        )

        # 当前 Milvus 使用 INT64 主键，其他规则字段写入动态字段即可。
        rows.append(
            {
                "id": base_id + index,
                VECTOR_FIELD: vector,
                "text": text,
                "rule_id": rule["rule_id"],
                "title": rule["title"],
                "category": rule["category"],
                "scope": rule["scope"],
                "summary": rule["summary"],
                "requirement": rule["requirement"],
                "check_method": rule["check_method"],
                "pass_condition": rule["pass_condition"],
                "fail_condition": rule["fail_condition"],
                "remediation": rule["remediation"],
                "source": rule["source"],
                "keywords": rule["keywords"],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    client.insert(collection_name=COLLECTION_NAME, data=rows)

    print("Upload success")
    print(f"collection={COLLECTION_NAME}")
    print(f"rules={len(rows)}")
    print("sample_queries=")
    print("1. uid=0 超级账户 检查")
    print("2. minlen 口令长度 检查")
    print("3. firewalld 防火墙 启用 状态")


if __name__ == "__main__":
    main()
