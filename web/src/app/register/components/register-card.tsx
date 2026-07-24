"use client";

import { useState } from "react";
import { toast } from "sonner";
import { AlertTriangle, LoaderCircle, Plus, Play, RotateCcw, Save, Send, Square, Trash2, UserPlus } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { fetchManualOTPWaiting, fetchRegisterRuntime, submitManualOTP, type ManualOTPWaiting, type RegisterRuntimeStatus } from "@/lib/api";

import { useSettingsStore } from "../../settings/store";

export function RegisterCard() {
  const [manualOTPWaiting, setManualOTPWaiting] = useState<ManualOTPWaiting[]>([]);
  const [manualOTPBusy, setManualOTPBusy] = useState(false);
  const [manualOTPCodes, setManualOTPCodes] = useState<Record<string, string>>({});
  const [runtimeStatus, setRuntimeStatus] = useState<RegisterRuntimeStatus | null>(null);
  const [runtimeBusy, setRuntimeBusy] = useState(false);
  const config = useSettingsStore((state) => state.registerConfig);
  const isLoading = useSettingsStore((state) => state.isLoadingRegister);
  const isSaving = useSettingsStore((state) => state.isSavingRegister);
  const setProxy = useSettingsStore((state) => state.setRegisterProxy);
  const setRegisterDriver = useSettingsStore((state) => state.setRegisterDriver);
  const setTotal = useSettingsStore((state) => state.setRegisterTotal);
  const setThreads = useSettingsStore((state) => state.setRegisterThreads);
  const setMode = useSettingsStore((state) => state.setRegisterMode);
  const setTargetQuota = useSettingsStore((state) => state.setRegisterTargetQuota);
  const setTargetAvailable = useSettingsStore((state) => state.setRegisterTargetAvailable);
  const setCheckInterval = useSettingsStore((state) => state.setRegisterCheckInterval);
  const setRegisterIntervalMin = useSettingsStore((state) => state.setRegisterIntervalMin);
  const setRegisterIntervalMax = useSettingsStore((state) => state.setRegisterIntervalMax);
  const setNewAccountWarmupMinutes = useSettingsStore((state) => state.setRegisterNewAccountWarmupMinutes);
  const setNewAccountVerifyDelaySeconds = useSettingsStore((state) => state.setRegisterNewAccountVerifyDelaySeconds);
  const setNewAccountMaxVerifyWorkers = useSettingsStore((state) => state.setRegisterNewAccountMaxVerifyWorkers);
  const setCodexAgentIdentityEnabled = useSettingsStore((state) => state.setRegisterCodexAgentIdentityEnabled);
  const setCodexAgentIdentityVerifyTask = useSettingsStore((state) => state.setRegisterCodexAgentIdentityVerifyTask);
  const setCodexOAuthEnabled = useSettingsStore((state) => state.setRegisterCodexOAuthEnabled);
  const setCodexOAuthViaCPA = useSettingsStore((state) => state.setRegisterCodexOAuthViaCPA);
  const setCodexOAuthCPAPoolId = useSettingsStore((state) => state.setRegisterCodexOAuthCPAPoolId);
  const setNestedField = useSettingsStore((state) => state.setRegisterNestedField);
  const setMailField = useSettingsStore((state) => state.setRegisterMailField);
  const setMailApiUseRegisterProxy = useSettingsStore((state) => state.setRegisterMailApiUseRegisterProxy);
  const addProvider = useSettingsStore((state) => state.addRegisterProvider);
  const updateProvider = useSettingsStore((state) => state.updateRegisterProvider);
  const deleteProvider = useSettingsStore((state) => state.deleteRegisterProvider);
  const save = useSettingsStore((state) => state.saveRegister);
  const toggle = useSettingsStore((state) => state.toggleRegister);
  const reset = useSettingsStore((state) => state.resetRegister);
  const resetOutlookPool = useSettingsStore((state) => state.resetOutlookPool);
  const pools = useSettingsStore((state) => state.pools);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center rounded-xl border border-stone-200 bg-white/80 p-10">
        <LoaderCircle className="size-5 animate-spin text-stone-400" />
      </div>
    );
  }

  if (!config) return null;

  const stats = config.stats || { success: 0, fail: 0, done: 0, running: 0, threads: config.threads };
  const providers = config.mail.providers || [];
  const logs = config.logs || [];
  const drivers = config.drivers || [];
  const currentDriver = config.registration_driver || "platform_oauth";
  const currentDriverInfo = drivers.find((item) => item.name === currentDriver);
  const driverSupportsAgentIdentity = Boolean(currentDriverInfo?.supports_agent_identity);
  const driverSupportsCodexOAuth = Boolean(currentDriverInfo?.supports_codex_oauth);
  const browserUse = config.browser_use || {};
  const skyvern = config.skyvern || {};
  const roxy = config.roxy || {};
  const cloak = config.cloak || {};
  const sms = config.sms || {};
  const flowTrigger = config.flow_trigger || {};
  const humanize = config.humanize || {};
  const chatgptWeb = config.chatgpt_web || {};
  const profile = config.profile || {};
  const checkRuntime = async () => {
    try {
      setRuntimeBusy(true);
      const data = await fetchRegisterRuntime();
      setRuntimeStatus(data);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "检查注册运行时失败");
    } finally {
      setRuntimeBusy(false);
    }
  };
  const refreshManualOTPWaiting = async () => {
    try {
      setManualOTPBusy(true);
      const data = await fetchManualOTPWaiting();
      setManualOTPWaiting(data.waiting || []);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "读取手动 OTP 等待列表失败");
    } finally {
      setManualOTPBusy(false);
    }
  };
  const submitManualOTPCode = async (email: string) => {
    const code = String(manualOTPCodes[email] || "").trim();
    if (!code) {
      toast.error("请输入验证码");
      return;
    }
    try {
      setManualOTPBusy(true);
      await submitManualOTP(email, code);
      toast.success("验证码已提交");
      setManualOTPCodes((state) => ({ ...state, [email]: "" }));
      await refreshManualOTPWaiting();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "提交验证码失败");
    } finally {
      setManualOTPBusy(false);
    }
  };
  const updateProviderType = (index: number, type: string) => {
    updateProvider(index, {
      type,
      enable: true,
      ...(type === "cloudmail_gen" ? { api_base: "", admin_email: "", admin_password: "", domain: [], subdomain: [], email_prefix: "" } : {}),
      ...(type === "cloudflare_temp_email" ? { api_base: "", admin_password: "", domain: [] } : {}),
      ...(type === "tempmail_lol" ? { api_key: "", domain: [] } : {}),
      ...(type === "moemail" ? { api_base: "", api_key: "", domain: [] } : {}),
      ...(type === "inbucket" ? { api_base: "", domain: [], random_subdomain: true } : {}),
      ...(type === "duckmail" ? { api_key: "", default_domain: "duckmail.sbs" } : {}),
      ...(type === "gptmail" ? { api_key: "", default_domain: "" } : {}),
      ...(type === "yyds_mail" ? { api_base: "https://maliapi.215.im/v1", api_key: "", domain: [], subdomain: "", wildcard: false } : {}),
      ...(type === "ddg_mail" ? { ddg_token: "", cf_inbox_jwt: "", cf_domain: [], admin_password: "" } : {}),
      ...(type === "outlook_token" ? { mailboxes: "", mode: "graph", imap_host: "outlook.office365.com", message_limit: 10 } : {}),
      ...(type === "qqmail_imap" ? { domain: [], qq_email: "", imap_password: "", imap_host: "imap.qq.com", imap_port: 993, message_limit: 15, local_length: 8 } : {}),
      ...(type === "generic_api" ? { mailboxes: "" } : {}),
      ...(type === "manual" ? { mailboxes: "" } : {}),
      ...(type === "mailnest" ? { api_base: "https://mailnest.top", api_key: "", project_code: "chatgpt001" } : {}),
    });
  };

  return (
    <div className="grid h-[calc(100vh-132px)] min-h-[640px] items-stretch gap-0 overflow-hidden rounded-xl border border-stone-200 bg-white/70 xl:grid-cols-2">
      <section className="space-y-4 overflow-y-auto border-b border-stone-200 p-4 xl:border-r xl:border-b-0">
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-center gap-3">
              <div className="flex size-9 items-center justify-center rounded-md bg-stone-100">
                <UserPlus className="size-5 text-stone-600" />
              </div>
              <div>
                <h2 className="text-lg font-semibold tracking-tight">注册配置</h2>
              </div>
            </div>
            <Button className="h-9 rounded-xl bg-stone-950 px-4 text-white hover:bg-stone-800" onClick={() => void save()} disabled={isSaving || config.enabled}>
              {isSaving ? <LoaderCircle className="size-4 animate-spin" /> : <Save className="size-4" />}
              保存配置
            </Button>
          </div>

          <div className="flex items-start gap-2 rounded-xl border border-sky-200 bg-sky-50 px-3 py-2 text-xs leading-5 text-sky-800">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            <span>如果注册失败，后端会保存失败网页/JSON 响应用于诊断；Cloudflare 拦截可在设置页启用 FlareSolverr 清障。</span>
          </div>

          <div className="flex flex-wrap items-center gap-2 rounded-xl border border-stone-200 bg-white px-3 py-2 text-xs text-stone-600">
            <Button type="button" variant="outline" className="h-8 rounded-xl border-stone-200 bg-white" onClick={() => void checkRuntime()} disabled={runtimeBusy}>
              {runtimeBusy ? <LoaderCircle className="size-4 animate-spin" /> : <RotateCcw className="size-4" />}
              检查运行时
            </Button>
            {runtimeStatus ? (
              <>
                <Badge variant={runtimeStatus.runtime.playwright.available ? "default" : "secondary"}>
                  Playwright {runtimeStatus.runtime.playwright.available ? runtimeStatus.runtime.playwright.version || "可用" : "不可用"}
                </Badge>
                <Badge variant={runtimeStatus.runtime.sentinel.available ? "default" : "secondary"}>
                  Sentinel {runtimeStatus.runtime.sentinel.available ? "Chrome 可用" : "Chrome 不可用"}
                </Badge>
                {!runtimeStatus.runtime.playwright.available ? <span className="text-stone-500">{runtimeStatus.runtime.playwright.error}</span> : null}
                {!runtimeStatus.runtime.sentinel.available ? <span className="text-stone-500">{runtimeStatus.runtime.sentinel.error}</span> : null}
              </>
            ) : (
              <span>检查 Playwright、云浏览器 CDP 和 Sentinel Chrome 环境。</span>
            )}
          </div>

          <div className="grid gap-4 md:grid-cols-3">
            <div className="space-y-2">
              <label className="text-sm text-stone-700">注册驱动</label>
              <Select value={currentDriver} onValueChange={setRegisterDriver} disabled={config.enabled}>
                <SelectTrigger className="h-10 rounded-xl border-stone-200 bg-white">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(drivers.length ? drivers : [{ name: "platform_oauth", label: "Platform OAuth" }]).map((driver) => (
                    <SelectItem key={driver.name} value={driver.name}>{driver.label || driver.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <label className="text-sm text-stone-700">注册模式</label>
              <Select value={config.mode || "total"} onValueChange={(value) => setMode(value as "total" | "quota" | "available")} disabled={config.enabled}>
                <SelectTrigger className="h-10 rounded-xl border-stone-200 bg-white">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="total">注册总数</SelectItem>
                  <SelectItem value="quota">号池剩余额度</SelectItem>
                  <SelectItem value="available">可用账号数量</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <label className="text-sm text-stone-700">注册总数</label>
              <Input value={String(config.total)} onChange={(event) => setTotal(event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled || config.mode !== "total"} />
            </div>
            <div className="space-y-2">
              <label className="text-sm text-stone-700">线程数</label>
              <Input value={String(config.threads)} onChange={(event) => setThreads(event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
            </div>
            <div className="space-y-2">
              <label className="text-sm text-stone-700">注册代理</label>
              <Input value={config.proxy} onChange={(event) => setProxy(event.target.value)} placeholder="http://127.0.0.1:7890" className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
            </div>
            <div className="space-y-2">
              <label className="text-sm text-stone-700">目标剩余额度</label>
              <Input value={String(config.target_quota || "")} onChange={(event) => setTargetQuota(event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled || config.mode !== "quota"} />
            </div>
            <div className="space-y-2">
              <label className="text-sm text-stone-700">目标可用账号</label>
              <Input value={String(config.target_available || "")} onChange={(event) => setTargetAvailable(event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled || config.mode !== "available"} />
            </div>
            <div className="space-y-2">
              <label className="text-sm text-stone-700">检查间隔（秒）</label>
              <Input value={String(config.check_interval || "")} onChange={(event) => setCheckInterval(event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled || config.mode === "total"} />
            </div>
            <div className="space-y-2">
              <label className="text-sm text-stone-700">注册间隔最小（秒）</label>
              <Input value={String(config.register_interval_min ?? 0)} onChange={(event) => setRegisterIntervalMin(event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
            </div>
            <div className="space-y-2">
              <label className="text-sm text-stone-700">注册间隔最大（秒）</label>
              <Input value={String(config.register_interval_max ?? 0)} onChange={(event) => setRegisterIntervalMax(event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
            </div>
            <div className="space-y-2">
              <label className="text-sm text-stone-700">新号保护期（分钟）</label>
              <Input value={String(config.new_account_warmup_minutes ?? 30)} onChange={(event) => setNewAccountWarmupMinutes(event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
            </div>
            <div className="space-y-2">
              <label className="text-sm text-stone-700">新号复查延迟（秒）</label>
              <Input value={String(config.new_account_verify_delay_seconds ?? 120)} onChange={(event) => setNewAccountVerifyDelaySeconds(event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
            </div>
            <div className="space-y-2">
              <label className="text-sm text-stone-700">新号复查并发</label>
              <Input value={String(config.new_account_max_verify_workers ?? 2)} onChange={(event) => setNewAccountMaxVerifyWorkers(event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
            </div>
            <div className="space-y-2">
              <label className="text-sm text-stone-700">注册最小年龄</label>
              <Input value={String(profile.min_age ?? 18)} onChange={(event) => setNestedField("profile", "min_age", Number(event.target.value) || 18)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
            </div>
            <div className="space-y-2">
              <label className="text-sm text-stone-700">注册最大年龄</label>
              <Input value={String(profile.max_age ?? 65)} onChange={(event) => setNestedField("profile", "max_age", Number(event.target.value) || 65)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
            </div>
          </div>

          <div className="grid gap-3 rounded-xl border border-stone-200 bg-stone-50 p-3 md:grid-cols-2">
            <label className="flex items-start gap-3 text-sm text-stone-700">
              <Checkbox checked={Boolean(config.codex_agent_identity_enabled)} onCheckedChange={(checked) => setCodexAgentIdentityEnabled(Boolean(checked))} disabled={config.enabled || !driverSupportsAgentIdentity} />
              <span>
                <span className="block font-medium text-stone-800">注册后生成 Codex Agent Identity</span>
                <span className="mt-1 block text-xs leading-5 text-stone-500">
                  {driverSupportsAgentIdentity ? "保存 agent_identity；当前请求链路不会使用它。" : "需要选择 ChatGPT Web Session 驱动。"}
                </span>
              </span>
            </label>
            <label className="flex items-start gap-3 text-sm text-stone-700">
              <Checkbox checked={config.codex_agent_identity_verify_task !== false} onCheckedChange={(checked) => setCodexAgentIdentityVerifyTask(Boolean(checked))} disabled={config.enabled || !config.codex_agent_identity_enabled} />
              <span>
                <span className="block font-medium text-stone-800">生成后验证 task 注册</span>
                <span className="mt-1 block text-xs leading-5 text-stone-500">验证失败会保留 warning，不会用于请求调度。</span>
              </span>
            </label>
            <label className="flex items-start gap-3 text-sm text-stone-700">
              <Checkbox checked={Boolean(config.codex_oauth_enabled)} onCheckedChange={(checked) => setCodexOAuthEnabled(Boolean(checked))} disabled={config.enabled || !driverSupportsCodexOAuth} />
              <span>
                <span className="block font-medium text-stone-800">注册后启用 Codex OAuth</span>
                <span className="mt-1 block text-xs leading-5 text-stone-500">首版通过 CPA 管理接口处理 callback；浏览器驱动后续复用同一开关。</span>
              </span>
            </label>
            <label className="flex items-start gap-3 text-sm text-stone-700">
              <Checkbox checked={config.codex_oauth_via_cpa !== false} onCheckedChange={(checked) => setCodexOAuthViaCPA(Boolean(checked))} disabled={config.enabled || !config.codex_oauth_enabled} />
              <span>
                <span className="block font-medium text-stone-800">Codex OAuth 使用 CPA</span>
                <span className="mt-1 block text-xs leading-5 text-stone-500">由 CPA 持有 verifier 并保存 auth 文件。</span>
              </span>
            </label>
            <div className="space-y-2 md:col-span-2">
              <label className="text-sm text-stone-700">Codex OAuth CPA 连接</label>
              <Select value={config.codex_oauth_cpa_pool_id || "none"} onValueChange={(value) => setCodexOAuthCPAPoolId(value === "none" ? "" : value)} disabled={config.enabled || !config.codex_oauth_enabled || config.codex_oauth_via_cpa === false}>
                <SelectTrigger className="h-10 rounded-xl border-stone-200 bg-white">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">未选择</SelectItem>
                  {pools.map((pool) => (
                    <SelectItem key={pool.id} value={pool.id}>{pool.name || pool.base_url}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {currentDriver === "chatgpt_web" ? (
            <div className="grid gap-3 rounded-xl border border-stone-200 bg-white p-3 md:grid-cols-2">
              <label className="flex items-start gap-3 text-sm text-stone-700">
                <Checkbox
                  checked={chatgptWeb.bootstrap_enabled !== false}
                  onCheckedChange={(checked) => setNestedField("chatgpt_web", "bootstrap_enabled", Boolean(checked))}
                  disabled={config.enabled}
                />
                <span>
                  <span className="block font-medium text-stone-800">ChatGPT Web bootstrap 预热</span>
                  <span className="mt-1 block text-xs leading-5 text-stone-500">注册前后访问 Web 首屏相关接口，让 session 更接近真实 ChatGPT Web 初始化。</span>
                </span>
              </label>
              <label className="flex items-start gap-3 text-sm text-stone-700">
                <Checkbox
                  checked={Boolean(chatgptWeb.bootstrap_strict)}
                  onCheckedChange={(checked) => setNestedField("chatgpt_web", "bootstrap_strict", Boolean(checked))}
                  disabled={config.enabled || chatgptWeb.bootstrap_enabled === false}
                />
                <span>
                  <span className="block font-medium text-stone-800">bootstrap 失败时中断注册</span>
                  <span className="mt-1 block text-xs leading-5 text-stone-500">默认关闭；关闭时预热失败只写 SSE 日志并继续注册。</span>
                </span>
              </label>
            </div>
          ) : null}

          <div className="grid gap-3 rounded-xl border border-stone-200 bg-stone-50 p-3 md:grid-cols-2">
            <label className="flex items-start gap-3 text-sm text-stone-700">
              <Checkbox checked={humanize.enabled !== false} onCheckedChange={(checked) => setNestedField("humanize", "enabled", Boolean(checked))} disabled={config.enabled} />
              <span>
                <span className="block font-medium text-stone-800">浏览器注册人类化延迟</span>
                <span className="mt-1 block text-xs leading-5 text-stone-500">BrowserUse / Skyvern / Roxy / Cloak 注册流会在导航、填表、OTP、拉 session 等动作间随机停顿。</span>
              </span>
            </label>
            <div className="space-y-2">
              <label className="text-sm text-stone-700">延迟倍率</label>
              <Input value={String(humanize.factor ?? 1)} onChange={(event) => setNestedField("humanize", "factor", Number(event.target.value) || 0)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled || humanize.enabled === false} />
            </div>
          </div>

          <div className="grid gap-3 rounded-xl border border-stone-200 bg-white p-3 md:grid-cols-2">
            <label className="flex items-start gap-3 text-sm text-stone-700 md:col-span-2">
              <Checkbox checked={Boolean(flowTrigger.enabled)} onCheckedChange={(checked) => setNestedField("flow_trigger", "enabled", Boolean(checked))} disabled={config.enabled} />
              <span>
                <span className="block font-medium text-stone-800">注册后 Flow Trigger</span>
                <span className="mt-1 block text-xs leading-5 text-stone-500">注册成功并保存基础账号后投递 access token；失败只写入账号字段和日志。</span>
              </span>
            </label>
            <div className="space-y-2 md:col-span-2">
              <label className="text-sm text-stone-700">Flow URL</label>
              <Input value={String(flowTrigger.url || "")} onChange={(event) => setNestedField("flow_trigger", "url", event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled || !flowTrigger.enabled} />
            </div>
            <div className="space-y-2">
              <label className="text-sm text-stone-700">Bearer</label>
              <Input value={String(flowTrigger.bearer || "")} onChange={(event) => setNestedField("flow_trigger", "bearer", event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled || !flowTrigger.enabled} />
            </div>
            <div className="space-y-2">
              <label className="text-sm text-stone-700">Cookie</label>
              <Input value={String(flowTrigger.cookie || "")} onChange={(event) => setNestedField("flow_trigger", "cookie", event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled || !flowTrigger.enabled} />
            </div>
            <div className="space-y-2">
              <label className="text-sm text-stone-700">Token 字段名</label>
              <Input value={String(flowTrigger.access_token_key || "access_token")} onChange={(event) => setNestedField("flow_trigger", "access_token_key", event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled || !flowTrigger.enabled} />
            </div>
            <div className="space-y-2">
              <label className="text-sm text-stone-700">超时（秒）</label>
              <Input value={String(flowTrigger.timeout || 30)} onChange={(event) => setNestedField("flow_trigger", "timeout", Number(event.target.value) || 30)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled || !flowTrigger.enabled} />
            </div>
            <div className="space-y-2">
              <label className="text-sm text-stone-700">Origin</label>
              <Input value={String(flowTrigger.origin || "")} onChange={(event) => setNestedField("flow_trigger", "origin", event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled || !flowTrigger.enabled} />
            </div>
            <div className="space-y-2">
              <label className="text-sm text-stone-700">Referer</label>
              <Input value={String(flowTrigger.referer || "")} onChange={(event) => setNestedField("flow_trigger", "referer", event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled || !flowTrigger.enabled} />
            </div>
            <label className="flex items-center gap-2 text-sm text-stone-700">
              <Checkbox checked={Boolean(flowTrigger.use_register_proxy)} onCheckedChange={(checked) => setNestedField("flow_trigger", "use_register_proxy", Boolean(checked))} disabled={config.enabled || !flowTrigger.enabled} />
              使用注册代理
            </label>
            <label className="flex items-center gap-2 text-sm text-stone-700">
              <Checkbox checked={flowTrigger.verify_ssl !== false} onCheckedChange={(checked) => setNestedField("flow_trigger", "verify_ssl", Boolean(checked))} disabled={config.enabled || !flowTrigger.enabled} />
              验证 TLS
            </label>
            <div className="space-y-2 md:col-span-2">
              <label className="text-sm text-stone-700">Payload JSON</label>
              <Textarea value={JSON.stringify(flowTrigger.payload || {}, null, 2)} onChange={(event) => setNestedField("flow_trigger", "payload", event.target.value)} className="min-h-24 rounded-xl border-stone-200 bg-white font-mono text-xs" disabled={config.enabled || !flowTrigger.enabled} />
            </div>
          </div>

          {currentDriver === "browser_use" ? (
            <div className="grid gap-3 rounded-xl border border-stone-200 bg-white p-3 md:grid-cols-2">
              <div className="space-y-2">
                <label className="text-sm text-stone-700">Browser Use API Key</label>
                <Input value={String(browserUse.api_key || "")} onChange={(event) => setNestedField("browser_use", "api_key", event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
              </div>
              <div className="space-y-2">
                <label className="text-sm text-stone-700">CDP Base</label>
                <Input value={String(browserUse.cdp_base || "wss://connect.browser-use.com")} onChange={(event) => setNestedField("browser_use", "cdp_base", event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
              </div>
              <div className="space-y-2">
                <label className="text-sm text-stone-700">Proxy Country</label>
                <Input value={String(browserUse.proxy_country_code || "")} onChange={(event) => setNestedField("browser_use", "proxy_country_code", event.target.value)} placeholder="us / jp / gb" className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
              </div>
              <div className="space-y-2">
                <label className="text-sm text-stone-700">Profile ID</label>
                <Input value={String(browserUse.profile_id || "")} onChange={(event) => setNestedField("browser_use", "profile_id", event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
              </div>
              <div className="space-y-2">
                <label className="text-sm text-stone-700">Start URL</label>
                <Input value={String(browserUse.start_url || "https://chatgpt.com/auth/login")} onChange={(event) => setNestedField("browser_use", "start_url", event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
              </div>
              <div className="space-y-2">
                <label className="text-sm text-stone-700">页面超时（秒）</label>
                <Input value={String(browserUse.timeout || 90)} onChange={(event) => setNestedField("browser_use", "timeout", Number(event.target.value) || 90)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
              </div>
            </div>
          ) : null}

          {currentDriver === "skyvern" ? (
            <div className="grid gap-3 rounded-xl border border-stone-200 bg-white p-3 md:grid-cols-2">
              <div className="space-y-2">
                <label className="text-sm text-stone-700">Skyvern API Key</label>
                <Input value={String(skyvern.api_key || "")} onChange={(event) => setNestedField("skyvern", "api_key", event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
              </div>
              <div className="space-y-2">
                <label className="text-sm text-stone-700">API Base</label>
                <Input value={String(skyvern.api_base || "https://api.skyvern.com")} onChange={(event) => setNestedField("skyvern", "api_base", event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
              </div>
              <div className="space-y-2">
                <label className="text-sm text-stone-700">Proxy Location</label>
                <Input value={String(skyvern.proxy_location || "")} onChange={(event) => setNestedField("skyvern", "proxy_location", event.target.value)} placeholder="US / JP / RESIDENTIAL_GB" className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
              </div>
              <div className="space-y-2">
                <label className="text-sm text-stone-700">Browser Profile ID</label>
                <Input value={String(skyvern.browser_profile_id || "")} onChange={(event) => setNestedField("skyvern", "browser_profile_id", event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
              </div>
              <div className="space-y-2">
                <label className="text-sm text-stone-700">Start URL</label>
                <Input value={String(skyvern.start_url || "https://chatgpt.com/auth/login")} onChange={(event) => setNestedField("skyvern", "start_url", event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
              </div>
              <div className="space-y-2">
                <label className="text-sm text-stone-700">页面超时（秒）</label>
                <Input value={String(skyvern.timeout || 90)} onChange={(event) => setNestedField("skyvern", "timeout", Number(event.target.value) || 90)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
              </div>
            </div>
          ) : null}

          {currentDriver === "roxy" ? (
            <div className="grid gap-3 rounded-xl border border-stone-200 bg-white p-3 md:grid-cols-2">
              <div className="space-y-2">
                <label className="text-sm text-stone-700">Roxy API Base</label>
                <Input value={String(roxy.api_base || "http://127.0.0.1:50100")} onChange={(event) => setNestedField("roxy", "api_base", event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
              </div>
              <div className="space-y-2">
                <label className="text-sm text-stone-700">Roxy API Token</label>
                <Input value={String(roxy.api_token || "")} onChange={(event) => setNestedField("roxy", "api_token", event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
              </div>
              <div className="space-y-2">
                <label className="text-sm text-stone-700">Profile ID</label>
                <Input value={String(roxy.profile_id || "")} onChange={(event) => setNestedField("roxy", "profile_id", event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
              </div>
              <div className="space-y-2">
                <label className="text-sm text-stone-700">Workspace ID</label>
                <Input value={String(roxy.workspace_id || "")} onChange={(event) => setNestedField("roxy", "workspace_id", event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
              </div>
              <div className="space-y-2">
                <label className="text-sm text-stone-700">Project ID</label>
                <Input value={String(roxy.project_id || "")} onChange={(event) => setNestedField("roxy", "project_id", event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
              </div>
              <div className="space-y-2">
                <label className="text-sm text-stone-700">Start URL</label>
                <Input value={String(roxy.start_url || "https://chatgpt.com/auth/login")} onChange={(event) => setNestedField("roxy", "start_url", event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
              </div>
              <label className="flex items-center gap-2 text-sm text-stone-700">
                <Checkbox checked={Boolean(roxy.open_headless)} onCheckedChange={(checked) => setNestedField("roxy", "open_headless", Boolean(checked))} disabled={config.enabled} />
                无头打开
              </label>
              <label className="flex items-center gap-2 text-sm text-stone-700">
                <Checkbox checked={Boolean(roxy.one_profile_per_account)} onCheckedChange={(checked) => setNestedField("roxy", "one_profile_per_account", Boolean(checked))} disabled={config.enabled} />
                一号一环境
              </label>
              <label className="flex items-center gap-2 text-sm text-stone-700">
                <Checkbox checked={Boolean(roxy.delete_profile_after_run)} onCheckedChange={(checked) => setNestedField("roxy", "delete_profile_after_run", Boolean(checked))} disabled={config.enabled} />
                结束后删除环境
              </label>
              <label className="flex items-center gap-2 text-sm text-stone-700">
                <Checkbox checked={Boolean(roxy.keep_browser_open)} onCheckedChange={(checked) => setNestedField("roxy", "keep_browser_open", Boolean(checked))} disabled={config.enabled} />
                保留浏览器
              </label>
              <label className="flex items-center gap-2 text-sm text-stone-700">
                <Checkbox checked={Boolean(roxy.create_use_proxy)} onCheckedChange={(checked) => setNestedField("roxy", "create_use_proxy", Boolean(checked))} disabled={config.enabled} />
                创建环境写入注册代理
              </label>
              <div className="space-y-2">
                <label className="text-sm text-stone-700">页面超时（秒）</label>
                <Input value={String(roxy.timeout || 90)} onChange={(event) => setNestedField("roxy", "timeout", Number(event.target.value) || 90)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
              </div>
            </div>
          ) : null}

          {currentDriver === "cloak" ? (
            <div className="grid gap-3 rounded-xl border border-stone-200 bg-white p-3 md:grid-cols-2">
              <label className="flex items-center gap-2 text-sm text-stone-700">
                <Checkbox checked={cloak.headless !== false} onCheckedChange={(checked) => setNestedField("cloak", "headless", Boolean(checked))} disabled={config.enabled} />
                无头启动
              </label>
              <label className="flex items-center gap-2 text-sm text-stone-700">
                <Checkbox checked={cloak.humanize !== false} onCheckedChange={(checked) => setNestedField("cloak", "humanize", Boolean(checked))} disabled={config.enabled} />
                Humanize
              </label>
              <label className="flex items-center gap-2 text-sm text-stone-700">
                <Checkbox checked={cloak.geoip !== false} onCheckedChange={(checked) => setNestedField("cloak", "geoip", Boolean(checked))} disabled={config.enabled} />
                按出口匹配地理信息
              </label>
              <label className="flex items-center gap-2 text-sm text-stone-700">
                <Checkbox checked={cloak.use_proxy !== false} onCheckedChange={(checked) => setNestedField("cloak", "use_proxy", Boolean(checked))} disabled={config.enabled} />
                使用注册代理
              </label>
              <label className="flex items-center gap-2 text-sm text-stone-700">
                <Checkbox checked={Boolean(cloak.keep_browser_open)} onCheckedChange={(checked) => setNestedField("cloak", "keep_browser_open", Boolean(checked))} disabled={config.enabled} />
                保留浏览器
              </label>
              <div className="space-y-2">
                <label className="text-sm text-stone-700">Locale</label>
                <Input value={String(cloak.locale || "")} onChange={(event) => setNestedField("cloak", "locale", event.target.value)} placeholder="ja-JP / en-US" className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
              </div>
              <div className="space-y-2">
                <label className="text-sm text-stone-700">Timezone</label>
                <Input value={String(cloak.timezone || "")} onChange={(event) => setNestedField("cloak", "timezone", event.target.value)} placeholder="Asia/Tokyo" className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
              </div>
              <div className="space-y-2">
                <label className="text-sm text-stone-700">Accept-Language</label>
                <Input value={String(cloak.accept_language || "")} onChange={(event) => setNestedField("cloak", "accept_language", event.target.value)} placeholder="ja-JP,ja;q=0.9,en-US;q=0.8" className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
              </div>
              <div className="space-y-2">
                <label className="text-sm text-stone-700">License Key</label>
                <Input value={String(cloak.license_key || "")} onChange={(event) => setNestedField("cloak", "license_key", event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
              </div>
              <div className="space-y-2">
                <label className="text-sm text-stone-700">Fingerprint Seed</label>
                <Input value={String(cloak.fingerprint_seed || "")} onChange={(event) => setNestedField("cloak", "fingerprint_seed", event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
              </div>
              <div className="space-y-2">
                <label className="text-sm text-stone-700">User Data Dir</label>
                <Input value={String(cloak.user_data_dir || "")} onChange={(event) => setNestedField("cloak", "user_data_dir", event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
              </div>
              <div className="space-y-2">
                <label className="text-sm text-stone-700">Start URL</label>
                <Input value={String(cloak.start_url || "https://chatgpt.com/auth/login")} onChange={(event) => setNestedField("cloak", "start_url", event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
              </div>
              <div className="space-y-2">
                <label className="text-sm text-stone-700">页面超时（秒）</label>
                <Input value={String(cloak.timeout || 90)} onChange={(event) => setNestedField("cloak", "timeout", Number(event.target.value) || 90)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
              </div>
            </div>
          ) : null}

          <div className="grid gap-3 rounded-xl border border-stone-200 bg-white p-3 md:grid-cols-3">
            <label className="flex items-start gap-3 text-sm text-stone-700 md:col-span-3">
              <Checkbox checked={Boolean(sms.enabled)} onCheckedChange={(checked) => setNestedField("sms", "enabled", Boolean(checked))} disabled={config.enabled} />
              <span>
                <span className="block font-medium text-stone-800">Codex OAuth 手机验证使用 SMS</span>
                <span className="mt-1 block text-xs leading-5 text-stone-500">仅在浏览器授权流程遇到手机号验证页时取号、填入短信码；未开启时不会调用短信平台。</span>
              </span>
            </label>
            <div className="space-y-2">
              <label className="text-sm text-stone-700">SMS Provider</label>
              <Select value={String(sms.provider || "grizzly")} onValueChange={(value) => setNestedField("sms", "provider", value)} disabled={config.enabled || !sms.enabled}>
                <SelectTrigger className="h-10 rounded-xl border-stone-200 bg-white">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="grizzly">GrizzlySMS</SelectItem>
                  <SelectItem value="l">L API</SelectItem>
                  <SelectItem value="h">H API</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <label className="text-sm text-stone-700">SMS API Key / Token</label>
              <Input value={String(sms.api_key || sms.l_admin_auth_code || sms.h_admin_auth_code || "")} onChange={(event) => {
                setNestedField("sms", "api_key", event.target.value);
                setNestedField("sms", "l_admin_auth_code", event.target.value);
                setNestedField("sms", "h_admin_auth_code", event.target.value);
              }} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled || !sms.enabled} />
            </div>
            <div className="space-y-2">
              <label className="text-sm text-stone-700">Country / Service</label>
              <Input value={`${String(sms.country || "187")} / ${String(sms.service || "ot")}`} onChange={(event) => {
                const [country = "", service = ""] = event.target.value.split("/");
                setNestedField("sms", "country", country.trim());
                setNestedField("sms", "service", service.trim());
              }} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled || !sms.enabled} />
            </div>
          </div>

          <div className="space-y-3 border-t border-stone-200 pt-3">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold text-stone-800">邮箱配置</h3>
                <p className="mt-1 text-xs text-stone-500">可配置多个 provider，按启用顺序轮换。</p>
              </div>
              <Button type="button" variant="outline" className="h-9 rounded-xl border-stone-200 bg-white px-3 text-stone-700" onClick={addProvider} disabled={config.enabled}>
                <Plus className="size-4" />
                添加
              </Button>
            </div>

            <div className="grid gap-4 md:grid-cols-3">
              <div className="space-y-2">
                <label className="text-sm text-stone-700">请求超时</label>
                <Input value={String(config.mail.request_timeout || "")} onChange={(event) => setMailField("request_timeout", event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
              </div>
              <div className="space-y-2">
                <label className="text-sm text-stone-700">等待验证码超时</label>
                <Input value={String(config.mail.wait_timeout || "")} onChange={(event) => setMailField("wait_timeout", event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
              </div>
              <div className="space-y-2">
                <label className="text-sm text-stone-700">轮询间隔</label>
                <Input value={String(config.mail.wait_interval || "")} onChange={(event) => setMailField("wait_interval", event.target.value)} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
              </div>
            </div>

            <label className="flex items-start gap-3 rounded-xl border border-stone-200 bg-white px-3 py-2 text-sm text-stone-700">
              <Checkbox checked={config.mail.api_use_register_proxy !== false} onCheckedChange={(checked) => setMailApiUseRegisterProxy(Boolean(checked))} disabled={config.enabled} />
              <span className="space-y-1">
                <span className="block font-medium text-stone-800">邮箱服务后台 API 使用注册代理</span>
                <span className="block text-xs leading-5 text-stone-500">关闭后邮箱平台 API 直连，注册 OpenAI/Auth0 请求仍使用注册代理。</span>
              </span>
            </label>

            <div className="space-y-3">
              {providers.map((provider, index) => {
                const type = String(provider.type || "tempmail_lol");
                const domains = Array.isArray(provider.domain) ? provider.domain.map(String).join("\n") : "";
                const subdomains = Array.isArray(provider.subdomain) ? provider.subdomain.map(String).join("\n") : "";
                return (
                  <div key={index} className="space-y-3 border-t border-stone-200 pt-3 first:border-t-0 first:pt-0">
                    <div className="flex items-center justify-between gap-3">
                      <label className="flex items-center gap-3 text-sm text-stone-700">
                        <Checkbox checked={Boolean(provider.enable)} onCheckedChange={(checked) => updateProvider(index, { enable: Boolean(checked) })} disabled={config.enabled} />
                        启用
                      </label>
                      <button type="button" className="rounded-lg p-2 text-stone-400 transition hover:bg-rose-50 hover:text-rose-500 disabled:opacity-50" onClick={() => deleteProvider(index)} disabled={config.enabled || providers.length <= 1} title="删除 provider">
                        <Trash2 className="size-4" />
                      </button>
                    </div>

                    <div className="grid gap-4 md:grid-cols-2">
                      <div className="space-y-2">
                        <label className="text-sm text-stone-700">类型</label>
                        <Select value={type} onValueChange={(value) => updateProviderType(index, value)} disabled={config.enabled}>
                          <SelectTrigger className="h-10 rounded-xl border-stone-200 bg-white">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="cloudmail_gen">cloudmail_gen</SelectItem>
                            <SelectItem value="cloudflare_temp_email">cloudflare_temp_email</SelectItem>
                            <SelectItem value="tempmail_lol">tempmail_lol</SelectItem>
                            <SelectItem value="moemail">moemail</SelectItem>
                            <SelectItem value="inbucket">inbucket_mail</SelectItem>
                            <SelectItem value="duckmail">duckmail</SelectItem>
                            <SelectItem value="gptmail">gptmail(未测试)</SelectItem>
                            <SelectItem value="generic_api">generic_api</SelectItem>
                            <SelectItem value="mailnest">mailnest</SelectItem>
                            <SelectItem value="yyds_mail">yyds_mail</SelectItem>
                            <SelectItem value="ddg_mail">ddg_mail (DDG邮箱+CF中转)</SelectItem>
                            <SelectItem value="outlook_token">outlook_token (Outlook/Hotmail 邮箱池)</SelectItem>
                            <SelectItem value="qqmail_imap">qqmail_imap (CF域名转发到QQ邮箱)</SelectItem>
                            <SelectItem value="manual">manual (手动验证码)</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>
                      {type === "cloudmail_gen" || type === "cloudflare_temp_email" || type === "moemail" || type === "inbucket" || type === "yyds_mail" || type === "ddg_mail" ? (
                        <>
                          <div className="space-y-2">
                            <label className="text-sm text-stone-700">{type === "cloudmail_gen" ? "CloudMail URL" : "API Base"}</label>
                            <Input value={String(provider.api_base || "")} onChange={(event) => updateProvider(index, { api_base: event.target.value })} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
                          </div>
                          {type === "cloudmail_gen" ? (
                            <>
                              <div className="space-y-2">
                                <label className="text-sm text-stone-700">管理员邮箱</label>
                                <Input value={String(provider.admin_email || "")} onChange={(event) => updateProvider(index, { admin_email: event.target.value })} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
                              </div>
                              <div className="space-y-2">
                                <label className="text-sm text-stone-700">管理员密码</label>
                                <Input value={String(provider.admin_password || "")} onChange={(event) => updateProvider(index, { admin_password: event.target.value })} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
                              </div>
                            </>
                          ) : null}
                          {type === "cloudflare_temp_email" || type === "ddg_mail" ? (
                            <div className="space-y-2">
                              <label className="text-sm text-stone-700">Admin Password</label>
                              <Input value={String(provider.admin_password || "")} onChange={(event) => updateProvider(index, { admin_password: event.target.value })} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
                            </div>
                          ) : null}
                        </>
                      ) : null}
                      {type === "ddg_mail" ? (
                        <>
                        <div className="space-y-2">
                          <label className="text-sm text-stone-700">DDG Token <span className="text-red-400">*</span></label>
                          <Input value={String(provider.ddg_token || "")} onChange={(event) => updateProvider(index, { ddg_token: event.target.value })} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} placeholder="DuckDuckGo Email Protection 的 Bearer Token" />
                        </div>
                        <div className="space-y-2">
                          <label className="text-sm text-stone-700">CF Inbox JWT <span className="text-red-400">*</span></label>
                          <Input value={String(provider.cf_inbox_jwt || "")} onChange={(event) => updateProvider(index, { cf_inbox_jwt: event.target.value })} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} placeholder="CF 临时邮箱后端的固定收件箱 JWT（DDG 转发目标）" />
                        </div>
                        <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
                          <p className="font-medium mb-1">使用说明</p>
                          <ol className="list-decimal list-inside space-y-0.5">
                            <li>先在 <a href="https://duckduckgo.com/email/" target="_blank" className="underline">DuckDuckGo Email Protection</a> 登录并设置转发目标为 CF 收件箱地址</li>
                            <li>DDG Token 从浏览器 DevTools → Network → quack.duckduckgo.com 请求中获取 <code className="bg-amber-100 px-1 rounded">Authorization: Bearer</code></li>
                            <li>CF Inbox JWT 从 CF 临时邮箱后端创建固定收件箱后获取</li>
                            <li>所有 @duck.com 别名收到的邮件会转发到同一个 CF 收件箱，系统按 To: 头自动匹配</li>
                          </ol>
                        </div>
                        </>
                      ) : null}
                      {type === "inbucket" ? (
                        <label className="flex items-center gap-3 pt-8 text-sm text-stone-700">
                          <Checkbox checked={Boolean(provider.random_subdomain ?? true)} onCheckedChange={(checked) => updateProvider(index, { random_subdomain: Boolean(checked) })} disabled={config.enabled} />
                          启用随机子域名
                        </label>
                      ) : null}
                      {type === "tempmail_lol" || type === "moemail" || type === "duckmail" || type === "gptmail" || type === "mailnest" || type === "yyds_mail" ? (
                        <div className="space-y-2">
                          <label className="text-sm text-stone-700">API Key</label>
                          <Input value={String(provider.api_key || "")} onChange={(event) => updateProvider(index, { api_key: event.target.value })} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
                        </div>
                      ) : null}
                      {type === "mailnest" ? (
                        <>
                          <div className="space-y-2">
                            <label className="text-sm text-stone-700">API Base</label>
                            <Input value={String(provider.api_base || "https://mailnest.top")} onChange={(event) => updateProvider(index, { api_base: event.target.value })} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
                          </div>
                          <div className="space-y-2">
                            <label className="text-sm text-stone-700">Project Code</label>
                            <Input value={String(provider.project_code || "chatgpt001")} onChange={(event) => updateProvider(index, { project_code: event.target.value })} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
                          </div>
                        </>
                      ) : null}
                      {type === "duckmail" || type === "gptmail" ? (
                        <div className="space-y-2">
                          <label className="text-sm text-stone-700">Default Domain</label>
                          <Input value={String(provider.default_domain || "")} onChange={(event) => updateProvider(index, { default_domain: event.target.value })} placeholder={type === "duckmail" ? "duckmail.sbs" : ""} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
                        </div>
                      ) : null}
                      {type === "yyds_mail" ? (
                        <>
                          <div className="space-y-2">
                            <label className="text-sm text-stone-700">Subdomain</label>
                            <Input value={String(provider.subdomain || "")} onChange={(event) => updateProvider(index, { subdomain: event.target.value })} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
                          </div>
                          <label className="flex items-center gap-3 pt-8 text-sm text-stone-700">
                            <Checkbox checked={Boolean(provider.wildcard)} onCheckedChange={(checked) => updateProvider(index, { wildcard: Boolean(checked) })} disabled={config.enabled} />
                            Wildcard
                          </label>
                        </>
                      ) : null}
                      {type === "outlook_token" ? (
                        <>
                          <div className="space-y-2">
                            <label className="text-sm text-stone-700">读取方式</label>
                            <Select value={String(provider.mode || "graph")} onValueChange={(value) => updateProvider(index, { mode: value })} disabled={config.enabled}>
                              <SelectTrigger className="h-10 rounded-xl border-stone-200 bg-white">
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                <SelectItem value="graph">Graph API</SelectItem>
                                <SelectItem value="imap">IMAP (XOAUTH2)</SelectItem>
                                <SelectItem value="auto">自动 (Graph→IMAP)</SelectItem>
                              </SelectContent>
                            </Select>
                          </div>
                          {String(provider.mode || "graph") !== "graph" ? (
                            <div className="space-y-2">
                              <label className="text-sm text-stone-700">IMAP Host</label>
                              <Input value={String(provider.imap_host || "outlook.office365.com")} onChange={(event) => updateProvider(index, { imap_host: event.target.value })} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
                            </div>
                          ) : null}
                        </>
                      ) : null}
                      {type === "qqmail_imap" ? (
                        <>
                          <div className="space-y-2">
                            <label className="text-sm text-stone-700">QQ 收件邮箱 <span className="text-red-400">*</span></label>
                            <Input value={String(provider.qq_email || "")} onChange={(event) => updateProvider(index, { qq_email: event.target.value })} placeholder="123456@qq.com" className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
                          </div>
                          <div className="space-y-2">
                            <label className="text-sm text-stone-700">IMAP 授权码 <span className="text-red-400">*</span></label>
                            <Input type="password" value={String(provider.imap_password || "")} onChange={(event) => updateProvider(index, { imap_password: event.target.value })} placeholder={provider.has_imap_password ? "已保存，留空表示保留" : "QQ邮箱 IMAP/SMTP 授权码"} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
                          </div>
                          <div className="space-y-2">
                            <label className="text-sm text-stone-700">IMAP Host</label>
                            <Input value={String(provider.imap_host || "imap.qq.com")} onChange={(event) => updateProvider(index, { imap_host: event.target.value })} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
                          </div>
                          <div className="space-y-2">
                            <label className="text-sm text-stone-700">端口 / 最近邮件数</label>
                            <div className="grid grid-cols-2 gap-2">
                              <Input value={String(provider.imap_port || "993")} onChange={(event) => updateProvider(index, { imap_port: event.target.value })} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
                              <Input value={String(provider.message_limit || "15")} onChange={(event) => updateProvider(index, { message_limit: event.target.value })} className="h-10 rounded-xl border-stone-200 bg-white" disabled={config.enabled} />
                            </div>
                          </div>
                          <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-800 md:col-span-2">
                            适用于 Cloudflare Email Routing 把随机域名邮箱转发到 QQ 邮箱的场景；授权码只写保存，不会在配置读取或 SSE 中回显。
                          </div>
                        </>
                      ) : null}
                    </div>

                    {type === "outlook_token" ? (() => {
                      const stats = (provider.mailboxes_stats || {}) as Record<string, number>;
                      const savedCount = Number(provider.mailboxes_count || 0);
                      const preview = Array.isArray(provider.mailboxes_preview) ? (provider.mailboxes_preview as string[]) : [];
                      const pendingCount = String(provider.mailboxes || "").split(/\r?\n/).filter((line) => line.includes("----") && line.split("----").length >= 4).length;
                      return (
                        <div className="space-y-2">
                          <label className="flex items-center justify-between text-sm text-stone-700">
                            <span>邮箱池导入 <span className="text-red-400">*</span></span>
                            <span className="text-xs text-stone-400">已保存 {savedCount} 个{pendingCount ? ` · 待导入 ${pendingCount} 个` : ""}</span>
                          </label>
                          <Textarea value={String(provider.mailboxes || "")} onChange={(event) => updateProvider(index, { mailboxes: event.target.value })} placeholder={"每行一个邮箱，格式：\n邮箱----密码----client_id----refresh_token\n（出于安全，已保存的密码/refresh_token 不会回显；此处仅用于新增或覆盖）"} className="min-h-32 rounded-xl border-stone-200 bg-white font-mono text-xs" disabled={config.enabled} />
                          <div className="flex flex-wrap items-center gap-1.5 text-xs">
                            <span className="rounded-md bg-stone-100 px-2 py-1 text-stone-600">未使用 {stats.unused ?? 0}</span>
                            <span className="rounded-md bg-blue-50 px-2 py-1 text-blue-600">占用中 {stats.in_use ?? 0}</span>
                            <span className="rounded-md bg-emerald-50 px-2 py-1 text-emerald-700">已用 {stats.used ?? 0}</span>
                            <span className="rounded-md bg-amber-50 px-2 py-1 text-amber-700">token失效 {stats.token_invalid ?? 0}</span>
                            <span className="rounded-md bg-rose-50 px-2 py-1 text-rose-600">失败 {stats.failed ?? 0}</span>
                          </div>
                          {preview.length ? (
                            <p className="text-xs text-stone-400">已保存邮箱（脱敏）：{preview.slice(0, 8).join("、")}{preview.length > 8 ? ` 等 ${preview.length} 个` : ""}</p>
                          ) : null}
                          <div className="flex flex-wrap items-center gap-2">
                            <Button type="button" variant="outline" className="h-8 rounded-lg border-stone-200 bg-white px-3 text-xs text-stone-700" onClick={() => void resetOutlookPool("failed")} disabled={config.enabled}>
                              清除失败/占用状态
                            </Button>
                            <Button type="button" variant="outline" className="h-8 rounded-lg border-amber-200 bg-white px-3 text-xs text-amber-700 hover:bg-amber-50" onClick={() => { if (window.confirm("确定要从 Outlook 邮箱池中删除所有未使用邮箱吗？此操作会移除这些已保存凭据。")) void resetOutlookPool("unused"); }} disabled={config.enabled}>
                              清空未使用
                            </Button>
                            <Button type="button" variant="outline" className="h-8 rounded-lg border-rose-200 bg-white px-3 text-xs text-rose-600 hover:bg-rose-50" onClick={() => { if (window.confirm("确定要重置整个 Outlook 邮箱池状态吗？所有邮箱会被标记为可重新使用。")) void resetOutlookPool("all"); }} disabled={config.enabled}>
                              重置全部状态
                            </Button>
                          </div>
                          <p className="text-xs text-stone-500">每个邮箱仅成功注册一次（状态记录在 data/outlook_token_used.json）。失败的邮箱会被标记原因，可用上方按钮释放后重试。</p>
                        </div>
                      );
                    })() : null}

                    {type === "generic_api" || type === "manual" ? (
                      <div className="space-y-2">
                        <label className="text-sm text-stone-700">{type === "manual" ? "手动邮箱池" : "邮箱池导入"}</label>
                        <Textarea value={String(provider.mailboxes || "")} onChange={(event) => updateProvider(index, { mailboxes: event.target.value })} placeholder={type === "manual" ? "每行一个邮箱，注册流程会等待你在右侧提交验证码" : "每行一个邮箱，格式：\nemail@example.com----https://example.com/code"} className="min-h-32 rounded-xl border-stone-200 bg-white font-mono text-xs" disabled={config.enabled} />
                      </div>
                    ) : null}

                    {type === "cloudmail_gen" || type === "tempmail_lol" || type === "cloudflare_temp_email" || type === "moemail" || type === "inbucket" || type === "yyds_mail" || type === "ddg_mail" || type === "qqmail_imap" ? (
                      <div className="space-y-2">
                        <label className="text-sm text-stone-700">{type === "cloudmail_gen" ? "邮箱域名" : type === "inbucket" ? "基础域名列表" : "Domain"}</label>
                        <Textarea value={domains} onChange={(event) => updateProvider(index, { domain: event.target.value.split(/[\n,]/).map((item) => item.trim()) })} placeholder={type === "cloudmail_gen" ? "每行一个域名（CloudMailGen 必填）" : type === "inbucket" ? "每行一个基础域名，系统会自动生成随机子域名" : type === "qqmail_imap" ? "每行一个已配置 Cloudflare 转发到 QQ 邮箱的域名" : type === "moemail" ? "每行一个域名" : "每行一个域名，留空则使用服务默认域名"} className="min-h-20 rounded-xl border-stone-200 bg-white font-mono text-xs" disabled={config.enabled} />
                      </div>
                    ) : null}
                    {type === "cloudmail_gen" ? (
                      <div className="space-y-2">
                        <label className="text-sm text-stone-700">子域名（支持多个）</label>
                        <Textarea value={subdomains} onChange={(event) => updateProvider(index, { subdomain: event.target.value.split(/[\n,]/).map((item) => item.trim()) })} placeholder="每行一个子域名前缀，留空则直接使用主域名" className="min-h-20 rounded-xl border-stone-200 bg-white font-mono text-xs" disabled={config.enabled} />
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </div>

      </section>

      <section className="flex min-h-0 flex-col p-4">
        <div className="space-y-3">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 className="text-lg font-semibold tracking-tight">运行结果</h2>
                <p className="mt-1 text-sm text-stone-500">SSE 实时推送当前状态。</p>
              </div>
              <Badge variant={config.enabled ? "success" : "secondary"} className="rounded-md">
                {config.enabled ? "运行中" : "已停止"}
              </Badge>
            </div>
            <div className="grid grid-cols-4 gap-2">
              {[
                ["成功 / 成功率", `${stats.success} / ${stats.success_rate || 0}%`],
                ["失败", stats.fail],
                ["完成", stats.done],
                ["运行 / 线程", `${stats.running} / ${stats.threads}`],
                ["运行时间", `${stats.elapsed_seconds || 0}s`],
                ["平均注册单个", `${stats.avg_seconds || 0}s`],
                ["当前额度", stats.current_quota || 0],
                ["正常账号", stats.current_available || 0],
              ].map(([label, value]) => (
                <div key={label} className="border border-stone-200 bg-white/70 px-3 py-2">
                  <div className="text-xs text-stone-400">{label}</div>
                  <div className="mt-1 text-base font-semibold text-stone-800">{value}</div>
                </div>
              ))}
            </div>
            <div className="grid grid-cols-3 gap-2">
              <Button className="h-10 rounded-xl bg-stone-950 px-3 text-white hover:bg-stone-800" onClick={() => void toggle()} disabled={isSaving}>
                {isSaving ? <LoaderCircle className="size-4 animate-spin" /> : config.enabled ? <Square className="size-4" /> : <Play className="size-4" />}
                {config.enabled ? "停止" : "启动"}
              </Button>
              <Button variant="outline" className="h-10 rounded-xl border-stone-200 bg-white px-3 text-stone-700" onClick={() => void reset()} disabled={isSaving || config.enabled}>
                <RotateCcw className="size-4" />
                重置
              </Button>
              <Button variant="outline" className="h-10 rounded-xl border-stone-200 bg-white px-3 text-stone-700" onClick={() => void save()} disabled={isSaving || config.enabled}>
                <Save className="size-4" />
                保存
              </Button>
            </div>
            <div className="flex items-center gap-2 border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              <AlertTriangle className="size-4 shrink-0" />
              启动之前注意先保存配置。
            </div>
            <div className="space-y-3 border border-stone-200 bg-white/70 px-3 py-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h3 className="text-sm font-semibold text-stone-900">手动 OTP</h3>
                  <p className="mt-1 text-xs text-stone-500">manual 邮箱 provider 等待时，在这里提交验证码。</p>
                </div>
                <Button variant="outline" className="h-8 rounded-lg border-stone-200 bg-white px-3 text-xs text-stone-700" onClick={() => void refreshManualOTPWaiting()} disabled={manualOTPBusy}>
                  {manualOTPBusy ? <LoaderCircle className="size-3.5 animate-spin" /> : <RotateCcw className="size-3.5" />}
                  刷新
                </Button>
              </div>
              {manualOTPWaiting.length ? (
                <div className="space-y-2">
                  {manualOTPWaiting.map((item) => (
                    <div key={item.email} className="grid gap-2 md:grid-cols-[1fr_112px_auto]">
                      <Input value={item.email} readOnly className="h-9 rounded-lg border-stone-200 bg-stone-50 text-xs" />
                      <Input value={manualOTPCodes[item.email] || ""} onChange={(event) => setManualOTPCodes((state) => ({ ...state, [item.email]: event.target.value }))} className="h-9 rounded-lg border-stone-200 bg-white text-xs" placeholder="验证码" />
                      <Button variant="outline" className="h-9 rounded-lg border-stone-200 bg-white px-3 text-xs text-stone-700" onClick={() => void submitManualOTPCode(item.email)} disabled={manualOTPBusy}>
                        <Send className="size-3.5" />
                        提交
                      </Button>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="rounded-lg bg-stone-50 px-3 py-2 text-xs text-stone-500">暂无等待手动输入验证码的邮箱。</div>
              )}
            </div>
        </div>

        <div className="mt-4 flex min-h-0 flex-1 flex-col space-y-3 overflow-hidden border-t border-stone-200 pt-4">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-sm font-semibold text-stone-900">实时日志</h3>
                <p className="mt-1 text-xs text-stone-500">展示注册流程进度；详细失败响应仍保存在后端诊断文件中。</p>
              </div>
              <Badge variant="secondary" className="rounded-md">
                {logs.length}
              </Badge>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto border border-stone-200 bg-white/70 p-3 font-mono text-xs leading-6">
              {logs.length === 0 ? (
                <div className="text-stone-500">暂无日志</div>
              ) : (
                logs.slice().reverse().map((item, index) => (
                  <div key={`${item.time}-${index}`} className={item.level === "red" ? "text-rose-600" : item.level === "green" ? "text-emerald-700" : item.level === "yellow" ? "text-amber-700" : "text-stone-700"}>
                    <span className="text-stone-400">{new Date(item.time).toLocaleTimeString()}</span>
                    <span className="pl-2">{item.text}</span>
                  </div>
                ))
              )}
            </div>
        </div>
      </section>
    </div>
  );
}
