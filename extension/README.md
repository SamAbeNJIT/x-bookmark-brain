# bookmark-brain capture spike (Increment 0)

A throwaway browser extension that proves the core product thesis: **can we capture your X
bookmarks from inside your own logged-in browser session, cross-browser, without you pasting a
token?** It passively hooks the network calls X already makes on your Bookmarks page, extracts
the bookmarked tweets, and logs them. It reads no cookies and sends nothing anywhere.

If this works in Chrome/Brave **and** Firefox, Increment 1 (push the captured items to the
local `/ingest` backend) is unblocked. If it doesn't, we rethink the approach before building more.

## Load it

**Chrome / Brave / Edge**
1. Go to `chrome://extensions` (or `brave://extensions`).
2. Toggle **Developer mode** (top-right).
3. **Load unpacked** → select this `extension/` folder.

**Firefox**
1. Go to `about:debugging#/runtime/this-firefox`.
2. **Load Temporary Add-on…** → select `extension/manifest.json`.
   (Temporary add-ons unload when Firefox restarts — fine for a spike.)

## Try it

1. Open <https://x.com/i/bookmarks> (logged in).
2. Open DevTools → **Console**.
3. You should see `[bookmark-brain] capture hook installed`.
4. **Scroll down** through your bookmarks. Each page X loads logs:
   `[bookmark-brain] captured N bookmarks (M total this session)` plus a sample entry.
5. Inspect everything captured so far: type `window.__xbbBookmarks` in the console.

## What "pass" looks like
Scrolling your bookmarks logs growing counts in **both** Chrome/Brave and Firefox, and
`window.__xbbBookmarks` holds the raw tweet entries (the same shape `parse_bookmark` already
handles server-side). That's the green light for Increment 1.
