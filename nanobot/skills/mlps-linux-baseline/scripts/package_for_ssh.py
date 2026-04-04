#!/usr/bin/env python3
"""Convert a local shell script into a remote command string for ssh_exec."""

from __future__ import annotations

import argparse
from pathlib import Path


def build_remote_command(script_text: str, remote_path: str) -> str:
    # 用带引号的 heredoc 包裹脚本正文，避免变量和特殊字符在上传阶段被提前展开。
    return "\n".join(
        [
            f"cat <<'NANOBOT_SCRIPT_EOF' > {remote_path}",
            script_text.rstrip(),
            "NANOBOT_SCRIPT_EOF",
            f"chmod +x {remote_path}",
            f"bash {remote_path}",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Package a local shell script for ssh_exec.")
    parser.add_argument("script_path", help="Path to the local .sh file")
    parser.add_argument(
        "--remote-path",
        default="/tmp/nanobot_mlps_check.sh",
        help="Target path on the remote Linux host",
    )
    args = parser.parse_args()

    script_path = Path(args.script_path).resolve()
    script_text = script_path.read_text(encoding="utf-8")
    print(build_remote_command(script_text, args.remote_path))


if __name__ == "__main__":
    main()
