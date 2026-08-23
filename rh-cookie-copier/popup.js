/* RedirectHunter Cookie Copier — popup logic.
 *
 * Uses the callback-style `chrome.*` API throughout (not `browser.*`),
 * because Firefox exposes `chrome.*` as a compatibility alias that also
 * accepts callbacks, so the exact same code runs unmodified on Chrome,
 * Edge, Brave, Opera, and Firefox (109+) without needing a polyfill.
 *
 * Privacy design: this extension does NOT request broad "read all your
 * browsing data" access at install time. It only asks for permission to
 * read/write cookies for one specific site, at the moment you click a
 * button, for the tab you're already looking at (via the
 * `optional_host_permissions` + `chrome.permissions.request` flow).
 * Nothing is sent anywhere — everything happens locally in this popup.
 *
 * Two directions:
 *   - "Copy from this tab": read this tab's cookies -> editable text ->
 *     clipboard, to paste into a bl-check headers file.
 *   - "Apply to this tab": paste a cookie line (e.g. one you copied on a
 *     *different* browser) -> parsed -> written into this browser's
 *     cookie jar for this site, so you can just reload the page and look
 *     at it logged in, without re-entering credentials. This is what lets
 *     you carry one login across browsers for manual spot-checking.
 */

function promisify(fn, ...args) {
  return new Promise((resolve) => fn(...args, (result) => resolve(result)));
}

function originPatternFor(url) {
  const u = new URL(url);
  return `*://${u.hostname}/*`;
}

async function ensureHostPermission(url) {
  const pattern = originPatternFor(url);
  const already = await promisify(chrome.permissions.contains.bind(chrome.permissions), {
    origins: [pattern],
  });
  if (already) return true;
  return promisify(chrome.permissions.request.bind(chrome.permissions), {
    origins: [pattern],
  });
}

// Mirrors redirecthunter/loader.py's `_ACCOUNT_ID_PATTERN` exactly:
// letters/digits/underscore/hyphen only, must start with a letter. A dot
// (or any other character) breaks the CLI's own account_id-vs-URL
// disambiguation -- a line whose account_id contains "." silently fails
// to be recognized as an account row at all (loader.py falls back to
// treating the whole line as a plain URL row instead of raising an
// error), so we normalize here rather than let that ambiguity into the
// copied line.
const ACCOUNT_ID_PATTERN = /^[A-Za-z][A-Za-z0-9_-]*$/;

function sanitizeAccountId(raw) {
  const trimmed = raw.trim();
  // Collapse any run of disallowed characters (dots, spaces, etc.) into
  // a single "-", e.g. "marketplace.whmcs.com" -> "marketplace-whmcs-com".
  let cleaned = trimmed.replace(/[^A-Za-z0-9_-]+/g, "-").replace(/-+/g, "-");
  // The pattern also requires starting with a letter -- strip any
  // leading digits/hyphens/underscores rather than guess a prefix.
  cleaned = cleaned.replace(/^[^A-Za-z]+/, "");
  return cleaned;
}

/**
 * Combines the "Account domain" and "Account username / label" fields into
 * one full account_id, e.g. domain "marketplace.whmcs.com" + username
 * "jasapasangngt" -> "marketplace-whmcs-com-jasapasangngt". Lets the user
 * type just the short, memorable part (the username) while the domain --
 * already known from the current tab -- is filled in for them, the same
 * way "Scope domain" is auto-filled for the "scoped" format. Returns ""
 * if either half is missing/unusable, so callers can treat that as
 * "not ready yet" without guessing.
 */
function computeAccountId(domainRaw, userRaw) {
  const domainPart = sanitizeAccountId(domainRaw);
  const userPart = sanitizeAccountId(userRaw);
  if (!domainPart || !userPart) return "";
  return `${domainPart}-${userPart}`.replace(/-+/g, "-");
}

function buildLine(format, domain, accountId, cookieValue) {
  if (format === "raw") return cookieValue;
  if (format === "global") return `Cookie: ${cookieValue}`;
  if (format === "account") return `${accountId}|Cookie: ${cookieValue}`;
  return `${domain}|Cookie: ${cookieValue}`;
}

