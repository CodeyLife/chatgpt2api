import { httpRequest, request } from "@/lib/request";

export type AccountType = string;
export type AccountStatus = "正常" | "限流" | "异常" | "禁用";
export type ImageModel = string;
export type AuthRole = "admin" | "user";
export type ImageStorageMode = "local" | "webdav" | "both";

export type ImageStorageSettings = {
  enabled: boolean;
  mode: ImageStorageMode;
  webdav_url: string;
  webdav_username: string;
  webdav_password: string;
  webdav_root_path: string;
  public_base_url: string;
};

export type Account = {
  access_token: string;
  type: AccountType;
  source_type?: string | null;
  status: AccountStatus;
  quota: number;
  email?: string | null;
  user_id?: string | null;
  note?: string | null;
  note_updated_at?: string | null;
  twofa_enabled?: boolean;
  totp_secret?: string | null;
  recovery_codes?: string[];
  chatgpt_plan_check?: {
    ok?: boolean;
    status?: "queued" | "running" | "success" | "failed" | string;
    job_id?: string;
    current_plan_type?: string;
    plus_trial_eligible?: boolean;
    error?: string;
    checked_at?: string;
    queued_at?: string;
    started_at?: string;
    finished_at?: string;
    [key: string]: unknown;
  } | null;
  limits_progress?: Array<{
    feature_name?: string;
    remaining?: number;
    reset_after?: string;
  }>;
  default_model_slug?: string | null;
  restore_at?: string | null;
  success: number;
  fail: number;
  /** 当前图片在途数(正在生成、尚未结束的图片数)。号池空闲时持续 > 0 表示并发槽位泄漏。 */
  image_inflight?: number;
  last_used_at?: string | null;
  warmup_until?: string | null;
  first_verified_at?: string | null;
  health_score?: number;
  last_health_event?: string | null;
  next_health_check_at?: string | null;
  proxy?: string | null;
  agent_identity?: CodexAgentIdentity | null;
  codex_oauth?: {
    status?: string;
    provider?: string;
    pool_id?: string;
    auth_url?: string;
    state?: string;
    error?: string;
    created_at?: string;
    updated_at?: string;
  } | null;
  flow_trigger?: {
    status?: string;
    ok?: boolean;
    http_status?: number | null;
    flow_id?: string | null;
    message?: string;
    triggered_at?: string;
  } | null;
  extract_link?: {
    ok?: boolean;
    status?: "queued" | "running" | "success" | "failed" | string;
    job_id?: string;
    remote_job_id?: string;
    link_type?: "pix" | "upi" | string;
    message?: string;
    error?: string;
    result?: Record<string, unknown>;
    logs?: string[];
    queued_at?: string;
    started_at?: string;
    finished_at?: string;
    cdk_remaining?: number | string | null;
  } | null;
};

export type CodexAgentIdentity = {
  agent_runtime_id: string;
  agent_private_key: string;
  account_id: string;
  chatgpt_user_id: string;
  email: string;
  plan_type: string;
  chatgpt_account_is_fedramp: boolean;
};

export type AccountImportPayload = {
  access_token: string;
  accessToken?: string;
  type?: string;
  export_type?: string;
  source_type?: string;
  agent_identity?: CodexAgentIdentity;
  twofa_enabled?: boolean;
  totp_secret?: string;
  recovery_codes?: string[];
  [key: string]: unknown;
};

export type Model = {
  id: string;
  object: string;
  created: number;
  owned_by: string;
  permission: unknown[];
  root: string;
  parent: string | null;
};

type AccountListResponse = {
  items: Account[];
};

type ModelListResponse = {
  object: string;
  data: Model[];
};

type AccountMutationResponse = {
  items: Account[];
  added?: number;
  skipped?: number;
  removed?: number;
  refreshed?: number;
  relogined?: number;
  errors?: Array<{ access_token: string; error: string }>;
};

export type CodexAgentIdentityResponse = AccountMutationResponse & {
  auth_json: {
    auth_mode: "agent_identity";
    agent_identity: CodexAgentIdentity;
  };
  account_payload: AccountImportPayload;
  verify_warning?: string;
};

export type AccountRefreshResponse = {
  items: Account[];
  refreshed: number;
  relogined?: number;
  errors: Array<{ access_token: string; error: string }>;
};

export type RefreshProgressResponse = {
  total: number;
  processed: number;
  done: boolean;
  error: string | null;
  status_counts?: Record<string, number>;
  total_quota?: number;
  result?: AccountRefreshResponse | null;
  results?: Array<{ token: string; status: string; error?: string | null }>;
};

type AccountUpdateResponse = {
  item: Account;
  items: Account[];
};

type AccountNoteUpdateResponse = {
  updated: number;
  skipped: number;
  items: Account[];
  skipped_items?: Array<{ access_token: string; reason: string }>;
};

export type ProxyRuntimeEgressMode = "direct" | "single_proxy";
export type ProxyRuntimeClearanceMode = "none" | "manual" | "flaresolverr";

export type ProxyRuntimeClearanceSettings = {
  enabled: boolean;
  mode: ProxyRuntimeClearanceMode;
  cf_cookies: string;
  cf_clearance: string;
  user_agent: string;
  browser: string;
  flaresolverr_url: string;
  timeout_sec: number | string;
  refresh_interval: number | string;
  warm_up_on_start: boolean;
  has_cf_cookies?: boolean;
  has_cf_clearance?: boolean;
};

