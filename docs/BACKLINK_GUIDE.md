# Backlink verification (`redirecthunter bl-check` / `bl-chain`)

## Quick reference: input row formats

```
https://source.example.com/post                                    # plain URL, checked against -d/--domain
https://source.example.com/post|target.example.com                 # per-row target override
https://source.example.com/post|a.example.com;b.example.com         # multiple acceptable targets
account_001|https://source.example.com/post                        # account-scoped session (see --accounts-file)
account_001|https://source.example.com/post|target.example.com     # account-scoped + target override
```

Separate from `scan`/`crawl`. Different question, different tool:

- **`redirecthunter scan`** answers *"does this URL redirect to my
  target?"* — HTTP `Location` headers, meta-refresh, JS redirects.
- **`redirecthunter bl-check`** answers *"does this page's rendered
  content contain a real outbound link to my target?"* — the actual
  question when auditing backlinks like a GitHub profile, a Disqus
  "about" page, or a blog post that's supposed to link back to you.
- **`redirecthunter bl-chain`** runs that same check across a *tiered*
  (pyramid) link structure in one pass — see
  ["Chained/tiered audits"](#chained-tiered-audits-redirecthunter-bl-chain)
  below.

```bash
redirecthunter bl-check examples/backlink.txt -d medilana.id
redirecthunter bl-stats                                        # list all runs
redirecthunter bl-show <backlink_id> --confirmed                # just the confirmed matches
redirecthunter bl-export <backlink_id> -o report.csv
```

Every run persists to `redirecthunter.db` (same as `scan`/`crawl`), so a
run can be revisited later with `bl-stats`/`bl-show`/`bl-export` instead
of re-running the check to see the results again.

## Static HTML vs. a real rendered browser (`--browser`)

By default `bl-check` fetches with a plain HTTP GET (`httpx`), which
never executes JavaScript. That's correct for the majority of sites
(anything server-rendered: blogs, GitHub, Blogger, forums, CMS profile
pages) and is the faster, lighter default. Pass `--browser` to render
with a real headless Chromium (Playwright) instead — needed for
JavaScript single-page apps (SPAs) where the real DOM is built by
client-side JS *after* the page loads, so a plain GET only ever sees the
raw, pre-JS HTML: YouTube, Disqus, X/Twitter, Instagram, Threads, and
similar.

```bash
# 1. Fast pass — no browser required (the default)
redirecthunter bl-check examples/backlink.txt -d medilana.id

# 2. Browser install, once
pip install "redirecthunter[js]"
playwright install chromium

# 3. Re-check just the ambiguous SPA rows with a real rendered browser
redirecthunter bl-check examples/backlink.txt -d medilana.id --browser
```

The base install (`pip install redirecthunter`) never needs Playwright —
the `js` extra and its ~300MB Chromium download are deliberately opt-in,
only pulled in when `--browser` is actually used.

`--browser` mode is much heavier per URL (a real page load, not a
lightweight request), so `-c/--concurrency`'s default is lower
automatically in this mode (`4` browser tabs vs. `8` HTTP connections) —
override with `-c` if you want something else. `--headed` shows the
browser window instead of running headless, useful for debugging a row
that's still not matching; it only makes sense together with `--browser`.

## Per-row target override

Any row can pin its own target instead of using the run's `-d/--domain`
default: append `|target` to a TXT line
(`https://blog.example.com/post|medilana.com`), or fill a `target`
column (CSV) / `"target"` key (JSON) for that row. A row can also list
*several* acceptable targets, separated by `;`:

```
https://blog.example.com/post|medilana.co.id;form.medilana.com;img.medilana.my.id
```

That row is confirmed as a match if the page links to *any* of the
listed targets — useful for a placement that might legitimately link to
a brand's main domain, a subdomain, or a country-variant domain.
`bl-show`'s `matched_target` column reports which one actually matched.
A row where every entry is blank falls back to `-d/--domain` as if it
had no override at all.

## Account-scoped sessions (`--accounts-file`)

Some login-walled platforms need more than one session at once -- e.g.
auditing placements posted from 30 different social-media accounts on
the same platform, where each placement's row needs its *own* Cookie,
not one shared session for the whole run. `--accounts-file` is a
registry of `account_id -> headers`, paired with input rows prefixed
`account_id|URL` (or an `account_id` column/key for CSV/JSON).

**`accounts.txt` format** — `account_id|Name: Value`, one header per
line. Repeat the same `account_id` on further lines to register more
than one header for that account:

```
account_001|Cookie: session=xxxxx
account_001|User-Agent: Mozilla/5.0
account_057|Cookie: session=yyyyy
```

See `examples/bl-check-accounts.txt` for a ready-to-copy template. Fill
in real values (and uncomment the lines) before running -- as shipped,
the example file has every account commented out, so it won't actually
satisfy a run that references `account_001`/`account_002` until you do.

**Input file format** — prefix any TXT row with `account_id|`:

```
account_001|https://social-platform.example.com/profile/brand-1
account_057|https://social-platform.example.com/profile/brand-2
```

This can combine with a per-row `|target` override, in that order —
`account_id|URL|target`:

```
account_001|https://social-platform.example.com/profile/brand-1|medilana.co.id
```

A row without the `account_id|` prefix is checked as a normal,
unauthenticated request, even if its host also appears in the
`--accounts-file` registry under a different row's account.

**Account not found is a hard error, not a silent fallback.** If any
row references an `account_id` that isn't in the `--accounts-file`
registry, `bl-check`/`bl-chain` refuse to start (exit code 1) and list
every missing `account_id` — before a single request goes out. This is
deliberate: silently falling back to an anonymous request for a typo'd
`account_id` would look, from the report, exactly like a legitimate
"requires login" result, hiding the real cause.

**An account registered with no headers is valid and intentional.** A
bare `account_id|` line (nothing after the `|`) explicitly registers
that account with zero headers — different from the account never
appearing in the file. Use this to document "this account is
intentionally checked without any special headers" rather than leaving
it out and triggering the missing-account error above.

```bash
redirecthunter bl-check examples/backlink-sample.txt -d medilana.id \
  --accounts-file examples/bl-check-accounts.txt
```

(As shipped, `examples/bl-check-accounts.txt` has every account
commented out, so this exact command errors with a "missing account_id"
message until you fill in real, uncommented entries for `account_001`/
`account_002` in your own copy.)

## Presets (`--config`)

`bl-check`/`bl-chain` read presets from the *same* `redirecthunter.yaml`
file `scan` already auto-discovers, under their own top-level keys
(`bl_check:` / `bl_chain:`) so a project only ever needs one config file
to remember, not one per command. This means long-lived flags -- `-d`,
`--accounts-file`, `-c`, and the rest -- don't need retyping on every
run.

```yaml
bl_check:
  domain: medilana.id
  accounts_file: examples/bl-check-accounts.txt
  concurrency: 8
  exact: false
  strict: false

bl_chain:
  domain: medilana.id
  accounts_file: examples/bl-check-accounts.txt
  require_confirmed_parent: false
```

Every `BacklinkCheckConfig`/`BacklinkChainConfig` field can be preset
this way: `domain`, `accounts_file`, `concurrency`, `timeout`, `exact`,
`strict`, `user_agent`, `browser`, `headed`, `nav_timeout`,
`render_wait`, `label`, `database`. `bl-chain` additionally accepts
`require_confirmed_parent`; `tier_paths` is never read from YAML --
tier order is always given on the command line.

**Priority: CLI flag > config file > built-in default.** A `-d` on the
command line always wins over `bl_check.domain`/`bl_chain.domain` in the
file, which in turn wins over the field's own default.

```bash
# Auto-discovered redirecthunter.yaml in the current directory:
redirecthunter bl-check backlinks.txt

# Explicit path:
redirecthunter bl-check backlinks.txt --config path/to/redirecthunter.yaml

# CLI overrides the file's domain, everything else still comes from it:
redirecthunter bl-check backlinks.txt -d other-domain.id --config path/to/redirecthunter.yaml
```

See `examples/redirecthunter.yaml` for a complete, working example of
both sections.

## Chained/tiered audits (`redirecthunter bl-chain`)

Backlink audits are often structured in **tiers** (a "link pyramid"):
tier 1 is a set of pages that should link straight to your domain, tier
2 is a set of pages that should link to tier 1's URLs, and so on. Running
`bl-check` once per tier and manually deriving each tier's target list
from the previous tier's results is tedious and easy to get wrong —
`bl-chain` does the whole thing in one command:

```bash
redirecthunter bl-chain examples/bl-chain-tier1.txt examples/bl-chain-tier2.txt -d medilana.id --accounts-file examples/bl-check-accounts.txt
redirecthunter bl-chain examples/bl-chain-tier1.txt examples/bl-chain-tier2.txt examples/bl-chain-tier3.txt -d medilana.id --require-confirmed-parent --accounts-file examples/bl-check-accounts.txt
```

`--accounts-file` is needed here because `examples/bl-chain-tier1.txt`
includes one `account_id|URL` row -- and, since the shipped
`bl-check-accounts.txt` ships with every account commented out, these
exact commands still error with a "missing account_id" message until
you fill in and uncomment a real `account_001` entry in your own copy.

- **Tier 1** is checked against `-d/--domain`, exactly like a plain
  `bl-check`.
- **Tier N (N > 1)** is, by default, checked against the set of
  hostnames extracted from tier N-1's own *input* URLs — every row, not
  just the ones tier N-1 confirmed. Pass `--require-confirmed-parent` to
  restrict that derived set to only the rows tier N-1 actually confirmed
  a match for — the stricter, "prove the whole chain" reading.
- A row's own per-row `|target` override (above) still wins over either
  derived default, within any tier.

Each tier is persisted as an ordinary `bl-check` run with its own
`backlink_id`, so `bl-stats`/`bl-show`/`bl-export` work on any individual
tier exactly like they do for a plain `bl-check` run — there's no
separate `bl-chain-show`/`bl-chain-export`. `bl-chain` itself prints a
combined summary table across all tiers when the run finishes.

`bl-chain` shares `bl-check`'s `--browser`/`--headed`/`--exact`/
`--strict`/`--accounts-file`/`--config` options, applied per tier — one
`--accounts-file` registry is shared across every tier (see
[Account-scoped sessions](#account-scoped-sessions-accounts-file)
above). See [`CLI_REFERENCE.md#bl-chain`](./CLI_REFERENCE.md#bl-chain)
for the full flag reference.

## CSV / JSON columns (`bl-export`)

| Column | Meaning |
|---|---|
| `source_url` | The URL as read from the input file. |
| `final_url` | Where the request actually ended up, after any redirects. |
| `status_code` | HTTP status of the final response (`999` = LinkedIn's non-standard anti-bot code, not a real status). |
| `match_found` | `True` for any positive `match_type` below except `not_found`. |
| `match_type` | See table below. |
| `matched_href` | The raw `href` that matched, if `match_type` is anchor-based. |
| `matched_target` | Which of a multi-target `|target` row's entries actually matched (see "Per-row target override" above). |
| `rel` | The matched anchor's `rel` attribute (e.g. `nofollow`), if present. |
| `target` | The matched anchor's `target` attribute (e.g. `_blank`), if present. |
| `blocked` | `True` if the site refused to serve real content to an automated request (bot-challenge page, or LinkedIn's `999`). |
| `requires_login` | `True` if the request was redirected to a login/authwall page instead of the real content. |
| `text_mentions` | Count of plain-text occurrences of the domain outside any `<a href>` (weak signal, see `text_mention_only` below). |
| `robots_meta` | Raw `content` of the page's `<meta name="robots">` tag, if present (e.g. `noindex, follow`). |
| `robots_header` | Raw value of the `X-Robots-Tag` response header, if present. Captured separately from `robots_meta` because the two can disagree — [per Google's own spec, the header takes precedence](https://developers.google.com/search/docs/crawling-indexing/robots-meta-tag), and hiding that disagreement behind one merged column would lose it. |
| `error` | Transport/timeout error, if the request failed outright. |
| `notes` | Human-readable detail on why a row landed in its `match_type`/flag. |

## `match_type` values, strongest signal first

| `match_type` | Meaning |
|---|---|
| `final_url_is_target` | The request itself landed on the target domain (e.g. a short-link redirect resolving straight to it) — verified independently of the page's HTML, the strongest possible signal. |
| `anchor` | A real `<a href>` whose hostname exactly matches the target domain. |
| `subdomain_anchor` | Same, but matches a subdomain of the target (`blog.medilana.id`). |
| `indirect_query` | The target domain appears *embedded inside* another host's URL — typically a tracker/redirector link (e.g. YouTube/Instagram's own `/redirect?...q=<url>` wrapper). Weaker than a direct anchor; verify manually. |
| `text_mention_only` | The domain appears as plain rendered text but not inside any `<a href>` — not a real backlink. |
| `not_found` | No sign of the target domain at all. |

Every hostname comparison (`anchor`/`subdomain_anchor`/`final_url_is_target`)
is a *whole-hostname* match, never a substring search — `notmedilana.id`
and `medilana.id.evil-tracker.com` can never register as a false match.

## Things worth knowing before you interpret a report

- **LinkedIn (`999`) and Facebook (login-redirect) block both modes
  equally.** This isn't a JS-rendering gap — it's dedicated anti-bot /
  auth-wall infrastructure that also blocks a real headless Chromium.
  `--browser` mode will still correctly tag these `blocked` /
  `requires_login` rather than pretending to succeed.
- **Some server-rendered sites (e.g. Neliti) return a bot-blocked `403`
  to the default User-Agent but a normal `200` to a real browser UA.** If
  a `not_found`/`403` row surprises you, re-check it with `--browser`
  even if the site isn't a SPA — it's not always about JavaScript,
  sometimes it's just UA-based bot filtering.
- **`bit.ly` (and similar shorteners) can behave differently between
  modes.** The default mode follows the HTTP redirect straight through
  to the destination (`final_url_is_target`). `--browser` mode can
  instead land on the shortener's own interstitial/warning page first,
  which then shows as an `anchor` match on `bit.ly` itself rather than
  on your domain — both are correct, they're just describing different
  hops of the same redirect.
- **YouTube/Instagram/Threads typically resolve to `indirect_query`, not
  `anchor`, even with `--browser`** — their profile "link in bio" is
  wrapped in the platform's own click-tracking redirector
  (`/redirect?...&q=<url>`), not a direct `<a href="https://yourdomain">`.
  That's still a real, working backlink — just verify the `q=` parameter
  manually before counting it as fully confirmed.
- Slow/heavy pages (Notion, some university CMSs) can exceed
  `--browser` mode's `--nav-timeout` (default 30s). Raise it with
  `--nav-timeout 60` rather than assuming the link genuinely isn't there.

See [`CLI_REFERENCE.md#bl-check`](./CLI_REFERENCE.md#bl-check) and
[`CLI_REFERENCE.md#bl-chain`](./CLI_REFERENCE.md#bl-chain) for the full
flag reference across all `bl-*` commands.

Back to [main README](../README.md).