/**
 * Accepts any of the formats a user might paste and reduces it to the raw
 * "name=value; name2=value2" cookie string.
 *   - "domain.com|Cookie: name=value; ..."
 *   - "account_001|Cookie: name=value; ..."
 *   - "Cookie: name=value; ..."
 *   - "name=value; ..."
 * Also tolerates surrounding whitespace/newlines from a sloppy paste.
 */
function extractCookieValue(pasted) {
  let text = pasted.trim();
  const pipeIndex = text.indexOf("|");
  if (pipeIndex !== -1 && /^[a-z0-9._-]+$/i.test(text.slice(0, pipeIndex).trim())) {
    text = text.slice(pipeIndex + 1).trim();
  }
  const match = text.match(/^cookie\s*:\s*/i);
  if (match) {
    text = text.slice(match[0].length).trim();
  }
  return text;
}

/** Splits "name=value; name2=value2" into [{name, value}, ...]. Value may itself contain "=". */
function parseCookiePairs(cookieValue) {
  return cookieValue
    .split(";")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const eq = part.indexOf("=");
      if (eq === -1) return null;
      return { name: part.slice(0, eq).trim(), value: part.slice(eq + 1).trim() };
    })
    .filter(Boolean);
}

function setStatus(elId, kind, text) {
  const el = document.getElementById(elId);
  el.className = kind;
  el.textContent = text;
}

function switchTab(which) {
  const isCopy = which === "copy";
  document.getElementById("tabCopyBtn").classList.toggle("active", isCopy);
  document.getElementById("tabApplyBtn").classList.toggle("active", !isCopy);
  document.getElementById("copyPanel").classList.toggle("active", isCopy);
  document.getElementById("applyPanel").classList.toggle("active", !isCopy);
}

