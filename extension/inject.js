// Runs in the isolated content-script world. Its only job is to inject capture.js into the
// PAGE (MAIN) world, where it can wrap the page's own fetch/XHR. This script-tag injection is
// the cross-browser way to reach the MAIN world (works on Chrome/Brave + Firefox alike).
(function () {
  const runtime = (typeof browser !== "undefined" ? browser : chrome).runtime;
  const s = document.createElement("script");
  s.src = runtime.getURL("capture.js");
  s.onload = function () { this.remove(); };
  (document.head || document.documentElement).appendChild(s);
})();
