(function () {
  const current = document.currentScript;
  const base = (current && current.dataset.base) || new URL(current.src).origin;
  const height = (current && current.dataset.height) || "820";
  const iframe = document.createElement("iframe");
  iframe.src = base + "/";
  iframe.title = "SupplyMargin AI";
  iframe.loading = "lazy";
  iframe.style.cssText = [
    "width:100%",
    "height:" + height + "px",
    "border:0",
    "border-radius:18px",
    "background:#f4f3ed"
  ].join(";");
  current.parentNode.insertBefore(iframe, current.nextSibling);
})();

