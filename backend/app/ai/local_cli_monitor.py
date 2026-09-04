"""Activity-aware lifecycle monitor for local Agent CLI processes."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

try:
    import psutil
except ImportError:
    psutil = None

from ..core.exceptions import LLMError
from .cli_process import terminate_cli_process_tree

_CLI_QUOTA_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\bfree\s+usage\s+exceeded\b",
        r"\bfree\s+(plan|tier|usage).{0,60}(exceeded|exhausted|limit|quota)\b",
        r"\busage\s+(exceeded|exhausted)\b",
        r"\binsufficient[_\s-]*quota\b",
        r"\bquota\s+(?:is\s+)?(?:not\s+enough|insufficient|low|depleted)\b",
        r"\bnot\s+enough\s+quota\b",
        r"\bquota[_\s-]*(exceeded|reached|exhausted)\b",
        r"\b(rate|request|usage|daily|monthly|credit|billing)[_\s-]*(limit|quota)\b",
        r"\b(limit|quota)[_\s-]*(exceeded|reached|exhausted)\b",
        r"\btoo\s+many\s+requests\b",
        r"\bresource\s+exhausted\b",
        r"\binsufficient\s+(credits|balance)\b",
        r"\bcredits?\s+(exhausted|depleted|used\s+up)\b",
        r"\bpayment\s+required\b",
        r"\bfree\s+(tier|usage)\s+limit\b",
        r"\bHTTP\s*(402|429)\b",
        r"\bstatus\s*(code)?\s*[:=]?\s*(402|429)\b",
        r"\b(402|429)\s+(Payment Required|Too Many Requests)\b",
        r"额度[已已经]*\s*(用尽|耗尽|不足|达到|超过)",
        r"配额[已已经]*\s*(用尽|耗尽|不足|达到|超过)",
        r"限额[已已经]*\s*(用尽|耗尽|不足|达到|超过)",
        r"(达到|超过).{0,12}(额度|配额|限额|用量上限|请求上限)",
        r"(余额|点数|积分|额度|配额).{0,8}不足",
        r"(今日|每日|本月|免费).{0,8}(额度|配额|次数|用量).{0,8}(用完|耗尽|达到上限)",
        r"(请求过多|速率限制|频率限制)",
    ]
]
_CLI_AUTH_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\bInvalidToken\b",
        r"\binvalid[_\s-]*token\b",
        r"\bexpired[_\s-]*token\b",
        r"\bunauthenticated\b",
        r"\bauthentication\s+(required|failed)\b",
        r"\blog\s*in\s+required\b",
        r"\b(sign|log)\s*in\b",
        r"\bplease\s+(sign|log)\s*in\b",
        r"\bnot\s+authenticated\b",
        r"\b401\s+(Unauthorized|Unauthenticated)\b",
    ]
]
_CLI_PERMISSION_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\b(?:allow|approve|authorize)\b.{0,80}\b(?:mcp|tool|command|access|permission)\b.{0,30}(?:\[?y/n\]?|yes/no|confirm|\?)",
        r"\bdo you want to (?:allow|approve|trust|continue)\b",
        r"\bwould you like to (?:allow|approve|trust|continue)\b",
        r"\bpermission (?:is )?(?:required|needed|requested)\b",
        r"\b(?:requires?|needs?) (?:user )?(?:approval|permission|authorization)\b",
        r"\btrust (?:this|the) (?:folder|workspace|project)\b",
        r"\bpress enter to (?:approve|allow|confirm|continue)\b",
        r"(?:是否|要不要)(?:允许|授权|批准|信任).{0,40}(?:MCP|工具|命令|目录|工作区|项目)?",
        r"(?:需要|请求)(?:用户)?(?:授权|批准|许可|确认).{0,40}(?:MCP|工具|命令|访问)?",
        r"(?:允许|授权|批准|信任).{0,40}(?:吗|？|\?|\[y/n\])",
    ]
]
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class CLIQuotaLimitError(RuntimeError):
    """Raised when a running CLI reports provider quota/rate-limit exhaustion."""

    def __init__(self, message: str, *, stdout: str = "", stderr: str = ""):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


class CLITimeoutError(RuntimeError):
    """Raised when a CLI is silent or retrying beyond Siming's timeout."""

    def __init__(self, message: str, *, stdout: str = "", stderr: str = ""):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


class CLIStalledError(CLITimeoutError):
    """Raised only after the complete CLI process tree has stopped making progress."""