async function main() {
  const domainBox = document.getElementById("domainBox");
  const domainInput = document.getElementById("domainInput");
  const domainRow = document.getElementById("domainRow");
  const accountDomainInput = document.getElementById("accountDomainInput");
  const accountUserInput = document.getElementById("accountUserInput");
  const accountIdPreview = document.getElementById("accountIdPreview");
  const accountRow = document.getElementById("accountRow");
  const formatSelect = document.getElementById("format");
  const fetchBtn = document.getElementById("fetchBtn");
  const copyAgainBtn = document.getElementById("copyAgainBtn");
  const preview = document.getElementById("preview");
  const previewLabel = document.getElementById("previewLabel");
  const cookieCount = document.getElementById("cookieCount");
  const applyInput = document.getElementById("applyInput");
  const applyBtn = document.getElementById("applyBtn");
  const reloadCheckbox = document.getElementById("reloadCheckbox");

  document.getElementById("tabCopyBtn").addEventListener("click", () => switchTab("copy"));
  document.getElementById("tabApplyBtn").addEventListener("click", () => switchTab("apply"));

  const tabs = await promisify(chrome.tabs.query.bind(chrome.tabs), {
    active: true,
    currentWindow: true,
  });
  const tab = tabs && tabs[0];

  if (!tab || !tab.url || !/^https?:\/\//.test(tab.url)) {
    domainBox.textContent = "This tab isn't a regular http(s) page — open the site first.";
    fetchBtn.disabled = true;
    applyBtn.disabled = true;
    return;
  }

  const url = new URL(tab.url);
  const hostname = url.hostname;

  domainBox.innerHTML = `Current tab: <strong>${hostname}</strong>`;
  domainInput.value = hostname;
  accountDomainInput.value = hostname; // auto-filled, same as domainInput -- still editable

  formatSelect.addEventListener("change", () => {
    domainRow.style.display = formatSelect.value === "scoped" ? "block" : "none";
    accountRow.style.display = formatSelect.value === "account" ? "block" : "none";
  });

  // Live preview of the combined account_id as either half changes, so
  // the user can see/verify the exact string that will end up in
  // bl-check-accounts.txt before they ever click Fetch.
  function refreshAccountIdPreview() {
    const full = computeAccountId(accountDomainInput.value, accountUserInput.value);
    accountIdPreview.textContent = full
      ? `Full account_id: ${full}`
      : "Enter a username/label to generate the account_id.";
  }
  accountDomainInput.addEventListener("input", refreshAccountIdPreview);
  accountUserInput.addEventListener("input", refreshAccountIdPreview);
  refreshAccountIdPreview();

  // ---------------- COPY PANEL ----------------

  fetchBtn.addEventListener("click", async () => {
    setStatus("status", "info", "Requesting access…");
    fetchBtn.disabled = true;

    try {
      const granted = await ensureHostPermission(tab.url);
      if (!granted) {
        setStatus("status", "err", "Permission was not granted, so cookies can't be read.");
        return;
      }

      let accountId = "";
      if (formatSelect.value === "account") {
        accountId = computeAccountId(accountDomainInput.value, accountUserInput.value);
        if (!accountId) {
          setStatus(
            "status",
            "err",
            "Enter the account username/label first (the domain is filled in for you)."
          );
          return;
        }
      }

      const cookies = await promisify(chrome.cookies.getAll.bind(chrome.cookies), {
        url: tab.url,
      });

      if (!cookies || cookies.length === 0) {
        setStatus("status", "err", `No cookies found for ${hostname}. Are you logged in on this tab?`);
        return;
      }

      cookieCount.textContent = `${cookies.length} cookie(s) found for this request.`;

      const cookieValue = cookies.map((c) => `${c.name}=${c.value}`).join("; ");
      const scopeDomain = domainInput.value.trim() || hostname;
      const line = buildLine(formatSelect.value, scopeDomain, accountId, cookieValue);

      preview.value = line;
      preview.style.display = "block";
      previewLabel.style.display = "block";
      copyAgainBtn.style.display = "block";

      await navigator.clipboard.writeText(line);
      setStatus("status", "ok", "Copied! You can edit the text below, then use Copy again.");
    } catch (err) {
      setStatus("status", "err", `Something went wrong: ${err.message || err}`);
    } finally {
      fetchBtn.disabled = false;
    }
  });

  copyAgainBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(preview.value);
      setStatus("status", "ok", "Copied your edited version to the clipboard.");
    } catch (err) {
      setStatus("status", "err", `Couldn't copy: ${err.message || err}`);
    }
  });

  // Manual re-copy uses whatever is currently in the (editable) preview box.
  // ---------------- APPLY PANEL ----------------

  applyBtn.addEventListener("click", async () => {
    const raw = applyInput.value;
    if (!raw || !raw.trim()) {
      setStatus("applyStatus", "err", "Paste a cookie line first.");
      return;
    }

    const cookieValue = extractCookieValue(raw);
    const pairs = parseCookiePairs(cookieValue);

    if (pairs.length === 0) {
      setStatus(
        "applyStatus",
        "err",
        "Couldn't find any name=value cookie pairs in that text — check the format."
      );
      return;
    }

    setStatus("applyStatus", "info", "Requesting access…");
    applyBtn.disabled = true;

    try {
      const granted = await ensureHostPermission(tab.url);
      if (!granted) {
        setStatus("applyStatus", "err", "Permission was not granted, so cookies can't be set.");
        return;
      }

      const isHttps = url.protocol === "https:";
      let successCount = 0;
      const failed = [];

      for (const { name, value } of pairs) {
        const result = await promisify(chrome.cookies.set.bind(chrome.cookies), {
          url: tab.url,
          name,
          value,
          path: "/",
          secure: isHttps,
          httpOnly: true,
        });
        if (result) {
          successCount += 1;
        } else {
          failed.push(name);
        }
      }

      if (successCount === 0) {
        setStatus(
          "applyStatus",
          "err",
          "The browser rejected all of these cookies (often means the domain " +
            "in the pasted line doesn't match this tab's site)."
        );
        return;
      }

      let msg = `Applied ${successCount}/${pairs.length} cookie(s) to ${hostname}.`;
      if (failed.length) msg += ` Rejected: ${failed.join(", ")}.`;
      setStatus("applyStatus", failed.length ? "info" : "ok", msg);

      if (reloadCheckbox.checked) {
        await promisify(chrome.tabs.reload.bind(chrome.tabs), tab.id);
      }
    } catch (err) {
      setStatus("applyStatus", "err", `Something went wrong: ${err.message || err}`);
    } finally {
      applyBtn.disabled = false;
    }
  });
}

main();
