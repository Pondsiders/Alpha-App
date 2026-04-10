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

import type { CSSProperties, FC } from "react";
import remarkGfm from "remark-gfm";
import {
  MarkdownTextPrimitive,
  unstable_memoizeMarkdownComponents as memoizeMarkdownComponents,
} from "@assistant-ui/react-markdown";

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

const defaultComponents = memoizeMarkdownComponents({
  SyntaxHighlighter,
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
  "prose-code:text-foreground prose-code:font-light",
  "prose-pre:my-0 prose-pre:bg-transparent prose-pre:p-0",
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
