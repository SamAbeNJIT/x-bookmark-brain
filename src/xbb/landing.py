"""Public landing page — what an anonymous visitor sees at x-bookmarks.ai.

Standalone marketing HTML (own CSS, no app sidebar) in the app's visual language. Shown by the
"/" route when REQUIRE_AUTH is on and there's no valid session; signed-in users get the app home.
Screenshots are packaged in xbb/static (served at /static, which is on the auth-exempt surface).
"""

from __future__ import annotations

from fastapi.responses import HTMLResponse

_CSS = """
:root { --bg:#f7f7f9; --panel:#fff; --ink:#16161a; --muted:#6b6b76; --line:#e7e7ee;
        --accent:#5b6cf0; --accent-ink:#3a47c4; --shadow:0 1px 3px rgba(20,20,40,.06); }
* { box-sizing:border-box; margin:0; }
body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; background:var(--bg);
       color:var(--ink); line-height:1.55; }
.wrap { max-width:1060px; margin:0 auto; padding:0 1.4rem; }
nav { display:flex; align-items:center; padding:1.1rem 0; }
.brand { font-weight:800; font-size:1.15rem; letter-spacing:-.02em; }
.brand .dot { color:var(--accent); }
nav a.signin { margin-left:auto; text-decoration:none; color:var(--ink); font-weight:600;
               padding:.55rem 1.1rem; border:1px solid var(--line); border-radius:10px; background:var(--panel); }
.hero { text-align:center; padding:4.2rem 0 2.6rem; }
.hero h1 { font-size:clamp(2rem,5vw,3.3rem); letter-spacing:-.03em; line-height:1.12; }
.hero p.sub { max-width:640px; margin:1.1rem auto 0; font-size:1.13rem; color:var(--muted); }
.cta { display:inline-block; margin-top:1.6rem; background:var(--accent); color:#fff; font-weight:700;
       font-size:1.05rem; padding:.9rem 1.8rem; border-radius:12px; text-decoration:none;
       box-shadow:0 6px 18px rgba(91,108,240,.35); }
.cta:hover { background:var(--accent-ink); }
.freeline { margin-top:.8rem; color:var(--muted); font-size:.92rem; }
.shot { display:block; width:100%; border-radius:14px; border:1px solid var(--line); box-shadow:var(--shadow); }
.fade { position:relative; overflow:hidden; border-radius:14px; }
.fade::after { content:''; position:absolute; left:0; right:0; bottom:0; height:70px;
               background:linear-gradient(180deg,transparent,var(--bg)); }
section { padding:2.6rem 0; }
.feature { display:grid; grid-template-columns:1fr 1.4fr; gap:2.2rem; align-items:center; padding:2.2rem 0; }
.feature.rev { grid-template-columns:1.4fr 1fr; }
.feature h2 { font-size:1.55rem; letter-spacing:-.02em; margin-bottom:.5rem; }
.feature p { color:var(--muted); }
.kicker { color:var(--accent); font-weight:700; font-size:.82rem; text-transform:uppercase; letter-spacing:.08em; }
.askdemo { background:var(--panel); border:1px solid var(--line); border-left:4px solid var(--accent);
           border-radius:14px; padding:1.4rem 1.5rem; box-shadow:var(--shadow); }
.askdemo .q { font-weight:700; margin-bottom:.6rem; }
.askdemo .a { color:var(--muted); }
.askdemo .cite { display:inline-block; margin-top:.7rem; font-size:.8rem; color:var(--accent-ink);
                 background:#eef0fe; border-radius:8px; padding:.15rem .55rem; }
.steps { display:grid; grid-template-columns:repeat(3,1fr); gap:1.1rem; }
.step { background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:1.3rem; box-shadow:var(--shadow); }
.step b { display:block; margin-bottom:.35rem; }
.step .n { color:var(--accent); font-weight:800; }
.pricing { display:grid; grid-template-columns:repeat(3,1fr); gap:1.1rem; }
.price { background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:1.4rem; box-shadow:var(--shadow); }
.price .amt { font-size:1.5rem; font-weight:800; letter-spacing:-.02em; }
.price .per { color:var(--muted); font-size:.9rem; }
.price ul { margin:.7rem 0 0 1.1rem; color:var(--muted); font-size:.93rem; }
.price.hot { border-color:var(--accent); box-shadow:0 6px 18px rgba(91,108,240,.18); }
h2.center { text-align:center; font-size:1.7rem; letter-spacing:-.02em; margin-bottom:1.4rem; }
footer { padding:2.5rem 0 3rem; color:var(--muted); font-size:.9rem; text-align:center; }
footer a { color:inherit; }
@media (max-width:840px) { .feature, .feature.rev { grid-template-columns:1fr; }
  .steps, .pricing { grid-template-columns:1fr; } }
"""


