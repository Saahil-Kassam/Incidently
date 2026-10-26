// Theme: runs in <head> before the first paint, so a light page never flashes dark.
// The choice ("system", "light", or "dark") is kept per browser; "system" follows the OS.
// A classic script, not a module, so it blocks rendering just long enough to set the theme.

(function () {
  var KEY = "incidently-theme";
  var media = window.matchMedia("(prefers-color-scheme: light)");

  function choice() {
    try {
      var saved = localStorage.getItem(KEY);
      return saved === "light" || saved === "dark" ? saved : "system";
    } catch (e) {
      return "system"; // storage blocked: follow the OS
    }
  }

  function apply() {
    var c = choice();
    var light = c === "light" || (c === "system" && media.matches);
    document.documentElement.dataset.theme = light ? "light" : "dark";
  }

  apply();
  media.addEventListener("change", apply);

  window.incidentlyTheme = {
    choice: choice,
    set: function (next) {
      try {
        if (next === "system") localStorage.removeItem(KEY);
        else localStorage.setItem(KEY, next);
      } catch (e) {
        /* the change still applies to this page */
      }
      // Ease the change of brightness instead of flashing the whole page.
      var root = document.documentElement;
      root.classList.add("theme-changing");
      apply();
      if (next !== "system") root.dataset.theme = next; // in case storage is blocked
      setTimeout(function () { root.classList.remove("theme-changing"); }, 300);
    },
  };
})();
