(function () {
  "use strict";

  // Keep protected photos out of a literal <img> element. Mobile browsers can
  // offer "Download image" for <img> even when contextmenu is cancelled.
  const selector = "[data-protected-student-photo]";
  const style = document.createElement("style");
  style.textContent = `
    ${selector}, .protected-photo-surface {
      -webkit-user-drag: none;
      -webkit-touch-callout: none;
      user-select: none;
    }
    .protected-photo-surface {
      display: block;
      width: 100%;
      height: 100%;
      background-repeat: no-repeat;
      background-position: center;
      background-size: cover;
      pointer-events: auto;
    }
  `;
  document.head.appendChild(style);

  const protectedTarget = (target) => target && target.closest && target.closest(selector);
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
    if (!(image instanceof HTMLImageElement) || image.dataset.protectedSurface === "1") return;
    const source = image.currentSrc || image.getAttribute("src");
    if (!source) return;

    const surface = document.createElement("span");
    surface.className = `${image.className || ""} protected-photo-surface`.trim();
    surface.setAttribute("data-protected-student-photo", "");
    surface.setAttribute("role", "img");
    if (image.alt) surface.setAttribute("aria-label", image.alt);
    surface.style.backgroundImage = `url("${String(source).replace(/(["\\])/g, "\\$1")}")`;
    image.dataset.protectedSurface = "1";
    image.replaceWith(surface);
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
