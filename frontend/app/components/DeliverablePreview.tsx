'use client'

import { useEffect, useState } from 'react'

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

function renderMarkdown(content: string) {
  return content.split('\n').map((line, idx) => {
    if (/^\s*#{1,6}\s/.test(line)) {
      return (
        <div key={idx} style={{ color: COLORS.heading, fontWeight: 700 }}>
          {line.replace(/^\s*#{1,6}\s/, '').split('**').map((seg, si) => (
            <span key={si}>{seg}</span>
          ))}
        </div>
      )
    }
    const parts = line.split(/(\*\*[^*]+\*\*|`[^`]+`)/g)
    return (
      <div key={idx}>
        {parts.map((seg, si) => {
          if (seg.startsWith('**') && seg.endsWith('**')) {
            return <span key={si} style={{ color: COLORS.fn, fontWeight: 700 }}>{seg.slice(2, -2)}</span>
          }
          if (seg.startsWith('`') && seg.endsWith('`')) {
            return <span key={si} style={{ color: COLORS.string }}>{seg.slice(1, -1)}</span>
          }
          return <span key={si}>{seg}</span>
        })}
      </div>
    )
  })
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

  return (
    <pre
      className="w-full h-full overflow-auto overscroll-contain m-0 p-4 text-[12px] leading-relaxed"
      style={{ background: '#0d1117', color: COLORS.plain, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace' }}
    >
      {tokens ? (
        <code>
          {tokens.map((t, idx) => (
            <TokenSpan key={idx} token={t} lang={lang} />
          ))}
        </code>
      ) : (
        <code>{renderMarkdown(content)}</code>
      )}
    </pre>
  )
}
