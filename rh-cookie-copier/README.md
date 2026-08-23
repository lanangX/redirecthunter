# RedirectHunter Cookie Copier

A tiny browser extension: one click copies the current site's session
cookies, already formatted as a `bl-check --accounts-file` line, straight to
your clipboard. Built to solve two specific `bl-check` pain points:

1. You shouldn't have to dig through DevTools and guess which of a dozen
   cookies (`_ga`, `_gid`, `__Secure-3PSID`, ...) is actually the session
   cookie.
2. You shouldn't have to retype `account_id|Cookie: ...` by hand every time.

It's not published to any extension store (that requires a review process
and a fixed, versioned account) — you load it locally, which takes under a
minute and works identically on every Chromium browser plus Firefox.

## What it does, in plain terms

- Reads the cookies your browser would send with a request to the site
  you're currently on — the exact same set the server sees, no guessing.
- Formats them as one line, in the format `bl-check --accounts-file` expects.
- Copies that line to your clipboard. Nothing is sent anywhere else, ever
  — there's no server, no network request, no analytics in this extension.
- Only asks for permission to read cookies for the **one site** you're on,
  at the moment you click the button — not "read all your browsing data"
  at install time.

## Install (Chrome, Edge, Brave, Opera — any Chromium browser)

1. Go to `chrome://extensions` (or `edge://extensions`, `brave://extensions`, ...).
2. Turn on **Developer mode** (toggle, usually top-right).
3. Click **Load unpacked**.
4. Select this folder (`rh-cookie-copier/`).
5. Pin the extension (puzzle-piece icon in the toolbar → pin) so it's
   always one click away.

## Install (Firefox)

1. Go to `about:debugging#/runtime/this-firefox`.
2. Click **Load Temporary Add-on…**.
3. Select `manifest.json` inside this folder.
4. Note: Firefox removes temporary add-ons when the browser closes, so
   you'll reload it each session. For a permanent install, the extension
   would need to be signed by Mozilla (a separate, optional step, not
   required to use it locally).

## Usage

### Copy from this tab

1. Log in to the site normally, in a regular tab (e.g.
   `marketplace.whmcs.com`).
2. Click the extension icon (**"Copy from this tab"** is the default tab).
3. First time on a given site, your browser will ask you to confirm
   access to that site's cookies — approve it (this is the one-time
   permission prompt, scoped to that domain only).
4. Pick a format:
   - **Account line** (`account_id|Cookie: ...`) — the default, for a
     `bl-check --accounts-file` setup, where one domain needs many
     different sessions (e.g. 30 social-media accounts on the same
     platform). Type the same `account_id` your input file uses in its
     `account_id|URL` rows, then paste the result into
     `bl-check-accounts.txt`. If an account needs more than one header
     (e.g. Cookie + User-Agent), run "Fetch" again with the same
     account_id and add the extra line by hand — the file format allows
     repeating an account_id across several lines.
   - **Raw value only** — just the `name=value; name=value` string, if
     you're pasting it somewhere else.
5. Click **Fetch cookies from this tab**. The result is copied
   automatically, and also shown in an editable text box — tweak it (drop
   a cookie you don't want, fix the account label, whatever) and click
   **Copy to clipboard** again to grab the edited version.
6. Paste the line into your `accounts.txt` (see
   `examples/bl-check-accounts.txt` in the main RedirectHunter repo), then:

   ```bash
   redirecthunter bl-check backlinks.txt -d medilana.id --accounts-file accounts.txt
   ```

### Apply to this tab

This is the other direction: take a cookie line you already have (e.g. one
you copied on a *different* browser, or a `Cookie:` header you copied out
of DevTools' Network tab) and write it into *this* browser's cookie jar,
so you can just reload the page and look at it while logged in — no
re-entering credentials, and no need to log into the same account on every
browser you want to eyeball-check with.

1. Open the site's tab in the browser you want to view it in.
2. Click the extension icon → the **"Apply to this tab"** tab.
3. Paste the cookie line — any of these work as-is:
   - `domain.com|Cookie: name=value; name2=value2` (what "Copy from this
     tab" produces)
   - `Cookie: name=value; name2=value2`
   - `name=value; name2=value2` (raw)
4. Click **Apply cookies to this browser**. Approve the one-time
   permission prompt if asked.
5. With "Reload this tab after applying" checked (default), the tab
   reloads automatically and you should see the logged-in page.

This is purely a local write into your own browser's cookie storage for
that one site — nothing is sent anywhere, and it only affects the current
browser profile/tab's cookies for that domain.

## Security notes

- The copied/applied value is a live, logged-in session — equivalent to a
  password. Don't paste it anywhere other than your own local headers
  file or the "Apply to this tab" box, don't share it, don't commit it to
  version control.
- Session cookies expire (often in hours), so re-copy when `bl-check`
  starts reporting `requires_login` again for that platform, or when an
  applied session stops working in another browser.
- "Apply to this tab" only writes cookies for the domain you're currently
  on; it can't be used to set cookies for a site you don't have the tab
  open on.
- Uninstalling the extension removes it entirely; it stores nothing
  itself and asks for nothing beyond the per-site permission grants,
  which you can review/revoke anytime from your browser's extension
  settings page.

## Files

```
manifest.json   Extension manifest (MV3, cross-browser)
popup.html      Popup UI
popup.js        Popup logic (cookie read + clipboard copy)
icons/          Toolbar/store icons
```
