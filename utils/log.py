import base64
import binascii
import json
import logging
import re
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any


# 错误日志文件位置：与 config.json 同级的 data/error.log
_ERROR_LOG_DIR = Path(__file__).resolve().parents[1] / "data"
_ERROR_LOG_FILE = _ERROR_LOG_DIR / "error.log"


class ErrorFileLogger:
    """把上游访问错误（403 / 5xx / CF 拦截等）写入按天滚动的文件日志。

    - 文件：data/error.log（每天滚动，保留 14 天）
    - 格式：每行一个 JSON，含时间/事件/URL/状态码/响应体摘要/附加字段
    - 受 config.error_log_enabled 开关控制
    """

    def __init__(self) -> None:
        self._handler: TimedRotatingFileHandler | None = None
        self._logger: logging.Logger | None = None
        self._initialized = False

    def _ensure_logger(self) -> logging.Logger | None:
        if not self._initialized:
            self._initialized = True
            try:
                _ERROR_LOG_DIR.mkdir(parents=True, exist_ok=True)
                handler = TimedRotatingFileHandler(
                    _ERROR_LOG_FILE,
                    when="midnight",
                    interval=1,
                    backupCount=14,
                    encoding="utf-8",
                )
                handler.setFormatter(logging.Formatter("%(message)s"))
                logger = logging.getLogger("chatgpt2api.error_file")
                logger.handlers.clear()
                logger.addHandler(handler)
                logger.setLevel(logging.INFO)
                logger.propagate = False
                self._handler = handler
                self._logger = logger
            except Exception:
                # 日志初始化失败不应影响业务流程
                self._logger = None
        return self._logger

    def _enabled(self) -> bool:
        try:
            from services.config import config

            return bool(config.error_log_enabled)
        except Exception:
            return False

    def log(
        self,
        event: str,
        url: str = "",
        status_code: int | None = None,
        body: Any = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """记录一条上游错误到文件。开关关闭时静默跳过。"""
        if not self._enabled():
            return
        logger = self._ensure_logger()
        if logger is None:
            return
        # body 归一化为字符串并截断，避免单行过长
        if body is None:
            body_str = ""
        elif isinstance(body, (dict, list)):
            try:
                body_str = json.dumps(body, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                body_str = repr(body)
        else:
            body_str = str(body)
        if len(body_str) > 2000:
            body_str = body_str[:2000] + "…[truncated]"
        record = {
            "ts": self._now_iso(),
            "event": event,
            "url": url,
            "status": status_code,
            "body": body_str,
        }
        if extra:
            # 仅保留可序列化字段，避免日志写入失败
            clean_extra: dict[str, Any] = {}
            for key, value in extra.items():
                try:
                    json.dumps(value, ensure_ascii=False, default=str)
                    clean_extra[key] = value
                except (TypeError, ValueError):
                    clean_extra[key] = repr(value)
            record["extra"] = clean_extra
        try:
            logger.info(json.dumps(record, ensure_ascii=False, default=str))
        except Exception:
            pass

    @staticmethod
    def _now_iso() -> str:
        import time as _time

        # 本地时区 ISO 时间，便于日志阅读
        offset = _time.strftime("%z")
        if offset:
            offset = offset[:3] + ":" + offset[3:]
        return _time.strftime("%Y-%m-%dT%H:%M:%S", _time.localtime()) + offset


# 模块级单例
error_logger = ErrorFileLogger()


def log_upstream_error(
    event: str,
    url: str = "",
    status_code: int | None = None,
    body: Any = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """对外暴露的上游错误日志写入入口。"""
    error_logger.log(event, url=url, status_code=status_code, body=body, extra=extra)


class Logger:
    _DATA_URL_RE = re.compile(r"data:image/[^;]+;base64,[A-Za-z0-9+/=]+")
    _JSON_B64_RE = re.compile(r'("b64_json"\s*:\s*")([A-Za-z0-9+/=]+)(")')

    def __init__(self, name: str = "chatgpt2api") -> None:
        self._logger = logging.getLogger(name)
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
            self._logger.addHandler(handler)
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False

    def _enabled(self, level: str) -> bool:
        try:
            from services.config import config
            levels = set(config.log_levels)
        except Exception:
            levels = set()
        return level in (levels or {"info", "warning", "error"})

    def _mask_string(self, value: str, keep: int = 10) -> str:
        if len(value) <= keep:
            return value
        return value[:keep] + "..."

    def _mask_base64(self, value: str) -> str:
        if value.startswith("data:") and ";base64," in value:
            header, _, data = value.partition(",")
            return f"{header},{self._mask_string(data, 24)} (base64 len={len(data)})"
        return f"{self._mask_string(value, 24)} (base64 len={len(value)})"

    def _is_base64_string(self, value: str) -> bool:
        if len(value) < 64 or len(value) % 4 != 0:
            return False
        if not any(char in value for char in "+/="):
            return False
        try:
            base64.b64decode(value, validate=True)
            return True
        except (binascii.Error, ValueError):
            return False

    def _sanitize_string(self, value: str) -> str:
        stripped = value.strip()
        if stripped.startswith("data:") and ";base64," in stripped:
            return self._mask_base64(stripped)
        if self._is_base64_string(stripped):
            return self._mask_base64(stripped)
        sanitized = self._DATA_URL_RE.sub(lambda match: self._mask_base64(match.group(0)), value)
        sanitized = self._JSON_B64_RE.sub(
            lambda match: f'{match.group(1)}{self._mask_base64(match.group(2))}{match.group(3)}',
            sanitized,
        )
        if sanitized != value:
            return sanitized
        return value

    def _sanitize(self, value: Any) -> Any:
        if isinstance(value, dict):
            sanitized = {}
            for key, item in value.items():
                lowered_key = key.lower()
                if isinstance(item, str) and ("token" in lowered_key or lowered_key == "dx"):
                    sanitized[key] = self._mask_string(item)
                elif isinstance(item, str) and ("base64" in lowered_key or lowered_key == "b64_json"):
                    sanitized[key] = self._mask_base64(item)
                else:
                    sanitized[key] = self._sanitize(item)
            return sanitized
        if isinstance(value, list):
            return [self._sanitize(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self._sanitize(item) for item in value)
        if isinstance(value, str):
            return self._sanitize_string(value)
        return value

    def _message(self, value: Any) -> str:
        sanitized = self._sanitize(value)
        if isinstance(sanitized, str):
            return sanitized
        return json.dumps(sanitized, ensure_ascii=False, default=str)

    def debug(self, message: Any) -> None:
        if self._enabled("debug"):
            self._logger.debug(self._message(message))

    def info(self, message: Any) -> None:
        if self._enabled("info"):
            self._logger.info(self._message(message))

    def warning(self, message: Any) -> None:
        if self._enabled("warning"):
            self._logger.warning(self._message(message))

    def error(self, message: Any) -> None:
        if self._enabled("error"):
            self._logger.error(self._message(message))


logger = Logger()
