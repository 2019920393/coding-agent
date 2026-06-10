import { isValidElement, memo, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import rehypeRaw from "rehype-raw";
import "highlight.js/styles/github-dark.css";

interface MarkdownRendererProps {
  content: string;
}

export const MarkdownRenderer = memo(function MarkdownRenderer({ content }: MarkdownRendererProps) {
  return (
    <ReactMarkdown
      rehypePlugins={[rehypeHighlight, rehypeRaw]}
      components={{
        code({ className, children, ...props }) {
          const isInline = !className;
          if (isInline) {
            return (
              <code className="inline-code" {...props}>
                {children}
              </code>
            );
          }

          // 提取语言名称
          const match = /language-(\w+)/.exec(className || '');
          const language = match ? match[1] : 'text';

          return (
            <code className={className} data-language={language} {...props}>
              {children}
            </code>
          );
        },
        pre({ children, ...props }) {
          return (
            <pre className="code-block" data-language={getPreLanguage(children)} {...props}>
              {children}
            </pre>
          );
        },
        a({ children, href, ...props }) {
          return (
            <a href={href} target="_blank" rel="noopener noreferrer" {...props}>
              {children}
            </a>
          );
        },
      }}
    >
      {content}
    </ReactMarkdown>
  );
});

function getPreLanguage(children: ReactNode): string {
  if (isValidElement<{ "data-language"?: unknown }>(children)) {
    const language = children.props["data-language"];
    if (typeof language === "string" && language.length > 0) {
      return language;
    }
  }

  return "code";
}
