"""SSH remote execution tool."""

from __future__ import annotations

import asyncio
import ctypes
import platform
import subprocess
import textwrap
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool


class SshExecTool(Tool):
    """Connect to Linux over SSH, run a command, and save a screenshot."""

    name = "ssh_exec"
    description = (
        "Connect to a Linux host over SSH, execute a shell command, and save a screenshot. "
        "On Windows, screenshots come from a real PowerShell window that displays the command "
        "and its result. On Linux, the current fallback renders the result as an image."
    )
    parameters = {
        "type": "object",
        "properties": {
            "host": {"type": "string", "description": "SSH target host or IP."},
            "port": {
                "type": "integer",
                "description": "SSH port. Defaults to 22.",
                "minimum": 1,
                "maximum": 65535,
            },
            "username": {"type": "string", "description": "SSH username."},
            "password": {
                "type": "string",
                "description": "Optional SSH password. Use password or private_key_path.",
            },
            "private_key_path": {
                "type": "string",
                "description": "Optional private key file path.",
            },
            "private_key_passphrase": {
                "type": "string",
                "description": "Optional passphrase for the private key.",
            },
            "command": {
                "type": "string",
                "description": "The shell command to execute on the remote Linux host.",
                "minLength": 1,
            },
            "timeout": {
                "type": "integer",
                "description": "Command timeout in seconds. Defaults to configured timeout.",
                "minimum": 1,
                "maximum": 3600,
            },
            "capture_screenshot": {
                "type": "boolean",
                "description": "Whether to save a screenshot of the command output.",
                "default": True,
            },
        },
        "required": ["host", "username", "command"],
    }

    def __init__(self, workspace: Path, config: Any):
        self.workspace = workspace
        self.config = config

    async def execute(
        self,
        command: str,
        host: str | None = None,
        port: int | None = None,
        username: str | None = None,
        password: str | None = None,
        private_key_path: str | None = None,
        private_key_passphrase: str | None = None,
        timeout: int | None = None,
        capture_screenshot: bool = True,
        **kwargs: Any,
    ) -> str:
        connection = self._build_connection(
            host=host,
            port=port,
            username=username,
            password=password,
            private_key_path=private_key_path,
            private_key_passphrase=private_key_passphrase,
            timeout=timeout,
        )

        if not connection["host"]:
            return "Error: SSH host is required."
        if not connection["username"]:
            return "Error: SSH username is required."
        if not connection["password"] and not connection["private_key_path"]:
            return "Error: Provide either SSH password or private_key_path."

        try:
            result = await asyncio.to_thread(self._execute_sync, connection, command)
            screenshot_path: str | None = None
            if capture_screenshot:
                screenshot_path = await asyncio.to_thread(
                    self._save_screenshot,
                    connection["host"],
                    command,
                    result,
                )
            return self._format_result(connection["host"], command, result, screenshot_path)
        except ImportError:
            return (
                "Error: SSH tool requires extra dependencies. "
                "Run `pip install \"nanobot-ai[ssh]\"` in the current environment."
            )
        except Exception as exc:
            logger.exception("SSH execution failed")
            return f"Error: SSH execution failed: {type(exc).__name__}: {exc}"

    def _build_connection(
        self,
        *,
        host: str | None,
        port: int | None,
        username: str | None,
        password: str | None,
        private_key_path: str | None,
        private_key_passphrase: str | None,
        timeout: int | None,
    ) -> dict[str, Any]:
        return {
            # 连接信息全部由当前对话传入，避免把主机和凭据固化在配置文件中。
            "host": (host or "").strip(),
            "port": port or 22,
            "username": (username or "").strip(),
            "password": password or "",
            "private_key_path": private_key_path or "",
            "private_key_passphrase": private_key_passphrase or "",
            "timeout": timeout or self.config.command_timeout,
        }

    def _execute_sync(self, connection: dict[str, Any], command: str) -> dict[str, Any]:
        import paramiko

        client = paramiko.SSHClient()
        if self.config.auto_add_host_key:
            # 开发场景下默认自动接收主机指纹，减少首次连接阻塞。
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs: dict[str, Any] = {
            "hostname": connection["host"],
            "port": connection["port"],
            "username": connection["username"],
            "timeout": self.config.connect_timeout,
            "banner_timeout": self.config.connect_timeout,
            "auth_timeout": self.config.connect_timeout,
            "look_for_keys": bool(connection["private_key_path"]),
        }
        if connection["password"]:
            connect_kwargs["password"] = connection["password"]
        if connection["private_key_path"]:
            connect_kwargs["key_filename"] = connection["private_key_path"]
        if connection["private_key_passphrase"]:
            connect_kwargs["passphrase"] = connection["private_key_passphrase"]

        try:
            client.connect(**connect_kwargs)
            stdin, stdout, stderr = client.exec_command(
                command,
                timeout=connection["timeout"],
                get_pty=True,
            )
            stdout.channel.settimeout(connection["timeout"])
            stderr.channel.settimeout(connection["timeout"])

            stdout_text = stdout.read().decode("utf-8", errors="replace")
            stderr_text = stderr.read().decode("utf-8", errors="replace")
            exit_status = stdout.channel.recv_exit_status()
            return {
                "exit_status": exit_status,
                "stdout": stdout_text,
                "stderr": stderr_text,
            }
        finally:
            client.close()

    def _save_screenshot(
        self,
        host: str,
        command: str,
        result: dict[str, Any],
    ) -> str:
        if platform.system() == "Windows":
            return self._save_windows_window_screenshot(host, command, result)
        return self._save_rendered_screenshot(host, command, result)

    def _save_windows_window_screenshot(
        self,
        host: str,
        command: str,
        result: dict[str, Any],
    ) -> str:
        import win32con
        import win32gui
        from PIL import ImageGrab

        screenshot_dir = self.workspace / self.config.screenshot_subdir
        screenshot_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"ssh_{self._safe_name(host)}_{timestamp}"
        output_path = screenshot_dir / f"{base_name}.png"
        content_path = screenshot_dir / f"{base_name}.txt"
        window_title = f"nanobot-ssh-{self._safe_name(host)}-{timestamp}"

        content_path.write_text(
            self._build_windows_first_page_text(host, command, result),
            encoding="utf-8",
        )
        escaped_content_path = str(content_path).replace("'", "''")

        # 打开一个更大的真实 PowerShell 窗口，只展示第一页内容，确保命令和首屏结果都可见。
        ps_command = (
            f"$Host.UI.RawUI.WindowTitle='{window_title}'; "
            "$raw = $Host.UI.RawUI; "
            "$size = $raw.BufferSize; "
            "$size.Width = 160; "
            "$size.Height = 4000; "
            "$raw.BufferSize = $size; "
            "$winsize = $raw.WindowSize; "
            "$winsize.Width = 160; "
            "$winsize.Height = 45; "
            "$raw.WindowSize = $winsize; "
            f"Get-Content -Path '{escaped_content_path}'"
        )
        process = subprocess.Popen(
            ["powershell.exe", "-NoLogo", "-NoExit", "-Command", ps_command],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )

        hwnd = self._wait_for_window(window_title, timeout_seconds=8.0)
        if hwnd is None:
            process.terminate()
            raise RuntimeError("Failed to locate the PowerShell window for screenshot capture.")

        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            # 尽量放大窗口，让第一页容纳更多内容。
            win32gui.MoveWindow(hwnd, 40, 40, 1500, 980, True)
        except Exception:
            pass

        time.sleep(1.0)
        rect = win32gui.GetWindowRect(hwnd)

        if not self._print_window_to_file(hwnd, output_path):
            image = ImageGrab.grab(bbox=rect)
            image.save(output_path)

        try:
            process.terminate()
            process.wait(timeout=3)
        except Exception:
            pass

        return str(output_path)

    def _build_windows_first_page_text(self, host: str, command: str, result: dict[str, Any]) -> str:
        """只保留首屏内容，避免真实终端自动滚动后截到中间位置。"""
        full_text = self._build_screenshot_text(host, command, result)
        lines = full_text.splitlines() or [""]
        page_lines = max(12, self.config.screenshot_rows)
        visible = lines[:page_lines]
        if len(lines) > page_lines:
            visible.extend(
                [
                    "",
                    "... output truncated for first-page screenshot ...",
                ]
            )
        return "\n".join(visible)

    def _wait_for_window(self, title: str, timeout_seconds: float) -> int | None:
        import win32gui

        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            matches: list[int] = []

            def _enum(hwnd: int, windows: list[int]) -> None:
                if not win32gui.IsWindowVisible(hwnd):
                    return
                if title == win32gui.GetWindowText(hwnd):
                    windows.append(hwnd)

            win32gui.EnumWindows(_enum, matches)
            if matches:
                return matches[0]
            time.sleep(0.2)
        return None

    def _print_window_to_file(self, hwnd: int, output_path: Path) -> bool:
        import win32gui
        import win32ui
        from PIL import Image

        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        width = right - left
        height = bottom - top
        if width <= 0 or height <= 0:
            return False

        hwnd_dc = win32gui.GetWindowDC(hwnd)
        src_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        mem_dc = src_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(src_dc, width, height)
        mem_dc.SelectObject(bitmap)

        result = ctypes.windll.user32.PrintWindow(hwnd, mem_dc.GetSafeHdc(), 2)
        if result != 1:
            result = ctypes.windll.user32.PrintWindow(hwnd, mem_dc.GetSafeHdc(), 0)

        if result == 1:
            bmpinfo = bitmap.GetInfo()
            bmpstr = bitmap.GetBitmapBits(True)
            image = Image.frombuffer(
                "RGB",
                (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
                bmpstr,
                "raw",
                "BGRX",
                0,
                1,
            )
            image.save(output_path)

        win32gui.DeleteObject(bitmap.GetHandle())
        mem_dc.DeleteDC()
        src_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)
        return result == 1

    def _save_rendered_screenshot(
        self,
        host: str,
        command: str,
        result: dict[str, Any],
    ) -> str:
        from PIL import Image, ImageDraw, ImageFont

        screenshot_dir = self.workspace / self.config.screenshot_subdir
        screenshot_dir.mkdir(parents=True, exist_ok=True)

        content = self._build_screenshot_text(host, command, result)
        wrapped = self._wrap_text(content)
        lines = wrapped.splitlines() or [""]

        font = ImageFont.load_default()
        left = 24
        top = 24
        line_height = 18
        width = 1100
        height = max(220, top * 2 + len(lines) * line_height)

        image = Image.new("RGB", (width, height), color=(15, 23, 42))
        draw = ImageDraw.Draw(image)
        draw.text((left, top), wrapped, font=font, fill=(226, 232, 240))

        filename = f"ssh_{self._safe_name(host)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        output_path = screenshot_dir / filename
        image.save(output_path)
        return str(output_path)


    def _build_screenshot_text(self, host: str, command: str, result: dict[str, Any]) -> str:
        stdout_text = (result.get("stdout") or "").strip()
        stderr_text = (result.get("stderr") or "").strip()
        body_parts = [
            f"Host: {host}",
            f"Command: {command}",
            f"Exit Status: {result.get('exit_status')}",
            "",
            "[STDOUT]",
            stdout_text or "(empty)",
        ]
        if stderr_text:
            body_parts.extend(["", "[STDERR]", stderr_text])
        text = "\n".join(body_parts)
        # 截图只保留有限字符，避免图片过高难以查看。
        return text[: self.config.max_screenshot_chars]

    def _wrap_text(self, text: str) -> str:
        wrapped_lines: list[str] = []
        for line in text.splitlines() or [""]:
            if not line:
                wrapped_lines.append("")
                continue
            wrapped_lines.extend(
                textwrap.wrap(
                    line,
                    width=max(20, self.config.screenshot_columns),
                    replace_whitespace=False,
                    drop_whitespace=False,
                )
                or [""]
            )
        return "\n".join(wrapped_lines[: max(5, self.config.screenshot_rows * 6)])

    def _format_result(
        self,
        host: str,
        command: str,
        result: dict[str, Any],
        screenshot_path: str | None,
    ) -> str:
        stdout_text = (result.get("stdout") or "").strip()
        stderr_text = (result.get("stderr") or "").strip()
        sections = [
            f"SSH command completed on {host}",
            f"command: {command}",
            f"exit_status: {result.get('exit_status')}",
        ]
        if screenshot_path:
            sections.append(f"screenshot_path: {screenshot_path}")
            sections.append(
                "Use the `message` tool with the screenshot path in `media` if you want to send the image to the user."
            )
        sections.extend(["", "[stdout]", stdout_text[:4000] or "(empty)"])
        if stderr_text:
            sections.extend(["", "[stderr]", stderr_text[:2000]])
        return "\n".join(sections)

    @staticmethod
    def _safe_name(value: str) -> str:
        cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)
        return cleaned.strip("_") or "host"
