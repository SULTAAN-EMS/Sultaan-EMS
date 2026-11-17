(function () {
  "use strict";

  // Keep the real <img> so every browser renders the photo consistently.
  // The image itself does not receive pointer events, so mobile Chrome does
  // not treat a long press as an image-download action.
  const selector = "[data-protected-student-photo]";
  const hostSelector = `${selector}, [data-protected-photo-host]`;
  const style = document.createElement("style");
  style.textContent = `
    ${selector} {
      -webkit-user-drag: none;
      -webkit-touch-callout: none;
      user-select: none;
      pointer-events: none;
    }
    [data-protected-photo-host] { -webkit-touch-callout: none; user-select: none; }
  `;
  document.head.appendChild(style);

  const protectedTarget = (target) => target && target.closest && target.closest(hostSelector);
  const blockImageAction = (event) => {
    if (!protectedTarget(event.target)) return;
    event.preventDefault();
    event.stopPropagation();
  };

  ["contextmenu", "dragstart", "selectstart", "copy", "cut"].forEach((eventName) => {
    document.addEventListener(eventName, blockImageAction, true);
  });

  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s" && document.querySelector(selector)) {
      event.preventDefault();
    }
  }, true);

  const protectImage = (image) => {
    if (!(image instanceof HTMLImageElement) || image.dataset.protectedReady === "1") return;
    image.draggable = false;
    image.dataset.protectedReady = "1";
    image.setAttribute("aria-hidden", "false");
    if (image.parentElement) image.parentElement.setAttribute("data-protected-photo-host", "");
  };

  const protectPhotos = (root = document) => {
    if (root instanceof HTMLImageElement && root.matches(selector)) protectImage(root);
    if (root.querySelectorAll) root.querySelectorAll(selector).forEach(protectImage);
  };

  protectPhotos();

  // QR/portal content can add the photo after the initial page load.
  const observer = new MutationObserver((mutations) => {
    mutations.forEach((mutation) => {
      if (mutation.type === "attributes" && mutation.target instanceof HTMLImageElement) {
        protectImage(mutation.target);
        return;
      }
      mutation.addedNodes.forEach((node) => {
        if (node.nodeType === Node.ELEMENT_NODE) protectPhotos(node);
      });
    });
  });
  observer.observe(document.documentElement, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ["src"],
  });
})();