class CLIInterruptedError(RuntimeError):
    """Raised when the monitored CLI process tree disappears unexpectedly."""


class CLITurnTerminal(RuntimeError):
    """Raised when a persisted result has reached a server-enforced turn boundary."""

    def __init__(self, reason: str, *, stdout: str = "", stderr: str = ""):
        super().__init__(reason)
        self.stdout = stdout
        self.stderr = stderr


class CLIPermissionRequiredError(LLMError):
    """The CLI requested an approval that only the user may grant."""

    def __init__(self, message: str, *, stdout: str = "", stderr: str = ""):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


def sample_cli_process_tree(pid: int) -> dict[str, Any]:
    """Return non-sensitive liveness, CPU and IO counters for a CLI process tree."""
    if psutil is None:
        return {"alive": True, "process_count": 1, "metrics_available": False}
    try:
        root = psutil.Process(pid)
        processes = [root, *root.children(recursive=True)]
    except (psutil.Error, OSError):
        return {"alive": False, "process_count": 0, "metrics_available": True}
    cpu_seconds = 0.0
    read_bytes = 0
    write_bytes = 0
    rss_bytes = 0
    alive = 0
    for process in processes:
        try:
            if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                continue
            alive += 1
            cpu = process.cpu_times()
            cpu_seconds += float(cpu.user) + float(cpu.system)
            io_reader = getattr(process, "io_counters", None)
            if callable(io_reader):
                io = io_reader()
                read_bytes += int(getattr(io, "read_bytes", 0) or 0)
                write_bytes += int(getattr(io, "write_bytes", 0) or 0)
            rss_bytes += int(process.memory_info().rss or 0)
        except (psutil.Error, OSError, AttributeError, NotImplementedError):
            continue
    return {
        "alive": alive > 0,
        "process_count": alive,
        "cpu_seconds": round(cpu_seconds, 3),
        "read_bytes": read_bytes,
        "write_bytes": write_bytes,
        "rss_bytes": rss_bytes,
        "metrics_available": True,
    }


def _process_metrics_advanced(previous: dict[str, Any] | None, current: dict[str, Any]) -> bool:
    if previous is None:
        return True
    if not current.get("metrics_available"):
        return False
    return any(
        float(current.get(key) or 0) > float(previous.get(key) or 0)
        for key in ("cpu_seconds", "read_bytes", "write_bytes")
    ) or int(current.get("process_count") or 0) != int(previous.get("process_count") or 0)


def _first_relevant_line(text: str) -> str:
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(pattern.search(stripped) for pattern in _CLI_QUOTA_PATTERNS):
            return stripped[:500]
    return str(text or "").strip()[:500]


def detect_cli_quota_error(*texts: str) -> str:
    combined = "\n".join(_cli_diagnostic_lines(*texts))
    if not combined:
        return ""
    combined = _ANSI_ESCAPE_RE.sub("", combined)
    if not any(pattern.search(combined) for pattern in _CLI_QUOTA_PATTERNS):
        return ""
    detail = _first_relevant_line(combined)
    suffix = f"：{detail}" if detail else ""
    return f"本机 CLI 提供方额度/限额已耗尽或触发速率限制{suffix}"


def detect_cli_auth_error(*texts: str) -> str:
    combined = "\n".join(_cli_diagnostic_lines(*texts))
    if not combined:
        return ""
    combined = _ANSI_ESCAPE_RE.sub("", combined)
    if not any(pattern.search(combined) for pattern in _CLI_AUTH_PATTERNS):
        return ""
    detail = ""
    for line in combined.splitlines():
        stripped = line.strip()
        if stripped and any(pattern.search(stripped) for pattern in _CLI_AUTH_PATTERNS):
            detail = stripped[:500]
            break
    suffix = f"：{detail}" if detail else ""
    return f"本机 CLI 登录凭据无效或已过期{suffix}"


