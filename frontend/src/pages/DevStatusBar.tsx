/**
 * Dev preview page for the StatusBar and ConnectionInfo components.
 *
 * Shows the status bar in multiple states:
 *   - Disconnected (no session)
 *   - Connected, idle
 *   - Connected, streaming (API in flight)
 *   - Various context levels
 *
 * Also shows the ConnectionInfo popover in isolation so we can
 * pixelfuck the layout without needing real backend state.
 *
 * Route: /dev/status-bar
 */

import { ConnectionInfo } from "../components/ConnectionInfo";
import { ContextMeter } from "../components/ContextMeter";

interface MockStatusBarProps {
  label: string;
  sublabel?: string;
  sessionId: string | null;
  isRunning: boolean;
  model: string | null;
  tokenCount: number;
  tokenLimit: number;
  contextPercent: number;
}

function getConnectionColor(
  sessionId: string | null,
  isRunning: boolean
): string {
  if (!sessionId) return "var(--theme-muted)";
  if (isRunning) return "var(--theme-success)";
  return "var(--theme-primary)";
}

function MockStatusBar({
  label,
  sublabel,
  sessionId,
  isRunning,
  model,
  tokenCount,
  tokenLimit,
  contextPercent,
}: MockStatusBarProps) {
  const dotColor = getConnectionColor(sessionId, isRunning);
  const shortId = sessionId ? sessionId.slice(0, 8) : "\u2014";

  return (
    <div className="rounded-lg border border-border overflow-hidden">
      {/* Label */}
      <div className="px-4 py-2 bg-background/50 border-b border-border">
        <span className="text-sm text-text">{label}</span>
        {sublabel && (
          <span className="text-xs text-muted/50 ml-2">{sublabel}</span>
        )}
      </div>

      {/* Status bar */}
      <div className="flex items-center justify-between px-4 h-8 bg-surface/50 border-b border-border">
        <div className="flex items-center gap-2.5">
          {/* Dot with health popover */}
          <ConnectionInfo
            sessionId={sessionId}
            isRunning={isRunning}
            model={model}
            tokenCount={tokenCount}
            tokenLimit={tokenLimit}
          >
            <button
              className="inline-flex items-center justify-center w-5 h-5 rounded cursor-pointer bg-transparent border-none hover:bg-background/50 transition-colors"
              aria-label="Connection info"
            >
              <span
                className={`inline-block w-[7px] h-[7px] rounded-full transition-colors duration-300 ${
                  isRunning ? "animate-pulse-dot" : ""
                }`}
                style={{ backgroundColor: dotColor }}
              />
            </button>
          </ConnectionInfo>

          {/* Session ID */}
          <span className="font-mono text-[11px] text-muted">{shortId}</span>
        </div>

        <ContextMeter percent={contextPercent} />
      </div>

      {/* Fake chat area */}
      <div className="h-32 bg-background flex items-center justify-center">
        <span className="text-muted/20 text-sm italic">
          hover the dot for health info
        </span>
      </div>
    </div>
  );
}

