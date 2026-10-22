'use client'

import { useEffect, useState, type ReactNode } from 'react'

type Lang =
  | 'python'
  | 'javascript'
  | 'typescript'
  | 'json'
  | 'yaml'
  | 'bash'
  | 'sql'
  | 'css'
  | 'toml'
  | 'markdown'
  | 'text'

type TokenType = 'comment' | 'string' | 'number' | 'word' | 'punct' | 'plain' | 'nl'

interface Token {
  type: TokenType
  value: string
  next?: string
}

const COLORS = {
  keyword: '#ff7b72',
  string: '#a5d6ff',
  comment: '#8b949e',
  number: '#79c0ff',
  fn: '#d2a8ff',
  cls: '#ffa657',
  punct: '#8b949e',
  plain: '#e6edf3',
  heading: '#ffa657',
}

const KEYWORDS: Record<string, string[]> = {
  python: [
    'def', 'return', 'if', 'elif', 'else', 'for', 'while', 'in', 'not', 'and', 'or',
    'import', 'from', 'as', 'with', 'try', 'except', 'finally', 'raise', 'class', 'lambda',
    'pass', 'break', 'continue', 'yield', 'global', 'nonlocal', 'assert', 'del', 'is',
    'None', 'True', 'False', 'self', 'async', 'await', 'match', 'case',
  ],
  javascript: [
    'const', 'let', 'var', 'function', 'return', 'if', 'else', 'for', 'while', 'do',
    'switch', 'case', 'break', 'continue', 'new', 'class', 'extends', 'super', 'this',
    'typeof', 'instanceof', 'in', 'of', 'import', 'export', 'from', 'default', 'async',
    'await', 'try', 'catch', 'finally', 'throw', 'null', 'undefined', 'true', 'false', 'delete', 'void',
  ],
  typescript: [
    'const', 'let', 'var', 'function', 'return', 'if', 'else', 'for', 'while', 'do',
    'switch', 'case', 'break', 'continue', 'new', 'class', 'extends', 'implements',
    'super', 'this', 'typeof', 'instanceof', 'in', 'of', 'import', 'export', 'from',
    'default', 'async', 'await', 'try', 'catch', 'finally', 'throw', 'null', 'undefined',
    'true', 'false', 'interface', 'type', 'enum', 'namespace', 'declare', 'readonly',
    'public', 'private', 'protected', 'abstract', 'as', 'satisfies', 'delete', 'void', 'keyof',
  ],
  json: ['true', 'false', 'null'],
  yaml: ['true', 'false', 'null', 'yes', 'no', 'on', 'off'],
  bash: ['if', 'then', 'else', 'elif', 'fi', 'for', 'while', 'do', 'done', 'case', 'esac', 'function', 'return', 'in', 'local', 'export', 'set', 'unset', 'shift', 'echo'],
  sql: ['SELECT', 'FROM', 'WHERE', 'INSERT', 'INTO', 'VALUES', 'UPDATE', 'SET', 'DELETE', 'CREATE', 'TABLE', 'DROP', 'ALTER', 'JOIN', 'LEFT', 'RIGHT', 'INNER', 'OUTER', 'ON', 'GROUP', 'BY', 'ORDER', 'HAVING', 'LIMIT', 'OFFSET', 'AND', 'OR', 'NOT', 'NULL', 'IS', 'AS', 'DISTINCT', 'PRIMARY', 'KEY', 'FOREIGN', 'REFERENCES', 'INDEX', 'UNIQUE', 'DEFAULT', 'CHECK', 'IF', 'EXISTS', 'UNION', 'ALL', 'CASE', 'WHEN', 'THEN', 'ELSE', 'END', 'TRUE', 'FALSE'],
  css: ['important', 'inherit', 'initial', 'unset'],
  toml: ['true', 'false'],
}

