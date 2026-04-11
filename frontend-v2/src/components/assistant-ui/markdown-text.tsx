/**
 * MarkdownText + UserMarkdownText — streaming markdown renderers.
 *
 * Built on @assistant-ui/react-markdown's MarkdownTextPrimitive, which
 * gives us built-in smooth character-by-character streaming for free.
 * Both components share the same set of memoized component overrides
 * (just the SyntaxHighlighter slot — see ./shiki-highlighter), so user
 * code blocks and assistant code blocks render with the same syntax
 * highlighting, line wrapping, and theme.
 *
 * Typography styling lives on the wrapper div via Tailwind Typography's
 * `prose` classes + a small set of CSS variable overrides that make
 * prose-invert use our warm amber-on-charcoal palette instead of the
 * default cool white-on-black look.
 *
 * One markdown rendering path across the app. No hand-rolled ReactMarkdown.
 */

import { useState, type CSSProperties, type FC } from "react";
import remarkGfm from "remark-gfm";
import {
  MarkdownTextPrimitive,
  unstable_memoizeMarkdownComponents as memoizeMarkdownComponents,
  type CodeHeaderProps,
} from "@assistant-ui/react-markdown";
import { CheckIcon, CopyIcon } from "lucide-react";

import { SyntaxHighlighter } from "./shiki-highlighter";

// ---------------------------------------------------------------------------
// Memoized component overrides
// ---------------------------------------------------------------------------
//
// We only override the SyntaxHighlighter slot. Everything else (h1, h2, p,
// lists, tables, etc.) is styled via Tailwind Typography `prose` classes on
// the wrapper — no per-element React components needed.
//
// memoizeMarkdownComponents guarantees component identity stability across
// renders so MarkdownTextPrimitive doesn't re-render the whole tree every
// streaming tick.

// ---------------------------------------------------------------------------
// CodeHeader — language label + copy button above fenced code blocks
// ---------------------------------------------------------------------------
//
// Rounded-top to match the pre's rounded-bottom. Returns null when no
// language is set, so plain ``` fences don't get an awkward empty bar.

// Language label on the left, copy button on the right. Rounded-top
// corners to match the pre's rounded-bottom. Returns null when no
// language is set so plain ``` fences don't get an awkward empty bar.
//
// Uses a plain <button> with a native `title` attribute for the hover
// tooltip, NOT Radix's TooltipIconButton. There's an open interaction
// between Radix Tooltip's attribute mutations and assistant-ui's thread
// viewport auto-scroll MutationObserver that can cause unwanted
// scroll-to-bottom on hover. Native title tooltip sidesteps it entirely.
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

const defaultComponents = memoizeMarkdownComponents({
  SyntaxHighlighter,
  CodeHeader,
});

// ---------------------------------------------------------------------------
// Shared prose styling
// ---------------------------------------------------------------------------
//
// These CSS variables override the ones Tailwind Typography's prose-invert
// sets by default. Without them, body text renders as cool white (wrong),
// list bullets render as muted gray (wrong), and table borders are the
// generic theme border color (bland). With them, we get warm foreground
// body text, amber bullets and counters, and amber-tinted table borders.
//
// Cast via `as CSSProperties` because TypeScript doesn't natively know
// about custom --tw-prose-* properties.

const PROSE_VARS = {
  "--tw-prose-body": "var(--color-foreground)",
  "--tw-prose-bullets": "var(--color-primary)",
  "--tw-prose-counters": "var(--color-primary)",
  "--tw-prose-th-borders":
    "color-mix(in oklch, var(--color-primary) 40%, transparent)",
  "--tw-prose-td-borders":
    "color-mix(in oklch, var(--color-primary) 25%, transparent)",
} as CSSProperties;

// Shared prose class tokens — used by both MarkdownText and UserMarkdownText.
// Size modifier (prose-base vs prose-sm) is added per-component.
const PROSE_CLASSES = [
  "prose prose-invert text-foreground font-light",
  "prose-headings:text-foreground",
  "prose-h1:font-[500] prose-h2:font-[600]",
  "prose-strong:text-foreground",
  "prose-a:text-primary",
  "prose-blockquote:text-muted-foreground prose-blockquote:border-primary/40",
  // Note: inline code font size/weight are driven by theme variables
  // (--md-code-span-*) via global CSS rules in index.css. We only keep
  // the color here; everything else is tunable per-theme.
  "prose-code:text-foreground",
  // Code blocks: let Shiki own the background (its theme paints it via
   // inline style); we add the border + bottom-radius so the <pre> visually
   // continues from the CodeHeader sitting above it.
   "prose-pre:my-0 prose-pre:bg-transparent prose-pre:p-0",
   "prose-pre:border prose-pre:border-t-0 prose-pre:border-border",
   "prose-pre:rounded-t-none prose-pre:rounded-b-lg",
  "prose-li:marker:text-primary",
  "prose-hr:border-primary/30",
  "prose-th:border-primary/30 prose-td:border-primary/20",
].join(" ");

// ---------------------------------------------------------------------------
// MarkdownText — assistant messages, smooth streaming
// ---------------------------------------------------------------------------

const MARKDOWN_CLASSES = [
  PROSE_CLASSES,
  "prose-base",
  "prose-p:my-2 prose-headings:mt-4 prose-headings:mb-2",
].join(" ");

export const MarkdownText: FC = () => (
  <MarkdownTextPrimitive
    remarkPlugins={[remarkGfm]}
    components={defaultComponents}
    className={MARKDOWN_CLASSES}
    containerProps={{ style: PROSE_VARS }}
    smooth
  />
);

// ---------------------------------------------------------------------------
// UserMarkdownText — user message bubbles, no streaming
// ---------------------------------------------------------------------------
//
// Smaller prose size (`prose-sm`), tighter spacing. Not streamed — user
// messages arrive whole — so no `smooth` prop. Shares defaultComponents
// with MarkdownText, so user code blocks get the same syntax highlighting.

const USER_MARKDOWN_CLASSES = [
  PROSE_CLASSES,
  "prose-sm",
  "prose-p:my-1.5 prose-headings:mt-3 prose-headings:mb-1.5",
].join(" ");

export const UserMarkdownText: FC = () => (
  <MarkdownTextPrimitive
    remarkPlugins={[remarkGfm]}
    components={defaultComponents}
    className={USER_MARKDOWN_CLASSES}
    containerProps={{ style: PROSE_VARS }}
  />
);
