import { cn } from '@/lib/utils';
import type { ComponentProps } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkBreaks from 'remark-breaks';
import remarkGfm from 'remark-gfm';

type MarkdownProps = ComponentProps<typeof ReactMarkdown>;

export function DocumentMarkdown({
  children,
  className,
  components,
  ...props
}: Omit<ComponentProps<'div'>, 'children'> & {
  children: string;
  components?: MarkdownProps['components'];
}) {
  return (
    <div className={cn('min-w-0 break-words', className)} {...props}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkBreaks]}
        components={{
          p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
          ul: ({ children }) => (
            <ul className="mb-2 list-disc space-y-1 ps-6 last:mb-0">
              {children}
            </ul>
          ),
          ol: ({ children, start }) => (
            <ol
              start={start}
              className="mb-2 list-decimal space-y-1 ps-6 last:mb-0"
            >
              {children}
            </ol>
          ),
          blockquote: ({ children }) => (
            <blockquote className="my-2 border-s-2 border-accent-primary ps-3 text-text-secondary">
              {children}
            </blockquote>
          ),
          table: ({ children }) => (
            <div className="mb-2 overflow-x-auto last:mb-0">
              <table className="w-full border-collapse">{children}</table>
            </div>
          ),
          th: ({ children }) => (
            <th className="border-b border-border-default bg-bg-card px-3 py-2 text-start font-medium">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="border-b border-border-button px-3 py-2 align-top">
              {children}
            </td>
          ),
          pre: ({ children }) => (
            <pre className="mb-2 overflow-x-auto rounded bg-bg-card p-3 last:mb-0">
              {children}
            </pre>
          ),
          code: ({ children, className }) => (
            <code
              className={cn(
                'rounded bg-bg-card px-1 py-0.5 font-mono text-[0.9em]',
                className,
              )}
            >
              {children}
            </code>
          ),
          a: ({ children, href }) => (
            <a
              href={href}
              target="_blank"
              rel="noreferrer noopener"
              className="text-accent-primary underline underline-offset-4"
            >
              {children}
            </a>
          ),
          h1: ({ children }) => (
            <h1 className="mb-2 mt-4 text-xl font-semibold first:mt-0">
              {children}
            </h1>
          ),
          h2: ({ children }) => (
            <h2 className="mb-2 mt-4 text-lg font-semibold first:mt-0">
              {children}
            </h2>
          ),
          h3: ({ children }) => (
            <h3 className="mb-2 mt-3 font-semibold first:mt-0">{children}</h3>
          ),
          ...components,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
