import type { ComponentPropsWithoutRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSlug from "rehype-slug";

/**
 * A wide code fence can overflow horizontally, which makes it a
 * keyboard-scrollable region (WCAG 2.1.1 / axe's `scrollable-region-focusable`).
 * `tabIndex={0}` plus a label lets keyboard/AT users reach and scroll it the
 * same way a mouse user would drag it.
 */
function FocusablePre(props: ComponentPropsWithoutRef<"pre">) {
  return <pre tabIndex={0} role="region" aria-label="Code block" {...props} />;
}

/**
 * Renders trusted, server-sourced Markdown (the eval report, architecture.md,
 * assumptions.md). `rehype-raw` is deliberately not installed: react-markdown
 * escapes raw HTML in the source by default, so embedded `<script>`/`<img
 * onerror>` text renders as literal text rather than executing.
 */
export function MarkdownDocument({ markdown }: { markdown: string }) {
  return (
    <div className="markdown-document">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeSlug]}
        components={{
          pre: FocusablePre,
          table: ({ node: _node, ...props }) => (
            <div className="table-scroll" tabIndex={0} role="region" aria-label="Table">
              <table {...props} />
            </div>
          ),
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
}
