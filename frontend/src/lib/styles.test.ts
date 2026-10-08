import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'
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
      .filter((b) => /(^|[^-])color:\s*var\(--tertiary\)/.test(b.body))
      .map((b) => b.selector)
    expect(offenders).toEqual([])
  })

  it('never truncates the session course name', () => {
    const course = blocks(css).find((b) => b.selector === '.session-course')!
    expect(course.body).not.toMatch(/text-overflow|white-space:\s*nowrap|max-width/)
    expect(course.body).toMatch(/overflow-wrap:\s*anywhere/)
  })

  it('keeps the input tokens under browser autofill (WebKit and Firefox)', () => {
    const webkit = blocks(css).find((b) => b.selector.startsWith('.field-input:-webkit-autofill,'))!
    expect(webkit.body).toMatch(/-webkit-text-fill-color:\s*var\(--ink\)/)
    expect(webkit.body).toMatch(/inset/)
    expect(webkit.body).toMatch(/var\(--input\)/)
    const firefox = blocks(css).find((b) => b.selector === '.field-input:autofill')!
    expect(firefox.body).toMatch(/background:\s*var\(--input\)/)
  })

  it('centres the narrow page columns inside the content area', () => {
    expect(blocks(css).find((b) => b.selector === '.not-found')!.body).toMatch(/margin:\s*0 auto/)
    expect(blocks(moduleCss).find((b) => b.selector === '.readingColumn')!.body).toMatch(/margin:\s*0 auto/)
  })

  it('keeps table scores on one line in lining figures', () => {
    const td = blocks(css).find((b) => b.selector === '.data-table td')!
    expect(td.body).toMatch(/font-variant-numeric:\s*lining-nums tabular-nums/)
    expect(blocks(css).find((b) => b.selector === '.data-table .cell-score')!.body).toMatch(/white-space:\s*nowrap/)
    const list = readFileSync(resolve(__dirname, '../features/progress/CourseProgressList.tsx'), 'utf8')
    expect(list).toMatch(/className="table-scroll"/)
    expect(list).toMatch(/className="cell-score"/)
  })

  it('has retired every temporary --clr-* alias', () => {
    // Restyle phase 3 follow-up: all uses point at the real tokens now.
    expect(css).not.toMatch(/--clr-/)
    expect(moduleCss).not.toMatch(/--clr-/)
  })

  it('has no literal colours outside the token block', () => {
    const outsideRoot = css.replace(/:root\s*\{[\s\S]*?\n\}/, '')
    const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '')
    const literal = /#[0-9a-fA-F]{3,8}\b|rgba?\(|gradient\(/
    expect(strip(outsideRoot)).not.toMatch(literal)
    expect(strip(moduleCss)).not.toMatch(literal)
  })

  it('draws inputs on --input with a --field-border edge (>=3:1, phase 6)', () => {
    const field = blocks(css).find((b) => b.selector === '.field-input')!
    expect(field.body).toMatch(/background:\s*var\(--input\)/)
    expect(field.body).toMatch(/border:\s*1px solid var\(--field-border\)/)
  })

  it('lets the sidebar background run the full height (only its contents stick)', () => {
    const sidebar = blocks(css).find((b) => b.selector === '.sidebar')!
    const inner = blocks(css).find((b) => b.selector === '.sidebar-inner')!
    expect(sidebar.body).not.toMatch(/height:\s*100vh/)
    expect(blocks(css).find((b) => b.selector === '.app-shell--sidebar')!.body).toMatch(/align-items:\s*stretch/)
    expect(inner.body).toMatch(/position:\s*sticky/)
  })

  it('uses no Tailwind-style class names anywhere (Tailwind is not installed)', () => {
    const files: string[] = []
    const walk = (dir: string) => {
      for (const name of readdirSync(dir)) {
        const path = join(dir, name)
        if (statSync(path).isDirectory()) walk(path)
        else if (path.endsWith('.tsx') && !path.includes('.test.')) files.push(path)
      }
    }
    walk(resolve(__dirname, '..'))
    const utility =
      /(^|\s)(space-[xy]-\d|flex|items-(center|start|end)|justify-(between|center|end|start)|text-(xs|sm|base|lg|xl|\dxl|center|left|right|white|(gray|red|green|blue)-\d+)|m[tbxylr]?-\d+|p[xytblr]?-\d+|rounded(-\w+)?|bg-\w+|grid-cols-\d+|gap-\d+|font-(bold|semibold|medium)|w-(full|\d+)|mx-auto|hover:\S+|disabled:\S+)(\s|$)/
    const offenders = files.flatMap((f) =>
      [...readFileSync(f, 'utf8').matchAll(/className="([^"]*)"/g)]
        .filter((m) => utility.test(m[1]))
        .map((m) => `${f.split(/[\\/]src[\\/]/)[1]}: ${m[1]}`)
    )
    expect(offenders).toEqual([])
  })

  it('sets the wordmark all in ink, in the serif', () => {
    const wordmark = blocks(css).find((b) => b.selector === '.wordmark')!
    expect(wordmark.body).toMatch(/font-family:\s*var\(--font-serif\)/)
    expect(wordmark.body).toMatch(/color:\s*var\(--ink\)/)
    expect(css).not.toMatch(/\.sidebar-logo span/)
  })
})

describe('motion, text size and fonts (phase 6)', () => {
  const reduceBlocks = [...css.matchAll(/@media \(prefers-reduced-motion: reduce\) \{([\s\S]*?)\n\}/g)]
    .map((m) => m[1])
    .join('\n')

  it('stills every looping animation under prefers-reduced-motion', () => {
    expect(reduceBlocks).toMatch(/\.skeleton\s*\{\s*animation:\s*none/)
    expect(reduceBlocks).toMatch(/\.thinking-dots\s*\{\s*display:\s*none/)
    expect(reduceBlocks).toMatch(/\.spinner\s*\{\s*animation:\s*none/)
    // And the global net: transitions and any other animation become instant.
    expect(reduceBlocks).toMatch(/transition-duration:\s*0\.01ms\s*!important/)
    expect(reduceBlocks).toMatch(/scroll-behavior:\s*auto\s*!important/)
  })

  it("respects the reader's browser text size (root size is a percentage, not px)", () => {
    const html = blocks(css).find((b) => b.selector === 'html')!
    expect(html.body).toMatch(/font-size:\s*100%/)
  })

  it('serves Lora (display text) locally with font-display: block, never from a CDN', () => {
    const lora = readFileSync(resolve(__dirname, '../fonts/lora-600.css'), 'utf8')
    expect(lora).not.toMatch(/font-display:\s*swap/)
    expect(lora.match(/font-display:\s*block/g)?.length).toBeGreaterThan(0)
    expect(lora).not.toMatch(/https?:\/\//)
    const main = readFileSync(resolve(__dirname, '../main.tsx'), 'utf8')
    expect(main).toMatch(/import '\.\/fonts\/lora-600\.css'/)
    expect(main).not.toMatch(/@fontsource\/lora\/600\.css/)
    const html = readFileSync(resolve(__dirname, '../../index.html'), 'utf8')
    expect(html).not.toMatch(/fonts\.googleapis|fonts\.gstatic|cdn\./)
  })

  it('defines no unused design tokens', () => {
    for (const t of ['--shadow-sm', '--space-16', '--radius-xl']) expect(css).not.toContain(t + ':')
  })
})
