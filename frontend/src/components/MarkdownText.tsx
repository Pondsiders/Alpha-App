import type { FC } from "react";
import { renderMarkdown } from "@/lib/renderMarkdown";

interface MarkdownTextProps {
  text: string;
}

/**
 * Markdown renderer for chat content.
 *
 * Uses dangerouslySetInnerHTML instead of react-markdown's component tree.
 * This means React treats the div's children as an opaque HTML string —
 * no child reconciliation, no NotFoundError when the DOM is modified
 * externally by the streaming buffer.
 *
 * During streaming, the TypeOnBuffer writes innerHTML to this div directly.
 * React doesn't notice because it doesn't track individual child nodes
 * when dangerouslySetInnerHTML is used. On settle (appendToAssistant →
 * re-render), React compares the old __html string to the new one. If
 * they match (both produced by renderMarkdown), React does NOTHING —
 * zero DOM manipulation. The handoff is invisible.
 *
 * Styling via shared md-* CSS classes (index.css), same as renderMarkdown.ts.
 */
export const MarkdownText: FC<MarkdownTextProps> = ({ text }) => {
  return (
    <div
      className="markdown-text"
      data-markdown-text
      dangerouslySetInnerHTML={{ __html: text ? renderMarkdown(text) : "" }}
    />
  );
};