export type ProxyRuntimeSettings = {
  enabled: boolean;
  egress_mode: ProxyRuntimeEgressMode;
  proxy_url: string;
  resource_proxy_url: string;
  skip_ssl_verify: boolean;
  reset_session_status_codes: number[];
  clearance: ProxyRuntimeClearanceSettings;
};

export type ProxyRuntimeStatus = {
  enabled: boolean;
  egress_mode: ProxyRuntimeEgressMode | string;
  proxy_source: string;
  has_proxy: boolean;
  clearance_enabled: boolean;
  clearance_mode: ProxyRuntimeClearanceMode | string;
  has_clearance_bundle: boolean;
  cached_clearance_hosts: string[];
};

export type ProxyRuntimeResponse = {
  runtime: ProxyRuntimeSettings;
  status: ProxyRuntimeStatus;
};

export type ThirdPartyAppsSettings = {
  infinite_canvas: {
    enabled: boolean;
    url: string;
  };
};

export type ExtractLinkSettings = {
  enabled: boolean;
  api_base: string;
  cdk: string;
  has_cdk?: boolean;
  link_type: "pix" | "upi" | string;
  workers: number | string;
  queue_limit: number | string;
  request_timeout: number | string;
  event_timeout: number | string;
};

export type SettingsConfig = {
  proxy: string;
  base_url?: string;
  global_system_prompt?: string;
  default_upstream_model_name?: string;
  default_thinking_effort?: "auto" | "standard" | "extended" | "max";
  sensitive_words?: string[];
  ai_review?: {
    enabled?: boolean;
    base_url?: string;
    api_key?: string;
    model?: string;
    prompt?: string;
  };
  refresh_account_interval_minute?: number | string;
  image_retention_days?: number | string;
  image_poll_timeout_secs?: number | string;
  image_account_concurrency?: number | string;
  image_parallel_generation?: boolean;
  image_settle_enabled?: boolean;
  image_check_before_hit_enabled?: boolean;
  image_remove_conversation_after_result?: boolean;
  image_convert_result_to_jpg?: boolean;
  image_remove_conversation_always?: boolean;
  image_settle_secs?: number | string;
  image_timeout_retry_secs?: number | string;
  auto_remove_invalid_accounts?: boolean;
  auto_remove_rate_limited_accounts?: boolean;
  auto_relogin_after_refresh?: boolean;
  account_management_log_enabled?: boolean;
  log_levels?: string[];
  image_storage?: ImageStorageSettings;
  proxy_runtime?: ProxyRuntimeSettings;
  third_party_apps?: ThirdPartyAppsSettings;
  extract_link?: ExtractLinkSettings;
  backup?: BackupSettings;
  backup_state?: BackupState;
  [key: string]: unknown;
};

export type BackupInclude = {
  config: boolean;
  cpa: boolean;
  sub2api: boolean;
  logs: boolean;
  image_tasks: boolean;
  accounts_snapshot: boolean;
  auth_keys_snapshot: boolean;
  images: boolean;
};

export type BackupSettings = {
  enabled: boolean;
  provider: "cloudflare_r2" | string;
  account_id: string;
  access_key_id: string;
  secret_access_key: string;
  bucket: string;
  prefix: string;
  interval_minutes: number | string;
  rotation_keep: number | string;
  encrypt: boolean;
  passphrase: string;
  include: BackupInclude;
};

export type BackupState = {
  running: boolean;
  last_started_at?: string | null;
  last_finished_at?: string | null;
  last_status?: string;
  last_error?: string | null;
  last_object_key?: string | null;
};

export type BackupItem = {
  key: string;
  name: string;
  size: number;
  updated_at?: string | null;
  encrypted: boolean;
};

export type BackupDetail = {
  key: string;
  name: string;
  encrypted: boolean;
  created_at?: string | null;
  trigger?: string | null;
  app_version?: string | null;
  storage_backend?: Record<string, unknown> | null;
  files: Array<{
    name: string;
    exists: boolean;
    content_type?: string;
    size: number;
    sha256?: string;
  }>;
  snapshots: Array<{
    name: string;
    count: number;
  }>;
};

export type ManagedImage = {
  rel: string;
  path?: string;
  name: string;
  date: string;
  size: number;
  url: string;
  thumbnail_url?: string;
  created_at: string;
  width?: number;
  height?: number;
  tags?: string[];
};

export type SystemLog = {
  id: string;
  time: string;
  type: "call" | "account" | string;
  summary?: string;
  detail?: Record<string, unknown>;
  [key: string]: unknown;
};

export type ImageResponse = {
  created: number;
  data: Array<{ b64_json?: string; url?: string; revised_prompt?: string }>;
};

export type ImageTask = {
  id: string;
  status: "queued" | "running" | "success" | "error";
  mode: "generate" | "edit";
  model?: ImageModel;
  size?: string;
  quality?: string;
  created_at: string;
  updated_at: string;
  conversation_id?: string;
  data?: Array<{ b64_json?: string; url?: string; revised_prompt?: string }>;
  error?: string;
  progress?: string;
  elapsed_secs?: number;
  duration_ms?: number;
};

type ImageTaskListResponse = {
  items: ImageTask[];
  missing_ids: string[];
};

export type LoginResponse = {
  ok: boolean;
  version: string;
  role: AuthRole;
  subject_id: string;
  name: string;
};

