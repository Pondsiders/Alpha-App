import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { FC } from "react";

interface MarkdownTextProps {
  text: string;
}

/**
 * Markdown renderer for chat content. Uses shared md-* CSS classes
 * (defined in index.css) so the output matches renderMarkdown.ts
 * exactly — no visual pop on the streaming→settled handoff.
 */
export const MarkdownText: FC<MarkdownTextProps> = ({ text }) => {
  return (
    <div className="markdown-text" data-markdown-text>
    <Markdown
      remarkPlugins={[remarkGfm]}
      components={{
        p: ({ children }) => (
          <p className="md-p">{children}</p>
        ),
        ul: ({ children }) => (
          <ul className="md-ul">{children}</ul>
        ),
        ol: ({ children }) => (
          <ol className="md-ol">{children}</ol>
        ),
        li: ({ children }) => (
          <li className="md-li">{children}</li>
        ),
        code: ({ children, className }) => {
          const isInline = !className && typeof children === 'string' && !children.includes('\n');

          if (isInline) {
            return (
              <code className="md-code-inline">
                {children}
              </code>
            );
          }

          return (
            <pre className="md-pre">
              <code className={`md-code-block${className ? ` ${className}` : ""}`}>{children}</code>
            </pre>
          );
        },
        pre: ({ children }) => <>{children}</>,
        blockquote: ({ children }) => (
          <blockquote className="md-blockquote">
            {children}
          </blockquote>
        ),
        h1: ({ children }) => (
          <h1 className="md-h1">
            {children}
          </h1>
        ),
        h2: ({ children }) => (
          <h2 className="md-h2">
            {children}
          </h2>
        ),
        h3: ({ children }) => (
          <h3 className="md-h3">
            {children}
          </h3>
        ),
        a: ({ href, children }) => (
          <a href={href} className="md-a">
            {children}
          </a>
        ),
        strong: ({ children }) => (
          <strong className="md-strong">{children}</strong>
        ),
        em: ({ children }) => (
          <em className="md-em">{children}</em>
        ),
        table: ({ children }) => (
          <div className="md-table-wrap">
            <table className="md-table">{children}</table>
          </div>
        ),
        thead: ({ children }) => (
          <thead className="md-thead">{children}</thead>
        ),
        tbody: ({ children }) => <tbody className="md-tbody">{children}</tbody>,
        tr: ({ children }) => (
          <tr className="md-tr">{children}</tr>
        ),
        th: ({ children }) => (
          <th className="md-th">{children}</th>
        ),
        td: ({ children }) => (
          <td className="md-td">{children}</td>
        ),
      }}
    >
      {text}
    </Markdown>
    </div>
  );
};