def landing_page() -> HTMLResponse:
    html = f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content='width=device-width, initial-scale=1'>
<title>x-bookmarks.ai — unlock the knowledge within your personal vault</title>
<meta name=description content="Import your X bookmarks, let AI organize them into your own topics, then search by meaning or just ask — with answers cited from your saved posts.">
<link rel="icon" type="image/png" sizes="32x32" href="/static/favicon-32.png">
<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
<style>{_CSS}</style></head><body>
<div class=wrap>
<nav><span class=brand>bookmark<span class=dot>.</span>brain</span>
<a class=signin href="/login">Sign in</a></nav>

<div class=hero>
  <h1>Unlock the knowledge within<br>your personal vault.</h1>
  <p class=sub>You saved thousands of posts you'll never scroll back to. Import them, let AI
  organize them into <b>your</b> topics, then search by meaning — or just ask a question and get
  an answer cited from your own bookmarks.</p>
  <a class=cta href="/login">Start free</a>
  <div class=freeline>Your 100 most recent bookmarks + 5 questions a day, free. No card needed.</div>
  <div class=freeline style="margin-top:.35rem">🔒 <b>Private by design</b> — your library is
  visible only to you. Official X sign-in; we can read your bookmarks, never post or touch
  anything else.</div>
</div>

<section>
  <div class=fade><img class=shot src="/static/feed.png" alt="A color-coded feed of organized bookmarks"></div>
</section>

<section class=feature>
  <div>
    <div class=kicker>Ask</div>
    <h2>Ask your bookmarks anything</h2>
    <p>"What did I save about prompt engineering?" A real answer, synthesized from your saved
    posts — every claim cited back to the exact bookmark, side by side.</p>
  </div>
  <div class=askdemo>
    <div class=q>What did I save about running local AI models?</div>
    <div class=a>You bookmarked several posts on this: falling hardware costs for large local
    models, a Mac&nbsp;Mini server farm build, and a thread arguing local inference wins on
    privacy…</div>
    <span class=cite>★ cited · 7 of your bookmarks</span>
  </div>
</section>

<section class=feature>
  <div>
    <div class=kicker>Organize</div>
    <h2>Topics derived from <i>your</i> corpus</h2>
    <p>No canned folders. The AI reads what you actually save and proposes your taxonomy —
    then color-codes every bookmark so the feed finally makes sense. Rename, merge, or re-derive
    anytime.</p>
  </div>
  <div><img class=shot src="/static/categories.png" alt="AI-derived categories with counts"></div>
</section>

<section>
  <h2 class=center>How it works</h2>
  <div class=steps>
    <div class=step><span class=n>1</span><b>Connect X</b>One tap through X's official
    sign-in. No password, no cookies — revoke anytime from your X settings.</div>
    <div class=step><span class=n>2</span><b>AI organizes</b>Your bookmarks are imported,
    understood, and sorted into your own topics in minutes.</div>
    <div class=step><span class=n>3</span><b>Search or ask</b>Find posts by meaning or exact
    keywords, or ask questions and get cited answers.</div>
  </div>
</section>

<section>
  <div><img class=shot src="/static/stats.png" alt="17,004 bookmarks organized into 22 categories"></div>
  <p style="text-align:center;color:var(--muted);font-size:.9rem;margin-top:.6rem">
  The founder's own library: 17,004 bookmarks, organized.</p>
</section>

<section>
  <h2 class=center>Simple pricing</h2>
  <div class=pricing>
    <div class=price><div class=amt>Free</div><div class=per>forever</div>
      <ul><li>Your 100 most recent bookmarks</li><li>5 questions every day</li>
      <li>Full search &amp; browsing</li></ul></div>
    <div class="price hot"><div class=amt>1¢</div><div class=per>per bookmark, one-time</div>
      <ul><li>Unlock your <b>entire</b> history</li><li>Pick how much to import — from $3</li>
      <li>Only pay for what you have — the rest refunds automatically</li></ul></div>
    <div class=price><div class=amt>5¢</div><div class=per>per question</div>
      <ul><li>No subscription — pay only for what you ask</li><li>$1 ≈ 20 questions</li>
      <li>Bigger packs get up to 30% bonus questions</li></ul></div>
  </div>
  <div style="text-align:center;margin-top:1.8rem"><a class=cta href="/login">Start free</a></div>
</section>

<footer>
  <a href="/terms">Terms</a> · <a href="/privacy">Privacy</a> ·
  <a href="mailto:support@x-bookmarks.ai">support@x-bookmarks.ai</a><br>
  Bookmarks are fetched with your permission via X's official API. Your library is private to you.<br>
  Not affiliated with X Corp.
</footer>
</div></body></html>"""
    return HTMLResponse(html)
