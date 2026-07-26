/* Wires #theme-btn on any page that has one.

   `theme-init.js` sets the theme before first paint; this handles the click
   afterwards. Split out because three pages need it now (landing, dashboard,
   terminal) and three copies of five lines is how they drift.

   Pages that must redraw on a theme change (the dashboard re-fills its inline
   SVG from CSS custom properties) listen for the `themechange` event rather
   than this file knowing anything about them. */
(function () {
  "use strict";
  var btn = document.getElementById("theme-btn");
  if (!btn) return;

  btn.addEventListener("click", function () {
    var next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("ae-theme", next); } catch (e) { /* private mode */ }
    document.dispatchEvent(new CustomEvent("themechange", { detail: { theme: next } }));
  });
})();
