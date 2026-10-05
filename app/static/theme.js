/* Theme choice: stored per browser, applied before the first paint.
 *
 * Loaded from <head> on purpose -- the attribute has to be on <html> before the
 * page renders, or someone who picked the light theme gets a flash of dark
 * first. The Content-Security-Policy forbids inline scripts, so this lives in
 * its own file rather than in three lines inside index.html.
 *
 * The same three choices the settings panel offers: follow the system, or pin
 * light or dark. Following the system means following it live -- the media
 * query is listened to, not just read once at load.
 */
(function () {
  "use strict";

  var KEY = "tuyue.theme";
  var media = window.matchMedia("(prefers-color-scheme: light)");

  function stored() {
    try {
      var value = localStorage.getItem(KEY);
      return value === "light" || value === "dark" ? value : "system";
    } catch (error) {
      // Private mode, or storage switched off: just follow the system.
      return "system";
    }
  }

  function resolve(choice) {
    if (choice === "light" || choice === "dark") return choice;
    return media.matches ? "light" : "dark";
  }

  function apply() {
    var effective = resolve(stored());
    document.documentElement.setAttribute("data-theme", effective);
    return effective;
  }

  function set(choice) {
    try {
      if (choice === "light" || choice === "dark") localStorage.setItem(KEY, choice);
      else localStorage.removeItem(KEY);
    } catch (error) {
      // The choice then lasts only for this page load, which beats failing.
    }
    return apply();
  }

  function onSystemChange() {
    if (stored() === "system") apply();
  }
  if (media.addEventListener) media.addEventListener("change", onSystemChange);
  else if (media.addListener) media.addListener(onSystemChange);

  apply();

  window.appTheme = {
    choice: stored,
    set: set,
    effective: function () {
      return document.documentElement.getAttribute("data-theme");
    },
  };
})();
