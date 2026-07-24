"use client";

import { useEffect, useMemo, useState } from "react";
import { Download, FileText, RefreshCcw, RotateCcw, Search, Square, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  deleteCodexCredential,
  downloadCodexBulk,
  downloadCodexCredential,
  fetchCodexCredentials,
  fetchCodexRetryLog,
  resetCodexExport,
  resetCodexRetrying,
  retryCodexBulk,
  retryCodexByEmail,
  stopCodexRetry,
  type CodexCredentialItem,
  type CodexCredentialSummary,
} from "@/lib/api";

type BusyState = Record<string, boolean>;

const emptySummary: CodexCredentialSummary = {
  total: 0,
  exported: 0,
  unexported: 0,
  retrying: 0,
};

function formatTime(value?: string) {
  if (!value) {
    return "-";
  }
  const time = new Date(value);
  if (Number.isNaN(time.getTime())) {
    return value;
  }
  return time.toLocaleString();
}

function statusBadgeVariant(status?: string): "success" | "warning" | "danger" | "info" | "outline" {
  const value = String(status || "").toLowerCase();
  if (value === "success") {
    return "success";
  }
  if (value === "retrying" || value === "pending_callback") {
    return "warning";
  }
  if (value === "failed" || value === "stopped") {
    return "danger";
  }
  if (value === "skipped") {
    return "info";
  }
  return "outline";
}

function rowKey(item: CodexCredentialItem) {
  return item.filename || item.email || "";
}

