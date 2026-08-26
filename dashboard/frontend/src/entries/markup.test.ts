import { describe, expect, it } from 'vitest';

import {
  ansiHtml,
  markdownHtml,
  plainTextHtml,
  sourceHtml,
  unifiedDiffHtml,
} from './markup';

describe('trusted entry markup', () => {
  it('escapes plain text', () => {
    expect(plainTextHtml('<script>alert(1)</script>').value).toBe(
      '<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>',
    );
  });

  it('renders supported markdown without trusting raw HTML', () => {
    const rendered = markdownHtml(
      '# Heading\n\n**bold** and [safe](https://example.com)\n\n<img src=x>',
    ).value;

    expect(rendered).toContain('<h3>Heading</h3>');
    expect(rendered).toContain('<strong>bold</strong>');
    expect(rendered).toContain('href="https://example.com"');
    expect(rendered).toContain('&lt;img src=x&gt;');
    expect(rendered).not.toContain('<img');
  });

  it('does not make non-http links active', () => {
    expect(markdownHtml('[bad](javascript:alert(1))').value).not.toContain(
      'href=',
    );
  });

  it('escapes ANSI text while retaining supported terminal styles', () => {
    const rendered = ansiHtml('\u001b[31mred <tag>\u001b[0m plain').value;

    expect(rendered).toContain('color:rgb(224,108,117)');
    expect(rendered).toContain('red &lt;tag&gt;');
    expect(rendered.endsWith(' plain')).toBe(true);
  });

  it('renders source and unified diffs with escaped content', () => {
    expect(sourceHtml('first\n<tag>\n', 'notes.txt').value).toContain(
      '<span class="ln">2</span><span class="tx">&#x3C;tag></span>',
    );
    const diff = unifiedDiffHtml(
      '@@ -1,1 +1,1 @@\n-old <tag>\n+new <tag>\n',
      'notes.txt',
    ).value;
    expect(diff).toContain('class="dl removed"');
    expect(diff).toContain('class="dl added"');
    expect(diff).toContain('aria-label="removed line 1"');
    expect(diff).toContain('aria-label="added line 1"');
    expect(diff).toContain('<span class="dm" aria-hidden="true">−</span>');
    expect(diff).toContain('<span class="dm" aria-hidden="true">+</span>');
    expect(diff).toContain('&#x3C;tag>');
    expect(diff).not.toContain('<tag>');
  });

  it('colors source and diff code from the file path', () => {
    const source = sourceHtml(
      'const ready: boolean = true;\n',
      'frontend/state.ts',
    ).value;
    const diff = unifiedDiffHtml(
      '@@ -1,1 +1,1 @@\n-const ready = false;\n+const ready = true;\n',
      'frontend/state.ts',
    ).value;

    expect(source).toContain('<span class="token keyword">const</span>');
    expect(source).toContain('<span class="token boolean">true</span>');
    expect(diff).toContain('<span class="token keyword">const</span>');
    expect(diff).toContain('<mark class="changed">fals</mark>');
    expect(diff).toContain('<mark class="changed">tru</mark>');
  });

  it('colors script lines from a Svelte diff', () => {
    const diff = unifiedDiffHtml(
      '@@ -1,1 +1,1 @@\n-let open = false;\n+let open = true;\n',
      'frontend/App.svelte',
    ).value;

    expect(diff).toContain('<span class="token keyword">let</span>');
    expect(diff).toContain(
      '<span class="token boolean"><mark class="changed">tru</mark>e</span>',
    );
  });
});
