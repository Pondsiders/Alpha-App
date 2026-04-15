/**
 * MarkdownText + UserMarkdownText — streaming markdown renderers.
 *
 * Built on @assistant-ui/react-streamdown's StreamdownTextPrimitive,
 * which gives us block-based incremental rendering with a visual caret.
 * No FOUC during streaming — code blocks are syntax-highlighted as they
 * stream in, not after they're complete.
 *
 * Shiki highlighting is built into the @streamdown/code plugin. Our
 * custom SyntaxHighlighter and react-shiki are no longer needed.
 *
 * Typography styling lives on the wrapper div via Tailwind Typography's
 * `prose` classes + CSS variable overrides for warm amber-on-charcoal.
 *
 * One rendering path across the app. Assistant and user messages share
 * the same components; only prose size differs.
 */

import { useState, type CSSProperties, type FC } from "react";
import {
  StreamdownTextPrimitive,
  type CodeHeaderProps,
  type StreamdownTextComponents,
} from "@assistant-ui/react-streamdown";
import { createCodePlugin } from "@streamdown/code";
import { math } from "@streamdown/math";
import { mermaid } from "@streamdown/mermaid";
import "katex/dist/katex.min.css";
import a11yEmoji from "@fec/remark-a11y-emoji";
import { CheckIcon, CopyIcon } from "lucide-react";

// ---------------------------------------------------------------------------
// Shiki code plugin — Kanagawa themes, configured once
// ---------------------------------------------------------------------------

const codePlugin = createCodePlugin({
  themes: ["vitesse-dark", "vitesse-light"],
});

// Stable references for remark plugins.
// Streamdown includes remark-gfm in its defaultRemarkPlugins, but passing
// our own remarkPlugins array REPLACES the defaults. Spread them to keep
// GFM tables, task lists, strikethrough, and autolinks working.
import { defaultRemarkPlugins } from "streamdown";

const remarkPlugins = [...Object.values(defaultRemarkPlugins), a11yEmoji];
const emojiAllowedTags = { span: ["role", "aria-label"] };

// ---------------------------------------------------------------------------
// CodeHeader — language label + copy button above fenced code blocks
// ---------------------------------------------------------------------------

const CodeHeader: FC<CodeHeaderProps> = ({ language, code }) => {
  const [copied, setCopied] = useState(false);
  if (!language) return null;

  const handleCopy = () => {
    if (!code) return;
    void navigator.clipboard.writeText(code).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <div className="flex items-center justify-between rounded-t-lg border border-b-0 border-border bg-card px-3 py-1 text-xs">
      <span className="font-mono lowercase text-muted-foreground">
        {language}
      </span>
      <button
        type="button"
        onClick={handleCopy}
        title={copied ? "Copied" : "Copy code"}
        aria-label={copied ? "Copied" : "Copy code"}
        className="flex size-6 items-center justify-center rounded text-muted-foreground hover:text-foreground"
      >
        {copied ? (
          <CheckIcon className="size-3.5" />
        ) : (
          <CopyIcon className="size-3.5" />
        )}
      </button>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Component overrides — shared between assistant and user messages
// ---------------------------------------------------------------------------

// Start with no custom components — use Streamdown's defaults.
// CodeHeader can be added back once we verify base rendering works.
const components = {} as StreamdownTextComponents;

// ---------------------------------------------------------------------------
// Shared prose styling
// ---------------------------------------------------------------------------

const PROSE_VARS = {
  "--tw-prose-body": "var(--color-foreground)",
  "--tw-prose-bullets": "var(--color-primary)",
  "--tw-prose-counters": "var(--color-primary)",
  "--tw-prose-th-borders":
    "color-mix(in oklch, var(--color-primary) 40%, transparent)",
  "--tw-prose-td-borders":
    "color-mix(in oklch, var(--color-primary) 25%, transparent)",
} as CSSProperties;

const PROSE_CLASSES = [
  "prose prose-invert text-foreground font-light",
  "prose-headings:text-foreground",
  "prose-h1:font-[500] prose-h2:font-[600]",
  "prose-strong:text-foreground",
  "prose-a:text-primary",
  "prose-blockquote:text-muted-foreground prose-blockquote:border-primary/40",
  "prose-code:text-foreground",
  "prose-pre:my-0 prose-pre:p-0",
  "prose-li:marker:text-primary",
  "prose-hr:border-primary/30",
  "prose-th:border-primary/30 prose-td:border-primary/20",
].join(" ");

// ---------------------------------------------------------------------------
// MarkdownText — assistant messages, streaming
// ---------------------------------------------------------------------------

const MARKDOWN_CLASSES = [
  PROSE_CLASSES,
  "prose-base",
  "prose-p:my-2 prose-headings:mt-4 prose-headings:mb-2",
].join(" ");

export const MarkdownText: FC = () => (
  <StreamdownTextPrimitive
    plugins={{ code: codePlugin, math, mermaid }}
    remarkPlugins={remarkPlugins}
    allowedTags={emojiAllowedTags}
    components={components}
    controls
    caret="block"
    containerClassName={MARKDOWN_CLASSES}
    containerProps={{ style: PROSE_VARS }}
  />
);

// ---------------------------------------------------------------------------
// UserMarkdownText — user message bubbles, no streaming
// ---------------------------------------------------------------------------

const USER_MARKDOWN_CLASSES = [
  PROSE_CLASSES,
  "prose-sm",
  "prose-p:my-1.5 prose-headings:mt-3 prose-headings:mb-1.5",
].join(" ");

export const UserMarkdownText: FC = () => (
  <StreamdownTextPrimitive
    plugins={{ code: codePlugin, math, mermaid }}
    components={components}
    controls
    containerClassName={USER_MARKDOWN_CLASSES}
    containerProps={{ style: PROSE_VARS }}
  />
);