/** Standalone popover demo — always visible, no hover needed */
function PopoverDemo({
  label,
  sessionId,
  isRunning,
  model,
  tokenCount,
  tokenLimit,
}: {
  label: string;
  sessionId: string | null;
  isRunning: boolean;
  model: string | null;
  tokenCount: number;
  tokenLimit: number;
}) {
  const status = !sessionId
    ? { text: "Disconnected", color: "var(--theme-muted)" }
    : isRunning
      ? { text: "Connected \u00b7 Streaming", color: "var(--theme-success)" }
      : { text: "Connected \u00b7 Idle", color: "var(--theme-primary)" };

  const formatTokens = (n: number): string => {
    if (n === 0) return "0";
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
    if (n >= 1_000) return (n / 1_000).toFixed(1) + "k";
    return n.toLocaleString();
  };

  const tokenDisplay =
    tokenLimit > 0
      ? `${formatTokens(tokenCount)} / ${formatTokens(tokenLimit)}`
      : "\u2014";

  return (
    <div className="rounded-lg border border-border overflow-hidden">
      <div className="px-4 py-2 bg-background/50 border-b border-border">
        <span className="text-sm text-text">{label}</span>
      </div>
      <div className="p-3 bg-surface">
        <div className="space-y-2">
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-[11px] text-muted">Status</span>
            <span className="text-[11px]" style={{ color: status.color }}>
              {status.text}
            </span>
          </div>
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-[11px] text-muted">Model</span>
            <span className="text-[11px] font-mono">
              {model ?? "\u2014"}
            </span>
          </div>
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-[11px] text-muted">Session</span>
            <span
              className="text-[11px] font-mono text-right"
              style={{ overflowWrap: "anywhere" }}
            >
              {sessionId ?? "\u2014"}
            </span>
          </div>
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-[11px] text-muted">Tokens</span>
            <span className="text-[11px] font-mono">{tokenDisplay}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function DevStatusBar() {
  return (
    <div className="min-h-screen bg-background text-text p-8">
      <h1 className="text-2xl mb-1">Status Bar + Connection Info</h1>
      <p className="text-muted mb-8 text-sm">
        Component preview. Hover the indicator dot to see the health popover.
      </p>

      <div className="max-w-2xl mx-auto space-y-8">
        {/* ── Mock status bars in different states ── */}
        <div>
          <h2 className="text-lg mb-4 text-muted">Status bar states</h2>
          <div className="space-y-6">
            <MockStatusBar
              label="Disconnected"
              sublabel="no session"
              sessionId={null}
              isRunning={false}
              model={null}
              tokenCount={0}
              tokenLimit={0}
              contextPercent={0}
            />

            <MockStatusBar
              label="Connected, idle"
              sublabel="fresh session, low context"
              sessionId="a1b2c3d4-e5f6-7890-abcd-ef1234567890"
              isRunning={false}
              model="claude-opus-4-6"
              tokenCount={12_400}
              tokenLimit={200_000}
              contextPercent={6.2}
            />

            <MockStatusBar
              label="Connected, streaming"
              sublabel="API in flight, moderate context"
              sessionId="f9e8d7c6-b5a4-3210-fedc-ba9876543210"
              isRunning={true}
              model="claude-opus-4-6"
              tokenCount={84_600}
              tokenLimit={200_000}
              contextPercent={42.3}
            />

            <MockStatusBar
              label="Amber zone"
              sublabel="context warming up"
              sessionId="deadbeef-cafe-1234-5678-abcdef012345"
              isRunning={false}
              model="claude-sonnet-4-6"
              tokenCount={138_000}
              tokenLimit={200_000}
              contextPercent={69.0}
            />

            <MockStatusBar
              label="Red zone"
              sublabel="compaction approaching"
              sessionId="cafebabe-dead-beef-1234-567890abcdef"
              isRunning={false}
              model="claude-opus-4-6"
              tokenCount={168_400}
              tokenLimit={200_000}
              contextPercent={84.2}
            />
          </div>
        </div>

        {/* ── Popover content rendered inline for easy pixelfucking ── */}
        <div className="mt-16">
          <h2 className="text-lg mb-4 text-muted">
            Popover content — rendered inline
          </h2>
          <p className="text-muted/60 text-xs mb-4">
            Same content as the hover popover, shown flat so you can see all
            states at once without hovering.
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <PopoverDemo
              label="Disconnected"
              sessionId={null}
              isRunning={false}
              model={null}
              tokenCount={0}
              tokenLimit={0}
            />
            <PopoverDemo
              label="Connected, idle"
              sessionId="a1b2c3d4-e5f6-7890-abcd-ef1234567890"
              isRunning={false}
              model="claude-opus-4-6"
              tokenCount={84_600}
              tokenLimit={200_000}
            />
            <PopoverDemo
              label="Streaming"
              sessionId="f9e8d7c6-b5a4-3210-fedc-ba9876543210"
              isRunning={true}
              model="claude-opus-4-6"
              tokenCount={142_000}
              tokenLimit={200_000}
            />
            <PopoverDemo
              label="Sonnet variant"
              sessionId="deadbeef-cafe-1234-5678-abcdef012345"
              isRunning={false}
              model="claude-sonnet-4-6"
              tokenCount={38_200}
              tokenLimit={200_000}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
