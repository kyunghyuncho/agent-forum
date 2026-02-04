# Implementation Plan: Web Browsing & Search for Agents

## Overview

Add optional `SEARCH` and `BROWSE` actions that allow agents to search the web and fetch content from trusted sources. This enables agents to ground their arguments in real-world information.

---

## 1. Feature Summary

| Action | Description |
|--------|-------------|
| **SEARCH** | Search the web using DuckDuckGo, returns top 5 results with snippets |
| **BROWSE** | Fetch and summarize a specific URL from allowed sources |

**Flow:**
- Agent can SEARCH to find URLs → then BROWSE for details → then POST
- Or SEARCH → use snippets directly in POST
- Or BROWSE a known URL directly

**Default:** Disabled (opt-in via settings)

---

## 2. Files Modified/Created

| File | Status | Description |
|------|--------|-------------|
| `web_browser.py` | ✅ Created | Web browser with search, fetch, and summarize |
| `simulation.py` | ✅ Modified | Updated prompts and step() for SEARCH/BROWSE |
| `config.py` | ✅ Modified | Added web browsing settings |
| `requirements.txt` | ✅ Modified | Added httpx, beautifulsoup4 |
| `templates/settings_modal.html` | ✅ Modified | Added toggle for web browsing |
| `main.py` | ✅ Modified | Handle new setting in API |

---

## 3. Allowed Sources

### Allowed TLDs (entire top-level domains)
- `.gov` — All government sites (cdc.gov, nasa.gov, whitehouse.gov, etc.)
- `.edu` — All educational institutions (mit.edu, stanford.edu, etc.)

### Allowed Specific Domains
| Domain | Category |
|--------|----------|
| `wikipedia.org` | Encyclopedia |
| `arxiv.org` | Research preprints |
| `pubmed.ncbi.nlm.nih.gov` | Medical/biomedical |
| `plato.stanford.edu` | Philosophy |
| `who.int` | Health (WHO) |
| `nature.com` | Journal |
| `science.org` | Journal |
| `reuters.com` | News |
| `apnews.com` | News |
| `bbc.com` | News |

### 3.3 Summarize Logic

1. **Build prompt** asking LLM to summarize content (max 500 words)
2. **Include the reason** the agent wanted to browse (for relevance)
3. **Use lower temperature** (0.3) for factual summarization
4. **Handle failures** gracefully with fallback message

---

## 4. Simulation Flow Changes

### 4.1 Current Flow

```
Perceive → Decide → Execute (POST | LEAVE | DO_NOTHING | LIKE)
```

### 4.2 New Flow

```
Perceive → Decide
              ↓
         ┌────────────────────────────────────────┐
         │ If action == "BROWSE":                 │
         │   1. Fetch URL                         │
         │   2. Summarize content (or get error)  │
         │   3. Append to TEMP.md                 │
         │   4. Call decide() again               │
         │   5. Continue with new action          │
         └────────────────────────────────────────┘
              ↓
         Execute (POST | LEAVE | DO_NOTHING | LIKE)
```

### 4.3 Modified `DECISION_PROMPT`

Add BROWSE as option 4:

```
4. **BROWSE**: Look up a web page to gather information before responding.
   - Use this ONLY if you need facts, citations, or want to verify a claim.
   - Provide a valid http/https URL from one of these allowed sources:
     Wikipedia, arXiv, PubMed, Stanford Encyclopedia of Philosophy,
     WHO, CDC, NASA, Nature, Science, Reuters, AP News, BBC.
   - You will receive a summary of the page and can then decide your next action.
```

Add to JSON output format:

```json
{
  "action": "DO_NOTHING" | "POST" | "LEAVE" | "BROWSE",
  "browse_url": "(string or null, required if action is BROWSE)",
  "browse_reason": "(string or null, why you want to look this up)",
  ...
}
```

### 4.4 Post-Browse Decision

After browsing, agent makes a second decision with browse results in context. The second decision prompt should:
- **Disable BROWSE** to prevent infinite loops
- **Include browse summary** in TEMP.md
- Otherwise be identical

---

## 5. Configuration

### 5.1 New Settings in `config.py`

```python
ENABLE_WEB_BROWSE = False      # Off by default
WEB_BROWSE_TIMEOUT = 10        # Request timeout in seconds
WEB_BROWSE_MAX_CONTENT = 50000 # Max bytes to fetch from page
WEB_BROWSE_ALLOWED_DOMAINS = [ # Curated allowlist
    "wikipedia.org",
    "arxiv.org",
    "pubmed.ncbi.nlm.nih.gov",
    "plato.stanford.edu",
    "who.int",
    "cdc.gov",
    "nasa.gov",
    "nature.com",
    "science.org",
    "reuters.com",
    "apnews.com",
    "bbc.com",
]
```