const IMAGE_EXT = ['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'ico']
const TEXT_EXT: Record<string, Lang> = {
  py: 'python',
  js: 'javascript',
  mjs: 'javascript',
  cjs: 'javascript',
  jsx: 'javascript',
  ts: 'typescript',
  tsx: 'typescript',
  json: 'json',
  yaml: 'yaml',
  yml: 'yaml',
  sh: 'bash',
  bash: 'bash',
  zsh: 'bash',
  sql: 'sql',
  css: 'css',
  toml: 'toml',
  ini: 'toml',
  md: 'markdown',
  markdown: 'markdown',
  txt: 'text',
  log: 'text',
  csv: 'text',
  tsv: 'text',
  env: 'text',
  dockerfile: 'text',
  gitignore: 'text',
}

function detectKind(filename: string): { kind: 'iframe' | 'text'; lang: Lang } {
  const ext = (filename.split('.').pop() || '').toLowerCase()
  if (ext === 'pdf' || ext === 'html' || ext === 'htm') return { kind: 'iframe', lang: 'text' }
  if (IMAGE_EXT.includes(ext)) return { kind: 'iframe', lang: 'text' }
  return { kind: 'text', lang: TEXT_EXT[ext] || 'text' }
}

function tokenize(text: string, lang: Lang): Token[] {
  const tokens: Token[] = []
  let i = 0
  const n = text.length
  let blockComment = false

  const isWordChar = (c: string) => /[A-Za-z0-9_]/.test(c)

  while (i < n) {
    const ch = text[i]
    const next = text[i + 1]

    if (ch === '\n') {
      tokens.push({ type: 'nl', value: '\n' })
      i++
      continue
    }

    if (blockComment) {
      if (ch === '*' && next === '/') {
        tokens.push({ type: 'comment', value: '*/' })
        blockComment = false
        i += 2
      } else {
        tokens.push({ type: 'comment', value: ch })
        i++
      }
      continue
    }
    if (
      (lang === 'javascript' || lang === 'typescript' || lang === 'css' || lang === 'sql') &&
      ch === '/' && next === '*'
    ) {
      tokens.push({ type: 'comment', value: '/*' })
      blockComment = true
      i += 2
      continue
    }

    if (lang === 'python' && (ch === '"' || ch === "'") && next === ch && text[i + 2] === ch) {
      const q = ch
      let j = i + 3
      let end = -1
      while (j < n - 2) {
        if (text[j] === q && text[j + 1] === q && text[j + 2] === q) {
          end = j
          break
        }
        j++
      }
      const stop = end === -1 ? n : end + 3
      tokens.push({ type: 'string', value: text.slice(i, stop) })
      i = stop
      continue
    }

    const lineComment =
      ((lang === 'python' || lang === 'yaml' || lang === 'bash' || lang === 'toml') && ch === '#') ||
      ((lang === 'javascript' || lang === 'typescript' || lang === 'css') && ch === '/' && next === '/') ||
      (lang === 'sql' && ch === '-' && next === '-')
    if (lineComment) {
      let j = i
      while (j < n && text[j] !== '\n') j++
      tokens.push({ type: 'comment', value: text.slice(i, j) })
      i = j
      continue
    }

    if (
      ch === '"' ||
      ch === "'" ||
      (ch === '`' && (lang === 'javascript' || lang === 'typescript' || lang === 'bash'))
    ) {
      const q = ch
      let j = i + 1
      while (j < n) {
        if (text[j] === '\\' && (lang === 'javascript' || lang === 'typescript')) {
          j += 2
          continue
        }
        if (text[j] === q || text[j] === '\n') break
        j++
      }
      const stop = j < n && text[j] === q ? j + 1 : j
      tokens.push({ type: 'string', value: text.slice(i, stop) })
      i = stop
      continue
    }

    if (/[0-9]/.test(ch) || (ch === '.' && /[0-9]/.test(next || ''))) {
      let j = i
      while (j < n && /[0-9a-fA-FxX_.eE+\-]/.test(text[j])) j++
      tokens.push({ type: 'number', value: text.slice(i, j) })
      i = j
      continue
    }

    if (isWordChar(ch)) {
      let j = i
      while (j < n && isWordChar(text[j])) j++
      const nextCh = text[j]
      tokens.push({ type: 'word', value: text.slice(i, j), next: nextCh })
      i = j
      continue
    }

    if (/\s/.test(ch)) {
      let j = i
      while (j < n && /\s/.test(text[j])) j++
      tokens.push({ type: 'plain', value: text.slice(i, j) })
      i = j
      continue
    }

    tokens.push({ type: 'punct', value: ch })
    i++
  }
  return tokens
}

function TokenSpan({ token, lang }: { token: Token; lang: Lang }) {
  switch (token.type) {
    case 'nl':
      return <br />
    case 'comment':
      return (
        <span style={{ color: COLORS.comment, fontStyle: 'italic' }}>{token.value}</span>
      )
    case 'string':
      return <span style={{ color: COLORS.string }}>{token.value}</span>
    case 'number':
      return <span style={{ color: COLORS.number }}>{token.value}</span>
    case 'word': {
      const kw = KEYWORDS[lang]?.includes(token.value) || false
      let color = COLORS.plain
      if (kw) color = COLORS.keyword
      else if (/^[A-Z]/.test(token.value) && lang !== 'sql') color = COLORS.cls
      else if (token.next === '(') color = COLORS.fn
      return <span style={{ color }}>{token.value}</span>
    }
    case 'punct':
      return <span style={{ color: COLORS.punct }}>{token.value}</span>
    default:
      return <span style={{ color: COLORS.plain }}>{token.value}</span>
  }
}

function safeHref(raw: string): string | null {
  const href = raw.trim()
  if (/^(https?:\/\/|mailto:|\/|#)/i.test(href)) return href
  return null
}

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\]\([^)]+\))/g)
  return parts.map((part, index) => {
    const key = `${keyPrefix}-${index}`
    if (!part) return null
    if (part.startsWith('`') && part.endsWith('`') && part.length > 1) {
      return (
        <code key={key} className="rounded bg-[#161b22] px-1 py-0.5 text-[0.9em] text-[#a5d6ff]">
          {part.slice(1, -1)}
        </code>
      )
    }
    if (part.startsWith('**') && part.endsWith('**') && part.length > 3) {
      return <strong key={key} className="font-semibold text-gray-100">{part.slice(2, -2)}</strong>
    }
    if (part.startsWith('*') && part.endsWith('*') && part.length > 2) {
      return <em key={key}>{part.slice(1, -1)}</em>
    }
    const link = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/)
    if (link) {
      const href = safeHref(link[2])
      if (!href) return <span key={key}>{link[1]}</span>
      return (
        <a
          key={key}
          href={href}
          target={href.startsWith('http') ? '_blank' : undefined}
          rel={href.startsWith('http') ? 'noreferrer' : undefined}
          className="text-indigo-300 underline decoration-indigo-500/40 hover:text-indigo-200"
        >
          {link[1]}
        </a>
      )
    }
    return <span key={key}>{part}</span>
  })
}

