(function () {
  "use strict";

  // This is intentionally non-invasive: protected photos must render exactly
  // like normal images on every browser and device.
  const selector = "[data-protected-student-photo]";
  const protectedTarget = (target) => target && target.closest && target.closest(selector);
  const blockImageAction = (event) => {
    if (!protectedTarget(event.target)) return;
    event.preventDefault();
    event.stopPropagation();
  };

  const style = document.createElement("style");
  style.textContent = `${selector} {
    -webkit-user-drag: none;
    -webkit-touch-callout: none;
    user-select: none;
  }`;
  document.head.appendChild(style);

  ["contextmenu", "dragstart", "selectstart", "copy", "cut"].forEach((eventName) => {
    document.addEventListener(eventName, blockImageAction, true);
  });

  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s" && document.querySelector(selector)) {
      event.preventDefault();
    }
  }, true);

  document.querySelectorAll(selector).forEach((image) => {
    image.draggable = false;
  });
})();
