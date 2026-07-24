from __future__ import annotations

import json
import random
import re
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path

from services.account_service import account_service
from services.config import DATA_DIR
from services.register import mail_provider, openai_register


REGISTER_FILE = DATA_DIR / "register.json"
REGISTER_LOG_DETAIL_PATTERNS = (
    re.compile(r"，?需要查看本地抓包目录[^;]*"),
    re.compile(r";\s*抓包目录=[^;]*"),
    re.compile(r";\s*url=[^;]*"),
    re.compile(r";\s*content_type=[^;]*"),
    re.compile(r";\s*cf-ray=[^;]*"),
    re.compile(r";\s*x-request-id=[^;]*"),
    re.compile(r";\s*openai-processing-ms=[^;]*"),
    re.compile(r";\s*json=.*$"),
    re.compile(r";\s*body=.*$"),
    re.compile(r"本地抓包目录"),
    re.compile(r"抓包目录"),
)


def _serialize_outlook_pool(credentials: list[dict]) -> str:
    return "\n".join(
        f'{c["email"]}----{c.get("password", "")}----{c["client_id"]}----{c["refresh_token"]}' for c in credentials
    )


def _merge_outlook_pool(old_text: str, new_text: str) -> str:
    """合并已存邮箱池与新导入文本，按邮箱去重，新导入的同名邮箱覆盖旧凭据。"""
    merged: dict[str, dict] = {}
    for credential in mail_provider.parse_outlook_credentials(old_text or ""):
        merged[credential["email"].strip().lower()] = credential
    for credential in mail_provider.parse_outlook_credentials(new_text or ""):
        merged[credential["email"].strip().lower()] = credential
    return _serialize_outlook_pool(list(merged.values()))


def _provider_type(provider: dict) -> str:
    return str(provider.get("type") or "").strip()


RUNTIME_SECRET_FIELDS = {
    "flow_trigger": ("bearer", "cookie"),
    "browser_use": ("api_key",),
    "skyvern": ("api_key",),
    "roxy": ("api_token",),
    "cloak": ("license_key",),
    "sms": ("api_key", "l_admin_auth_code", "h_admin_auth_code"),
}


def _has_secret_key(field: str) -> str:
    return f"has_{field}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_realtime_log_text(text: str) -> str:
    """注册页实时日志只展示流程摘要，隐藏本地抓包目录和上游响应细节。

    详细诊断仍保留在进程日志、worker 返回值和 data/register_failures 文件里；
    这里仅过滤前端/SSE 的实时日志，避免页面被长路径、响应头、JSON body 刷屏。
    """
    sanitized = str(text).replace("；", ";").replace("写入注册失败抓包目录失败", "写入注册失败诊断文件失败")
    for pattern in REGISTER_LOG_DETAIL_PATTERNS:
        sanitized = pattern.sub("", sanitized)
    return sanitized.strip()


def _default_config() -> dict:
    return {**openai_register.config, "mode": "total", "target_quota": 100, "target_available": 10, "check_interval": 5, "register_interval_min": 2.0, "register_interval_max": 6.0, "enabled": False, "stats": {"success": 0, "fail": 0, "done": 0, "running": 0, "threads": openai_register.config["threads"], "elapsed_seconds": 0, "avg_seconds": 0, "success_rate": 0, "current_quota": 0, "current_available": 0}}


def _runtime_config_payload(cfg: dict) -> dict:
    return {k: cfg[k] for k in openai_register.REGISTER_RUNTIME_CONFIG_KEYS if k in cfg}