export type UserKey = {
  id: string;
  name: string;
  role: "user";
  enabled: boolean;
  created_at: string | null;
  last_used_at: string | null;
};

export type OutlookPoolStats = {
  unused: number;
  in_use: number;
  used: number;
  token_invalid: number;
  failed: number;
};

export type RegisterConfig = {
  enabled: boolean;
  registration_driver?: string;
  drivers?: Array<{
    name: string;
    label: string;
    supports_agent_identity: boolean;
    supports_codex_oauth: boolean;
    description: string;
  }>;
  mail: {
    request_timeout: number;
    wait_timeout: number;
    wait_interval: number;
    api_use_register_proxy: boolean;
    providers: Array<Record<string, unknown>>;
  };
  proxy: string;
  total: number;
  threads: number;
  mode: "total" | "quota" | "available";
  target_quota: number;
  target_available: number;
  check_interval: number;
  register_interval_min: number;
  register_interval_max: number;
  sentinel_browser_enabled: boolean;
  sentinel_browser_headless: boolean;
  sentinel_browser_timeout: number;
  sentinel_browser_chrome_path: string;
  sentinel_browser_sdk_url: string;
  sentinel_browser_fallback: boolean;
  codex_agent_identity_enabled: boolean;
  codex_agent_identity_verify_task: boolean;
  codex_oauth_enabled?: boolean;
  codex_oauth_via_cpa?: boolean;
  codex_oauth_cpa_pool_id?: string;
  chatgpt_web?: Record<string, unknown>;
  humanize?: Record<string, unknown>;
  profile?: Record<string, unknown>;
  flow_trigger?: Record<string, unknown>;
  browser_use?: Record<string, unknown>;
  skyvern?: Record<string, unknown>;
  roxy?: Record<string, unknown>;
  cloak?: Record<string, unknown>;
  sms?: Record<string, unknown>;
  new_account_warmup_minutes: number;
  new_account_verify_delay_seconds: number;
  new_account_max_verify_workers: number;
  stats: {
    job_id?: string;
    success: number;
    fail: number;
    done: number;
    running: number;
    threads: number;
    elapsed_seconds?: number;
    avg_seconds?: number;
    success_rate?: number;
    current_quota?: number;
    current_available?: number;
    started_at?: string;
    updated_at?: string;
    finished_at?: string;
  };
  logs?: Array<{
    time: string;
    text: string;
    level: string;
  }>;
};

export type RegisterRuntimeStatus = {
  runtime: {
    playwright: { available: boolean; version?: string; error?: string };
    sentinel: { available: boolean; chrome_path?: string; error?: string };
  };
  drivers: NonNullable<RegisterConfig["drivers"]>;
  registration_driver: string;
};

export async function login(authKey: string) {
  const normalizedAuthKey = String(authKey || "").trim();
  return httpRequest<LoginResponse>("/auth/login", {
    method: "POST",
    body: {},
    headers: {
      Authorization: `Bearer ${normalizedAuthKey}`,
    },
    redirectOnUnauthorized: false,
  });
}

export async function fetchAccounts() {
  return httpRequest<AccountListResponse>("/api/accounts");
}

export async function fetchModels() {
  return httpRequest<ModelListResponse>("/v1/models");
}

export async function createAccounts(tokens: string[], accounts: AccountImportPayload[] = []) {
  return httpRequest<AccountMutationResponse>("/api/accounts", {
    method: "POST",
    body: { tokens, accounts },
  });
}

export async function exportAccounts(accessTokens: string[] = [], format: "json" | "zip" = "json") {
  const response = await request.post("/api/accounts/export", { access_tokens: accessTokens, format }, { responseType: "blob" });
  const disposition = String(response.headers["content-disposition"] || "");
  const match = disposition.match(/filename="?([^";]+)"?/i);
  const filename = match?.[1] || (format === "zip" ? "codex-accounts.zip" : "codex-accounts.json");
  const blob = new Blob([response.data], { type: format === "zip" ? "application/zip" : "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export async function createCodexAgentIdentity(input: {
  access_token?: string;
  session_json?: unknown;
  verify_task?: boolean;
  import_account?: boolean;
}) {
  return httpRequest<CodexAgentIdentityResponse>("/api/accounts/codex-agent-identity", {
    method: "POST",
    body: input,
  });
}

export type OAuthLoginStartResponse = {
  session_id: string;
  authorize_url: string;
  expires_in: string;
  redirect_uri_prefix: string;
};

export async function startOAuthLogin(emailHint?: string) {
  return httpRequest<OAuthLoginStartResponse>("/api/accounts/oauth/start", {
    method: "POST",
    body: { email_hint: emailHint ?? "" },
  });
}

export async function finishOAuthLogin(sessionId: string, callback: string) {
  return httpRequest<AccountMutationResponse>("/api/accounts/oauth/finish", {
    method: "POST",
    body: { session_id: sessionId, callback },
  });
}

export async function deleteAccounts(tokens: string[]) {
  return httpRequest<AccountMutationResponse>("/api/accounts", {
    method: "DELETE",
    body: { tokens },
  });
}

export async function refreshAccounts(accessTokens: string[]) {
  return httpRequest<{ progress_id: string }>("/api/accounts/refresh", {
    method: "POST",
    body: { access_tokens: accessTokens },
  });
}

export async function checkAccountPlan(accessToken: string, proxy = "") {
  return httpRequest<Record<string, unknown>>("/api/accounts/plan-check", {
    method: "POST",
    body: { access_token: accessToken, proxy },
  });
}

