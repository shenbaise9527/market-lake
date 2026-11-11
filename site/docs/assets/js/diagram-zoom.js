/**
 * 点击图解放大：把一个 figure.diagram 里的内联 SVG 克隆到全屏浮层。
 * 支持：滚轮缩放、双击复位、Esc / 点击遮罩 / 右上角 × 关闭。
 * 无依赖，样式见 assets/css/extra.css 的 .dz-* 部分。
 */
(function () {
  "use strict";

  var MAX_SCALE = 4;
  var MIN_SCALE = 0.5;
  var STEP = 0.15;

  var overlay = null;
  var host = null;
  var clone = null;
  var hint = null;
  var scale = 1;

  function renderHint() {
    if (hint) {
      hint.textContent =
        Math.round(scale * 100) + "%　滚轮缩放 · 双击复位 · Esc 关闭";
    }
  }

  function setScale(next) {
    scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, next));
    if (clone) {
      clone.style.zoom = scale;
    }
    renderHint();
  }

  function onKeydown(event) {
    if (event.key === "Escape") {
      close();
    }
  }

  function close() {
    if (!overlay) return;
    overlay.remove();
    overlay = null;
    host = null;
    clone = null;
    hint = null;
    document.body.style.overflow = "";
    document.removeEventListener("keydown", onKeydown);
  }

  function open(svg) {
    overlay = document.createElement("div");
    overlay.className = "dz-overlay";

    host = document.createElement("div");
    host.className = "dz-host";

    clone = svg.cloneNode(true);
    clone.removeAttribute("width");
    clone.removeAttribute("height");
    clone.classList.add("dz-svg");
    host.appendChild(clone);

    hint = document.createElement("div");
    hint.className = "dz-hint";

    var button = document.createElement("button");
    button.type = "button";
    button.className = "dz-close";
    button.setAttribute("aria-label", "关闭");
    button.textContent = "\u00d7";
    button.addEventListener("click", close);

    overlay.appendChild(host);
    overlay.appendChild(hint);
    overlay.appendChild(button);

    // 点击遮罩（而非图本体）关闭
    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) close();
    });

    // 滚轮缩放：阻止页面跟着滚动
    overlay.addEventListener(
      "wheel",
      function (event) {
        event.preventDefault();
        setScale(scale - Math.sign(event.deltaY) * STEP);
      },
      { passive: false }
    );

    overlay.addEventListener("dblclick", function (event) {
      event.preventDefault();
      setScale(1);
    });

    document.body.appendChild(overlay);
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", onKeydown);

    setScale(1);
  }

  document.addEventListener("click", function (event) {
    if (!event.target.closest) return;
    var figure = event.target.closest("figure.diagram");
    if (!figure) return;
    var svg = figure.querySelector("svg");
    if (!svg) return;
    event.preventDefault();
    open(svg);
  });
})();
