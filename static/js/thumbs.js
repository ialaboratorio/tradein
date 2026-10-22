(function () {
  var RETRY_DELAYS_MS = [4000, 4000, 6000, 8000, 10000, 15000, 20000, 30000];
  var watched = new WeakSet();

  function isPendingPlaceholder(img) {
    return img.naturalWidth === 491 && img.naturalHeight === 683;
  }

  function watch(img, idProduct) {
    if (watched.has(img)) return;
    watched.add(img);
    var timer = null;
    function scheduleRetry() {
      var tries = Number(img.getAttribute("data-thumb-tries") || "0");
      if (tries >= RETRY_DELAYS_MS.length || timer !== null) return;
      img.setAttribute("data-thumb-tries", String(tries + 1));
      timer = setTimeout(function () {
        timer = null;
        if (!img.isConnected) return;
        img.src = "/img/" + idProduct + "?r=" + Date.now();
      }, RETRY_DELAYS_MS[tries]);
    }
    function onLoad() {
      if (isPendingPlaceholder(img)) scheduleRetry();
    }
    function onError() {
      scheduleRetry();
    }
    img.addEventListener("load", onLoad);
    img.addEventListener("error", onError);
    if (img.complete) {
      if (img.naturalWidth > 0) onLoad();
      else onError();
    }
  }

  window.watchThumb = watch;

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("img[data-thumb-id]").forEach(function (img) {
      watch(img, img.getAttribute("data-thumb-id"));
    });
  });
})();
