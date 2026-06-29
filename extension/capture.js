// Runs in the PAGE (MAIN) world. Wraps the page's own fetch + XHR and, when X loads its
// Bookmarks GraphQL response, extracts the bookmarked-tweet entries and logs them.
//
// It only OBSERVES responses X already makes in your logged-in session — it never reads your
// cookies, never makes its own authenticated calls, and sends nothing off the page. This is
// the Increment-0 spike: prove in-session capture works cross-browser before building /ingest.
(function () {
  if (window.__xbbHooked) return;
  window.__xbbHooked = true;
  window.__xbbBookmarks = []; // inspect this array in the console to see everything captured

  let total = 0;

  function handle(url, json) {
    try {
      if (!url || url.indexOf("/Bookmarks") === -1) return;
      const tl =
        json && json.data && json.data.bookmark_timeline_v2 && json.data.bookmark_timeline_v2.timeline;
      if (!tl) return;
      let n = 0;
      (tl.instructions || []).forEach(function (ins) {
        (ins.entries || []).forEach(function (e) {
          if (e.entryId && e.entryId.indexOf("tweet-") === 0) {
            window.__xbbBookmarks.push(e);
            n++;
          }
        });
      });
      if (n) {
        total += n;
        const last = window.__xbbBookmarks[window.__xbbBookmarks.length - 1];
        console.log(
          "%c[bookmark-brain]%c captured " + n + " bookmarks (" + total + " total this session). newest sample:",
          "color:#5b53e8;font-weight:bold", "color:inherit",
          last
        );
      }
    } catch (err) {
      console.warn("[bookmark-brain] parse error", err);
    }
  }

  // --- wrap fetch ---
  const origFetch = window.fetch;
  window.fetch = function () {
    const args = arguments;
    return origFetch.apply(this, args).then(function (res) {
      try {
        const url = (res && res.url) || (args[0] && args[0].url) || String(args[0]);
        if (url && url.indexOf("/Bookmarks") !== -1) {
          res.clone().json().then(function (j) { handle(url, j); }).catch(function () {});
        }
      } catch (e) {}
      return res;
    });
  };

  // --- wrap XHR (X sometimes uses XMLHttpRequest) ---
  const origOpen = XMLHttpRequest.prototype.open;
  const origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__xbbUrl = url;
    return origOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function () {
    const xhr = this;
    xhr.addEventListener("load", function () {
      try {
        if (xhr.__xbbUrl && xhr.__xbbUrl.indexOf("/Bookmarks") !== -1) {
          handle(xhr.__xbbUrl, JSON.parse(xhr.responseText));
        }
      } catch (e) {}
    });
    return origSend.apply(this, arguments);
  };

  console.log(
    "%c[bookmark-brain]%c capture hook installed. Open https://x.com/i/bookmarks and scroll — " +
      "captured bookmarks log here, and live in window.__xbbBookmarks",
    "color:#5b53e8;font-weight:bold", "color:inherit"
  );
})();
