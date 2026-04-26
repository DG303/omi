"use client";

import { useState } from "react";
import { Loader2, Send, ExternalLink, Copy, Check } from "lucide-react";
import { useAuthFetch } from "@/hooks/useAuthToken";

type DispatchState =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "dispatched"; runUrl?: string; prUrl?: string; runId?: number }
  | { kind: "error"; message: string };

export function AgentPromptWidget() {
  const { fetchWithAuth } = useAuthFetch();
  const [prompt, setPrompt] = useState("");
  const [state, setState] = useState<DispatchState>({ kind: "idle" });
  const [copied, setCopied] = useState(false);

  const submit = async () => {
    if (!prompt.trim()) return;
    setState({ kind: "submitting" });
    try {
      const res = await fetchWithAuth("/api/omi/agent/customize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: prompt.trim() }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setState({ kind: "error", message: data?.error || `Dispatch failed (${res.status})` });
        return;
      }
      setState({
        kind: "dispatched",
        runUrl: data.runUrl,
        prUrl: data.prUrl,
        runId: data.runId,
      });
    } catch (err: any) {
      setState({ kind: "error", message: err?.message || "Network error" });
    }
  };

  const reset = () => {
    setState({ kind: "idle" });
    setPrompt("");
  };

  const localCheckoutCmd =
    state.kind === "dispatched" && state.prUrl
      ? `gh pr checkout ${state.prUrl.split("/").pop()} && cd web/admin && npm run dev`
      : "";

  return (
    <div className="flex h-full flex-col gap-2">
      <p className="text-xs text-muted-foreground">
        Describe a change to this dashboard. An agent will edit the code, push a branch, and open
        a PR you can review and run locally.
      </p>

      {state.kind === "idle" || state.kind === "submitting" || state.kind === "error" ? (
        <>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder='e.g., "Add a chart of weekly desktop signups split by macOS version"'
            disabled={state.kind === "submitting"}
            className="min-h-0 flex-1 resize-none rounded-md border border-input bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-60"
          />
          <div className="flex items-center justify-between gap-2">
            {state.kind === "error" && (
              <p className="line-clamp-2 text-xs text-destructive">{state.message}</p>
            )}
            <button
              type="button"
              onClick={submit}
              disabled={!prompt.trim() || state.kind === "submitting"}
              className="ml-auto inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
            >
              {state.kind === "submitting" ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" /> Dispatching…
                </>
              ) : (
                <>
                  <Send className="h-3.5 w-3.5" /> Send to agent
                </>
              )}
            </button>
          </div>
        </>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-auto rounded-md border border-border bg-muted/30 p-3 text-sm">
          <p className="font-medium text-foreground">Dispatched ✓</p>
          <p className="text-xs text-muted-foreground">
            The agent is editing the dashboard. The PR will appear here when ready (poll the
            workflow for status).
          </p>
          {state.runUrl && (
            <a
              href={state.runUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
            >
              View workflow run <ExternalLink className="h-3 w-3" />
            </a>
          )}
          {state.prUrl && (
            <a
              href={state.prUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
            >
              Open PR <ExternalLink className="h-3 w-3" />
            </a>
          )}
          {localCheckoutCmd && (
            <div className="rounded bg-background p-2">
              <p className="mb-1 text-xs text-muted-foreground">Run locally:</p>
              <div className="flex items-center gap-2">
                <code className="flex-1 truncate text-xs">{localCheckoutCmd}</code>
                <button
                  type="button"
                  onClick={async () => {
                    await navigator.clipboard.writeText(localCheckoutCmd);
                    setCopied(true);
                    setTimeout(() => setCopied(false), 1500);
                  }}
                  className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                  aria-label="Copy command"
                >
                  {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                </button>
              </div>
            </div>
          )}
          <button
            type="button"
            onClick={reset}
            className="mt-auto self-start text-xs text-muted-foreground hover:text-foreground hover:underline"
          >
            Send another prompt
          </button>
        </div>
      )}
    </div>
  );
}
