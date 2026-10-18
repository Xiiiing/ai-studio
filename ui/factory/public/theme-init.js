// Apply before the app or styles render. No user configuration is stored here.
(function () {
  var preference = "system";
  var accent = "slate";
  try {
    var saved = localStorage.getItem("studio.theme");
    if (saved === "light" || saved === "dark") preference = saved;
  } catch (_) {}
  try {
    var savedAccent = localStorage.getItem("studio.accent");
    if (
      ["slate", "graphite", "teal", "jade", "iris", "bronze"].indexOf(
        savedAccent,
      ) !== -1
    )
      accent = savedAccent;
  } catch (_) {}
  var theme =
    preference === "system"
      ? matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light"
      : preference;
  document.documentElement.dataset.theme = theme;
  document.documentElement.dataset.themePreference = preference;
  document.documentElement.dataset.accent = accent;
  document.documentElement.style.colorScheme = theme;
  var meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = theme === "dark" ? "#171719" : "#f7f7f8";
})();
