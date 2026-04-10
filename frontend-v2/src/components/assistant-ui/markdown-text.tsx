/**
 * MarkdownText — renders assistant message text as styled Markdown.
 *
 * Uses react-markdown + remark-gfm for parsing, Tailwind Typography
 * (`prose`) for styling, and react-shiki (Vitesse Dark) for code blocks.
 *
 * Replaces assistant-ui's MarkdownTextPrimitive + aui-md wrapper system
 * with clean HTML that Tailwind Typography can style directly.
 */

import { memo, useState, type FC } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ShikiHighlighter, rehypeInlineCodeProperty } from "react-shiki";
import { useAuiState } from "@assistant-ui/store";
import { CheckIcon, CopyIcon } from "lucide-react";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";

// ---------------------------------------------------------------------------
// Copy-to-clipboard hook (kept from the original)
// ---------------------------------------------------------------------------

function useCopyToClipboard(copiedDuration = 3000) {
  const [isCopied, setIsCopied] = useState(false);
  const copyToClipboard = (value: string) => {
    if (!value) return;
    navigator.clipboard.writeText(value).then(() => {
      setIsCopied(true);
      setTimeout(() => setIsCopied(false), copiedDuration);
    });
  };
  return { isCopied, copyToClipboard };
}

// ---------------------------------------------------------------------------
// Code block with syntax highlighting + copy button
// ---------------------------------------------------------------------------

const CodeBlock: FC<{
  language: string;
  code: string;
}> = ({ language, code }) => {
  const { isCopied, copyToClipboard } = useCopyToClipboard();

  return (
    <div className="group/code relative my-3">
      {/* Header bar with language + copy */}
      <div className="flex items-center justify-between rounded-t-lg border border-b-0 border-border/50 bg-muted/50 px-3 py-1.5 text-xs">
        <span className="font-medium text-muted-foreground lowercase">
          {language || "text"}
        </span>
        <TooltipIconButton
          tooltip="Copy"
          onClick={() => copyToClipboard(code)}
        >
          {isCopied ? <CheckIcon /> : <CopyIcon />}
        </TooltipIconButton>
      </div>

      {/* Highlighted code */}
      <div className="overflow-x-auto rounded-b-lg border border-t-0 border-border/50 bg-muted/30 p-3 text-xs leading-relaxed [&_pre]:!m-0 [&_pre]:!bg-transparent [&_pre]:!p-0">
        <ShikiHighlighter
          language={language || "text"}
          theme="vitesse-dark"
          delay={150}
          showLanguage={false}
        >
          {code}
        </ShikiHighlighter>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// MarkdownText — the main component
// ---------------------------------------------------------------------------

const MarkdownTextImpl: FC = () => {
  const text = useAuiState((s) =>
    s.part.type === "text" ? s.part.text : "",
  );

  return (
    <div className="prose prose-base prose-invert text-foreground font-light prose-p:my-2 prose-headings:mt-4 prose-headings:mb-2 prose-headings:text-foreground prose-h1:font-[500] prose-h2:font-[600] prose-strong:text-foreground prose-a:text-primary prose-blockquote:text-muted-foreground prose-code:text-foreground prose-code:font-light prose-pre:my-0 prose-pre:bg-transparent prose-pre:p-0 prose-li:marker:text-primary prose-hr:border-primary/30 prose-blockquote:border-primary/40 prose-th:border-primary/30 prose-td:border-primary/20" style={{ "--tw-prose-body": "var(--color-foreground)", "--tw-prose-bullets": "var(--color-primary)", "--tw-prose-counters": "var(--color-primary)", "--tw-prose-th-borders": "color-mix(in oklch, var(--color-primary) 40%, transparent)", "--tw-prose-td-borders": "color-mix(in oklch, var(--color-primary) 25%, transparent)" } as React.CSSProperties}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeInlineCodeProperty]}
        components={{
          code({ className, children, ...props }) {
            // react-markdown v9+ no longer passes `inline` as a prop.
            // Detect fenced blocks by the presence of a `language-*` class
            // (remark-parse always sets one on fenced code, even for blocks
            // with no language — it just sets `language-` with an empty
            // suffix). Inline code has no language class at all.
            const isFenced = /language-/.test(className || "");
            if (!isFenced) {
              return (
                <code
                  className="rounded-md border border-border/50 bg-muted/50 px-1.5 py-0.5 font-mono text-[0.85em]"
                  {...props}
                >
                  {children}
                </code>
              );
            }
            // Fenced code block — extract language from className
            const match = /language-(\w+)/.exec(className || "");
            const lang = match ? match[1] : "";
            const code = String(children).replace(/\n$/, "");
            return <CodeBlock language={lang!} code={code} />;
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
};

export const MarkdownText = memo(MarkdownTextImpl);

// ---------------------------------------------------------------------------
// UserMarkdownText — lighter prose for user message bubbles
// ---------------------------------------------------------------------------

const UserMarkdownTextImpl: FC = () => {
  const text = useAuiState((s) =>
    s.part.type === "text" ? s.part.text : "",
  );

  return (
    <div
      className="prose prose-sm prose-invert text-foreground prose-p:my-1.5 prose-headings:mt-3 prose-headings:mb-1.5 prose-headings:text-foreground prose-strong:text-foreground prose-a:text-primary prose-code:text-foreground prose-pre:my-1 prose-pre:bg-black/20 prose-pre:text-xs"
      style={{ "--tw-prose-body": "var(--color-foreground)" } as React.CSSProperties}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {text}
      </ReactMarkdown>
    </div>
  );
};

export const UserMarkdownText = memo(UserMarkdownTextImpl);
