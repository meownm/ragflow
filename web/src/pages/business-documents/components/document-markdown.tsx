import { cn } from '@/lib/utils';
import { type ComponentProps, useId } from 'react';
import ReactMarkdown from 'react-markdown';
import rehypeRaw from 'rehype-raw';
import remarkBreaks from 'remark-breaks';
import remarkGfm from 'remark-gfm';
import { rehypeDocumentHtml } from './document-html';

type MarkdownProps = ComponentProps<typeof ReactMarkdown>;

function documentAttributes<T extends { node?: unknown }>(
  props: T,
): Omit<T, 'node'> {
  const attributes = { ...props };
  delete attributes.node;
  return attributes;
}

export function DocumentMarkdown({
  children,
  className,
  components,
  ...props
}: Omit<ComponentProps<'div'>, 'children'> & {
  children: string;
  components?: MarkdownProps['components'];
}) {
  const anchorPrefix = `business-document-${useId().replace(/:/g, '')}-`;
  return (
    <div className={cn('min-w-0 break-words', className)} {...props}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkBreaks]}
        rehypePlugins={[rehypeRaw, [rehypeDocumentHtml, { anchorPrefix }]]}
        components={{
          p: (props) => (
            <p {...documentAttributes(props)} className="mb-2 last:mb-0" />
          ),
          ul: (props) => (
            <ul
              {...documentAttributes(props)}
              className="mb-2 list-disc space-y-1 ps-6 last:mb-0"
            />
          ),
          ol: (props) => (
            <ol
              {...documentAttributes(props)}
              style={{
                listStyleType: {
                  '1': 'decimal',
                  a: 'lower-alpha',
                  A: 'upper-alpha',
                  i: 'lower-roman',
                  I: 'upper-roman',
                }[props.type ?? '1'],
                ...props.style,
              }}
              className="mb-2 list-decimal space-y-1 ps-6 last:mb-0"
            />
          ),
          blockquote: (props) => (
            <blockquote
              {...documentAttributes(props)}
              className="my-2 border-s-2 border-accent-primary ps-3 text-text-secondary"
            />
          ),
          table: (props) => (
            <div className="mb-2 overflow-x-auto last:mb-0">
              <table
                {...documentAttributes(props)}
                className="w-full border-collapse"
              />
            </div>
          ),
          th: (props) => (
            <th
              {...documentAttributes(props)}
              className="border-b border-border-default bg-bg-card px-3 py-2 text-start font-medium"
            />
          ),
          td: (props) => (
            <td
              {...documentAttributes(props)}
              className="border-b border-border-button px-3 py-2 align-top"
            />
          ),
          pre: (props) => (
            <pre
              {...documentAttributes(props)}
              className="mb-2 overflow-x-auto rounded bg-bg-card p-3 last:mb-0"
            />
          ),
          code: ({ className, ...props }) => (
            <code
              {...documentAttributes(props)}
              className={cn(
                'rounded bg-bg-card px-1 py-0.5 font-mono text-[0.9em]',
                className,
              )}
            />
          ),
          a: ({ children, href, title, style, id }) => (
            <a
              id={id}
              href={href}
              title={title}
              style={style}
              target={href?.startsWith('#') ? undefined : '_blank'}
              rel="noreferrer noopener"
              className="text-accent-primary underline underline-offset-4"
            >
              {children}
            </a>
          ),
          h1: (props) => (
            <h1
              {...documentAttributes(props)}
              className="mb-2 mt-4 text-xl font-semibold first:mt-0"
            />
          ),
          h2: (props) => (
            <h2
              {...documentAttributes(props)}
              className="mb-2 mt-4 text-lg font-semibold first:mt-0"
            />
          ),
          h3: (props) => (
            <h3
              {...documentAttributes(props)}
              className="mb-2 mt-3 font-semibold first:mt-0"
            />
          ),
          ...components,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
