#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import signal
import ssl
import time
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlsplit


BUFFER_SIZE = 64 * 1024
HOP_BY_HOP_HEADERS = {
    b"connection",
    b"keep-alive",
    b"proxy-authenticate",
    b"proxy-authorization",
    b"te",
    b"trailer",
    b"transfer-encoding",
    b"upgrade",
}

ClientHandler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


def tracked_client_callback(
    handler: ClientHandler,
) -> tuple[Callable[[asyncio.StreamReader, asyncio.StreamWriter], None], set[asyncio.Task[None]]]:
    """Keep client tasks alive until completion or explicit server shutdown."""
    active_tasks: set[asyncio.Task[None]] = set()

    def on_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.create_task(handler(reader, writer))
        active_tasks.add(task)
        task.add_done_callback(active_tasks.discard)

    return on_client, active_tasks


async def cancel_client_tasks(active_tasks: set[asyncio.Task[None]]) -> None:
    tasks = tuple(active_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(BUFFER_SIZE):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        with contextlib.suppress(Exception):
            writer.close()
            await writer.wait_closed()


async def relay_streams(
    left_reader: asyncio.StreamReader,
    left_writer: asyncio.StreamWriter,
    right_reader: asyncio.StreamReader,
    right_writer: asyncio.StreamWriter,
) -> None:
    await asyncio.gather(pump(left_reader, right_writer), pump(right_reader, left_writer))


def parse_connect_target(request: bytes) -> tuple[str, int] | None:
    try:
        first_line = request.split(b"\r\n", 1)[0].decode("ascii")
        method, target, _version = first_line.split(" ", 2)
    except (UnicodeDecodeError, ValueError):
        return None
    if method.upper() != "CONNECT" or ":" not in target:
        return None
    host, port_text = target.rsplit(":", 1)
    try:
        port = int(port_text)
    except ValueError:
        return None
    return host.rstrip(".").lower(), port


def isolate_messages_request(body: bytes, target: str, isolation_key: str | None) -> bytes:
    """Add run-scoped cache isolation without changing the prompt content."""
    if not isolation_key or "/v1/messages" not in target or not body:
        return body
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body
    if not isinstance(payload, dict):
        return body
    metadata = payload.get("metadata")
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        return body
    metadata["user_id"] = f"ecosyncbench-{isolation_key}"
    payload["metadata"] = metadata
    stop_sequences = payload.get("stop_sequences")
    if stop_sequences is None:
        stop_sequences = []
    if not isinstance(stop_sequences, list):
        return body
    cache_buster = f"ecosyncbench-cache-bust-{isolation_key}"
    if cache_buster not in stop_sequences and len(stop_sequences) < 4:
        stop_sequences.append(cache_buster)
    payload["stop_sequences"] = stop_sequences
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


async def handle_acl_proxy(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    allowed_hosts: set[str],
) -> None:
    try:
        request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=15)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError):
        writer.close()
        await writer.wait_closed()
        return
    target = parse_connect_target(request)
    if target is None:
        writer.write(b"HTTP/1.1 405 Method Not Allowed\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        return
    host, port = target
    if host not in allowed_hosts or port != 443:
        writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        return
    try:
        upstream_reader, upstream_writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=20)
    except (OSError, asyncio.TimeoutError):
        writer.write(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        return
    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await writer.drain()
    await relay_streams(reader, writer, upstream_reader, upstream_writer)


async def handle_unix_relay(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    socket_path: Path,
) -> None:
    try:
        upstream_reader, upstream_writer = await asyncio.open_unix_connection(str(socket_path))
    except OSError:
        writer.close()
        await writer.wait_closed()
        return
    await relay_streams(reader, writer, upstream_reader, upstream_writer)


async def read_http_request(reader: asyncio.StreamReader) -> tuple[bytes, list[tuple[bytes, bytes]], bytes] | None:
    try:
        raw_headers = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=30)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError):
        return None
    lines = raw_headers[:-4].split(b"\r\n")
    if not lines:
        return None
    request_line = lines[0]
    headers: list[tuple[bytes, bytes]] = []
    content_length = 0
    chunked = False
    for line in lines[1:]:
        if b":" not in line:
            return None
        name, value = line.split(b":", 1)
        name = name.strip()
        value = value.strip()
        headers.append((name, value))
        lower = name.lower()
        if lower == b"content-length":
            try:
                content_length = int(value)
            except ValueError:
                return None
        elif lower == b"transfer-encoding" and b"chunked" in value.lower():
            chunked = True
    if chunked:
        body = bytearray()
        while True:
            try:
                size_line = await reader.readuntil(b"\r\n")
                size = int(size_line.split(b";", 1)[0].strip(), 16)
                chunk = await reader.readexactly(size + 2)
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError):
                return None
            body.extend(chunk[:-2])
            if size == 0:
                while True:
                    trailer = await reader.readuntil(b"\r\n")
                    if trailer == b"\r\n":
                        break
                break
        return request_line, headers, bytes(body)
    try:
        body = await reader.readexactly(content_length) if content_length else b""
    except asyncio.IncompleteReadError:
        return None
    return request_line, headers, body


