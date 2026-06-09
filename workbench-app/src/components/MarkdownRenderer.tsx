import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import rehypeRaw from "rehype-raw";
import "highlight.js/styles/github-dark.css";

interface MarkdownRendererProps {
  content: string;
}

export function MarkdownRenderer({ content }: MarkdownRendererProps) {
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
          // 从子元素中提取语言信息
          let language = 'code';
          if (children && typeof children === 'object' && 'props' in children) {
            const codeProps = children.props as any;
            if (codeProps?.['data-language']) {
              language = codeProps['data-language'];
            }
          }

          return (
            <pre className="code-block" data-language={language} {...props}>
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
}
