# 内存占用优化方案

## 概述

针对 chatgpt2api 项目运行时内存占用高的问题,在「仅配置 + 高风险代码点」范围内进行优化。不引入新依赖,不添加监控端点,保持单 worker 架构不变。

容器内存上限统一设为 **1GB**。

---

## 当前状态分析

### 1. 容器无内存限制
- `docker-compose.yml` 和 `docker-compose.local.yml` 均未设置 `mem_limit` / `deploy.resources.limits.memory`
- 进程内存无上限,可能持续增长直至 OOM Killer 介入

### 2. 缓存深拷贝次数过多(高风险)
- `services/protocol/chat_completion_cache.py` 中 `ChatCompletionCache` 使用 `copy.deepcopy` 多次复制同一份数据:
  - **流式路径**(`get_or_compute_stream`,第 213-235 行):
    - 第 216 行:每个 chunk 都 `self._copy(chunk)` → **每条 chunk deepcopy 1 次**
    - 第 229 行:`value=self._copy(chunks)` → **整个 chunks 列表 deepcopy 1 次** 写入 entries
    - 第 233 行:`inflight.value = self._copy(chunks)` → **整个 chunks 列表再 deepcopy 1 次** 写入 inflight
    - 同一段响应被复制 3 次,长回复(32K tokens 流式)峰值内存可达数百 MB
  - **非流式路径**(`get_or_compute_response`,第 155-175 行):
    - 第 168 行:`value=self._copy(value)` 写入 entries
    - 第 172 行:`inflight.value = self._copy(value)` 写入 inflight
    - 第 175 行:`return value` 返回原始对象
    - 复制 2 次(缓存一份 + inflight 一份)
- 默认配置 `max_entries=256, ttl_seconds=60, stream_cache=true`(`config.json:83-92`),256 条流式缓存的峰值内存很高

### 3. 缓存条目数偏高
- `config.json` 第 86 行 `max_entries: 256`,对于单 worker 中小规模部署偏大

---

## 优化方案

### 变更 1:docker-compose 增加内存限制(1GB)

**文件**:`docker-compose.yml`
**改动**:在 `services.app` 下增加 `mem_limit` 和 `deploy.resources`
**原因**:硬性约束进程内存上限,防止无限制增长导致宿主机内存耗尽
**具体**:
```yaml
services:
  app:
    image: yjj5918/chatgpt2api:latest
    container_name: chatgpt2api
    restart: unless-stopped
    ports:
      - "127.0.0.1:3001:80"
    volumes:
      - ./data:/app/data
      - ./config.json:/app/config.json
    mem_limit: 1g
    mem_reservation: 512m
    environment:
      ...
```

**文件**:`docker-compose.local.yml`
**改动**:同样增加 `mem_limit: 1g` 和 `mem_reservation: 512m`
**原因**:本地构建测试环境也需约束内存

---

### 变更 2:降低缓存条目上限

**文件**:`config.json`
**改动**:第 86 行 `max_entries` 从 `256` 改为 `64`
**原因**:
- 单 worker 架构下,并发去重(inflight)已能拦截大部分重复请求
- 64 条已足够覆盖常见热点 prompt 的 60 秒 TTL 窗口
- 流式缓存单条可达数 MB,降到 64 条可将峰值从数百 MB 压到数十 MB
**风险**:缓存命中率略降,热点请求可能多打一次上游;影响可接受

---

### 变更 3:减少 ChatCompletionCache 的 deepcopy 次数(高风险代码点)

**文件**:`services/protocol/chat_completion_cache.py`
**改动概述**:去掉写入缓存和 inflight 时的全列表/全对象 deepcopy,改为存引用;仅在缓存命中返回时 deepcopy(保证调用方拿到独立副本)。

#### 3a. 非流式路径 `get_or_compute_response`(第 155-175 行)

**原逻辑**:
```python
value = compute()
expires_at = time.time() + int(settings.get("ttl_seconds") or 0)
with self._lock:
    self._entries[key] = CacheEntry(expires_at=expires_at, value=self._copy(value))  # deepcopy #1
    self._prune_locked(time.time(), max_entries)
    self._inflight.pop(key, None)
with inflight.condition:
    inflight.value = self._copy(value)  # deepcopy #2
    inflight.done = True
    inflight.condition.notify_all()
return value  # 返回原始对象(无 copy)
```

**优化后**:
```python
value = compute()
expires_at = time.time() + int(settings.get("ttl_seconds") or 0)
with self._lock:
    self._entries[key] = CacheEntry(expires_at=expires_at, value=value)  # 存引用,不 deepcopy
    self._prune_locked(time.time(), max_entries)
    self._inflight.pop(key, None)
with inflight.condition:
    inflight.value = value  # 存引用,不 deepcopy
    inflight.done = True
    inflight.condition.notify_all()
return self._copy(value)  # 返回 deepcopy,防止调用方修改影响缓存
```

**效果**:从 2 次 deepcopy 降到 1 次(仅返回时)。

**安全性论证**:
- `value = compute()` 返回的是新建对象,上游不会再修改它
- entries 和 inflight 存同一引用,互不干扰(inflight 在 pop 后释放引用)
- 返回 deepcopy 保证调用方修改不影响缓存
- 缓存命中时(`get_or_compute_response` 第 137 行、第 153 行)仍走 `self._copy`,保持不变

