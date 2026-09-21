# Extraction Ladder (T2 scraper tier)

The T2 tier is **not a fetch**. A URL that returns HTTP 200 is not a grounded
source — a SPA landing page can return 200 with 688 chars of marketing copy and
*no* concept text. So T2 has an internal extraction ladder, and the verifier
measures the **extracted** result, not the HTTP status.

```
  URL
   │
   ├─► 1. trafilatura          article-scoped text + title/author/date
   │        │  (most static sites, incl. Wikipedia, arXiv, .edu)
   │        │
   │        └─► 2. raw-HTML strip   fallback; drops <script>/<style>, tags
   │                 │
   │                 └─► verdict:  body_len + needs_render
   │
   └─► 3. browser render        SPA fallback (P0.5)
            BrowserOS neo MCP / headless Chromium
            → re-extract from the rendered DOM
```

The verdict (`body_len`, `needs_render`, `http_status`, `method`, `author`) is
what the grounding check consumes. See [VERIFIER-CONTRACT.md](VERIFIER-CONTRACT.md)
§2 for the `MIN_GROUNDING_CHARS` rule.

---

## 1. trafilatura (primary)

`trafilatura.fetch_url(url)` → raw HTML (raises `HTTPError` on 4xx/5xx; we recover
the code). `trafilatura.extract(html, include_tables=True, include_formatting=False)`
→ clean article markdown.

**Why trafilatura over BeautifulSoup:** article-focused, ignores nav/boilerplate,
and gives `title` / `author` / `date` metadata for free (from `<meta>` tags).
It is the right tool for "give me the article body," which is exactly what a
grounding source is.

**Proven (PoC run):** Wikipedia pages yield 13k–40k chars; Preskill Phys 229
index 5.4k; Nielsen & Chuang arXiv abstract 1.6k. All correct.

**Limit:** it sees only the **server-rendered** HTML. SPAs return a shell.

## 2. raw-HTML strip (fallback)

`urllib` fetch → drop `<script>`/`<style>` → strip tags → collapse whitespace.
Used when trafilatura returns nothing but the page *is* static. The result is
noisier than trafilatura but beats nothing.

If the stripped body is < 200 chars, the source is flagged `needs_render=True` —
the ladder has done what it can without a browser.

## 3. browser render (SPA fallback, **P0.5**)

For client-side-rendered pages (SPAs), the only way to get the concept text is a
real browser. Two viable engines:

### BrowserOS neo MCP (preferred — logged-in, user's real browser)

- App runs on `127.0.0.1:9010/mcp`.
- **Page ownership is per-MCP-session** — a fresh connection per tool call loses
  page ids. So the whole flow runs in **one session / one process**.
- Pattern: `tabs new` → `navigate` → `wait` → `read` (markdown) → `pages close`,
  all inside one `run` script (see `neo_batch.py` / the `browseros-neo` skill).
- `read format=markdown` returns rendered DOM text — the SPA content.

### Headless Chromium (self-contained, no user browser)

- `playwright` / `puppeteer` launch a headless Chromium.
- Navigate → `wait_for_selector('main')` → `page.content()` → extract.
- Good for CI / unattended runs where the user's browser isn't available.

**Decision (P0.5):** wire the browser-render step into `scraper.extract()` as the
third rung, gated on `needs_render`. Verifier unchanged — it still measures the
extracted body against `MIN_GROUNDING_CHARS`. A rendered SPA body that clears the
bar promotes the source `needs-render` → `live`.

---

## 4. What the ladder does NOT do

- **It does not find sources.** Source *discovery* is the LLM + web-search step
  (Designer stage 3). The ladder only *extracts* a URL the generator already
  chose.
- **It does not guarantee content.** Some "authoritative" sources are
  auth-walled or pure landing pages (IBM Quantum Learning's module pages
  redirect to a marketing shell even when rendered). The ladder honestly reports
  `needs_render` / thin — the verifier then refuses to let it ground a node.

That honesty — "I fetched it, it's thin, here's the char count" — is the entire
point of the tier.

---

## 5. Empirical results (current PoC run)

| Source | Method | Extracted chars | Grounds a node? |
|---|---|---|---|
| Preskill Phys 229 | trafilatura | 5,437 | ✅ yes |
| Nielsen & Chuang (arXiv abs) | trafilatura | 1,604 | ⚠️ below 2500 bar — secondary |
| IBM Quantum Learning | trafilatura | 688 | ❌ SPA shell — `needs-render` (P0.5) |
| Wikipedia: Qubit | trafilatura | 26,589 | ✅ yes |
| Wikipedia: Quantum gate | trafilatura | 40,000 | ✅ yes |
| Wikipedia: Bell state | trafilatura | 14,508 | ✅ yes |
| Wikipedia: Grover's algorithm | trafilatura | 20,689 | ✅ yes |

Every quantum node is still **grounded** because each has a substantive Wikipedia
or Preskill source carrying it. The thin ones are correctly demoted to secondary —
exactly the behavior the `MIN_GROUNDING_CHARS` rule was added to produce.
