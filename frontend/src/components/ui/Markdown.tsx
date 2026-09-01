import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { normalizeMarkdown } from './markdownNormalize'

interface MarkdownProps {
  children: string
  /** Extra class on the wrapper, for callers that need their own spacing. */
  className?: string
}

/**
 * Renders LLM-authored text as markdown.
 *
 * Tutor responses, hints and grading feedback all come out of the model with
 * `###` headings, `**bold**` and `-` bullets. Every one of those surfaces
 * rendered the text raw, so students read literal hash and asterisk characters
 * instead of a formatted explanation.
 *
 * react-markdown was added for this (nothing in package.json parsed markdown).
 * It builds React elements directly rather than going through
 * dangerouslySetInnerHTML, which matters here because every string passed in is
 * model output: there is no path from generated text to injected HTML.
 *
 * The element allowlist is deliberately narrow. Anything outside it is dropped
 * to its text content, so a hallucinated `<img>` or `<iframe>` cannot reach the
 * DOM. Links are included but neutralised by `urlTransform` below.
 *
 * remark-gfm is on because the model really does emit GitHub-flavoured
 * markdown. A live DBMS explanation answered with a pipe table comparing an
 * unnormalised schema to a normalised one; without the plugin that renders as
 * a wall of literal `| EmployeeID | Name |` rows, which is the same class of
 * defect as the raw `###` headings this component exists to fix.
 */
const ALLOWED_ELEMENTS = [
  'p',
  'br',
  'strong',
  'em',
  'del',
  'code',
  'pre',
  'blockquote',
  'ul',
  'ol',
  'li',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
  'hr',
  'a',
  // GFM. Tables show up in comparison-style explanations; strikethrough and
  // task-list checkboxes come along with the same plugin.
  'table',
  'thead',
  'tbody',
  'tr',
  'th',
  'td',
  'input',
]

/** Protocols an anchor in model output may use. Everything else is dropped. */
const SAFE_PROTOCOLS = ['http:', 'https:', 'mailto:']

/**
 * Strip any URL we would not want a student to click.
 *
 * The tutor is grounded in uploaded course material and has no reason to emit
 * links at all, but it is a language model and may anyway. `javascript:` and
 * `data:` hrefs are the reason this is not left to the default.
 */
function safeUrl(url: string): string {
  try {
    const parsed = new URL(url, window.location.origin)
    return SAFE_PROTOCOLS.includes(parsed.protocol) ? url : ''
  } catch {
    return ''
  }
}

export function Markdown({ children, className }: MarkdownProps) {
  return (
    <div className={className ? `markdown ${className}` : 'markdown'}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        allowedElements={ALLOWED_ELEMENTS}
        unwrapDisallowed
        urlTransform={safeUrl}
        components={{
          // Model output is a fragment inside a card, not a document. Its
          // headings must not compete with the page's own h1/h2, so the whole
          // scale is flattened into one styled class.
          h1: ({ children: c }) => <p className="markdown-heading">{c}</p>,
          h2: ({ children: c }) => <p className="markdown-heading">{c}</p>,
          h3: ({ children: c }) => <p className="markdown-heading">{c}</p>,
          h4: ({ children: c }) => <p className="markdown-heading">{c}</p>,
          h5: ({ children: c }) => <p className="markdown-heading">{c}</p>,
          h6: ({ children: c }) => <p className="markdown-heading">{c}</p>,
          // Tables can be wider than a chat bubble; let them scroll rather
          // than force the whole page to.
          table: ({ children: c }) => (
            <div className="markdown-table-wrap">
              <table>{c}</table>
            </div>
          ),
          // GFM task-list checkboxes only. Any other input is inert and
          // read-only — this is rendered model output, not a form.
          input: (props) =>
            props.type === 'checkbox' ? (
              <input type="checkbox" checked={Boolean(props.checked)} disabled readOnly />
            ) : null,
          a: ({ href, children: c }) =>
            href ? (
              <a href={href} target="_blank" rel="noopener noreferrer nofollow">
                {c}
              </a>
            ) : (
              <>{c}</>
            ),
        }}
      >
        {normalizeMarkdown(children)}
      </ReactMarkdown>
    </div>
  )
}
