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
.cta { display:inline-block; margin-top:1.6rem; background:#0f0f14; color:#fff; font-weight:700;
       font-size:1.1rem; padding:1rem 2rem; border-radius:12px; text-decoration:none;
       box-shadow:0 6px 18px rgba(15,15,20,.3); }
.cta:hover { background:#26262e; }
.cta .x { font-weight:800; margin-right:.45rem; }
.freeline { margin-top:.8rem; color:var(--muted); font-size:.92rem; }
.alt-signin { display:block; margin-top:.55rem; color:var(--muted); font-size:.85rem; }
.alt-signin a { color:var(--muted); }
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
<title>x-bookmarks.ai: find any X bookmark in seconds</title>
<meta name=description content="You saved thousands of X bookmarks you can never find again. Turn them into searchable knowledge: find any saved post in seconds, or ask and get answers cited from your own bookmarks.">
<link rel="canonical" href="https://x-bookmarks.ai/">
<meta property="og:type" content="website">
<meta property="og:url" content="https://x-bookmarks.ai/">
<meta property="og:site_name" content="x-bookmarks.ai">
<meta property="og:title" content="Find any X bookmark in seconds">
<meta property="og:description" content="Turn your X bookmarks into searchable knowledge. Sign in with X, your last 100 organize themselves, free.">
<meta property="og:image" content="https://x-bookmarks.ai/static/feed.png">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="Find any X bookmark in seconds">
<meta name="twitter:description" content="Turn your X bookmarks into searchable knowledge. Sign in with X, your last 100 organize themselves, free.">
<meta name="twitter:image" content="https://x-bookmarks.ai/static/feed.png">
<link rel="icon" type="image/png" sizes="32x32" href="/static/favicon-32.png">
<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
<script type="application/ld+json">{{"@context":"https://schema.org","@type":"SoftwareApplication",
"name":"x-bookmarks.ai","applicationCategory":"UtilitiesApplication","operatingSystem":"Web",
"url":"https://x-bookmarks.ai/","description":"AI search and organization for your X (Twitter) bookmarks: import your saved posts, get them sorted into your own topics, search by meaning, and ask questions with cited answers.",
"offers":{{"@type":"Offer","price":"0","priceCurrency":"USD","description":"Free: 100 most recent bookmarks and 5 questions a day"}}}}</script>
<style>{_CSS}</style></head><body>
<div class=wrap>
<nav><span class=brand>bookmark<span class=dot>.</span>brain</span>
<a class=signin href="/login">Sign in</a></nav>

<div class=hero>
  <h1>You saved 1,000 bookmarks.<br>You can't find one of them.</h1>
  <p class=sub>Every thread you meant to come back to is buried in an endless, unsearchable list.
  x-bookmarks.ai turns your X bookmarks into searchable knowledge: find any saved post in
  seconds, or ask a question and get an answer built from what <b>you</b> saved.</p>
  <a class=cta href="/oauth/signin"><span class=x>𝕏</span>Sign in with X, free</a>
  <div class=freeline><b>Free forever:</b> your last 100 bookmarks organized + 5 AI questions a
  day. No card, nothing to set up. You're searching your bookmarks about a minute from now.</div>
  <div class=freeline style="margin-top:.35rem">🔒 Read-only bookmark access via X's official
  sign-in. We never post, follow, or touch anything else, and only you can see your library.</div>
</div>

<section>
  <div class=fade><img class=shot src="/static/feed.png" alt="A color-coded feed of organized bookmarks"></div>
</section>

<section class=feature>
  <div>
    <div class=kicker>Find it</div>
    <h2>Find any bookmarked tweet in seconds</h2>
    <p>Search by what you remember, not exact words: "that thread about pricing psychology"
    works. Or ask a question and get a real answer, every claim cited back to the exact
    bookmark it came from.</p>
  </div>
  <div class=askdemo>
    <div class=q>What did I save about growing an audience on X?</div>
    <div class=a>Three threads stand out: a 30-day writing system you bookmarked in March, a
    breakdown of hook formulas with 12 examples, and the post arguing consistency beats
    virality, plus 4 more on profile design and reply strategy…</div>
    <span class=cite>★ every claim cited to one of your bookmarks</span>
  </div>
</section>

<section class=feature>
  <div>
    <div class=kicker>Organized for you</div>
    <h2>Never lose another thread</h2>
    <p>AI sorts every bookmark into topics drawn from what <i>you</i> actually save, color-coded
    and browsable. No folders to maintain, nothing to file. Saving on X stays one tap; finding
    finally works.</p>
  </div>
  <div><img class=shot src="/static/categories.png" alt="AI-derived categories with counts"></div>
</section>

<section>
  <h2 class=center>From sign-in to searchable in about a minute</h2>
  <div class=steps>
    <div class=step><span class=n>1</span><b>Sign in with X</b>One tap, official X sign-in.
    Read-only bookmark access. Revoke anytime from your X settings.</div>
    <div class=step><span class=n>2</span><b>Your bookmarks organize themselves</b>Your last
    100 sync automatically and land in your own topics, free, no card.</div>
    <div class=step><span class=n>3</span><b>Search, browse, ask</b>Find that post you half
    remember, or ask questions and get cited answers.</div>
  </div>
</section>

<section>
  <div><img class=shot src="/static/stats.png" alt="17,004 bookmarks organized into 22 categories"></div>
  <p style="text-align:center;color:var(--muted);font-size:.9rem;margin-top:.6rem">
  The founder's own library: 17,004 bookmarks, every one findable.</p>
</section>

<section>
  <h2 class=center>Free to try. Pay only if you want more.</h2>
  <div class=pricing>
    <div class="price hot"><div class=amt>Free</div><div class=per>forever, no card</div>
      <ul><li>Your 100 most recent bookmarks, organized</li><li>5 AI questions every day</li>
      <li>Full search &amp; browsing</li></ul></div>
    <div class=price><div class=amt>1¢</div><div class=per>per bookmark, one-time</div>
      <ul><li>Unlock your <b>entire</b> history, from $3</li>
      <li>Only pay for what you have; unused capacity refunds to your card automatically</li></ul></div>
    <div class=price><div class=amt>5¢</div><div class=per>per question</div>
      <ul><li>No subscription. $1 ≈ 20 questions</li>
      <li>Bigger packs get up to 30% bonus questions</li></ul></div>
  </div>
  <div style="text-align:center;margin-top:1.8rem">
    <a class=cta href="/oauth/signin"><span class=x>𝕏</span>Sign in with X, free</a>
    <span class=alt-signin>prefer email? <a href="/login">sign in with a magic link</a></span>
  </div>
</section>

<footer>
  <a href="/terms">Terms</a> · <a href="/privacy">Privacy</a> ·
  <a href="mailto:support@x-bookmarks.ai">support@x-bookmarks.ai</a><br>
  Bookmarks are fetched with your permission via X's official API. Your library is private to you.<br>
  Not affiliated with X Corp.
</footer>
</div></body></html>"""
    return HTMLResponse(html)