function splitTableRow(line: string): string[] {
  let row = line.trim()
  if (row.startsWith('|')) row = row.slice(1)
  if (row.endsWith('|')) row = row.slice(0, -1)
  return row.split('|').map((cell) => cell.trim())
}

const TABLE_DIVIDER = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/

export function RenderedMarkdown({ content }: { content: string }) {
  const lines = content.replace(/\r\n/g, '\n').split('\n')
  const blocks: ReactNode[] = []
  let i = 0

  while (i < lines.length) {
    const line = lines[i]

    if (!line.trim()) {
      i++
      continue
    }

    const fence = line.match(/^\s*```(\w*)\s*$/)
    if (fence) {
      const code: string[] = []
      i++
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) {
        code.push(lines[i])
        i++
      }
      i++
      blocks.push(
        <pre
          key={`code-${i}`}
          className="overflow-auto rounded border border-[#1f2937] bg-[#0d1117] p-3 font-mono text-[12px] leading-relaxed text-[#e6edf3]"
        >
          <code>{code.join('\n')}</code>
        </pre>,
      )
      continue
    }

    const heading = line.match(/^(#{1,6})\s+(.*)$/)
    if (heading) {
      const level = heading[1].length
      const sizes: Record<number, string> = {
        1: 'text-2xl mt-4',
        2: 'text-xl mt-4',
        3: 'text-lg mt-3',
        4: 'text-base mt-3',
        5: 'text-sm mt-2 uppercase tracking-wide',
        6: 'text-xs mt-2 uppercase tracking-wide',
      }
      blocks.push(
        <div
          key={`h-${i}`}
          className={`${sizes[level]} font-semibold text-gray-100`}
        >
          {renderInline(heading[2], `h-${i}`)}
        </div>,
      )
      i++
      continue
    }

    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      blocks.push(<hr key={`hr-${i}`} className="my-4 border-[#1f2937]" />)
      i++
      continue
    }

    if (line.trimStart().startsWith('>')) {
      const quote: string[] = []
      while (i < lines.length && lines[i].trimStart().startsWith('>')) {
        quote.push(lines[i].replace(/^\s*>\s?/, ''))
        i++
      }
      blocks.push(
        <blockquote
          key={`quote-${i}`}
          className="border-l-2 border-indigo-500/50 bg-indigo-500/5 px-3 py-1.5 text-gray-300 italic"
        >
          {renderInline(quote.join(' '), `quote-${i}`)}
        </blockquote>,
      )
      continue
    }

    if (line.includes('|') && i + 1 < lines.length && TABLE_DIVIDER.test(lines[i + 1])) {
      const header = splitTableRow(line)
      i += 2
      const rows: string[][] = []
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
        rows.push(splitTableRow(lines[i]))
        i++
      }
      blocks.push(
        <div key={`table-${i}`} className="overflow-auto">
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr className="border-b border-[#2a3441] text-left text-gray-400">
                {header.map((cell, ci) => (
                  <th key={ci} className="px-2 py-1.5 font-semibold">{renderInline(cell, `th-${i}-${ci}`)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, ri) => (
                <tr key={ri} className="border-b border-[#1a2029] align-top">
                  {row.map((cell, ci) => (
                    <td key={ci} className="px-2 py-1.5 text-gray-300">{renderInline(cell, `td-${i}-${ri}-${ci}`)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      )
      continue
    }

    const unordered = line.match(/^(\s*)[-*+]\s+(.*)$/)
    if (unordered) {
      const items: string[] = []
      while (i < lines.length) {
        const match = lines[i].match(/^(\s*)[-*+]\s+(.*)$/)
        if (!match) break
        items.push(match[2])
        i++
      }
      blocks.push(
        <ul key={`ul-${i}`} className="list-disc space-y-1 pl-5">
          {items.map((item, index) => (
            <li key={index}>{renderInline(item, `ul-${i}-${index}`)}</li>
          ))}
        </ul>,
      )
      continue
    }

    const ordered = line.match(/^(\s*)\d+[.)]\s+(.*)$/)
    if (ordered) {
      const items: string[] = []
      while (i < lines.length) {
        const match = lines[i].match(/^(\s*)\d+[.)]\s+(.*)$/)
        if (!match) break
        items.push(match[2])
        i++
      }
      blocks.push(
        <ol key={`ol-${i}`} className="list-decimal space-y-1 pl-5">
          {items.map((item, index) => (
            <li key={index}>{renderInline(item, `ol-${i}-${index}`)}</li>
          ))}
        </ol>,
      )
      continue
    }

    const paragraph: string[] = []
    while (i < lines.length) {
      const current = lines[i]
      if (
        !current.trim() ||
        /^\s*```/.test(current) ||
        /^#{1,6}\s/.test(current) ||
        /^\s*>/.test(current) ||
        /^\s*([-*+]|\d+[.)])\s+/.test(current) ||
        /^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(current) ||
        (current.includes('|') && i + 1 < lines.length && TABLE_DIVIDER.test(lines[i + 1]))
      ) {
        break
      }
      paragraph.push(current.trim())
      i++
    }
    blocks.push(
      <p key={`p-${i}`} className="text-gray-300">
        {renderInline(paragraph.join(' '), `p-${i}`)}
      </p>,
    )
  }

  return <div className="space-y-2.5">{blocks}</div>
}