#### 3b. 流式路径 `get_or_compute_stream`(第 213-235 行)

**原逻辑**:
```python
chunks: list[dict[str, Any]] = []
try:
    for chunk in compute():
        chunks.append(self._copy(chunk))  # 每个 chunk deepcopy #1
        yield chunk
...
with self._lock:
    self._entries[key] = CacheEntry(expires_at=expires_at, value=self._copy(chunks))  # 整列表 deepcopy #2
    ...
with inflight.condition:
    inflight.value = self._copy(chunks)  # 整列表 deepcopy #3
    ...
```

**优化后**:
```python
chunks: list[dict[str, Any]] = []
try:
    for chunk in compute():
        chunks.append(chunk)  # 存原始引用,不 deepcopy
        yield chunk
...
with self._lock:
    self._entries[key] = CacheEntry(expires_at=expires_at, value=chunks)  # 存引用,不 deepcopy
    ...
with inflight.condition:
    inflight.value = chunks  # 存引用,不 deepcopy
    ...
```

**效果**:
- 去掉每个 chunk 的 deepcopy(第 216 行):省 N 次 deepcopy(N = chunk 数,可能数百)
- 去掉写入 entries 的全列表 deepcopy(第 229 行):省 1 次
- 去掉写入 inflight 的全列表 deepcopy(第 233 行):省 1 次
- 缓存命中时(第 193 行 `yield from self._copy(entry.value)`、第 210 行 `yield from self._copy(inflight.value)`)仍保持 deepcopy,确保返回独立副本

**安全性论证**:
- `compute()` 产出的 chunk 是上游解析的新对象,产出后不会被上游修改
- `yield chunk` 直接返回原始 chunk;流式消费方(OpenAI SSE 适配层)只做序列化读取,不会修改 chunk dict
- entries 和 inflight 存同一 chunks 引用;inflight 被 pop 后不影响 entries
- 命中缓存时仍 deepcopy 整个列表,返回的副本与缓存隔离
- **唯一风险**:若消费方在 yield 后修改了 chunk dict,会影响缓存。经审查,流式 chunk 消费路径(`services/protocol/openai_v1_chat_complete.py`)只做 `json.dumps` 序列化,不做修改,风险可接受

---

## 不做的事项(明确排除)

| 项 | 原因 |
|---|---|
| 增加 `--workers` 多进程 | 单例架构(AccountService/ConfigStore 模块级单例),多 worker 会导致每个进程独立加载账号池,内存反增 |
| 引入 psutil / 内存监控端点 | 用户明确选择"暂不加监控" |
| AccountService 改分片加载 | 改动大,超出"少数高危代码点"范围 |
| backup_service tar.gz 流式化 | 改动大,涉及上传/下载/加解密全链路,超出本次范围 |
| log_service 改行式读取 | 改动中等,本次聚焦配置+缓存深拷贝 |
| Dockerfile CMD 修改 | 保持单 worker,uvicorn 参数已合理 |

---

## 验证步骤

### 1. 配置验证
- `docker-compose config` 确认 YAML 语法正确,`mem_limit` 被识别
- `docker compose -f docker-compose.local.yml up -d --build` 本地启动
- `docker stats chatgpt2api-local` 观察 MEM USAGE / LIMIT 是否显示 1GiB 上限

### 2. 缓存功能验证
- 启动后访问 Web UI(`http://127.0.0.1:8000/`),使用 auth key 登录
- 发送两次相同的 chat completion 请求(流式 + 非流式),确认第二次命中缓存(响应速度明显加快,日志无重复上游调用)
- 发送不同 prompt 的请求,确认缓存未错误命中(返回内容正确)

### 3. 内存占用验证
- 使用 `docker stats` 持续观察内存:
  - 启动后空载内存应低于 150MB
  - 发送 10+ 次流式请求后,内存峰值应显著低于优化前(预期从数百 MB 降到数十 MB 量级)
  - 等待 60 秒(TTL 过期)后,缓存自动清理,内存应回落
- 触发 `POST /v1/chat/completions` 长回复请求(如要求生成 2000 字),观察内存峰值
- 确认无 OOM Kill: `docker inspect chatgpt2api-local --format '{{.State.OOMKilled}}'` 应为 false

### 4. 回归验证
- 执行 `/health`、`/version`、`/v1/models` 端点确认服务正常
- 账号刷新、图片生成等后台任务正常执行(观察日志无异常)

---

## 假设与决策

1. **单 worker 保持不变**:项目使用模块级单例(`account_service`、`config`、`chat_completion_cache`),多 worker 会导致状态不同步和内存成倍增加
2. **deepcopy 优化基于上游不改返回值**:经审查 `compute()` 返回的 chunk/dict 在 yield 后不会被上游修改,存引用安全
3. **max_entries=64 足够**:inflight 去重已覆盖并发热点,64 条 × 60s TTL 覆盖常见热点 prompt
4. **mem_reservation=512m**:软限制,确保容器启动时宿主机预留足够内存
5. **不改 Dockerfile**:uvicorn 单 worker 参数已合理,无需调整
