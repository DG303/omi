import { NextRequest, NextResponse } from "next/server";
import { verifyAdmin } from "@/lib/auth";

export const dynamic = "force-dynamic";

const REPO_OWNER = process.env.AGENT_REPO_OWNER || "BasedHardware";
const REPO_NAME = process.env.AGENT_REPO_NAME || "omi";
const WORKFLOW_FILE = process.env.AGENT_WORKFLOW_FILE || "admin_dashboard_agent.yml";
const WORKFLOW_REF = process.env.AGENT_WORKFLOW_REF || "main";

const MAX_PROMPT_LEN = 4000;

export async function POST(request: NextRequest) {
  const auth = await verifyAdmin(request);
  if (auth instanceof NextResponse) return auth;

  const ghToken = process.env.AGENT_GITHUB_TOKEN || process.env.GITHUB_TOKEN;
  if (!ghToken) {
    return NextResponse.json(
      {
        error:
          "AGENT_GITHUB_TOKEN not configured. Set a fine-scoped PAT (repo+workflow) on the admin server to enable dispatch.",
      },
      { status: 503 },
    );
  }

  let body: { prompt?: string };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const prompt = (body.prompt ?? "").trim();
  if (!prompt) {
    return NextResponse.json({ error: "prompt is required" }, { status: 400 });
  }
  if (prompt.length > MAX_PROMPT_LEN) {
    return NextResponse.json(
      { error: `prompt exceeds ${MAX_PROMPT_LEN} chars` },
      { status: 400 },
    );
  }

  const dispatchUrl = `https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/actions/workflows/${WORKFLOW_FILE}/dispatches`;
  // GitHub returns 204 with no body for workflow_dispatch and gives no run id;
  // we read the most recent run for this workflow to surface a link in the UI.
  const dispatchedAt = Date.now();
  const dispatchRes = await fetch(dispatchUrl, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${ghToken}`,
      "X-GitHub-Api-Version": "2022-11-28",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      ref: WORKFLOW_REF,
      inputs: {
        prompt,
        admin_uid: auth.uid,
      },
    }),
  });

  if (!dispatchRes.ok) {
    const errText = await dispatchRes.text().catch(() => "");
    return NextResponse.json(
      {
        error: `GitHub dispatch failed (${dispatchRes.status}): ${errText.slice(0, 400)}`,
      },
      { status: 502 },
    );
  }

  // Best-effort: poll the runs endpoint briefly to get a runId/runUrl. The
  // dispatch is async on GitHub's side so we wait up to ~5s.
  let runId: number | null = null;
  let runUrl: string | null = null;
  const runsUrl = `https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/actions/workflows/${WORKFLOW_FILE}/runs?event=workflow_dispatch&per_page=5`;
  for (let i = 0; i < 5; i++) {
    await new Promise((r) => setTimeout(r, 1000));
    const runsRes = await fetch(runsUrl, {
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${ghToken}`,
        "X-GitHub-Api-Version": "2022-11-28",
      },
    });
    if (!runsRes.ok) continue;
    const runsData: { workflow_runs?: Array<{ id: number; html_url: string; created_at: string }> } =
      await runsRes.json();
    const candidate = runsData.workflow_runs?.find(
      (r) => new Date(r.created_at).getTime() >= dispatchedAt - 5000,
    );
    if (candidate) {
      runId = candidate.id;
      runUrl = candidate.html_url;
      break;
    }
  }

  return NextResponse.json({
    dispatched: true,
    runId,
    runUrl,
    prUrl: null, // PR URL fills in once the workflow finishes; UI links to the run.
    workflow: WORKFLOW_FILE,
    ref: WORKFLOW_REF,
  });
}
