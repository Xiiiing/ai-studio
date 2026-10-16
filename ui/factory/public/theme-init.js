// Apply before the app or styles render. No user configuration is stored here.
(function () {
  var preference = "system";
  try {
    var saved = localStorage.getItem("studio.theme");
    if (saved === "light" || saved === "dark") preference = saved;
  } catch (_) {}
  var theme =
    preference === "system"
      ? matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light"
      : preference;
  document.documentElement.dataset.theme = theme;
  document.documentElement.dataset.themePreference = preference;
  document.documentElement.style.colorScheme = theme;
  var meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = theme === "dark" ? "#14151c" : "#f5f5f8";
})();
