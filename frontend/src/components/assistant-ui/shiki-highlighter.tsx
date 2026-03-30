/**
 * SyntaxHighlighter — wraps react-shiki for use with assistant-ui's
 * MarkdownTextPrimitive. Uses the `vesper` theme: warm dark, amber accents,
 * background close to our --theme-code-bg (#101010 vs #161616).
 *
 * The component is debounced internally by react-shiki so highlighting
 * doesn't fire on every streaming delta — only after the code settles.
 */
import { ShikiHighlighter } from "react-shiki";

interface SyntaxHighlighterProps {
  children: string;
  language: string | undefined;
  className?: string;
}

export function SyntaxHighlighter({ children, language }: SyntaxHighlighterProps) {
  return (
    <ShikiHighlighter
      language={language ?? "text"}
      theme="vesper"
      delay={150}
    >
      {children}
    </ShikiHighlighter>
  );
}