class RequestBudget:
    def __init__(self, maximum: int, log_path: Path) -> None:
        self.maximum = maximum
        self.log_path = log_path
        self.count = 0
        self.lock = asyncio.Lock()

    async def consume(self, method: str, path: str) -> tuple[bool, int]:
        async with self.lock:
            self.count += 1
            allowed = self.maximum == 0 or self.count <= self.maximum
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "timestamp": time.time(),
                            "request_number": self.count,
                            "max_requests": self.maximum,
                            "allowed": allowed,
                            "method": method,
                            "path": path,
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
            return allowed, self.count


async def handle_counting_gateway(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    upstream_host: str,
    upstream_port: int,
    upstream_tls: bool,
    budget: RequestBudget,
    request_isolation_key: str | None,
) -> None:
    request = await read_http_request(reader)
    if request is None:
        writer.close()
        await writer.wait_closed()
        return
    request_line, headers, body = request
    try:
        method_raw, target_raw, version = request_line.split(b" ", 2)
        method = method_raw.decode("ascii")
        target = target_raw.decode("ascii")
    except (UnicodeDecodeError, ValueError):
        writer.write(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        return
    if method.upper() == "CONNECT" or not target.startswith("/"):
        writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        return
    allowed, request_number = await budget.consume(method, target)
    if not allowed:
        payload = json.dumps(
            {"error": "ecosyncbench_api_request_limit", "limit": budget.maximum, "request": request_number}
        ).encode("utf-8")
        writer.write(
            b"HTTP/1.1 429 Too Many Requests\r\n"
            + b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(payload)}\r\n".encode("ascii")
            + b"Connection: close\r\n\r\n"
            + payload
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        return
    body = isolate_messages_request(body, target, request_isolation_key)
    ssl_context = ssl.create_default_context() if upstream_tls else None
    try:
        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(
                upstream_host,
                upstream_port,
                ssl=ssl_context,
                server_hostname=upstream_host if upstream_tls else None,
            ),
            timeout=20,
        )
    except (OSError, asyncio.TimeoutError):
        writer.write(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        return
    forwarded = [method_raw + b" " + target_raw + b" " + version]
    for name, value in headers:
        if name.lower() in HOP_BY_HOP_HEADERS or name.lower() == b"host":
            continue
        if name.lower() == b"content-length":
            continue
        forwarded.append(name + b": " + value)
    if body:
        forwarded.append(f"Content-Length: {len(body)}".encode("ascii"))
    if request_isolation_key:
        forwarded.append(f"X-Ecosyncbench-Request-Isolation: {request_isolation_key}".encode("ascii"))
    forwarded.extend(
        [
            f"Host: {upstream_host}".encode("ascii"),
            b"Connection: close",
            b"",
            b"",
        ]
    )
    upstream_writer.write(b"\r\n".join(forwarded) + body)
    await upstream_writer.drain()
    await pump(upstream_reader, writer)
    with contextlib.suppress(Exception):
        upstream_writer.close()
        await upstream_writer.wait_closed()


async def serve_until_signalled(server: asyncio.AbstractServer) -> None:
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    async with server:
        await stop.wait()


async def run_server(socket_path: Path, allowed_hosts: set[str]) -> None:
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists() or socket_path.is_socket():
        socket_path.unlink()
    on_client, active_tasks = tracked_client_callback(
        lambda reader, writer: handle_acl_proxy(reader, writer, allowed_hosts)
    )
    server = await asyncio.start_unix_server(on_client, path=str(socket_path))
    os.chmod(socket_path, 0o600)
    try:
        await serve_until_signalled(server)
    finally:
        await cancel_client_tasks(active_tasks)
        if socket_path.exists() or socket_path.is_socket():
            socket_path.unlink()


async def run_relay(
    socket_path: Path,
    host: str,
    port: int,
    ready_file: Path | None = None,
) -> None:
    on_client, active_tasks = tracked_client_callback(
        lambda reader, writer: handle_unix_relay(reader, writer, socket_path)
    )
    server = await asyncio.start_server(on_client, host=host, port=port)
    if ready_file is not None:
        ready_file.parent.mkdir(parents=True, exist_ok=True)
        ready_file.write_text("ready\n", encoding="utf-8")
    try:
        await serve_until_signalled(server)
    finally:
        await cancel_client_tasks(active_tasks)
        if ready_file is not None:
            ready_file.unlink(missing_ok=True)


async def run_gateway(
    socket_path: Path,
    upstream_url: str,
    max_requests: int,
    log_path: Path,
    request_isolation_key: str | None,
) -> None:
    parsed = urlsplit(upstream_url)
    loopback_hosts = {"127.0.0.1", "::1", "localhost"}
    if not parsed.hostname or (
        parsed.scheme != "https"
        and not (parsed.scheme == "http" and parsed.hostname in loopback_hosts)
    ):
        raise ValueError("gateway upstream must be an https origin or loopback http origin")
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists() or socket_path.is_socket():
        socket_path.unlink()
    budget = RequestBudget(max_requests, log_path)
    on_client, active_tasks = tracked_client_callback(
        lambda reader, writer: handle_counting_gateway(
            reader,
            writer,
            parsed.hostname or "",
            parsed.port or (443 if parsed.scheme == "https" else 80),
            parsed.scheme == "https",
            budget,
            request_isolation_key,
        )
    )
    server = await asyncio.start_unix_server(on_client, path=str(socket_path))
    os.chmod(socket_path, 0o600)
    try:
        await serve_until_signalled(server)
    finally:
        await cancel_client_tasks(active_tasks)
        if socket_path.exists() or socket_path.is_socket():
            socket_path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description="Restrict agent egress to explicitly allowed LLM API hosts.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    server = subparsers.add_parser("server")
    server.add_argument("--socket", required=True, type=Path)
    server.add_argument("--allow-host", action="append", required=True)

    relay = subparsers.add_parser("relay")
    relay.add_argument("--socket", required=True, type=Path)
    relay.add_argument("--host", default="127.0.0.1")
    relay.add_argument("--port", type=int, default=18080)
    relay.add_argument("--ready-file", type=Path)

    gateway = subparsers.add_parser("gateway")
    gateway.add_argument("--socket", required=True, type=Path)
    gateway.add_argument("--upstream-url", required=True)
    gateway.add_argument("--max-requests", required=True, type=int)
    gateway.add_argument("--log", required=True, type=Path)
    gateway.add_argument("--request-isolation-key")

    args = parser.parse_args()
    if args.command == "server":
        allowed = {str(host).rstrip(".").lower() for host in args.allow_host}
        asyncio.run(run_server(args.socket.resolve(), allowed))
    elif args.command == "relay":
        asyncio.run(run_relay(args.socket, args.host, args.port, args.ready_file))
    else:
        asyncio.run(
            run_gateway(
                args.socket.resolve(),
                args.upstream_url,
                args.max_requests,
                args.log.resolve(),
                args.request_isolation_key,
            )
        )


if __name__ == "__main__":
    main()