export type AccountPlanCheckStartResponse = {
  accepted: number;
  skipped: number;
  jobs: Array<{ access_token: string; job_id: string; email?: string }>;
  skipped_items: Array<{ access_token: string; reason: string }>;
  items: Account[];
};

export async function startAccountPlanCheck(accessTokens: string[], proxy = "") {
  return httpRequest<AccountPlanCheckStartResponse>("/api/accounts/plan-check/batch", {
    method: "POST",
    body: { access_tokens: accessTokens, proxy },
  });
}

export type ExtractLinkStartResponse = {
  accepted: number;
  skipped: number;
  items: Account[];
  jobs: Array<{ access_token: string; job_id: string; email?: string }>;
  skipped_items: Array<{ access_token: string; reason: string }>;
};

export async function fetchExtractLinkSettings() {
  return httpRequest<{ settings: ExtractLinkSettings }>("/api/accounts/extract-link");
}

export async function queryExtractLinkCDK(cdk = "") {
  return httpRequest<Record<string, unknown>>("/api/accounts/extract-link/cdk", {
    method: "POST",
    body: { cdk },
  });
}

export async function startExtractLink(accessTokens: string[], linkType = "", cdk = "") {
  return httpRequest<ExtractLinkStartResponse>("/api/accounts/extract-link", {
    method: "POST",
    body: { access_tokens: accessTokens, link_type: linkType, cdk },
  });
}

export type CodexOAuthRetryJob = {
  job_id: string;
  status: "queued" | "running" | "stopping" | "done" | "failed" | "stopped" | string;
  pool_id: string;
  created_at: string;
  updated_at: string;
  total: number;
  completed: number;
  succeeded: number;
  pending_callback?: number;
  failed: number;
  stopped: number;
  results: Array<{
    token: string;
    email?: string | null;
    status: string;
    auth_url?: string;
    state?: string;
    error?: string;
  }>;
};

export async function startAccountCodexOAuthRetry(accessTokens: string[], cpaPoolId: string) {
  return httpRequest<CodexOAuthRetryJob>("/api/accounts/codex-oauth/retry", {
    method: "POST",
    body: { access_tokens: accessTokens, cpa_pool_id: cpaPoolId },
  });
}

export async function fetchAccountCodexOAuthRetry(jobId: string) {
  return httpRequest<CodexOAuthRetryJob>(`/api/accounts/codex-oauth/retry/${jobId}`);
}

export async function stopAccountCodexOAuthRetry(jobId: string) {
  return httpRequest<CodexOAuthRetryJob>(`/api/accounts/codex-oauth/retry/${jobId}/stop`, { method: "POST" });
}

export type AccountCodexOAuthCallbackResponse = {
  ok: boolean;
  access_token: string;
  pool_id: string;
  auth_json?: AccountImportPayload | null;
  import_result?: AccountMutationResponse | null;
  items?: Account[] | null;
  browser?: {
    provider?: string;
    session_id?: string;
    profile_id?: string;
    proxy_country_code?: string;
    [key: string]: unknown;
  } | null;
};

export async function submitAccountCodexOAuthCallback(input: {
  access_token: string;
  callback_url: string;
  cpa_pool_id?: string;
}) {
  return httpRequest<AccountCodexOAuthCallbackResponse>("/api/accounts/codex-oauth/callback", {
    method: "POST",
    body: input,
  });
}

export async function captureAccountCodexOAuthCallback(input: {
  access_token: string;
  provider: string;
  cpa_pool_id?: string;
  timeout_seconds?: number;
}) {
  return httpRequest<AccountCodexOAuthCallbackResponse>("/api/accounts/codex-oauth/browser-callback", {
    method: "POST",
    body: input,
  });
}

export async function fetchRefreshProgress(progressId: string) {
  return httpRequest<RefreshProgressResponse>(`/api/accounts/refresh/progress/${progressId}`);
}

export async function reLoginAccounts(accessTokens: string[]) {
  return httpRequest<{ progress_id: string }>("/api/accounts/re-login", {
    method: "POST",
    body: { access_tokens: accessTokens },
  });
}

export async function fetchReLoginProgress(progressId: string) {
  return httpRequest<RefreshProgressResponse>(`/api/accounts/re-login/progress/${progressId}`);
}

export async function updateAccount(
  accessToken: string,
  updates: {
    type?: AccountType;
    status?: AccountStatus;
    quota?: number;
    proxy?: string;
    note?: string;
  },
) {
  return httpRequest<AccountUpdateResponse>("/api/accounts/update", {
    method: "POST",
    body: {
      access_token: accessToken,
      ...updates,
    },
  });
}

export async function generateImage(prompt: string, model?: ImageModel, size?: string, quality = "auto") {
  return httpRequest<ImageResponse>(
    "/v1/images/generations",
    {
      method: "POST",
      body: {
        prompt,
        ...(model ? { model } : {}),
        ...(size ? { size } : {}),
        quality,
        n: 1,
        response_format: "b64_json",
      },
    },
  );
}

export async function editImage(files: File | File[], prompt: string, model?: ImageModel, size?: string, quality = "auto") {
  const formData = new FormData();
  const uploadFiles = Array.isArray(files) ? files : [files];

  uploadFiles.forEach((file) => {
    formData.append("image", file);
  });
  formData.append("prompt", prompt);
  if (model) {
    formData.append("model", model);
  }
  if (size) {
    formData.append("size", size);
  }
  formData.append("quality", quality);
  formData.append("n", "1");

  return httpRequest<ImageResponse>(
    "/v1/images/edits",
    {
      method: "POST",
      body: formData,
    },
  );
}