def _cli_diagnostic_lines(*texts: str) -> list[str]:
    """Read CLI diagnostics, never prose or tool arguments inside JSON events.

    A completed tool event may contain quoted permission requests, login text,
    or quotas as ordinary novel content. Only the native error envelope is a
    diagnostic. Incomplete JSON lines stay undecided until the next chunk.
    """
    diagnostics: list[str] = []
    for text in texts:
        for line in _ANSI_ESCAPE_RE.sub("", str(text or "")).splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except (ValueError, TypeError):
                if not stripped.startswith(("{", "[", '"')):
                    diagnostics.append(stripped)
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            if kind in {"error", "turn.failed"} or (
                kind == "result" and event.get("is_error") is True
            ) or (not kind and isinstance(event.get("error"), (str, dict))):
                # Retain diagnostic fields only; input/output payloads may
                # contain user content even in a failed envelope.
                detail = {key: event[key] for key in ("error", "message", "code", "result") if key in event}
                diagnostics.append(json.dumps(detail, ensure_ascii=False))
    return diagnostics


def detect_cli_permission_request(*texts: str) -> str:
    combined = "\n".join(_cli_diagnostic_lines(*texts))
    if not combined:
        return ""
    combined = _ANSI_ESCAPE_RE.sub("", combined)
    matching_line = ""
    for line in combined.splitlines():
        stripped = line.strip()
        if stripped and any(pattern.search(stripped) for pattern in _CLI_PERMISSION_PATTERNS):
            matching_line = stripped[:500]
            break
    if not matching_line:
        return ""
    return f"本机 CLI 请求额外权限，需要你在聊天窗口确认后才能继续：{matching_line}"


