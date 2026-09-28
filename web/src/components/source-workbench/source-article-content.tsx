import DOMPurify from 'dompurify';
import ReactMarkdown from 'react-markdown';
import rehypeRaw from 'rehype-raw';
import remarkGfm from 'remark-gfm';

export function SourceArticleContent({
  text,
  label,
  allowHtml = false,
}: {
  text: string;
  label: string;
  allowHtml?: boolean;
}) {
  return (
    <article
      aria-label={label}
      className="mt-3 min-w-0 break-words rounded-md border border-border-button bg-bg-card px-5 py-4 text-sm leading-7 text-text-primary"
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={allowHtml ? [rehypeRaw] : []}
        components={{
          h1: ({ children }) => (
            <h1 className="mb-5 text-2xl font-semibold">{children}</h1>
          ),
          h2: ({ children }) => (
            <h2 className="mb-3 mt-8 text-xl font-semibold">{children}</h2>
          ),
          h3: ({ children }) => (
            <h3 className="mb-2 mt-6 text-base font-semibold">{children}</h3>
          ),
          p: ({ children }) => <p className="mb-4">{children}</p>,
          ul: ({ children }) => (
            <ul className="mb-4 list-disc ps-6">{children}</ul>
          ),
          ol: ({ children }) => (
            <ol className="mb-4 list-decimal ps-6">{children}</ol>
          ),
          li: ({ children }) => <li className="mb-1">{children}</li>,
          blockquote: ({ children }) => (
            <blockquote className="mb-4 border-s-2 border-accent-primary ps-4">
              {children}
            </blockquote>
          ),
          pre: ({ children }) => (
            <pre className="mb-4 overflow-x-auto rounded bg-bg-base p-3">
              {children}
            </pre>
          ),
          table: ({ children }) => (
            <div className="mb-4 overflow-x-auto">
              <table className="w-full border-collapse">{children}</table>
            </div>
          ),
          th: ({ children }) => (
            <th className="border border-border-button p-2 text-start">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="border border-border-button p-2">{children}</td>
          ),
        }}
      >
        {allowHtml ? DOMPurify.sanitize(text) : text}
      </ReactMarkdown>
    </article>
  );
}
