import { memo } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import remarkMath from "remark-math"
import rehypeKatex from "rehype-katex"
import rehypeHighlight from "rehype-highlight"

import { cn } from "@/lib/utils"

interface MarkdownProps {
  children: string
  className?: string
}

// Shared markdown renderer: GFM (tables/strikethrough), math ($..$ / $$..$$) via KaTeX,
// and syntax highlighting via highlight.js. Used by both panes.
function MarkdownImpl({ children, className }: MarkdownProps) {
  return (
    <div
      className={cn(
        "prose prose-neutral max-w-none dark:prose-invert",
        "prose-pre:bg-muted prose-pre:text-foreground",
        className,
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex, rehypeHighlight]}
      >
        {children}
      </ReactMarkdown>
    </div>
  )
}

export const Markdown = memo(MarkdownImpl)