@dataclass
class _CLIMonitor:
    process: asyncio.subprocess.Process
    input_bytes: bytes | None
    extra_texts: tuple[str, ...]
    timeout_seconds: float | None
    operation_id: str | None
    external_activity_probe: Callable[[], Any] | None
    terminal_probe: Callable[[], Any] | None
    terminal_poll_seconds: float
    poll_seconds: float
    quiet_seconds: float | None
    suspected_stall_seconds: float | None
    stalled_seconds: float | None
    stop_on_permission_request: bool
    stdout_chunks: list[bytes] = field(default_factory=list)
    stderr_chunks: list[bytes] = field(default_factory=list)
    queue: asyncio.Queue[tuple[str, bytes | None]] = field(default_factory=asyncio.Queue)
    last_metrics: dict[str, Any] | None = None
    last_external_activity: Any = None
    reported_health: str = "active"

    def __post_init__(self) -> None:
        now = time.monotonic()
        self.deadline = now + self.timeout_seconds if self.timeout_seconds else None
        self.quiet_after = self.quiet_seconds or float(
            os.environ.get("SIMING_CLI_QUIET_SECONDS", 600)
        )
        self.suspect_after = self.suspected_stall_seconds or float(
            os.environ.get("SIMING_CLI_SUSPECTED_STALL_SECONDS", 1800)
        )
        self.stalled_after = self.stalled_seconds or float(
            os.environ.get("SIMING_CLI_STALLED_SECONDS", 3600)
        )
        self.last_meaningful_activity = now
        self.last_output_activity = now
        if self.operation_id is None:
            try:
                from ..modules.operations.interfaces.runtime import current_operation_id

                self.operation_id = current_operation_id()
            except Exception:
                self.operation_id = None
        self.readers: list[asyncio.Task[Any]] = []
        self.stdin_task: asyncio.Task[Any] | None = None
        self.terminal_task: asyncio.Task[Any] | None = None
        self.active_readers = 0

    def report(
        self,
        signal: str,
        payload: dict[str, Any] | None = None,
        message: str | None = None,
    ) -> None:
        if not self.operation_id:
            return
        try:
            from ..services.operation_runtime import record_operation_signal

            record_operation_signal(self.operation_id, signal, payload, message)
        except Exception:
            return

    def decoded_output(self) -> tuple[str, str]:
        return (
            b"".join(self.stdout_chunks).decode("utf-8", errors="replace"),
            b"".join(self.stderr_chunks).decode("utf-8", errors="replace"),
        )

    async def _read_stream(
        self,
        name: str,
        stream: asyncio.StreamReader | None,
        chunks: list[bytes],
    ) -> None:
        if stream is None:
            await self.queue.put((name, None))
            return
        while chunk := await stream.read(4096):
            chunks.append(chunk)
            await self.queue.put((name, chunk))
        await self.queue.put((name, None))

    async def _write_stdin(self) -> None:
        if self.input_bytes is None or self.process.stdin is None:
            return
        try:
            self.process.stdin.write(self.input_bytes)
            await self.process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            try:
                self.process.stdin.close()
            except Exception:
                pass

    async def _watch_terminal(self) -> None:
        if self.terminal_probe is None:
            return
        while self.process.returncode is None:
            try:
                terminal_result = self.terminal_probe()
                if asyncio.iscoroutine(terminal_result):
                    terminal_result = await terminal_result
            except Exception:
                terminal_result = None
            if terminal_result:
                await self.queue.put((
                    "__terminal__",
                    str(terminal_result).encode("utf-8"),
                ))
                return
            await asyncio.sleep(max(0.1, self.terminal_poll_seconds))

    def start(self) -> None:
        self.report("phase", {"pid": self.process.pid}, "本机 CLI 已启动，正在等待模型处理")
        self.readers = [
            asyncio.create_task(self._read_stream("stdout", self.process.stdout, self.stdout_chunks)),
            asyncio.create_task(self._read_stream("stderr", self.process.stderr, self.stderr_chunks)),
        ]
        self.stdin_task = asyncio.create_task(self._write_stdin())
        self.terminal_task = (
            asyncio.create_task(self._watch_terminal())
            if self.terminal_probe else None
        )
        self.active_readers = len(self.readers)

    async def _handle_health_poll(self, timeout_error: TimeoutError) -> None:
        now = time.monotonic()
        if self.deadline is not None and now >= self.deadline:
            out_text, err_text = self.decoded_output()
            quota_error = detect_cli_quota_error(*self.extra_texts, err_text, out_text)
            await terminate_cli_process_tree(self.process)
            if quota_error:
                raise CLIQuotaLimitError(
                    quota_error,
                    stdout=out_text,
                    stderr=err_text,
                ) from timeout_error
            seconds = int(self.timeout_seconds or 0)
            raise CLITimeoutError(
                f"本机 CLI 请求超时（{seconds}秒）",
                stdout=out_text,
                stderr=err_text,
            ) from timeout_error

        metrics = sample_cli_process_tree(self.process.pid)
        if not metrics.get("alive") and self.process.returncode is None:
            # Some sandboxed/container runtimes expose subprocess PIDs to
            # asyncio but not to psutil.  A failed psutil sample is therefore
            # not proof that the child vanished.  Keep monitoring the
            # authoritative asyncio lifecycle and mark this sample as
            # inconclusive instead of killing a healthy CLI.
            await asyncio.sleep(0)
            if self.process.returncode is None:
                metrics = {
                    **metrics,
                    "alive": True,
                    "metrics_available": False,
                    "lifecycle_status": "metrics_unavailable",
                }
        try:
            external_activity = (
                self.external_activity_probe()
                if self.external_activity_probe else None
            )
        except Exception:
            external_activity = None
        advanced = _process_metrics_advanced(self.last_metrics, metrics)
        if external_activity is not None and external_activity != self.last_external_activity:
            self.last_external_activity = external_activity
            advanced = True
            self.report(
                "tool",
                {"activity": str(external_activity)[:200]},
                "CLI 已执行司命工具",
            )
        if advanced:
            self.last_meaningful_activity = now
            self.reported_health = "active"
            self.report("process", metrics, "模型进程仍在计算")
        else:
            self.report("heartbeat", metrics)
        self.last_metrics = metrics
        idle = now - self.last_meaningful_activity
        output_idle = now - self.last_output_activity
        if idle >= self.stalled_after and (
            metrics.get("metrics_available") or self.active_readers == 0
        ):
            self.report("stalled", metrics, "CLI 进程已确认长时间没有任何活动")
            out_text, err_text = self.decoded_output()
            await terminate_cli_process_tree(self.process)
            raise CLIStalledError(
                "本机 CLI 已确认卡住：进程、输出、工具调用和磁盘读写均长时间没有变化",
                stdout=out_text,
                stderr=err_text,
            )
        elif idle >= self.suspect_after and self.reported_health != "suspected_stall":
            self.reported_health = "suspected_stall"
            self.report(
                "suspected_stall",
                metrics,
                "暂时没有检测到活动，可继续等待或重试当前任务",
            )
        elif output_idle >= self.quiet_after and self.reported_health == "active":
            self.reported_health = "quiet"
            self.report("quiet", metrics, "暂时没有新文字输出，模型进程仍在运行")

    async def _handle_event(self, name: str, chunk: bytes | None) -> None:
        if name == "__terminal__":
            out_text, err_text = self.decoded_output()
            reason = (chunk or b"").decode("utf-8", errors="replace") or "turn_terminal"
            self.report(
                "terminal",
                {"reason": reason},
                "已达到本模型步骤的终止边界，正在停止本次 CLI",
            )
            await terminate_cli_process_tree(self.process)
            raise CLITurnTerminal(reason, stdout=out_text, stderr=err_text)
        if chunk is None:
            self.active_readers -= 1
            return
        now = time.monotonic()
        self.last_meaningful_activity = now
        self.last_output_activity = now
        self.reported_health = "active"
        self.report("output", {"stream": name, "bytes": len(chunk)}, "模型正在返回内容")
        out_text, err_text = self.decoded_output()
        quota_error = detect_cli_quota_error(*self.extra_texts, err_text, out_text)
        if quota_error:
            await terminate_cli_process_tree(self.process)
            raise CLIQuotaLimitError(quota_error, stdout=out_text, stderr=err_text)
        if self.stop_on_permission_request:
            permission_error = detect_cli_permission_request(
                *self.extra_texts,
                err_text,
                out_text,
            )
            if permission_error:
                await terminate_cli_process_tree(self.process)
                raise CLIPermissionRequiredError(
                    permission_error,
                    stdout=out_text,
                    stderr=err_text,
                )

    async def _monitor_loop(self) -> None:
        while self.active_readers or self.process.returncode is None:
            if self.active_readers == 0 and self.process.returncode is not None:
                return
            now = time.monotonic()
            remaining = self.deadline - now if self.deadline is not None else None
            if remaining is not None and remaining <= 0:
                await self._handle_health_poll(TimeoutError())
                continue
            wait_seconds = (
                max(0.1, min(self.poll_seconds, remaining))
                if remaining is not None
                else max(0.1, self.poll_seconds)
            )
            try:
                name, chunk = await asyncio.wait_for(
                    self.queue.get(),
                    timeout=wait_seconds,
                )
            except TimeoutError as exc:
                await self._handle_health_poll(exc)
                continue
            await self._handle_event(name, chunk)

    async def run(self) -> tuple[bytes, bytes]:
        self.start()
        try:
            await self._monitor_loop()
            await self.process.wait()
            if self.stdin_task is not None:
                await self.stdin_task
            return b"".join(self.stdout_chunks), b"".join(self.stderr_chunks)
        except asyncio.CancelledError:
            out_text, err_text = self.decoded_output()
            quota_error = detect_cli_quota_error(
                *self.extra_texts,
                err_text,
                out_text,
            )
            await terminate_cli_process_tree(self.process)
            if quota_error:
                raise CLIQuotaLimitError(
                    quota_error,
                    stdout=out_text,
                    stderr=err_text,
                )
            raise
        finally:
            tasks = [
                *self.readers,
                *([self.stdin_task] if self.stdin_task else []),
                *([self.terminal_task] if self.terminal_task else []),
            ]
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def communicate_with_cli_quota_detection(
    process: asyncio.subprocess.Process,
    *,
    input_bytes: bytes | None = None,
    extra_texts: tuple[str, ...] = (),
    timeout_seconds: float | None = None,
    operation_id: str | None = None,
    external_activity_probe: Callable[[], Any] | None = None,
    terminal_probe: Callable[[], Any] | None = None,
    terminal_poll_seconds: float = 0.25,
    poll_seconds: float = 5.0,
    quiet_seconds: float | None = None,
    suspected_stall_seconds: float | None = None,
    stalled_seconds: float | None = None,
    stop_on_permission_request: bool = False,
) -> tuple[bytes, bytes]:
    monitor = _CLIMonitor(
        process=process,
        input_bytes=input_bytes,
        extra_texts=extra_texts,
        timeout_seconds=timeout_seconds,
        operation_id=operation_id,
        external_activity_probe=external_activity_probe,
        terminal_probe=terminal_probe,
        terminal_poll_seconds=terminal_poll_seconds,
        poll_seconds=poll_seconds,
        quiet_seconds=quiet_seconds,
        suspected_stall_seconds=suspected_stall_seconds,
        stalled_seconds=stalled_seconds,
        stop_on_permission_request=stop_on_permission_request,
    )
    return await monitor.run()



__all__ = [
    "CLIInterruptedError",
    "CLIPermissionRequiredError",
    "CLIQuotaLimitError",
    "CLIStalledError",
    "CLITimeoutError",
    "CLITurnTerminal",
    "communicate_with_cli_quota_detection",
    "detect_cli_auth_error",
    "detect_cli_permission_request",
    "detect_cli_quota_error",
    "sample_cli_process_tree",
]