export async function createImageGenerationTask(clientTaskId: string, prompt: string, model?: ImageModel, size?: string, quality = "auto") {
  return httpRequest<ImageTask>("/api/image-tasks/generations", {
    method: "POST",
    body: {
      client_task_id: clientTaskId,
      prompt,
      ...(model ? { model } : {}),
      ...(size ? { size } : {}),
      quality,
    },
  });
}

export async function createImageEditTask(
  clientTaskId: string,
  files: File | File[],
  prompt: string,
  model?: ImageModel,
  size?: string,
  quality = "auto",
) {
  const formData = new FormData();
  const uploadFiles = Array.isArray(files) ? files : [files];

  uploadFiles.forEach((file) => {
    formData.append("image", file);
  });
  formData.append("client_task_id", clientTaskId);
  formData.append("prompt", prompt);
  if (model) {
    formData.append("model", model);
  }
  if (size) {
    formData.append("size", size);
  }
  formData.append("quality", quality);

  return httpRequest<ImageTask>("/api/image-tasks/edits", {
    method: "POST",
    body: formData,
  });
}

export async function fetchImageTasks(ids: string[]) {
  const params = new URLSearchParams();
  if (ids.length > 0) {
    params.set("ids", ids.join(","));
  }
  params.set("_t", String(Date.now()));
  return httpRequest<ImageTaskListResponse>(`/api/image-tasks?${params.toString()}`);
}

export async function resumeImagePoll(taskId: string, extraTimeoutSecs = 30) {
  return httpRequest<ImageTask>(`/api/image-tasks/${encodeURIComponent(taskId)}/resume-poll`, {
    method: "POST",
    body: { extra_timeout_secs: extraTimeoutSecs },
  });
}

export async function fetchSettingsConfig() {
  return httpRequest<{ config: SettingsConfig }>("/api/settings");
}

export async function updateSettingsConfig(settings: SettingsConfig) {
  return httpRequest<{ config: SettingsConfig }>("/api/settings", {
    method: "POST",
    body: settings,
  });
}

export async function fetchThirdPartyApps() {
  return httpRequest<{ third_party_apps: ThirdPartyAppsSettings }>("/api/third-party-apps");
}

export async function testBackupConnection() {
  return httpRequest<{ result: { ok: boolean; status: number } }>("/api/backup/test", {
    method: "POST",
    body: {},
  });
}

export async function testImageStorageConnection() {
  return httpRequest<{ result: { ok: boolean; status: number; error?: string } }>("/api/image-storage/test", {
    method: "POST",
    body: {},
  });
}

export async function syncImageStorage() {
  return httpRequest<{ result: { uploaded: number; skipped: number; failed: number } }>("/api/image-storage/sync", {
    method: "POST",
    body: {},
  });
}

export async function fetchBackups() {
  return httpRequest<{ items: BackupItem[]; state: BackupState; settings: BackupSettings }>("/api/backups");
}

export async function runBackupNow() {
  return httpRequest<{ result: { key: string; size: number; encrypted: boolean } }>("/api/backups/run", {
    method: "POST",
    body: {},
  });
}

export async function deleteBackup(key: string) {
  return httpRequest<{ ok: boolean }>("/api/backups/delete", {
    method: "POST",
    body: { key },
  });
}

export async function fetchBackupDetail(key: string) {
  const params = new URLSearchParams();
  params.set("key", key);
  return httpRequest<{ item: BackupDetail }>(`/api/backups/detail?${params.toString()}`);
}

export function getBackupDownloadUrl(key: string) {
  const params = new URLSearchParams();
  params.set("key", key);
  return `/api/backups/download?${params.toString()}`;
}

export async function fetchManagedImages(filters: { start_date?: string; end_date?: string }) {
  const params = new URLSearchParams();
  if (filters.start_date) params.set("start_date", filters.start_date);
  if (filters.end_date) params.set("end_date", filters.end_date);
  return httpRequest<{ items: ManagedImage[]; groups: Array<{ date: string; items: ManagedImage[] }> }>(
    `/api/images${params.toString() ? `?${params.toString()}` : ""}`,
  );
}

export async function deleteManagedImages(body: { paths?: string[]; start_date?: string; end_date?: string; all_matching?: boolean }) {
  return httpRequest<{ removed: number }>("/api/images/delete", { method: "POST", body });
}

