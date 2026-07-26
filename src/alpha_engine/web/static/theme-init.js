/* Sets the theme before first paint so there's no flash. Loaded synchronously
   in <head>; the strict CSP forbids inline scripts, so this lives in its own
   file. Kept deliberately tiny. */
(function () {
  "use strict";
  try {
    var saved = localStorage.getItem("ae-theme");
    var theme = saved || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
    document.documentElement.dataset.theme = theme;
  } catch (e) {
    document.documentElement.dataset.theme = "dark";
  }
})();