export default function DeliverablePreview({ url, filename }: { url: string; filename: string }) {
  const { kind, lang } = detectKind(filename)

  if (kind === 'iframe') {
    return (
      <div className="w-full h-full overflow-hidden overscroll-contain">
        <iframe src={url} className="w-full h-full" title={`Preview: ${filename}`} />
      </div>
    )
  }

  return <TextPreview url={url} filename={filename} lang={lang} />
}

function TextPreview({ url, filename, lang }: { url: string; filename: string; lang: Lang }) {
  const [content, setContent] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [wrap, setWrap] = useState(true)

  useEffect(() => {
    let cancelled = false
    setContent(null)
    setError(null)
    fetch(url)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.text()
      })
      .then((t) => {
        if (!cancelled) setContent(t)
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message)
      })
    return () => {
      cancelled = true
    }
  }, [url])

  if (error) {
    return (
      <div className="flex items-center justify-center h-full">
        <p className="text-sm text-red-300">
          Failed to load preview: {error} — use <span className="text-gray-300">Open</span> in the header.
        </p>
      </div>
    )
  }

  if (content === null) {
    return (
      <div className="flex items-center justify-center h-full">
        <p className="text-xs text-gray-500 animate-pulse">Loading {filename}…</p>
      </div>
    )
  }

  const tokens = lang === 'markdown' ? null : tokenize(content, lang)

  if (!tokens) {
    return (
      <div className="w-full h-full overflow-auto overscroll-contain bg-[#0d1117] p-4">
        <RenderedMarkdown content={content} />
      </div>
    )
  }

  return (
    <div className="relative w-full h-full">
      <div className="absolute top-2 right-2 z-10 flex items-center">
        <button
          type="button"
          onClick={() => setWrap((w) => !w)}
          className={`px-2 py-0.5 rounded border text-[10px] transition-colors ${
            wrap
              ? 'border-indigo-500/60 bg-indigo-900/70 text-indigo-200'
              : 'border-gray-700 bg-gray-900/80 text-gray-400 hover:text-gray-200'
          }`}
          title={wrap ? 'Disable line wrapping' : 'Enable line wrapping'}
        >
          Wrap: {wrap ? 'on' : 'off'}
        </button>
      </div>
      <pre
        className={`w-full h-full overflow-auto overscroll-contain m-0 p-4 text-[12px] leading-relaxed ${wrap ? 'whitespace-pre-wrap break-words' : 'whitespace-pre'}`}
        style={{ background: '#0d1117', color: COLORS.plain, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace' }}
      >
        <code>
          {tokens.map((t, idx) => (
            <TokenSpan key={idx} token={t} lang={lang} />
          ))}
        </code>
      </pre>
    </div>
  )
}