export async function downloadImages(paths: string[]) {
  const response = await request.post("/api/images/download", { paths }, { responseType: "blob" });
  const blob = response.data as Blob;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "images.zip";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export async function downloadSingleImage(path: string) {
  const response = await request.get(`/api/images/download/${path}`, { responseType: "blob" });
  const blob = response.data as Blob;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = path.split("/").pop() || "image.png";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export async function fetchImageTags() {
  return httpRequest<{ tags: string[] }>("/api/images/tags");
}

export async function setImageTags(path: string, tags: string[]) {
  return httpRequest<{ ok: boolean; tags: string[] }>("/api/images/tags", {
    method: "POST",
    body: { path, tags },
  });
}

export async function deleteImageTag(tag: string) {
  return httpRequest<{ ok: boolean; removed_from: number }>(`/api/images/tags/${encodeURIComponent(tag)}`, {
    method: "DELETE",
  });
}

export type ImageStorageStats = {
  disk_total_mb: number; disk_used_mb: number; disk_free_mb: number;
  image_count: number; image_size_mb: number; image_size_bytes: number;
};

export async function fetchImageStorage() {
  return httpRequest<ImageStorageStats>("/api/images/storage");
}

export async function compressAllImages() {
  return httpRequest<{ compressed: number; saved_bytes: number; saved_mb: number }>("/api/images/storage/compress", { method: "POST" });
}

export async function deleteToTarget(targetFreeMb: number) {
  return httpRequest<{ removed: number; freed_mb: number; done: boolean }>(
    `/api/images/storage/cleanup-to-target?target_free_mb=${targetFreeMb}&dry_run=false`,
    { method: "POST" },
  );
}

export async function fetchSystemLogs(filters: { type?: string; start_date?: string; end_date?: string }) {
  const params = new URLSearchParams();
  if (filters.type) params.set("type", filters.type);
  if (filters.start_date) params.set("start_date", filters.start_date);
  if (filters.end_date) params.set("end_date", filters.end_date);
  return httpRequest<{ items: SystemLog[] }>(`/api/logs${params.toString() ? `?${params.toString()}` : ""}`);
}

export async function deleteSystemLogs(ids: string[]) {
  return httpRequest<{ removed: number }>("/api/logs/delete", {
    method: "POST",
    body: { ids },
  });
}

export async function fetchUserKeys() {
  return httpRequest<{ items: UserKey[] }>("/api/auth/users");
}

export async function createUserKey(name: string) {
  return httpRequest<{ item: UserKey; key: string; items: UserKey[] }>("/api/auth/users", {
    method: "POST",
    body: { name },
  });
}

export async function updateUserKey(keyId: string, updates: { enabled?: boolean; name?: string; key?: string }) {
  return httpRequest<{ item: UserKey; items: UserKey[] }>(`/api/auth/users/${keyId}`, {
    method: "POST",
    body: updates,
  });
}

export async function deleteUserKey(keyId: string) {
  return httpRequest<{ items: UserKey[] }>(`/api/auth/users/${keyId}`, {
    method: "DELETE",
  });
}

export async function fetchRegisterConfig() {
  return httpRequest<{ register: RegisterConfig }>("/api/register");
}

export async function fetchRegisterRuntime() {
  return httpRequest<RegisterRuntimeStatus>("/api/register/runtime");
}

export async function updateRegisterConfig(updates: Partial<RegisterConfig>) {
  return httpRequest<{ register: RegisterConfig }>("/api/register", {
    method: "POST",
    body: updates,
  });
}

export async function startRegister() {
  return httpRequest<{ register: RegisterConfig }>("/api/register/start", { method: "POST" });
}

export async function stopRegister() {
  return httpRequest<{ register: RegisterConfig }>("/api/register/stop", { method: "POST" });
}

export async function resetRegister() {
  return httpRequest<{ register: RegisterConfig }>("/api/register/reset", { method: "POST" });
}

export async function resetOutlookPool(scope: "all" | "failed" | "unused" = "all") {
  return httpRequest<{ register: RegisterConfig }>("/api/register/outlook-pool/reset", {
    method: "POST",
    body: { scope },
  });
}

export async function updateAccountNotes(accessTokens: string[], note: string) {
  return httpRequest<AccountNoteUpdateResponse>("/api/accounts/notes", {
    method: "POST",
    body: { access_tokens: accessTokens, note },
  });
}

export type ManualOTPWaiting = {
  email: string;
  task_id?: number | null;
  provider_ref?: string;
  since: number;
  age_seconds: number;
};

export async function fetchManualOTPWaiting() {
  return httpRequest<{ waiting: ManualOTPWaiting[] }>("/api/register/manual-otp");
}

export async function submitManualOTP(email: string, code: string) {
  return httpRequest<{ ok: boolean; email: string; code: string }>("/api/register/manual-otp", {
    method: "POST",
    body: { email, code },
  });
}

// ── CPA (CLIProxyAPI) ──────────────────────────────────────────────

export type CPAPool = {
  id: string;
  name: string;
  base_url: string;
  import_job?: CPAImportJob | null;
};

export type CPARemoteFile = {
  name: string;
  email: string;
};

export type CPAImportJob = {
  job_id: string;
  status: "pending" | "running" | "completed" | "failed";
  created_at: string;
  updated_at: string;
  total: number;
  completed: number;
  added: number;
  skipped: number;
  refreshed: number;
  failed: number;
  errors: Array<{ name: string; error: string }>;
};

export type CPACodexAuthUrlResponse = {
  pool_id: string;
  auth_url: string;
  state: string;
  raw?: Record<string, unknown>;
};

export type CPACodexOAuthCallbackResponse = {
  ok: boolean;
  callback_url: string;
  payload: Record<string, unknown>;
  auth_json?: AccountImportPayload | null;
  import_result?: AccountMutationResponse | null;
};

export type CodexOAuthAuthUrlResponse = {
  auth_url: string;
  state: string;
  code_verifier: string;
  code_challenge: string;
  redirect_uri: string;
};

export type CodexOAuthCallbackResponse = {
  ok: boolean;
  callback_url: string;
  state: string;
  auth_json: AccountImportPayload;
  token_response: Record<string, unknown>;
  import_result?: AccountMutationResponse | null;
};

export async function createCodexOAuthAuthUrl(prompt = "login") {
  return httpRequest<CodexOAuthAuthUrlResponse>("/api/codex-oauth/auth-url", {
    method: "POST",
    body: { prompt },
  });
}

export async function submitCodexOAuthCallback(input: {
  callback_url: string;
  code_verifier: string;
  state?: string;
  import_account?: boolean;
}) {
  return httpRequest<CodexOAuthCallbackResponse>("/api/codex-oauth/callback", {
    method: "POST",
    body: {
      callback_url: input.callback_url,
      code_verifier: input.code_verifier,
      state: input.state || "",
      import_account: input.import_account !== false,
    },
  });
}

export async function fetchCPAPools() {
  return httpRequest<{ pools: CPAPool[] }>("/api/cpa/pools");
}

export async function createCPAPool(pool: { name: string; base_url: string; secret_key: string }) {
  return httpRequest<{ pool: CPAPool; pools: CPAPool[] }>("/api/cpa/pools", {
    method: "POST",
    body: pool,
  });
}

export async function updateCPAPool(
  poolId: string,
  updates: { name?: string; base_url?: string; secret_key?: string },
) {
  return httpRequest<{ pool: CPAPool; pools: CPAPool[] }>(`/api/cpa/pools/${poolId}`, {
    method: "POST",
    body: updates,
  });
}

export async function deleteCPAPool(poolId: string) {
  return httpRequest<{ pools: CPAPool[] }>(`/api/cpa/pools/${poolId}`, {
    method: "DELETE",
  });
}

export async function fetchCPAPoolFiles(poolId: string) {
  return httpRequest<{ pool_id: string; files: CPARemoteFile[] }>(`/api/cpa/pools/${poolId}/files`);
}

export async function fetchCPACodexAuthUrl(poolId: string) {
  return httpRequest<CPACodexAuthUrlResponse>(`/api/cpa/pools/${poolId}/codex-auth-url`);
}

export async function submitCPACodexOAuthCallback(poolId: string, callbackUrl: string, importAccount = true) {
  return httpRequest<CPACodexOAuthCallbackResponse>(`/api/cpa/pools/${poolId}/codex-oauth-callback`, {
    method: "POST",
    body: { callback_url: callbackUrl, import_account: importAccount },
  });
}

export async function startCPAImport(poolId: string, names: string[]) {
  return httpRequest<{ import_job: CPAImportJob | null }>(`/api/cpa/pools/${poolId}/import`, {
    method: "POST",
    body: { names },
  });
}

export async function fetchCPAPoolImportJob(poolId: string) {
  return httpRequest<{ import_job: CPAImportJob | null }>(`/api/cpa/pools/${poolId}/import`);
}

// ── Codex credential management ───────────────────────────────────

export type CodexCredentialItem = {
  filename: string;
  email?: string;
  source?: string;
  cpa_pool_id?: string;
  cpa_filename?: string;
  created_at?: string;
  updated_at?: string;
  exported_at?: string;
  export_count?: number;
  size?: number;
  codex_status?: string;
  codex_error?: string;
  retrying?: boolean;
  account_present?: boolean;
};

export type CodexCredentialSummary = {
  total: number;
  exported: number;
  unexported: number;
  retrying: number;
};

export type CodexCredentialListResponse = {
  summary: CodexCredentialSummary;
  accounts: CodexCredentialItem[];
};

export type CodexRetryLogResponse = {
  ok: boolean;
  log: string;
  running: boolean;
};

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function filenameFromDisposition(disposition: unknown, fallback: string) {
  const value = String(disposition || "");
  const utf8Match = value.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) {
    try {
      return decodeURIComponent(utf8Match[1]);
    } catch {
      return utf8Match[1];
    }
  }
  const match = value.match(/filename="?([^";]+)"?/i);
  return match?.[1] || fallback;
}

