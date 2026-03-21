/**
 * renderMarkdown — converts raw markdown to HTML with shared md-* classes.
 *
 * Uses the same remark/rehype pipeline as react-markdown (via MarkdownText.tsx),
 * so the output is structurally identical. This means:
 *   - No paragraph-boundary flicker during streaming (remark handles partial
 *     markdown correctly — it doesn't re-evaluate paragraph structure on new text)
 *   - Clean handoff from streaming (innerHTML) to settled (React) — same CSS
 *     classes, same HTML structure, invisible transition
 *
 * Pipeline: markdown → remark-parse → remark-gfm → remark-rehype →
 *           rehype-class-names → rehype-stringify → HTML string
 */

import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import remarkRehype from "remark-rehype";
import rehypeClassNames from "rehype-class-names";
import rehypeStringify from "rehype-stringify";

// Build the processor once — reuse across frames. Synchronous (runSync/processSync).
const processor = unified()
  .use(remarkParse)
  .use(remarkGfm)
  .use(remarkRehype)
  .use(rehypeClassNames, {
    "p": "md-p",
    "h1": "md-h1",
    "h2": "md-h2",
    "h3": "md-h3",
    "h4": "md-h3",  // h4+ share h3 styling
    "h5": "md-h3",
    "h6": "md-h3",
    "ul": "md-ul",
    "ol": "md-ol",
    "li": "md-li",
    "pre": "md-pre",
    "code": "md-code-inline",  // inline code; blocks handled via pre > code
    "blockquote": "md-blockquote",
    "table": "md-table",
    "thead": "md-thead",
    "tbody": "md-tbody",
    "tr": "md-tr",
    "th": "md-th",
    "td": "md-td",
    "hr": "md-hr",
    "strong": "md-strong",
    "em": "md-em",
    "a": "md-a",
    "img": "md-img",
  })
  .use(rehypeStringify);

/**
 * Convert a markdown string to HTML with md-* classes.
 * Synchronous — safe to call on every animation frame.
 */
export function renderMarkdown(text: string): string {
  const result = processor.processSync(text);
  return String(result);
}