def _safe_bool(value: object, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return fallback
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return fallback


def _normalize(raw: dict) -> dict:
    default_cfg = _default_config()
    cfg = {**default_cfg}
    cfg.update({k: v for k, v in raw.items() if k not in {"stats", "logs"}})
    cfg["total"] = max(1, int(cfg.get("total") or 1))
    cfg["threads"] = max(1, int(cfg.get("threads") or 1))
    driver_names = {item.get("name") for item in openai_register.list_drivers()}
    registration_driver = str(cfg.get("registration_driver") or "platform_oauth").strip().lower()
    cfg["registration_driver"] = registration_driver if registration_driver in driver_names else "platform_oauth"
    cfg["mode"] = str(cfg.get("mode") or "total").strip() if str(cfg.get("mode") or "total").strip() in {"total", "quota", "available"} else "total"
    cfg["target_quota"] = max(1, int(cfg.get("target_quota") or 1))
    cfg["target_available"] = max(1, int(cfg.get("target_available") or 1))
    cfg["check_interval"] = max(1, int(cfg.get("check_interval") or 5))
    cfg["register_interval_min"] = max(0.0, float(cfg.get("register_interval_min") or 2.0))
    cfg["register_interval_max"] = max(cfg["register_interval_min"], float(cfg.get("register_interval_max") or 6.0))
    cfg["sentinel_browser_enabled"] = _safe_bool(cfg.get("sentinel_browser_enabled"), True)
    cfg["sentinel_browser_headless"] = _safe_bool(cfg.get("sentinel_browser_headless"), True)
    cfg["sentinel_browser_timeout"] = max(5.0, float(cfg.get("sentinel_browser_timeout") or 35.0))
    cfg["sentinel_browser_chrome_path"] = str(cfg.get("sentinel_browser_chrome_path") or "").strip()
    cfg["sentinel_browser_sdk_url"] = str(cfg.get("sentinel_browser_sdk_url") or "").strip()
    cfg["sentinel_browser_fallback"] = _safe_bool(cfg.get("sentinel_browser_fallback"), True)
    cfg["codex_agent_identity_enabled"] = _safe_bool(cfg.get("codex_agent_identity_enabled"), False)
    cfg["codex_agent_identity_verify_task"] = _safe_bool(cfg.get("codex_agent_identity_verify_task"), True)
    cfg["codex_oauth_enabled"] = _safe_bool(cfg.get("codex_oauth_enabled"), False)
    cfg["codex_oauth_via_cpa"] = _safe_bool(cfg.get("codex_oauth_via_cpa"), True)
    cfg["codex_oauth_cpa_pool_id"] = str(cfg.get("codex_oauth_cpa_pool_id") or "").strip()
    for section in ("chatgpt_web", "browser_use", "skyvern", "roxy", "cloak", "sms", "flow_trigger", "humanize", "profile"):
        defaults = default_cfg.get(section) if isinstance(default_cfg.get(section), dict) else {}
        current = cfg.get(section) if isinstance(cfg.get(section), dict) else {}
        cfg[section] = {**defaults, **current}
    chatgpt_web = cfg["chatgpt_web"]
    chatgpt_web["bootstrap_enabled"] = _safe_bool(chatgpt_web.get("bootstrap_enabled"), True)
    chatgpt_web["bootstrap_strict"] = _safe_bool(chatgpt_web.get("bootstrap_strict"), False)
    humanize = cfg["humanize"]
    humanize["enabled"] = _safe_bool(humanize.get("enabled"), True)
    try:
        humanize["factor"] = max(0.0, float(humanize.get("factor") if humanize.get("factor") is not None else 1.0))
    except (TypeError, ValueError):
        humanize["factor"] = 1.0
    if not isinstance(humanize.get("delays"), dict):
        humanize["delays"] = {}
    profile = cfg["profile"]
    try:
        profile["min_age"] = max(13, int(profile.get("min_age") if profile.get("min_age") is not None else 18))
    except (TypeError, ValueError):
        profile["min_age"] = 18
    try:
        profile["max_age"] = max(profile["min_age"], int(profile.get("max_age") if profile.get("max_age") is not None else 65))
    except (TypeError, ValueError):
        profile["max_age"] = max(profile["min_age"], 65)
    flow = cfg["flow_trigger"]
    sms = cfg["sms"]
    sms["enabled"] = _safe_bool(sms.get("enabled"), False)
    flow["enabled"] = _safe_bool(flow.get("enabled"), False)
    flow["url"] = str(flow.get("url") or "").strip()
    flow["bearer"] = str(flow.get("bearer") or "").strip()
    flow["cookie"] = str(flow.get("cookie") or "").strip()
    flow["access_token_key"] = str(flow.get("access_token_key") or "access_token").strip() or "access_token"
    try:
        flow["timeout"] = max(1, int(flow.get("timeout") if flow.get("timeout") is not None else 30))
    except (TypeError, ValueError):
        flow["timeout"] = 30
    flow["origin"] = str(flow.get("origin") or "").strip()
    flow["referer"] = str(flow.get("referer") or "").strip()
    flow["user_agent"] = str(flow.get("user_agent") or "").strip()
    flow["use_register_proxy"] = _safe_bool(flow.get("use_register_proxy"), False)
    flow["verify_ssl"] = _safe_bool(flow.get("verify_ssl"), True)
    if isinstance(flow.get("payload"), str):
        try:
            parsed_payload = json.loads(str(flow.get("payload") or "{}"))
            flow["payload"] = parsed_payload if isinstance(parsed_payload, dict) else {}
        except Exception:
            flow["payload"] = {}
    elif not isinstance(flow.get("payload"), dict):
        flow["payload"] = {}
    cfg["new_account_warmup_minutes"] = max(0, int(cfg.get("new_account_warmup_minutes") if cfg.get("new_account_warmup_minutes") is not None else 30))
    cfg["new_account_verify_delay_seconds"] = max(0, int(cfg.get("new_account_verify_delay_seconds") if cfg.get("new_account_verify_delay_seconds") is not None else 120))
    cfg["new_account_max_verify_workers"] = max(1, int(cfg.get("new_account_max_verify_workers") if cfg.get("new_account_max_verify_workers") is not None else 2))
    cfg["proxy"] = str(cfg.get("proxy") or "").strip()
    default_mail = default_cfg["mail"] if isinstance(default_cfg.get("mail"), dict) else {}
    mail = cfg.get("mail") if isinstance(cfg.get("mail"), dict) else {}
    cfg["mail"] = {**default_mail, **mail}
    cfg["mail"]["api_use_register_proxy"] = _safe_bool(cfg["mail"].get("api_use_register_proxy"), True)
    cfg["mail"].pop("proxy", None)
    cfg["enabled"] = bool(cfg.get("enabled"))
    stats = {**default_cfg["stats"], **(raw.get("stats") if isinstance(raw.get("stats"), dict) else {}),
             "threads": cfg["threads"]}
    cfg["stats"] = stats
    return cfg


class RegisterService:
    def __init__(self, store_file: Path):
        self._store_file = store_file
        self._lock = threading.RLock()
        self._runner: threading.Thread | None = None
        self._logs: list[dict] = []
        openai_register.register_log_sink = self._append_log
        self._config = self._load()
        if self._config["enabled"]:
            self.start()

    def _load(self) -> dict:
        try:
            return _normalize(json.loads(self._store_file.read_text(encoding="utf-8")))
        except Exception:
            return _normalize({})

    def _save(self) -> None:
        self._store_file.parent.mkdir(parents=True, exist_ok=True)
        self._store_file.write_text(json.dumps(self._config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def get(self) -> dict:
        with self._lock:
            # logs 是只读追加列表，浅拷贝即可（条目写入后不会被修改）
            # config 需要深拷贝，因为 _redact_public_secrets 会修改嵌套 dict。
            snapshot = {**json.loads(json.dumps(self._config, ensure_ascii=False)), "logs": list(self._logs[-300:])}
            snapshot["drivers"] = openai_register.list_drivers()
        self._redact_public_secrets(snapshot)
        return snapshot

    @staticmethod
    def _mask_email(email: str) -> str:
        local, sep, domain = str(email or "").partition("@")
        if not sep:
            return "***"
        masked = (local[:2] + "***" + local[-1:]) if len(local) > 2 else (local[:1] + "***")
        return f"{masked}@{domain}"

    def _redact_public_secrets(self, snapshot: dict) -> None:
        self._redact_mail_provider_secrets(snapshot)
        self._redact_runtime_secrets(snapshot)

    def _redact_mail_provider_secrets(self, snapshot: dict) -> None:
        """把邮箱 provider 敏感字段从对外输出中抹掉，仅保留脱敏预览与统计。

        mailboxes/授权码改为只写输入框，避免通过 GET/SSE 反复广播。
        """
        mail = snapshot.get("mail")
        if not isinstance(mail, dict):
            return
        providers = mail.get("providers")
        if not isinstance(providers, list):
            return
        for provider in providers:
            if not isinstance(provider, dict):
                continue
            provider_type = _provider_type(provider)
            if provider_type == "outlook_token":
                credentials = mail_provider.parse_outlook_credentials(str(provider.get("mailboxes") or ""))
                provider["mailboxes"] = ""
                provider["mailboxes_count"] = len(credentials)
                provider["mailboxes_preview"] = [self._mask_email(c["email"]) for c in credentials]
                provider["mailboxes_stats"] = mail_provider.outlook_token_pool_stats(credentials)
            if provider_type == "qqmail_imap":
                imap_password = str(provider.get("imap_password") or provider.get("password") or "").strip()
                provider["imap_password"] = ""
                provider.pop("password", None)
                provider["has_imap_password"] = bool(imap_password)

    def _redact_runtime_secrets(self, snapshot: dict) -> None:
        """隐藏注册运行时配置里的令牌/授权码，保留是否已配置的标记。"""
        for section, fields in RUNTIME_SECRET_FIELDS.items():
            values = snapshot.get(section)
            if not isinstance(values, dict):
                continue
            for field in fields:
                secret = str(values.get(field) or "").strip()
                values[field] = ""
                values[_has_secret_key(field)] = bool(secret)

    def _drop_mail_proxy(self) -> None:
        if isinstance(self._config.get("mail"), dict):
            self._config["mail"].pop("proxy", None)

    def _merge_mail_provider_secrets(self, updates: dict) -> None:
        """合并/保留邮箱 provider 的只写敏感字段。

        前端 mailboxes 是只写导入框，留空表示不改动；填入的新行追加/覆盖已存凭据。
        按数组下标与已存的同类型 provider 对齐。
        """
        mail = updates.get("mail")
        if not isinstance(mail, dict) or not isinstance(mail.get("providers"), list):
            return
        old_mail = self._config.get("mail") if isinstance(self._config.get("mail"), dict) else {}
        old_providers = old_mail.get("providers") if isinstance(old_mail.get("providers"), list) else []
        for index, provider in enumerate(mail["providers"]):
            if not isinstance(provider, dict):
                continue
            provider_type = _provider_type(provider)
            old = old_providers[index] if index < len(old_providers) and isinstance(old_providers[index], dict) else {}
            if provider_type == "outlook_token":
                old_text = str(old.get("mailboxes") or "") if _provider_type(old) == "outlook_token" else ""
                new_text = str(provider.get("mailboxes") or "")
                provider["mailboxes"] = _merge_outlook_pool(old_text, new_text) if (old_text or new_text) else ""
                for key in ("mailboxes_count", "mailboxes_preview", "mailboxes_stats"):
                    provider.pop(key, None)
            if provider_type == "qqmail_imap":
                old_password = str(old.get("imap_password") or old.get("password") or "") if _provider_type(old) == "qqmail_imap" else ""
                new_password = str(provider.get("imap_password") or provider.get("password") or "")
                if not new_password.strip() and bool(provider.get("has_imap_password")) and old_password:
                    provider["imap_password"] = old_password
                provider.pop("password", None)
                provider.pop("has_imap_password", None)

    def _merge_runtime_secrets(self, updates: dict) -> None:
        """前端带 has_* 且密钥留空时，保留已有运行时密钥。"""
        for section, fields in RUNTIME_SECRET_FIELDS.items():
            incoming = updates.get(section)
            if not isinstance(incoming, dict):
                continue
            old = self._config.get(section) if isinstance(self._config.get(section), dict) else {}
            for field in fields:
                has_key = _has_secret_key(field)
                incoming_value = str(incoming.get(field) or "").strip()
                old_value = old.get(field) if isinstance(old, dict) else ""
                if not incoming_value and bool(incoming.get(has_key)) and str(old_value or "").strip():
                    incoming[field] = old_value
                incoming.pop(has_key, None)

    def _prune_unused_outlook_pools(self) -> int:
        mail = self._config.get("mail")
        if not isinstance(mail, dict):
            return 0
        providers = mail.get("providers")
        if not isinstance(providers, list):
            return 0
        total_removed = 0
        for provider in providers:
            if not isinstance(provider, dict) or provider.get("type") != "outlook_token":
                continue
            credentials = mail_provider.parse_outlook_credentials(str(provider.get("mailboxes") or ""))
            kept, removed = mail_provider.prune_outlook_unused_credentials(credentials)
            if removed:
                provider["mailboxes"] = _serialize_outlook_pool(kept)
                total_removed += removed
            for key in ("mailboxes_count", "mailboxes_preview", "mailboxes_stats"):
                provider.pop(key, None)
        return total_removed

    def update(self, updates: dict) -> dict:
        with self._lock:
            self._merge_mail_provider_secrets(updates)
            self._merge_runtime_secrets(updates)
            self._config = _normalize({**self._config, **updates})
            self._drop_mail_proxy()
            openai_register.config.update(_runtime_config_payload(self._config))
            self._save()
            return self.get()

    def start(self) -> dict:
        with self._lock:
            if self._runner and self._runner.is_alive():
                self._config["enabled"] = True
                self._save()
                return self.get()
            self._config["enabled"] = True
            self._drop_mail_proxy()
            self._logs = []
            metrics = self._pool_metrics()
            self._config["stats"] = {"job_id": uuid.uuid4().hex, "success": 0, "fail": 0, "done": 0, "running": 0, "threads": self._config["threads"], **metrics, "started_at": _now(), "updated_at": _now()}
            openai_register.config.update(_runtime_config_payload(self._config))
            with openai_register.stats_lock:
                openai_register.stats.update({"done": 0, "success": 0, "fail": 0, "start_time": time.time()})
            self._save()
            self._runner = threading.Thread(target=self._run, daemon=True, name="openai-register")
            self._runner.start()
            self._append_log(f"注册任务启动，模式={self._config['mode']}，线程数={self._config['threads']}", "yellow")
            return self.get()

    def stop(self) -> dict:
        with self._lock:
            self._config["enabled"] = False
            self._config["stats"]["updated_at"] = _now()
            self._save()
            self._append_log("已请求停止注册任务，正在等待当前运行任务结束", "yellow")
            return self.get()

    def reset(self) -> dict:
        with self._lock:
            self._logs = []
            self._config["stats"] = {"success": 0, "fail": 0, "done": 0, "running": 0, "threads": self._config["threads"], "elapsed_seconds": 0, "avg_seconds": 0, "success_rate": 0, **self._pool_metrics(), "updated_at": _now()}
            with openai_register.stats_lock:
                openai_register.stats.update({"done": 0, "success": 0, "fail": 0, "start_time": 0.0})
            self._save()
            return self.get()

    def reset_outlook_pool(self, scope: str = "all") -> dict:
        scope = str(scope or "all").strip().lower()
        if scope == "unused":
            with self._lock:
                removed = self._prune_unused_outlook_pools()
                openai_register.config.update(_runtime_config_payload(self._config))
                self._save()
                self._append_log(f"已清空 Outlook 邮箱池未使用邮箱，移除 {removed} 个", "yellow")
            return self.get()
        scope = "failed" if str(scope) == "failed" else "all"
        cleared = mail_provider.reset_outlook_token_pool_state(scope)
        with self._lock:
            self._append_log(
                f"已重置 Outlook 邮箱池状态（范围={'仅失败/占用' if scope == 'failed' else '全部'}），清除 {cleared} 条记录",
                "yellow",
            )
        return self.get()

    def _append_log(self, text: str, color: str = "") -> None:
        with self._lock:
            self._logs.append({"time": _now(), "text": _sanitize_realtime_log_text(text), "level": str(color or "info")})
            self._logs = self._logs[-300:]

    def _pool_metrics(self) -> dict:
        items = account_service.list_accounts()
        normal = [item for item in items if item.get("status") == "正常"]
        return {
            "current_quota": sum(int(item.get("quota") or 0) for item in normal if not item.get("image_quota_unknown")),
            "current_available": len(normal),
        }

    def _target_reached(self, cfg: dict, submitted: int) -> bool:
        mode = str(cfg.get("mode") or "total")
        metrics = self._pool_metrics()
        self._bump(**metrics)
        if mode == "quota":
            reached = metrics["current_quota"] >= int(cfg.get("target_quota") or 1)
            self._append_log(f"检查号池：当前正常账号={metrics['current_available']}，当前剩余额度={metrics['current_quota']}，目标额度={cfg.get('target_quota')}，{'跳过注册' if reached else '继续注册'}", "yellow")
            return reached
        if mode == "available":
            reached = metrics["current_available"] >= int(cfg.get("target_available") or 1)
            self._append_log(f"检查号池：当前正常账号={metrics['current_available']}，目标账号={cfg.get('target_available')}，当前剩余额度={metrics['current_quota']}，{'跳过注册' if reached else '继续注册'}", "yellow")
            return reached
        return submitted >= int(cfg.get("total") or 1)

    def _bump(self, **updates) -> None:
        with self._lock:
            self._config["stats"].update(updates)
            stats = self._config["stats"]
            started_at = str(stats.get("started_at") or "")
            if started_at:
                try:
                    elapsed = max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(started_at)).total_seconds())
                except Exception:
                    elapsed = 0.0
                done = int(stats.get("done") or 0)
                success = int(stats.get("success") or 0)
                fail = int(stats.get("fail") or 0)
                stats["elapsed_seconds"] = round(elapsed, 1)
                stats["avg_seconds"] = round(elapsed / success, 1) if success else 0
                stats["success_rate"] = round(success * 100 / max(1, success + fail), 1)
            self._config["stats"]["updated_at"] = _now()
            self._save()

    def _run(self) -> None:
        threads = int(self.get()["threads"])
        submitted, done, success, fail = 0, 0, 0, 0
        with ThreadPoolExecutor(max_workers=threads) as executor:
            futures = set()
            while True:
                cfg = self.get()
                interval_min = float(cfg.get("register_interval_min") or 0.0)
                interval_max = float(cfg.get("register_interval_max") or 0.0)
                while self.get()["enabled"] and not self._target_reached(cfg, submitted) and len(futures) < threads:
                    submitted += 1
                    futures.add(executor.submit(openai_register.worker, submitted))
                    # 注册间隔抖动，防同 IP 短时批量注册触发风控
                    if interval_max > 0:
                        time.sleep(random.uniform(interval_min, interval_max))
                self._bump(running=len(futures), done=done, success=success, fail=fail)
                if not futures and (not self.get()["enabled"] or str(cfg.get("mode") or "total") == "total"):
                    break
                if not futures:
                    time.sleep(max(1, int(cfg.get("check_interval") or 5)))
                    continue
                finished, futures = wait(futures, return_when=FIRST_COMPLETED)
                for future in finished:
                    done += 1
                    try:
                        result = future.result()
                        success += 1 if result.get("ok") else 0
                        fail += 0 if result.get("ok") else 1
                    except Exception:
                        fail += 1
        self._bump(running=0, done=done, success=success, fail=fail, finished_at=_now())
        with self._lock:
            self._config["enabled"] = False
            self._save()
        self._append_log(f"注册任务结束，成功{success}，失败{fail}", "yellow")


register_service = RegisterService(REGISTER_FILE)
