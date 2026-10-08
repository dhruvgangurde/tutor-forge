import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

// Guards for the restyle's colour rules (design/DESIGN-BRIEF.md).
const css = readFileSync(resolve(__dirname, '../index.css'), 'utf8')
const moduleCss = readFileSync(resolve(__dirname, '../features/assessments/assessments.module.css'), 'utf8')

function blocks(source: string) {
  const out: { selector: string; body: string }[] = []
  for (const m of source.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    out.push({ selector: m[1].trim(), body: m[2] })
  }
  return out
}

describe('restyle colour rules', () => {
  it('never puts --tertiary text on --surface-2 (only 4.42:1)', () => {
    const offenders = [...blocks(css), ...blocks(moduleCss)]
      .filter((b) => /background:\s*var\(--(clr-)?surface-2\)/.test(b.body))
      .filter((b) => /(^|[^-])color:\s*var\(--(tertiary|clr-text-dim)\)/.test(b.body))
      .map((b) => b.selector)
    expect(offenders).toEqual([])
    // The legacy "dim" alias points at --muted, so old rules cannot do it either.
    expect(css).toMatch(/--clr-text-dim:\s*var\(--muted\)/)
  })

  it('has no literal colours outside the token block', () => {
    const outsideRoot = css.replace(/:root\s*\{[\s\S]*?\n\}/, '')
    const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '')
    const literal = /#[0-9a-fA-F]{3,8}\b|rgba?\(|gradient\(/
    expect(strip(outsideRoot)).not.toMatch(literal)
    expect(strip(moduleCss)).not.toMatch(literal)
  })

  it('draws inputs on --input with a --line-strong border', () => {
    const field = blocks(css).find((b) => b.selector === '.field-input')!
    expect(field.body).toMatch(/background:\s*var\(--input\)/)
    expect(field.body).toMatch(/border:\s*1px solid var\(--line-strong\)/)
  })

  it('lets the sidebar background run the full height (only its contents stick)', () => {
    const sidebar = blocks(css).find((b) => b.selector === '.sidebar')!
    const inner = blocks(css).find((b) => b.selector === '.sidebar-inner')!
    expect(sidebar.body).not.toMatch(/height:\s*100vh/)
    expect(blocks(css).find((b) => b.selector === '.app-shell--sidebar')!.body).toMatch(/align-items:\s*stretch/)
    expect(inner.body).toMatch(/position:\s*sticky/)
  })

  it('sets the wordmark all in ink, in the serif', () => {
    const wordmark = blocks(css).find((b) => b.selector === '.wordmark')!
    expect(wordmark.body).toMatch(/font-family:\s*var\(--font-serif\)/)
    expect(wordmark.body).toMatch(/color:\s*var\(--ink\)/)
    expect(css).not.toMatch(/\.sidebar-logo span/)
  })
})
