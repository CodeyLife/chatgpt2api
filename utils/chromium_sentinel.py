"""真实 Chromium Sentinel SDK token provider。

通过 Chrome DevTools Protocol 启动本机 Chrome，在真实浏览器环境里执行官方
Sentinel SDK 的 `SentinelSDK.token(flow)` / `sessionObserverToken(flow)`。
不依赖 Playwright/Selenium，失败时由调用方回退到 Python PoW 实现。
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import subprocess
import shutil
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


DEFAULT_SENTINEL_SDK_URL = "https://sentinel.openai.com/sentinel/20260219f9f6/sdk.js"
DEFAULT_CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    str(Path.home() / r"AppData\Local\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    str(Path.home() / r"AppData\Local\Microsoft\Edge\Application\msedge.exe"),
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/snap/bin/chromium",
    "/opt/google/chrome/chrome",
]

AUTH_PAGE_URL = "https://auth.openai.com/"
BLOCKED_AUTH_RESOURCE_URLS = (
    "*://auth-cdn.oaistatic.com/*",
    "*://cdn.openai.com/*",
    "*://accounts.google.com/*",
    "*://chatgpt.com/*.js*",
    "*://chat.openai.com/*.js*",
    "*.css",
    "*.css?*",
    "*.png",
    "*.png?*",
    "*.jpg",
    "*.jpg?*",
    "*.jpeg",
    "*.jpeg?*",
    "*.gif",
    "*.gif?*",
    "*.webp",
    "*.webp?*",
    "*.svg",
    "*.svg?*",
    "*.ico",
    "*.ico?*",
    "*.woff*",
    "*.ttf*",
    "*.otf*",
    "*.mp4*",
    "*.webm*",
)


@dataclass(frozen=True)
class ChromiumSentinelResult:
    token: str
    so_token: str = ""


class _CDPClient:
    def __init__(
        self,
        websocket_url: str,
        timeout: float,
        event_handler: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.websocket_url = websocket_url
        self.timeout = timeout
        self.event_handler = event_handler
        self._id = 0
        self._socket: socket.socket | None = None

    def __enter__(self) -> "_CDPClient":
        url = urllib.parse.urlparse(self.websocket_url)
        host = str(url.hostname or "127.0.0.1")
        port = int(url.port or 80)
        path = url.path or "/"
        sock = socket.create_connection((host, port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
        sock.sendall(request)
        response = sock.recv(4096)
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise RuntimeError(f"Chrome DevTools WebSocket 握手失败: {response[:120]!r}")
        self._socket = sock
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._socket is not None:
                self._socket.close()
        finally:
            self._socket = None

    def call(self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
        self._id += 1
        message_id = self._id
        self._send({"id": message_id, "method": method, "params": params or {}})
        deadline = time.monotonic() + float(timeout or self.timeout)
        while time.monotonic() < deadline:
            message = self._recv()
            if "id" not in message and self.event_handler is not None:
                self.event_handler(message)
            if message.get("id") == message_id:
                if "error" in message:
                    raise RuntimeError(f"CDP {method} failed: {message['error']}")
                return message
        raise TimeoutError(f"CDP {method} timeout")

    def _send(self, value: dict[str, Any]) -> None:
        if self._socket is None:
            raise RuntimeError("CDP socket is closed")
        data = json.dumps(value, separators=(",", ":")).encode("utf-8")
        header = bytearray([0x81])
        length = len(data)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.extend([0x80 | 126])
            header.extend(struct.pack("!H", length))
        else:
            header.extend([0x80 | 127])
            header.extend(struct.pack("!Q", length))
        mask = os.urandom(4)
        header.extend(mask)
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        self._socket.sendall(bytes(header) + payload)

    def _read_exact(self, length: int) -> bytes:
        if self._socket is None:
            raise RuntimeError("CDP socket is closed")
        out = b""
        while len(out) < length:
            chunk = self._socket.recv(length - len(out))
            if not chunk:
                raise RuntimeError("CDP socket closed")
            out += chunk
        return out

    def _recv(self) -> dict[str, Any]:
        first, second = self._read_exact(2)
        opcode = first & 0x0F
        if opcode == 8:
            raise RuntimeError("CDP socket closed by browser")
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(8))[0]
        mask = self._read_exact(4) if second & 0x80 else None
        payload = self._read_exact(length)
        if mask:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return json.loads(payload.decode("utf-8", errors="replace"))


def _find_chrome(chrome_path: str = "") -> str:
    explicit = str(chrome_path or os.getenv("CHATGPT2API_CHROME_PATH") or "").strip()
    candidates = [explicit] if explicit else []
    candidates.extend(DEFAULT_CHROME_PATHS)
    candidates.extend(
        shutil.which(name) or ""
        for name in (
            "google-chrome",
            "google-chrome-stable",
            "chromium",
            "chromium-browser",
            "msedge",
        )
    )
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise RuntimeError("未找到 Chrome/Edge，可设置 CHATGPT2API_CHROME_PATH")


def _sdk_cache_path(sdk_url: str) -> Path:
    safe = "".join(ch if ch.isalnum() else "_" for ch in sdk_url)[-120:]
    return Path(tempfile.gettempdir()) / "chatgpt2api_sentinel_sdk" / f"{safe}.js"


def _load_sdk(sdk_url: str, user_agent: str, timeout: float) -> str:
    cache = _sdk_cache_path(sdk_url)
    if cache.exists() and cache.stat().st_size > 1000:
        return cache.read_text(encoding="utf-8", errors="replace")
    request = urllib.request.Request(
        sdk_url,
        headers={
            "User-Agent": user_agent,
            "Accept": "*/*",
            "Referer": "https://auth.openai.com/",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        source = response.read().decode("utf-8", errors="replace")
    if "SentinelSDK" not in source:
        raise RuntimeError("Sentinel SDK 响应缺少 SentinelSDK")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(source, encoding="utf-8")
    return source


def _read_devtools_port(user_data_dir: Path, timeout: float) -> int:
    port_file = user_data_dir / "DevToolsActivePort"
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        if port_file.exists():
            try:
                text = port_file.read_text(encoding="utf-8", errors="replace").splitlines()
                if text:
                    return int(text[0])
            except (OSError, PermissionError) as error:
                # Windows 服务环境中 DevToolsActivePort 可能刚创建就短暂锁定。
                # 不要立刻 fallback，继续等到 Chrome 完成写入/释放句柄。
                last_error = str(error)
        time.sleep(0.1)
    suffix = f"; last_error={last_error}" if last_error else ""
    raise TimeoutError(f"等待 Chrome DevToolsActivePort 超时{suffix}")


def _json_get(url: str, timeout: float) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _select_page(port: int, timeout: float) -> dict[str, Any]:
    tabs = _json_get(f"http://127.0.0.1:{port}/json", timeout)
    browser_pages = [
        item
        for item in tabs
        if item.get("type") == "page"
        and item.get("webSocketDebuggerUrl")
    ]
    preferred_hosts = (
        "https://auth.openai.com",
        # auth.openai.com/ 会按当前上游路由跳到 ChatGPT 登录入口；这仍是真实
        # Chromium 页面，可用于执行 Sentinel SDK。之前只接受 auth host 会导致
        # 误判 target 不存在并回退后端 PoW。
        "https://chatgpt.com/auth/",
        "https://chat.openai.com/auth/",
    )
    for prefix in preferred_hosts:
        for page in browser_pages:
            if str(page.get("url") or "").startswith(prefix):
                return page
    if browser_pages:
        return browser_pages[0]
    raise RuntimeError(f"未找到可执行 Sentinel SDK 的 page target: {tabs!r}")


def _window_size_arg(screen_resolution: str) -> str:
    parts = str(screen_resolution or "1920x1080").lower().split("x", 1)
    try:
        width = max(800, int(parts[0]))
        height = max(600, int(parts[1]))
    except Exception:
        width, height = 1920, 1080
    return f"--window-size={width},{height}"


def _is_target_navigation_error(error: Exception) -> bool:
    text = str(error).lower()
    return (
        "navigated or closed" in text
        or "target closed" in text
        or "session closed" in text
        or "cannot find context with specified id" in text
        or "cannot find default execution context" in text
        or "execution context was destroyed" in text
    )


def _chrome_stderr_tail(stderr_path: Path, limit: int = 1600) -> str:
    try:
        data = stderr_path.read_bytes()
    except Exception:
        return ""
    return data[-limit:].decode("utf-8", errors="replace").strip()


def _chrome_startup_error(error: Exception, proc: subprocess.Popen, chrome: str, stderr_path: Path) -> RuntimeError:
    stderr_tail = _chrome_stderr_tail(stderr_path)
    exit_code = proc.poll()
    detail = f"{error}; chrome={chrome}; exit_code={exit_code}"
    if stderr_tail:
        detail += f"; stderr_tail={stderr_tail}"
    else:
        detail += "; stderr_tail=<empty>"
    return RuntimeError(detail)


def _cleanup_chrome_process_and_profile(proc: subprocess.Popen, user_data_dir: Path) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except Exception:
            pass
    for _ in range(10):
        try:
            shutil.rmtree(user_data_dir, ignore_errors=False)
            return
        except FileNotFoundError:
            return
        except Exception:
            time.sleep(0.25)
    shutil.rmtree(user_data_dir, ignore_errors=True)


def _chrome_user_data_parent() -> Path:
    """返回 Chrome profile 临时目录根路径。

    Windows 服务运行时 `tempfile.gettempdir()` 常落到 C:\\Windows\\TEMP，
    Chrome 写出的 DevToolsActivePort 可能被服务账号/ACL 短暂拒读。
    默认改用项目 data/chromium_tmp；也可用 CHATGPT2API_CHROME_TMPDIR 覆盖。
    """
    configured = str(os.getenv("CHATGPT2API_CHROME_TMPDIR") or "").strip()
    base = Path(configured) if configured else Path(__file__).resolve().parents[1] / "data" / "chromium_tmp"
    base.mkdir(parents=True, exist_ok=True)
    return base


class _UpstreamChromiumSentinelSession:
    """保留远端 provider 行为，供兼容调用复用。"""

    def __init__(
        self,
        *,
        user_agent: str,
        sdk_url: str = DEFAULT_SENTINEL_SDK_URL,
        screen_resolution: str = "1920x1080",
        headless: bool = True,
        chrome_path: str = "",
        timeout: float = 35.0,
    ) -> None:
        self.user_agent = user_agent
        self.sdk_url = sdk_url
        self.screen_resolution = screen_resolution
        self.headless = headless
        self.chrome_path = chrome_path
        self.timeout = timeout
        self._sdk_source = ""
        self._port = 0
        self._proc: subprocess.Popen | None = None
        self._user_data_dir: Path | None = None
        self._stderr_path: Path | None = None
        self._stderr_file = None
        self._network_client: _CDPClient | None = None

    def __enter__(self) -> "ChromiumSentinelSession":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _start(self) -> None:
        if self._proc is not None:
            return
        chrome = _find_chrome(self.chrome_path)
        self._sdk_source = _load_sdk(self.sdk_url, self.user_agent, min(20.0, self.timeout))
        user_data_dir = Path(tempfile.mkdtemp(prefix="sentinel-chrome-", dir=str(_chrome_user_data_parent())))
        stderr_path = user_data_dir / "chrome-stderr.log"
        self._user_data_dir = user_data_dir
        self._stderr_path = stderr_path
        args = _chrome_launch_args(
            chrome=chrome,
            user_agent=self.user_agent,
            screen_resolution=self.screen_resolution,
            user_data_dir=user_data_dir,
            headless=self.headless,
        )
        self._stderr_file = stderr_path.open("wb")
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=self._stderr_file)
        self._proc = proc
        try:
            try:
                self._port = _read_devtools_port(user_data_dir, min(15.0, self.timeout))
            except Exception as error:
                raise _chrome_startup_error(error, proc, chrome, stderr_path) from error
            self._prepare_auth_page()
        except Exception:
            self.close()
            raise

    def _prepare_auth_page(self) -> None:
        page = _select_page(self._port, min(10.0, self.timeout))
        client = _CDPClient(str(page["webSocketDebuggerUrl"]), timeout=self.timeout)
        try:
            client.__enter__()
            self._network_client = client
            client.call("Page.enable", timeout=5)
            client.call("Network.enable", timeout=5)
            client.call("Network.setBlockedURLs", {"urls": list(BLOCKED_AUTH_RESOURCE_URLS)}, timeout=5)
            navigation = client.call("Page.navigate", {"url": AUTH_PAGE_URL}, timeout=10)
            error_text = str(navigation.get("result", {}).get("errorText") or "").strip()
            if error_text:
                raise RuntimeError(f"Chromium auth page navigation failed: {error_text}")
            # 保持该 CDP 连接直到 close，确保阻断规则覆盖后续懒加载资源。
            time.sleep(2.5)
        except Exception:
            self._network_client = None
            client.__exit__(None, None, None)
            raise

    def token(self, *, flow: str, device_id: str) -> ChromiumSentinelResult:
        self._start()
        expression = _sentinel_expression(
            sdk_source=self._sdk_source,
            sdk_url=self.sdk_url,
            flow=flow,
            device_id=device_id,
            timeout=self.timeout,
        )
        response = None
        last_error: Exception | None = None
        for attempt in range(6):
            if attempt:
                time.sleep(min(1.5, 0.5 + attempt * 0.25))
            page = _select_page(self._port, min(10.0, self.timeout))
            try:
                with _CDPClient(str(page["webSocketDebuggerUrl"]), timeout=self.timeout) as client:
                    try:
                        client.call("Page.enable", timeout=5)
                    except Exception:
                        pass
                    client.call("Runtime.enable", timeout=10)
                    time.sleep(0.25)
                    response = client.call(
                        "Runtime.evaluate",
                        {"expression": expression, "awaitPromise": True, "returnByValue": True},
                        timeout=self.timeout + 5,
                    )
                break
            except Exception as error:
                last_error = error
                if attempt < 5 and _is_target_navigation_error(error):
                    continue
                raise
        if response is None:
            raise RuntimeError(f"Chromium Sentinel 执行失败: {last_error}")
        result = response.get("result", {}).get("result", {})
        if response.get("result", {}).get("exceptionDetails"):
            detail = response["result"]["exceptionDetails"]
            raise RuntimeError(f"Chromium Sentinel 执行异常: {detail.get('text') or detail}")
        value = result.get("value") if isinstance(result, dict) else None
        if not isinstance(value, dict) or not str(value.get("token") or "").strip():
            raise RuntimeError(f"Chromium Sentinel 未返回 token: {value!r}")
        return ChromiumSentinelResult(token=str(value["token"]).strip(), so_token=str(value.get("soToken") or "").strip())

    def close(self) -> None:
        network_client = self._network_client
        self._network_client = None
        if network_client is not None:
            try:
                network_client.__exit__(None, None, None)
            except Exception:
                pass
        stderr_file = self._stderr_file
        self._stderr_file = None
        if stderr_file is not None:
            try:
                stderr_file.close()
            except Exception:
                pass
        proc = self._proc
        user_data_dir = self._user_data_dir
        self._proc = None
        self._user_data_dir = None
        self._port = 0
        if proc is not None and user_data_dir is not None:
            _cleanup_chrome_process_and_profile(proc, user_data_dir)


def _chrome_launch_args(
    *,
    chrome: str,
    user_agent: str,
    screen_resolution: str,
    user_data_dir: Path,
    headless: bool,
) -> list[str]:
    extra_args = [
        item.strip()
        for item in str(os.getenv("CHATGPT2API_CHROME_ARGS") or "").split()
        if item.strip()
    ]
    args = [
        chrome,
        "--disable-gpu",
        "--disable-extensions",
        "--disable-component-extensions-with-background-pages",
        "--disable-background-networking",
        "--disable-default-apps",
        "--disable-dev-shm-usage",
        "--disable-setuid-sandbox",
        "--disable-sync",
        "--metrics-recording-only",
        "--no-sandbox",
        "--no-first-run",
        "--no-default-browser-check",
        "--remote-debugging-port=0",
        "--remote-allow-origins=*",
        "--blink-settings=imagesEnabled=false",
        "--lang=zh-CN",
        f"--user-agent={user_agent}",
        _window_size_arg(screen_resolution),
        f"--user-data-dir={user_data_dir}",
        "about:blank",
    ]
    if headless:
        args.insert(1, "--headless=new")
    if extra_args:
        args[1:1] = extra_args
    return args


class ChromiumSentinelSession:
    """单个注册任务持有的 Chromium Sentinel 会话。"""

    _LIGHTWEIGHT_BLOCKED_URLS = [
        "*.png",
        "*.jpg",
        "*.jpeg",
        "*.gif",
        "*.webp",
        "*.css",
        "*.woff",
        "*.woff2",
        "*.ttf",
        "*.mp4",
        "*.webm",
    ]

    def __init__(
        self,
        *,
        user_agent: str,
        sdk_url: str = DEFAULT_SENTINEL_SDK_URL,
        screen_resolution: str = "1920x1080",
        headless: bool = True,
        chrome_path: str = "",
        timeout: float = 35.0,
    ) -> None:
        self.user_agent = user_agent
        self.sdk_url = sdk_url
        self.screen_resolution = screen_resolution
        self.headless = headless
        self.chrome_path = chrome_path
        self.timeout = timeout
        self._sdk_source: str | None = None
        self._proc: subprocess.Popen | None = None
        self._user_data_dir: Path | None = None
        self._stderr_path: Path | None = None
        self._stderr_file: Any = None
        self._network_client: _CDPClient | None = None
        self._port = 0
        self._lightweight = True
        self._request_hosts: dict[str, str] = {}
        self._download_bytes_by_host: dict[str, int] = {}
        self.start_count = 0
        self.token_count = 0
        self.lightweight_fallback_count = 0

    def __enter__(self) -> "ChromiumSentinelSession":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "start_count": self.start_count,
            "token_count": self.token_count,
            "lightweight_fallback_count": self.lightweight_fallback_count,
            "download_bytes": sum(self._download_bytes_by_host.values()),
            "download_bytes_by_host": dict(sorted(self._download_bytes_by_host.items())),
        }

    def close(self) -> None:
        network_client = self._network_client
        self._network_client = None
        if network_client is not None:
            try:
                network_client.__exit__(None, None, None)
            except Exception:
                pass
        proc, user_data_dir = self._proc, self._user_data_dir
        self._proc = None
        self._user_data_dir = None
        self._port = 0
        if self._stderr_file is not None:
            try:
                self._stderr_file.close()
            except Exception:
                pass
            self._stderr_file = None
        if proc is not None and user_data_dir is not None:
            _cleanup_chrome_process_and_profile(proc, user_data_dir)

    def get_token(self, *, flow: str, device_id: str) -> ChromiumSentinelResult:
        self.token_count += 1
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                if self._proc is None:
                    self._start(lightweight=self._lightweight if self.start_count else True)
                elif self._proc.poll() is not None:
                    restart_lightweight = self._lightweight
                    self.close()
                    self._start(lightweight=restart_lightweight)
                return self._evaluate(flow=flow, device_id=device_id)
            except Exception as error:
                last_error = error
                was_lightweight = self._lightweight
                self.close()
                if attempt == 0 and was_lightweight:
                    self.lightweight_fallback_count += 1
                    self._start(lightweight=False)
                    try:
                        return self._evaluate(flow=flow, device_id=device_id)
                    except Exception as full_error:
                        last_error = full_error
                        self.close()
                elif attempt == 0:
                    continue
                break
        raise RuntimeError(f"Chromium Sentinel 会话执行失败: {last_error}") from last_error

    def _start(self, *, lightweight: bool) -> None:
        self.close()
        chrome = _find_chrome(self.chrome_path)
        if self._sdk_source is None:
            self._sdk_source = _load_sdk(self.sdk_url, self.user_agent, min(20.0, self.timeout))
        user_data_dir = Path(tempfile.mkdtemp(prefix="sentinel-chrome-", dir=str(_chrome_user_data_parent())))
        stderr_path = user_data_dir / "chrome-stderr.log"
        extra_args = [
            item.strip()
            for item in str(os.getenv("CHATGPT2API_CHROME_ARGS") or "").split()
            if item.strip()
        ]
        args = [
            chrome,
            "--disable-gpu",
            "--disable-extensions",
            "--disable-component-extensions-with-background-pages",
            "--disable-background-networking",
            "--disable-default-apps",
            "--disable-dev-shm-usage",
            "--disable-setuid-sandbox",
            "--disable-sync",
            "--disable-features=OptimizationHints,OptimizationGuideModelDownloading,OptimizationTargetPrediction",
            "--metrics-recording-only",
            "--no-sandbox",
            "--no-first-run",
            "--no-default-browser-check",
            "--remote-debugging-port=0",
            "--remote-allow-origins=*",
            "--lang=zh-CN",
            f"--user-agent={self.user_agent}",
            _window_size_arg(self.screen_resolution),
            f"--user-data-dir={user_data_dir}",
            "about:blank" if lightweight else "https://auth.openai.com/",
        ]
        if self.headless:
            args.insert(1, "--headless=new")
        if extra_args:
            args[1:1] = extra_args
        stderr_file = stderr_path.open("wb")
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=stderr_file)
        self._proc = proc
        self._user_data_dir = user_data_dir
        self._stderr_path = stderr_path
        self._stderr_file = stderr_file
        self._lightweight = lightweight
        self.start_count += 1
        try:
            self._port = _read_devtools_port(user_data_dir, min(15.0, self.timeout))
            if lightweight:
                self._prepare_lightweight_page()
            else:
                time.sleep(2.5)
        except Exception as error:
            startup_error = _chrome_startup_error(error, proc, chrome, stderr_path)
            self.close()
            raise startup_error from error

    def _prepare_lightweight_page(self) -> None:
        self._prepare_auth_page()
        time.sleep(1.25)
        if self._network_client is not None:
            try:
                self._network_client.call("Page.stopLoading", timeout=5)
            except Exception:
                pass

    def _prepare_auth_page(self) -> None:
        page = _select_page(self._port, min(10.0, self.timeout))
        client = _CDPClient(str(page["webSocketDebuggerUrl"]), self.timeout, self._handle_cdp_event)
        try:
            client.__enter__()
            self._network_client = client
            client.call("Page.enable", timeout=5)
            client.call("Network.enable", timeout=5)
            blocked_urls = list(dict.fromkeys([*BLOCKED_AUTH_RESOURCE_URLS, *self._LIGHTWEIGHT_BLOCKED_URLS]))
            client.call("Network.setBlockedURLs", {"urls": blocked_urls}, timeout=5)
            client.call("Page.navigate", {"url": "https://auth.openai.com/"}, timeout=10)
        except Exception:
            self._network_client = None
            client.__exit__(None, None, None)
            raise

    def _expression(self, flow: str, device_id: str) -> str:
        return f"""