export async function fetchCodexCredentials() {
  return httpRequest<CodexCredentialListResponse>("/api/codex");
}

export async function fetchCodexRetryLog(email: string) {
  return httpRequest<CodexRetryLogResponse>(`/api/codex/retry-log?email=${encodeURIComponent(email)}`);
}

export async function retryCodexByEmail(email: string, cpaPoolId = "", provider = "") {
  return httpRequest<{ ok: boolean; message?: string }>("/api/codex/retry", {
    method: "POST",
    body: { email, cpa_pool_id: cpaPoolId, provider },
  });
}

export async function retryCodexBulk(emails: string[], workers = 1, cpaPoolId = "", provider = "") {
  return httpRequest<{
    ok: boolean;
    message?: string;
    started_count?: number;
    skipped?: Array<{ email: string; reason: string }>;
    batch_id?: string;
  }>("/api/codex/retry-bulk", {
    method: "POST",
    body: { emails, workers, cpa_pool_id: cpaPoolId, provider },
  });
}

export async function stopCodexRetry(email: string) {
  return httpRequest<{ ok: boolean; message?: string; running?: boolean }>("/api/codex/stop", {
    method: "POST",
    body: { email },
  });
}

export async function resetCodexRetrying(email: string, status: "failed" | "skipped" | "empty" = "failed") {
  return httpRequest<{ ok: boolean; message?: string; status?: string }>("/api/codex/reset-retrying", {
    method: "POST",
    body: { email, status },
  });
}

export async function resetCodexExport(filename: string) {
  return httpRequest<{ ok: boolean }>("/api/codex/reset-export", {
    method: "POST",
    body: { filename },
  });
}