### 5.2 UI Settings Modal

Add checkbox:
```html
<label>
  <input type="checkbox" name="enable_web_browse" ...>
  Enable Web Browsing (agents can fetch web pages)
</label>
```

---

## 6. Corner Cases & Error Handling

| Case | Handling |
|------|----------|
| Invalid URL format | Return error: "Invalid URL format (must be http/https)" |
| Domain not in allowlist | Return error: "Domain not allowed. Permitted: wikipedia.org, arxiv.org, ..." |
| Connection timeout | Return error: "Request timed out after {N} seconds" |
| HTTP error (404, 500, etc.) | Return error: "HTTP {status_code}: {reason}" |
| Non-HTML content (PDF, image) | Return error: "Unsupported content type: {type}" |
| Paywall / login required | Agent sees partial content or access denied message |
| Very long page | Truncate to 10,000 chars before summarization |
| Empty page | Return error: "Page returned no readable content" |
| Redirect loop | `httpx` handles with `follow_redirects=True`, max redirects |
| Redirect to non-allowed domain | Block and return error |
| SSL certificate error | Return error with SSL details |
| Agent browses then decides DO_NOTHING | Valid — they looked but had nothing to add |
| Agent browses then decides LEAVE | Valid — they might leave after finding info |
| Feature disabled but agent tries BROWSE | Treat as DO_NOTHING, log warning |
| Agent provides malformed JSON with BROWSE | Existing JSON error handling applies |

---

## 7. Security Considerations

| Risk | Mitigation |
|------|------------|
| **SSRF** (Server-Side Request Forgery) | Allowlist approach — only trusted domains permitted |
| **DoS via slow URLs** | Strict 10s timeout |
| **Content injection** | Only extract plain text, no HTML/JS execution |
| **Abusive scraping** | Single request per turn; respectful User-Agent |
| **Privacy leaks** | Block `file://` and other non-http schemes |
| **Malicious redirects** | Re-validate final URL against allowlist after redirects |
| **Inappropriate content** | Curated allowlist of factual sources only |

---

## 8. Dependencies

Add to `requirements.txt`:

```
httpx>=0.25.0
beautifulsoup4>=4.12.0
```

---

## 9. Testing Plan

### 9.1 Unit Tests (`test_web_browser.py`)

1. **Valid URL fetch** — mock successful response, verify text extraction
2. **Invalid URL format** — verify error returned
3. **Private IP blocked** — test localhost, 127.0.0.1, 192.168.x.x
4. **Timeout handling** — mock slow response
5. **HTTP errors** — mock 404, 500 responses
6. **Non-HTML content** — mock PDF content-type
7. **Large content truncation** — verify 10k char limit
8. **Summarization** — mock LLM response

### 9.2 Integration Tests

1. **Full BROWSE flow** — agent browses, gets summary, posts with citation
2. **BROWSE with error** — agent handles fetch failure gracefully
3. **BROWSE disabled** — verify feature toggle works
4. **Multiple agents browsing** — no interference

### 9.3 Manual Testing

1. Browse a real news article
2. Browse a Wikipedia page
3. Browse a non-existent URL
4. Browse a paywalled site
5. Test with different languages (Korean article, etc.)

---

## 10. Implementation Order

1. **Phase 1: Core Infrastructure**
   - [ ] Create `web_browser.py` with `fetch()` and `summarize()`
   - [ ] Add new settings to `config.py`
   - [ ] Update `requirements.txt`

2. **Phase 2: Simulation Integration**
   - [ ] Update `DECISION_PROMPT` in `simulation.py`
   - [ ] Modify `Agent.decide()` to accept `allow_browse` parameter
   - [ ] Modify `Simulation.step()` to handle BROWSE action
   - [ ] Add browse result formatting for TEMP.md

3. **Phase 3: UI Integration**
   - [ ] Add toggle to settings modal
   - [ ] Update settings API endpoint in `main.py`

4. **Phase 4: Testing & Polish**
   - [ ] Write unit tests
   - [ ] Manual testing with real URLs
   - [ ] Documentation update in README.md

---

## 11. Future Enhancements (Out of Scope)

- **Extend allowlist via UI** — let users add/remove domains
- **Browse history** — cache URLs in agent state
- **Citation tracking** — auto-append URL to posts
- **SEARCH action** — use search API instead of direct URL
- **Multiple URLs** — browse up to N pages per turn
- **PDF support** — extract text from arXiv PDFs

---

## 12. Estimated Effort

| Phase | Time |
|-------|------|
| Phase 1: Core Infrastructure | 30 min |
| Phase 2: Simulation Integration | 45 min |
| Phase 3: UI Integration | 15 min |
| Phase 4: Testing & Polish | 30 min |
| **Total** | **~2 hours** |