export default function CodexPage() {
  const [items, setItems] = useState<CodexCredentialItem[]>([]);
  const [summary, setSummary] = useState<CodexCredentialSummary>(emptySummary);
  const [selected, setSelected] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [cpaPoolId, setCpaPoolId] = useState("");
  const [workers, setWorkers] = useState(1);
  const [isLoading, setIsLoading] = useState(true);
  const [busy, setBusy] = useState<BusyState>({});
  const [logEmail, setLogEmail] = useState("");
  const [logText, setLogText] = useState("");
  const [logRunning, setLogRunning] = useState(false);

  const load = async (silent = false) => {
    if (!silent) {
      setIsLoading(true);
    }
    try {
      const data = await fetchCodexCredentials();
      setItems(data.accounts || []);
      setSummary(data.summary || emptySummary);
      setSelected((prev) => prev.filter((name) => data.accounts.some((item) => item.filename === name)));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载 Codex 凭证失败");
    } finally {
      if (!silent) {
        setIsLoading(false);
      }
    }
  };

  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    if (!logEmail) {
      return;
    }
    let active = true;

    const loadLog = async () => {
      try {
        const data = await fetchCodexRetryLog(logEmail);
        if (!active) {
          return;
        }
        setLogText(data.log || "");
        setLogRunning(Boolean(data.running));
      } catch (error) {
        if (active) {
          toast.error(error instanceof Error ? error.message : "加载 Codex 日志失败");
        }
      }
    };

    void loadLog();
    const timer = window.setInterval(loadLog, 2500);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [logEmail]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) {
      return items;
    }
    return items.filter((item) =>
      [item.email, item.filename, item.codex_status, item.source, item.cpa_filename]
        .map((value) => String(value || "").toLowerCase())
        .some((value) => value.includes(needle)),
    );
  }, [items, query]);

  const selectedItems = useMemo(
    () => items.filter((item) => selected.includes(item.filename)),
    [items, selected],
  );

  const setRowBusy = (key: string, value: boolean) => {
    setBusy((prev) => ({ ...prev, [key]: value }));
  };

  const toggleSelected = (filename: string) => {
    setSelected((prev) => (prev.includes(filename) ? prev.filter((item) => item !== filename) : [...prev, filename]));
  };

  const toggleAllVisible = () => {
    const visible = filtered.map((item) => item.filename).filter(Boolean);
    if (visible.length > 0 && visible.every((name) => selected.includes(name))) {
      setSelected((prev) => prev.filter((name) => !visible.includes(name)));
      return;
    }
    setSelected((prev) => Array.from(new Set([...prev, ...visible])));
  };

  const handleDownload = async (item: CodexCredentialItem, fromCpa = false) => {
    const key = `${item.filename}:${fromCpa ? "cpa" : "local"}`;
    setRowBusy(key, true);
    try {
      await downloadCodexCredential(item.filename, fromCpa);
      toast.success(fromCpa ? "已从 CPA 下载 Codex 凭证" : "已下载本地 Codex 凭证");
      void load(true);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "下载失败");
    } finally {
      setRowBusy(key, false);
    }
  };

  const handleBulkDownload = async (fromCpa = false) => {
    if (selected.length === 0) {
      toast.error("请先选择 Codex 凭证");
      return;
    }
    const key = fromCpa ? "bulk-cpa" : "bulk-local";
    setRowBusy(key, true);
    try {
      await downloadCodexBulk(selected, fromCpa);
      toast.success(fromCpa ? "已批量从 CPA 下载" : "已批量下载本地凭证");
      void load(true);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "批量下载失败");
    } finally {
      setRowBusy(key, false);
    }
  };

  const handleRetry = async (item: CodexCredentialItem) => {
    if (!item.email) {
      toast.error("该凭证没有邮箱，无法按邮箱补跑");
      return;
    }
    const key = `${item.filename}:retry`;
    setRowBusy(key, true);
    try {
      const data = await retryCodexByEmail(item.email, cpaPoolId);
      toast.success(data.message || "已启动 Codex 补跑");
      setLogEmail(item.email);
      void load(true);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "启动补跑失败");
    } finally {
      setRowBusy(key, false);
    }
  };

  const handleBulkRetry = async () => {
    const emails = Array.from(new Set(selectedItems.map((item) => item.email).filter(Boolean) as string[]));
    if (emails.length === 0) {
      toast.error("所选凭证没有可补跑邮箱");
      return;
    }
    setRowBusy("bulk-retry", true);
    try {
      const data = await retryCodexBulk(emails, workers, cpaPoolId);
      toast.success(data.message || `已启动 ${data.started_count || emails.length} 个 Codex 补跑`);
      void load(true);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "批量补跑失败");
    } finally {
      setRowBusy("bulk-retry", false);
    }
  };

  const handleStop = async (item: CodexCredentialItem) => {
    if (!item.email) {
      toast.error("该凭证没有邮箱，无法停止");
      return;
    }
    const key = `${item.filename}:stop`;
    setRowBusy(key, true);
    try {
      const data = await stopCodexRetry(item.email);
      toast.success(data.message || "已发送停止信号");
      void load(true);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "停止失败");
    } finally {
      setRowBusy(key, false);
    }
  };

  const handleResetRetrying = async (item: CodexCredentialItem) => {
    if (!item.email) {
      toast.error("该凭证没有邮箱，无法 reset");
      return;
    }
    const key = `${item.filename}:reset-retry`;
    setRowBusy(key, true);
    try {
      await resetCodexRetrying(item.email, "failed");
      toast.success("已重置补跑状态");
      void load(true);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "重置失败");
    } finally {
      setRowBusy(key, false);
    }
  };

  const handleResetExport = async (item: CodexCredentialItem) => {
    const key = `${item.filename}:reset-export`;
    setRowBusy(key, true);
    try {
      await resetCodexExport(item.filename);
      toast.success("已重置导出标记");
      void load(true);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "重置导出标记失败");
    } finally {
      setRowBusy(key, false);
    }
  };

  const handleDelete = async (item: CodexCredentialItem) => {
    if (!window.confirm(`删除 ${item.filename}？`)) {
      return;
    }
    const key = `${item.filename}:delete`;
    setRowBusy(key, true);
    try {
      await deleteCodexCredential(item.filename);
      toast.success("已删除 Codex 凭证");
      setSelected((prev) => prev.filter((name) => name !== item.filename));
      void load(true);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "删除失败");
    } finally {
      setRowBusy(key, false);
    }
  };

  return (
    <section className="min-h-0 rounded-2xl border border-stone-200/70 bg-white/82 shadow-sm dark:border-white/10 dark:bg-stone-950/68">
      <div className="border-b border-stone-100 px-4 py-4 dark:border-white/10 sm:px-6">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
          <div className="space-y-3">
            <div>
              <h1 className="text-xl font-semibold tracking-normal text-stone-950 dark:text-stone-50">Codex 凭证管理</h1>
              <p className="mt-1 text-sm text-stone-500 dark:text-stone-400">按邮箱补跑、查看日志、停止、reset、下载和删除 Codex OAuth 凭证。</p>
            </div>
            <div className="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap">
              {[
                ["总数", summary.total],
                ["未导出", summary.unexported],
                ["已导出", summary.exported],
                ["补跑中", summary.retrying],
              ].map(([label, value]) => (
                <div key={label} className="rounded-lg border border-stone-200 bg-stone-50 px-3 py-2 dark:border-white/10 dark:bg-white/5">
                  <div className="text-xs text-stone-500 dark:text-stone-400">{label}</div>
                  <div className="mt-1 text-lg font-semibold text-stone-950 dark:text-stone-50">{value}</div>
                </div>
              ))}
            </div>
          </div>
          <div className="flex flex-col gap-2 lg:min-w-[520px]">
            <div className="flex flex-col gap-2 sm:flex-row">
              <div className="relative flex-1">
                <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-stone-400" />
                <Input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  className="h-10 rounded-lg border-stone-200 bg-white pl-9 dark:border-white/10 dark:bg-stone-900"
                  placeholder="搜索邮箱、文件名、状态"
                />
              </div>
              <Button variant="outline" className="h-10 rounded-lg" onClick={() => void load()} disabled={isLoading}>
                <RefreshCcw className="size-4" />
                刷新
              </Button>
            </div>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-[1fr_120px_auto]">
              <Input
                value={cpaPoolId}
                onChange={(event) => setCpaPoolId(event.target.value)}
                className="h-10 rounded-lg border-stone-200 bg-white dark:border-white/10 dark:bg-stone-900"
                placeholder="CPA pool id，可留空"
              />
              <Input
                type="number"
                min={1}
                max={16}
                value={workers}
                onChange={(event) => setWorkers(Math.max(1, Math.min(16, Number(event.target.value) || 1)))}
                className="h-10 rounded-lg border-stone-200 bg-white dark:border-white/10 dark:bg-stone-900"
              />
              <Button className="h-10 rounded-lg bg-stone-950 text-white hover:bg-stone-800" onClick={handleBulkRetry} disabled={selected.length === 0 || busy["bulk-retry"]}>
                <RotateCcw className="size-4" />
                批量补跑
              </Button>
            </div>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <Button variant="outline" size="sm" className="rounded-lg" onClick={() => void handleBulkDownload(false)} disabled={selected.length === 0 || busy["bulk-local"]}>
            <Download className="size-4" />
            下载所选
          </Button>
          <Button variant="outline" size="sm" className="rounded-lg" onClick={() => void handleBulkDownload(true)} disabled={selected.length === 0 || busy["bulk-cpa"]}>
            <Download className="size-4" />
            从 CPA 下载所选
          </Button>
          <span className="self-center text-xs text-stone-500 dark:text-stone-400">已选 {selected.length} 个</span>
        </div>
      </div>

      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-10">
                <input
                  type="checkbox"
                  className="size-4 rounded border-stone-300"
                  checked={filtered.length > 0 && filtered.every((item) => selected.includes(item.filename))}
                  onChange={toggleAllVisible}
                  aria-label="选择当前列表"
                />
              </TableHead>
              <TableHead>账号</TableHead>
              <TableHead>状态</TableHead>
              <TableHead>文件</TableHead>
              <TableHead>导出</TableHead>
              <TableHead>更新时间</TableHead>
              <TableHead className="min-w-[320px]">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              <TableRow>
                <TableCell colSpan={7} className="py-12 text-center text-sm text-stone-500">加载中...</TableCell>
              </TableRow>
            ) : filtered.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} className="py-12 text-center text-sm text-stone-500">暂无 Codex 凭证</TableCell>
              </TableRow>
            ) : (
              filtered.map((item) => {
                const key = rowKey(item);
                const exported = Number(item.export_count || 0) > 0;
                return (
                  <TableRow key={key}>
                    <TableCell>
                      <input
                        type="checkbox"
                        className="size-4 rounded border-stone-300"
                        checked={selected.includes(item.filename)}
                        onChange={() => toggleSelected(item.filename)}
                        aria-label={`选择 ${item.filename}`}
                      />
                    </TableCell>
                    <TableCell>
                      <div className="max-w-[220px] truncate font-medium text-stone-900 dark:text-stone-100" title={item.email || ""}>{item.email || "-"}</div>
                      <div className="mt-1 text-xs text-stone-500">{item.account_present ? "号池存在" : "号池缺失"}</div>
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-col gap-1">
                        <Badge variant={statusBadgeVariant(item.retrying ? "retrying" : item.codex_status)} className="w-fit">
                          {item.retrying ? "retrying" : item.codex_status || "unknown"}
                        </Badge>
                        {item.codex_error ? <div className="max-w-[240px] truncate text-xs text-rose-600" title={item.codex_error}>{item.codex_error}</div> : null}
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="max-w-[260px] truncate font-mono text-xs text-stone-700 dark:text-stone-300" title={item.filename}>{item.filename}</div>
                      <div className="mt-1 text-xs text-stone-500">{item.source || "local"} {item.cpa_filename ? `· ${item.cpa_filename}` : ""}</div>
                    </TableCell>
                    <TableCell>
                      <Badge variant={exported ? "success" : "outline"}>{exported ? `${item.export_count} 次` : "未导出"}</Badge>
                    </TableCell>
                    <TableCell className="text-xs text-stone-500">{formatTime(item.updated_at || item.created_at)}</TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1.5">
                        <Button size="sm" variant="outline" className="h-8 rounded-lg px-2" onClick={() => void handleDownload(item)} disabled={busy[`${item.filename}:local`]}>
                          <Download className="size-4" />
                          本地
                        </Button>
                        <Button size="sm" variant="outline" className="h-8 rounded-lg px-2" onClick={() => void handleDownload(item, true)} disabled={busy[`${item.filename}:cpa`]}>
                          <Download className="size-4" />
                          CPA
                        </Button>
                        <Button size="sm" variant="outline" className="h-8 rounded-lg px-2" onClick={() => item.email && setLogEmail(item.email)} disabled={!item.email}>
                          <FileText className="size-4" />
                          日志
                        </Button>
                        <Button size="sm" variant="outline" className="h-8 rounded-lg px-2" onClick={() => void handleRetry(item)} disabled={!item.email || busy[`${item.filename}:retry`]}>
                          <RotateCcw className="size-4" />
                          补跑
                        </Button>
                        <Button size="sm" variant="outline" className="h-8 rounded-lg px-2" onClick={() => void handleStop(item)} disabled={!item.email || busy[`${item.filename}:stop`]}>
                          <Square className="size-4" />
                          停止
                        </Button>
                        <Button size="sm" variant="outline" className="h-8 rounded-lg px-2" onClick={() => void handleResetRetrying(item)} disabled={!item.email || busy[`${item.filename}:reset-retry`]}>
                          <RefreshCcw className="size-4" />
                          reset
                        </Button>
                        <Button size="sm" variant="outline" className="h-8 rounded-lg px-2" onClick={() => void handleResetExport(item)} disabled={busy[`${item.filename}:reset-export`]}>
                          <RotateCcw className="size-4" />
                          导出标记
                        </Button>
                        <Button size="sm" variant="outline" className="h-8 rounded-lg px-2 text-rose-600 hover:text-rose-700" onClick={() => void handleDelete(item)} disabled={busy[`${item.filename}:delete`]}>
                          <Trash2 className="size-4" />
                          删除
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })
            )}
          </TableBody>
        </Table>
      </div>

      <Dialog open={Boolean(logEmail)} onOpenChange={(open) => !open && setLogEmail("")}>
        <DialogContent className="max-w-4xl rounded-xl p-0">
          <DialogHeader className="border-b border-stone-100 px-5 py-4 dark:border-white/10">
            <DialogTitle className="text-base">Codex 补跑日志</DialogTitle>
            <DialogDescription>{logEmail} {logRunning ? "· 运行中" : ""}</DialogDescription>
          </DialogHeader>
          <pre className="max-h-[70vh] overflow-auto bg-stone-950 px-4 py-3 font-mono text-xs leading-5 whitespace-pre-wrap text-stone-100">
            {logText || "暂无日志"}
          </pre>
        </DialogContent>
      </Dialog>
    </section>
  );
}