export async function deleteCodexCredential(filename: string) {
  return httpRequest<{ ok: boolean; deleted: string }>("/api/codex/delete", {
    method: "POST",
    body: { filename },
  });
}

export async function downloadCodexCredential(filename: string, fromCpa = false) {
  const path = fromCpa ? "/api/codex/download-from-cpa" : "/api/codex/download";
  const response = await request.get(`${path}/${encodeURIComponent(filename)}`, { responseType: "blob" });
  const fallback = fromCpa ? filename : filename || "codex.json";
  downloadBlob(response.data as Blob, filenameFromDisposition(response.headers["content-disposition"], fallback));
}

export async function downloadCodexBulk(filenames: string[], fromCpa = false) {
  const path = fromCpa ? "/api/codex/download-bulk-from-cpa" : "/api/codex/download-bulk";
  const response = await request.post(path, { filenames }, { responseType: "blob" });
  const fallback = fromCpa ? "codex-cpa-bulk.zip" : "codex-bulk.json";
  downloadBlob(response.data as Blob, filenameFromDisposition(response.headers["content-disposition"], fallback));
}

// ── Sub2API ────────────────────────────────────────────────────────

export type Sub2APIServer = {
  id: string;
  name: string;
  base_url: string;
  email: string;
  has_api_key: boolean;
  group_id: string;
  import_job?: CPAImportJob | null;
};

export type Sub2APIRemoteAccount = {
  id: string;
  name: string;
  email: string;
  plan_type: string;
  status: string;
  expires_at: string;
  has_refresh_token: boolean;
};

export type Sub2APIRemoteGroup = {
  id: string;
  name: string;
  description: string;
  platform: string;
  status: string;
  account_count: number;
  active_account_count: number;
};

export async function fetchSub2APIServers() {
  return httpRequest<{ servers: Sub2APIServer[] }>("/api/sub2api/servers");
}

export async function createSub2APIServer(server: {
  name: string;
  base_url: string;
  email: string;
  password: string;
  api_key: string;
  group_id: string;
}) {
  return httpRequest<{ server: Sub2APIServer; servers: Sub2APIServer[] }>("/api/sub2api/servers", {
    method: "POST",
    body: server,
  });
}

export async function updateSub2APIServer(
  serverId: string,
  updates: {
    name?: string;
    base_url?: string;
    email?: string;
    password?: string;
    api_key?: string;
    group_id?: string;
  },
) {
  return httpRequest<{ server: Sub2APIServer; servers: Sub2APIServer[] }>(`/api/sub2api/servers/${serverId}`, {
    method: "POST",
    body: updates,
  });
}

export async function fetchSub2APIServerGroups(serverId: string) {
  return httpRequest<{ server_id: string; groups: Sub2APIRemoteGroup[] }>(
    `/api/sub2api/servers/${serverId}/groups`,
  );
}

export async function deleteSub2APIServer(serverId: string) {
  return httpRequest<{ servers: Sub2APIServer[] }>(`/api/sub2api/servers/${serverId}`, {
    method: "DELETE",
  });
}

export async function fetchSub2APIServerAccounts(serverId: string) {
  return httpRequest<{ server_id: string; accounts: Sub2APIRemoteAccount[] }>(
    `/api/sub2api/servers/${serverId}/accounts`,
  );
}

export async function startSub2APIImport(serverId: string, accountIds: string[]) {
  return httpRequest<{ import_job: CPAImportJob | null }>(`/api/sub2api/servers/${serverId}/import`, {
    method: "POST",
    body: { account_ids: accountIds },
  });
}

export async function fetchSub2APIImportJob(serverId: string) {
  return httpRequest<{ import_job: CPAImportJob | null }>(`/api/sub2api/servers/${serverId}/import`);
}

// ── Upstream proxy ────────────────────────────────────────────────

export type ProxySettings = {
  enabled: boolean;
  url: string;
};

export type ProxyTestResult = {
  ok: boolean;
  status: number;
  latency_ms: number;
  error: string | null;
  proxy_source?: string;
  has_proxy?: boolean;
};

export type ClearanceTestResult = {
  ok: boolean;
  status: string;
  latency_ms: number;
  has_cookies: boolean;
  user_agent: string;
  error: string | null;
  runtime: ProxyRuntimeStatus;
};

export async function fetchProxy() {
  return httpRequest<{ proxy: ProxySettings }>("/api/proxy");
}

export async function updateProxy(updates: { enabled?: boolean; url?: string }) {
  return httpRequest<{ proxy: ProxySettings }>("/api/proxy", {
    method: "POST",
    body: updates,
  });
}

export async function testProxy(url?: string) {
  return httpRequest<{ result: ProxyTestResult }>("/api/proxy/test", {
    method: "POST",
    body: { url: url ?? "" },
  });
}

export async function fetchProxyRuntime() {
  return httpRequest<ProxyRuntimeResponse>("/api/proxy/runtime");
}

export async function updateProxyRuntime(runtime: ProxyRuntimeSettings) {
  return httpRequest<ProxyRuntimeResponse>("/api/proxy/runtime", {
    method: "POST",
    body: runtime,
  });
}

export async function testProxyClearance(targetUrl?: string) {
  return httpRequest<{ result: ClearanceTestResult }>("/api/proxy/clearance/test", {
    method: "POST",
    body: { target_url: targetUrl ?? "https://chatgpt.com" },
  });
}