(async()=>{{
  const sdkSource = {json.dumps(self._sdk_source or '')};
  const flow = {json.dumps(flow)};
  const deviceId = {json.dumps(device_id)};
  const timeoutMs = {int(max(5.0, self.timeout - 5.0) * 1000)};
  try {{ document.cookie = 'oai-did=' + encodeURIComponent(deviceId) + '; path=/; SameSite=Lax'; }} catch (e) {{}}
  Object.defineProperty(document, 'currentScript', {{
    configurable: true,
    get: () => ({{ src: {json.dumps(self.sdk_url)} }})
  }});
  if (!window.SentinelSDK) {{ (0, eval)(sdkSource); }}
  const withTimeout = (promise, label) => Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error(label + ' timeout')), timeoutMs))
  ]);
  try {{ await withTimeout(window.SentinelSDK.init(flow), 'sentinel init'); }} catch (e) {{}}
  const token = await withTimeout(window.SentinelSDK.token(flow), 'sentinel token');
  let soToken = '';
  try {{ soToken = await withTimeout(window.SentinelSDK.sessionObserverToken(flow), 'sentinel so'); }} catch (e) {{}}
  return {{ token, soToken }};
}})()
"""

    def _evaluate(self, *, flow: str, device_id: str) -> ChromiumSentinelResult:
        expression = self._expression(flow, device_id)
        response = None
        last_error: Exception | None = None
        # 首次打开 auth.openai.com 后上游可能立刻跳到 chatgpt.com/auth/login_with，
        # CDP 在 Runtime.evaluate 期间会偶发 target navigated/closed。这里重选
        # 当前 page target 再试，避免直接回退后端 PoW。
        for attempt in range(6):
            if attempt:
                time.sleep(min(1.5, 0.5 + attempt * 0.25))
            page = _select_page(self._port, min(10.0, self.timeout))
            try:
                with _CDPClient(
                    str(page["webSocketDebuggerUrl"]),
                    timeout=self.timeout,
                    event_handler=self._handle_cdp_event,
                ) as client:
                    try:
                        client.call("Page.enable", timeout=5)
                    except Exception:
                        pass
                    client.call("Runtime.enable", timeout=10)
                    client.call("Network.enable", timeout=5)
                    if self._lightweight:
                        client.call("Network.setBlockedURLs", {"urls": self._LIGHTWEIGHT_BLOCKED_URLS}, timeout=5)
                    # 等一小段时间让新导航后的默认 execution context 出现。
                    time.sleep(0.25)
                    response = client.call(
                        "Runtime.evaluate",
                        {"expression": expression, "awaitPromise": True, "returnByValue": True},
                        timeout=self.timeout + 5,
                    )
                break
            except Exception as error:
                last_error = error
                if attempt < 5 and _is_target_navigation_error(error):
                    continue
                raise
        if response is None:
            raise RuntimeError(f"Chromium Sentinel 执行失败: {last_error}")
        result = response.get("result", {}).get("result", {})
        if response.get("result", {}).get("exceptionDetails"):
            detail = response["result"]["exceptionDetails"]
            raise RuntimeError(f"Chromium Sentinel 执行异常: {detail.get('text') or detail}")
        value = result.get("value") if isinstance(result, dict) else None
        if not isinstance(value, dict) or not str(value.get("token") or "").strip():
            raise RuntimeError(f"Chromium Sentinel 未返回 token: {value!r}")
        return ChromiumSentinelResult(token=str(value["token"]).strip(), so_token=str(value.get("soToken") or "").strip())

    def _handle_cdp_event(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        request_id = str(params.get("requestId") or "")
        if method == "Network.responseReceived":
            response = params.get("response") if isinstance(params.get("response"), dict) else {}
            host = str(urllib.parse.urlparse(str(response.get("url") or "")).hostname or "")
            if request_id and host:
                self._request_hosts[request_id] = host
        elif method == "Network.loadingFinished":
            host = self._request_hosts.pop(request_id, "")
            try:
                encoded_length = max(0, int(float(params.get("encodedDataLength") or 0)))
            except (TypeError, ValueError):
                encoded_length = 0
            if host and encoded_length:
                self._download_bytes_by_host[host] = self._download_bytes_by_host.get(host, 0) + encoded_length

    def token(self, *, flow: str, device_id: str) -> ChromiumSentinelResult:
        """兼容远端 provider 接口。"""
        return self.get_token(flow=flow, device_id=device_id)


def _sentinel_expression(*, sdk_source: str, sdk_url: str, flow: str, device_id: str, timeout: float) -> str:
    session = object.__new__(ChromiumSentinelSession)
    session._sdk_source = sdk_source
    session.sdk_url = sdk_url
    session.timeout = timeout
    return session._expression(flow, device_id)


def build_chromium_sentinel_token(
    *,
    flow: str,
    device_id: str,
    user_agent: str,
    sdk_url: str = DEFAULT_SENTINEL_SDK_URL,
    screen_resolution: str = "1920x1080",
    headless: bool = True,
    chrome_path: str = "",
    timeout: float = 35.0,
) -> ChromiumSentinelResult:
    """兼容的一次性 Chromium Sentinel token 接口。"""
    with ChromiumSentinelSession(
        user_agent=user_agent,
        sdk_url=sdk_url,
        screen_resolution=screen_resolution,
        headless=headless,
        chrome_path=chrome_path,
        timeout=timeout,
    ) as session:
        return session.get_token(flow=flow, device_id=device_id)
