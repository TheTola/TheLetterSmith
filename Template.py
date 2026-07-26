# ===============================
# File: Template.py
# Purpose: HTML/CSS/JS templates for the eLetter viewer
# ===============================

TEMPLATE_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{{TITLE}}</title>
  <link rel="stylesheet" href="styles.css">

  <link rel="preload" as="image" href="gallery/pages/cover.png" fetchpriority="high">
  <link rel="preload" as="image" href="gallery/controls/cleft.png" fetchpriority="high">
  <link rel="preload" as="image" href="gallery/controls/cright.png" fetchpriority="high">
  <link rel="preload" as="image" href="gallery/controls/R_cleft.png" fetchpriority="high">
  <link rel="preload" as="image" href="gallery/controls/R_cright.png" fetchpriority="high">
  <link rel="preload" as="audio" href="gallery/sounds/glissando.mp3?v={{BUILD_ID}}" type="audio/mpeg">
</head>

<body data-has-message="{{HAS_MESSAGE}}" data-has-user-music="true" data-message-preset="{{MESSAGE_OVERLAY_PRESET}}" data-tap-navigation="true">
  <div id="curtain-overlay" aria-hidden="false" role="dialog" aria-modal="true" aria-labelledby="begin-button">
    <img id="curtain-left"  src="gallery/controls/cleft.png"  alt="" aria-hidden="true" decoding="async" fetchpriority="high">
    <img id="curtain-right" src="gallery/controls/cright.png" alt="" aria-hidden="true" decoding="async" fetchpriority="high">
    <img id="curtain-left-detail"  src="gallery/controls/R_cleft.png"  alt="" aria-hidden="true" decoding="async" fetchpriority="high">
    <img id="curtain-right-detail" src="gallery/controls/R_cright.png" alt="" aria-hidden="true" decoding="async" fetchpriority="high">
    <button id="begin-button" type="button">Tap to Begin</button>
    <p id="mobile-sound-hint">Sound starts after you tap Begin.</p>
  </div>

  <div id="slideshow" aria-hidden="true">
    <div id="turn" aria-hidden="true">
      <div class="sheet sheet-front visible" id="sheetFront">
        <img id="turnFrontImg" alt="" aria-hidden="true">
      </div>
      <div class="sheet sheet-back hidden" id="sheetBack">
        <img id="turnBackImg" alt="" aria-hidden="true">
      </div>
    </div>
    <div id="turnShadow" aria-hidden="true"></div>

    <section class="slide active" data-index="0" id="slide-0">
      <img src="gallery/pages/cover.png" alt="Cover Page" decoding="async" fetchpriority="high">
    </section>

    <section class="slide" data-index="1" id="slide-1">
      <img src="gallery/pages/letter.png" alt="Main Letter" decoding="async">
    </section>

    <section class="slide" data-index="2" id="slide-2">
      <img src="gallery/pages/wall.png" alt="Text Wall Background" decoding="async">
      {{MESSAGE_OVERLAY_HTML}}
    </section>

    <section class="slide" data-index="3" id="slide-3">
      <img src="gallery/pages/back.png" alt="Final Backdrop" decoding="async">
    </section>

    <button id="prev" class="nav-button" type="button" title="Previous page" aria-label="Previous page">
      <img src="gallery/controls/ppage.png" alt="" aria-hidden="true">
    </button>
    <button id="next" class="nav-button" type="button" title="Next page" aria-label="Next page">
      <img src="gallery/controls/npage.png" alt="" aria-hidden="true">
    </button>

    <div id="progress" aria-live="polite">Page 1 of 4</div>
    <div id="mobile-actions" aria-label="Viewer actions">
      <button id="fullscreen-button" class="hud-button text-action" type="button">Fullscreen</button>
      <button id="share-button" class="hud-button text-action" type="button" hidden>Share</button>
    </div>

    {{MESSAGE_BUTTON_HTML}}
  </div>

  <div id="volume-control" aria-hidden="true">
    <button
      id="volume-icon"
      class="hud-button hud-button-icon"
      type="button"
      title="Volume"
      aria-label="Toggle volume slider"
      aria-controls="volume-slider"
      aria-expanded="false"
    >
      <img id="volume-icon-img" src="gallery/controls/volon.png" alt="" aria-hidden="true" decoding="async">
    </button>
  </div>

  <script>
    const INITIAL_VOLUME = {{INITIAL_VOLUME}};
    const HAS_MESSAGE = {{HAS_MESSAGE}};
  </script>

  <script src="script.js"></script>
</body>
</html>
"""

TEMPLATE_CSS = r"""
:root{
  --font-ui:Inter,Segoe UI,Roboto,Arial,sans-serif;
  --font-letter:Georgia,'Times New Roman',serif;
  --bg-top:#0b0c12;
  --bg-bottom:#05060a;
  --bg-rim:rgba(255,255,255,.08);
  --bg-depth:rgba(0,0,0,.35);
  --text-main:#fff;
  --text-soft:rgba(255,255,255,.86);
  --accent:#9bfffb;
  --accent-strong:#00ffff;
  --hud-surface-top:rgba(18,27,41,.80);
  --hud-surface-bottom:rgba(7,11,19,.62);
  --hud-border:rgba(255,255,255,.14);
  --hud-border-strong:rgba(255,255,255,.24);
  --hud-shadow:0 12px 28px rgba(0,0,0,.28);
  --hud-shadow-strong:0 18px 38px rgba(0,0,0,.34);
  --page-radius:14px;
  --panel-radius:18px;
  --pill-radius:999px;
  --paper-edge:rgba(112,81,44,.34);
  --paper-line:rgba(255,255,255,.42);
  --paper-shadow:rgba(0,0,0,.54);
  --message-overlay-rgb:245,235,210;
  --message-overlay-opacity:.68;
  --message-ink:#221710;
  --wall-fade-ms:900ms;
  --stage-backdrop:
    radial-gradient(900px 600px at 30% 25%, var(--bg-rim), transparent 60%),
    radial-gradient(900px 600px at 80% 70%, var(--bg-depth), transparent 60%),
    linear-gradient(180deg, var(--bg-top), var(--bg-bottom));
  --shadow-page:0 18px 40px rgba(0,0,0,.45);
  --motion-fast:180ms ease;
  --motion-medium:240ms cubic-bezier(.2,.8,.2,1);
  --nav-offset:clamp(16px,4vw,50px);
  --nav-size:clamp(78px,9.5vw,120px);
  --nav-pad:clamp(8px,1vw,12px);
  --icon-size:clamp(46px,5vw,56px);
  --close-size:clamp(42px,4.8vw,48px);
  --corner-offset:clamp(14px,3vw,20px);
  --slider-width:clamp(112px,16vw,150px);
  --wall-gap:clamp(20px,4vw,42px);
  --wall-max-width:960px;
  --wall-frame-pad:clamp(12px,2vw,20px);
  --wall-block-pad:clamp(24px,4vw,40px);
  --wall-inline-pad:clamp(18px,3vw,30px);
}
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:100%;height:100%;overflow:hidden;background:var(--bg-top);color:var(--text-main);font-family:var(--font-ui)}
button,input{font:inherit}
button{margin:0;border:0;background:none;color:inherit}

#slideshow{
  position:relative;width:100%;height:100%;
  perspective:6000px;
  perspective-origin:50% 50%;
  opacity:0;visibility:hidden;pointer-events:none;
  transition:opacity var(--motion-fast);
  background:var(--stage-backdrop);
  transform-style:preserve-3d;
  -webkit-transform-style:preserve-3d;
}
body.stage-ready #slideshow{opacity:1;visibility:visible;pointer-events:auto}

#curtain-overlay{position:absolute;inset:0;z-index:9999;overflow:hidden;pointer-events:all;opacity:0;background:var(--stage-backdrop);will-change:opacity}
#curtain-overlay.is-visible{animation:curtainIntroFadeIn 520ms ease-out forwards}
#curtain-left,#curtain-right,#curtain-left-detail,#curtain-right-detail{position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover;pointer-events:none;opacity:0;will-change:opacity,transform}
#curtain-left,#curtain-right{z-index:10000}
#curtain-left-detail,#curtain-right-detail{z-index:10001}
#curtain-overlay.is-visible #curtain-left,#curtain-overlay.is-visible #curtain-right,#curtain-overlay.is-visible #curtain-left-detail,#curtain-overlay.is-visible #curtain-right-detail{animation:curtainPanelFadeIn 420ms ease-out forwards}
#curtain-overlay.curtain-fallback #curtain-left,#curtain-overlay.curtain-fallback #curtain-right,#curtain-overlay.curtain-fallback #curtain-left-detail,#curtain-overlay.curtain-fallback #curtain-right-detail{display:none}
#begin-button{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);min-width:min(80vw,280px);padding:14px 30px;border:1px solid rgba(155,255,251,.42);border-radius:var(--pill-radius);background:linear-gradient(180deg,rgba(15,24,38,.88),rgba(6,10,18,.72));color:var(--text-main);font-size:clamp(22px,3.2vw,42px);font-weight:700;letter-spacing:.02em;cursor:pointer;z-index:10002;animation:pulse 2s infinite;box-shadow:var(--hud-shadow),0 0 0 1px rgba(0,255,255,.14)}
#curtain-overlay:not(.is-visible) #begin-button{opacity:0}
#mobile-sound-hint{display:none;position:absolute;left:50%;top:calc(50% + 66px);transform:translateX(-50%);width:min(82vw,360px);margin:0;color:var(--text-soft);font:600 14px/1.4 var(--font-ui);text-align:center;z-index:10002}
#begin-button:hover{background:linear-gradient(180deg,rgba(20,31,48,.94),rgba(8,12,20,.84))}
@keyframes curtainIntroFadeIn{from{opacity:0}to{opacity:1}}
@keyframes curtainPanelFadeIn{from{opacity:0}to{opacity:1}}
@keyframes pulse{0%,100%{opacity:.68;transform:translate(-50%,-50%) scale(1)}50%{opacity:1;transform:translate(-50%,-50%) scale(1.04)}}
@keyframes curtainLeftOut{from{transform:translateX(0)}to{transform:translateX(-100vw)}}
@keyframes curtainRightOut{from{transform:translateX(0)}to{transform:translateX(100vw)}}

.slide{position:absolute;inset:0;opacity:0;pointer-events:none;z-index:5;transform-style:preserve-3d;-webkit-transform-style:preserve-3d;backface-visibility:hidden;-webkit-backface-visibility:hidden}
.slide.active{opacity:1;pointer-events:auto}
.slide.peek{opacity:1;pointer-events:none;z-index:4}
.slide.ghost{opacity:0;pointer-events:none}
.slide.asset-failed img{opacity:0}
.slide.asset-failed::after{content:attr(data-fallback-label);position:absolute;inset:50% auto auto 50%;transform:translate(-50%,-50%);width:min(520px,72%);padding:18px 22px;color:var(--text-main);text-align:center;font:700 20px/1.4 var(--font-ui);background:rgba(0,0,0,.58);border:1px solid var(--hud-border);border-radius:var(--page-radius);box-shadow:var(--shadow-page);z-index:6}
.slide img{max-width:100%;max-height:100%;position:absolute;inset:0;margin:auto;object-fit:contain;border-radius:var(--page-radius);box-shadow:inset 0 0 30px rgba(0,0,0,.20),var(--shadow-page)}

#turn{position:absolute;left:0;top:0;width:0;height:0;opacity:0;pointer-events:none;overflow:hidden;border-radius:var(--page-radius);z-index:40;transform-style:preserve-3d;-webkit-transform-style:preserve-3d;will-change:transform,opacity}
.sheet{position:absolute;inset:0;border-radius:var(--page-radius);overflow:hidden;transform-style:preserve-3d;-webkit-transform-style:preserve-3d;backface-visibility:hidden;-webkit-backface-visibility:hidden;transform-origin:0% 50%;-webkit-transform-origin:0% 50%;--edgeA:0;--glintA:0}
.sheet img{width:100%;height:100%;object-fit:contain;border-radius:var(--page-radius);display:block}
.sheet.hidden{opacity:0;visibility:hidden}
.sheet.visible{opacity:1;visibility:visible}
.sheet::after{content:"";position:absolute;inset:0;pointer-events:none;background:linear-gradient(90deg,rgba(255,255,255,0) 60%,rgba(255,255,255,var(--edgeA)) 86%,rgba(255,255,255,0) 100%);mix-blend-mode:screen}
.sheet::before{content:"";position:absolute;inset:-10%;pointer-events:none;background:radial-gradient(320px 220px at 92% 45%,rgba(255,255,255,var(--glintA)) 0%,rgba(255,255,255,0) 62%);mix-blend-mode:overlay}
#turnShadow{position:absolute;left:0;top:0;width:0;height:0;pointer-events:none;z-index:39;opacity:0;border-radius:var(--page-radius);overflow:hidden;--sx:18%;--sd:.28;--sb:14px;background:radial-gradient(140% 90% at var(--sx) 55%,rgba(0,0,0,var(--sd)) 0%,rgba(0,0,0,0) 62%);filter:blur(var(--sb))}

.nav-button,.hud-button{display:inline-flex;align-items:center;justify-content:center;border:1px solid var(--hud-border);background:linear-gradient(180deg,var(--hud-surface-top),var(--hud-surface-bottom));color:var(--text-main);box-shadow:var(--hud-shadow);backdrop-filter:blur(10px);cursor:pointer;transition:transform var(--motion-fast),background var(--motion-fast),border-color var(--motion-fast),box-shadow var(--motion-fast),opacity var(--motion-fast),visibility 0s linear 220ms}
.nav-button:hover,.hud-button:hover{background:linear-gradient(180deg,rgba(25,37,55,.88),rgba(10,14,24,.76));border-color:var(--hud-border-strong);box-shadow:var(--hud-shadow-strong)}
.nav-button:active,.hud-button:active{transform:scale(.98)}
.nav-button img,.hud-button img{display:block;width:100%;height:100%;object-fit:contain;pointer-events:none;filter:drop-shadow(0 6px 12px rgba(0,0,0,.34))}
#begin-button:focus-visible,.nav-button:focus-visible,.hud-button:focus-visible,#volume-slider:focus-visible{outline:3px solid rgba(255,255,255,.94);outline-offset:4px}

.nav-button{position:absolute;top:50%;width:calc(var(--nav-size) + (2 * var(--nav-pad)));height:calc(var(--nav-size) + (2 * var(--nav-pad)));padding:var(--nav-pad);border-radius:50%;z-index:108}
#prev{left:var(--nav-offset);transform:translateY(-50%)}
#next{right:var(--nav-offset);transform:translateY(-50%)}
.nav-button:hover{transform:translateY(-50%) scale(1.04)}
.nav-button img{width:var(--nav-size);height:var(--nav-size)}
.nav-button[disabled]{opacity:.35;cursor:not-allowed;filter:grayscale(1);box-shadow:none}
.nav-button[disabled]:hover{transform:translateY(-50%)}

#progress{position:absolute;right:var(--corner-offset);bottom:var(--corner-offset);display:inline-flex;align-items:center;min-height:40px;padding:0 14px;border:1px solid rgba(255,255,255,.10);border-radius:var(--pill-radius);background:rgba(6,9,16,.56);box-shadow:var(--hud-shadow);color:var(--text-soft);font:700 clamp(12px,1.2vw,14px)/1 var(--font-ui);letter-spacing:.08em;text-transform:uppercase;z-index:101}
#mobile-actions{position:absolute;top:var(--corner-offset);left:var(--corner-offset);display:flex;gap:8px;z-index:102}
.text-action{min-height:40px;padding:0 14px;border-radius:var(--pill-radius);font:700 12px/1 var(--font-ui)}
.text-action[hidden]{display:none}

.text-wall{position:absolute;top:50%;left:50%;width:min(var(--wall-max-width),calc(100% - (2 * var(--wall-gap))));max-height:calc(100% - (2 * var(--wall-gap)));overflow:auto;padding:var(--wall-frame-pad);border:1px solid var(--paper-edge);border-radius:var(--panel-radius);background:linear-gradient(180deg,rgba(var(--message-overlay-rgb),var(--message-overlay-opacity)),rgba(var(--message-overlay-rgb),var(--message-overlay-opacity)));color:var(--message-ink);box-shadow:0 30px 80px var(--paper-shadow),0 12px 28px rgba(0,0,0,.22),inset 0 1px 0 var(--paper-line);z-index:105;opacity:0;visibility:hidden;pointer-events:none;transform:translate(-50%,-48%);transition:opacity var(--wall-fade-ms) ease,transform var(--motion-medium),visibility 0s linear var(--wall-fade-ms);isolation:isolate;scrollbar-width:thin;scrollbar-color:rgba(92,67,40,.52) rgba(0,0,0,.06)}
.text-wall.is-open{opacity:1;visibility:visible;pointer-events:auto;transform:translate(-50%,-50%);transition:opacity var(--wall-fade-ms) ease,transform var(--motion-medium),visibility 0s}
.text-wall::before{content:"";position:absolute;inset:10px;border:1px solid rgba(116,85,47,.16);border-radius:calc(var(--panel-radius) - 4px);box-shadow:inset 0 0 0 1px rgba(255,255,255,.16),inset 0 18px 28px rgba(255,255,255,.08);pointer-events:none}
.text-wall::after{content:"";position:absolute;inset:0;border-radius:inherit;background:linear-gradient(180deg,rgba(255,255,255,.16),rgba(255,255,255,0) 22%,rgba(0,0,0,.10) 100%);mix-blend-mode:soft-light;pointer-events:none}
.text-wall-content{position:relative;z-index:1;max-width:44rem;margin:0 auto;padding:var(--wall-block-pad) var(--wall-inline-pad);color:var(--message-ink);font-family:var(--font-letter);font-size:clamp(17px,1.25vw,20px);line-height:1.68;text-rendering:optimizeLegibility}
.text-wall-content,.text-wall-content *{max-width:100%;overflow-wrap:anywhere}
.text-wall-content > :first-child{margin-top:0 !important}
.text-wall-content > :last-child{margin-bottom:0 !important}
.text-wall-content :where(p,div,blockquote,ul,ol,h1,h2,h3,h4,table){margin-left:0 !important;margin-right:0 !important}
.text-wall-content :where(img,table){height:auto}
.text-wall-content a[href^="hypernote:"]{text-decoration:none;cursor:help;color:inherit;outline-offset:3px}
.text-wall-content a[href^="hypernote:"]:focus-visible{outline:2px solid rgba(5,99,193,.55);border-radius:3px}
.hypernote-tooltip{position:fixed;left:0;top:0;max-width:min(22rem,calc(100vw - 32px));padding:12px 14px;border:1px solid rgba(112,81,44,.42);border-radius:8px;background:linear-gradient(180deg,#fff8df,#f2e3bd);color:#2b1b0e;box-shadow:0 18px 42px rgba(0,0,0,.32),inset 0 1px 0 rgba(255,255,255,.65);font:500 15px/1.45 Georgia,'Times New Roman',serif;white-space:pre-wrap;overflow-wrap:anywhere;z-index:220;opacity:0;visibility:hidden;transform:translateY(4px);transition:opacity var(--motion-fast),transform var(--motion-fast),visibility 0s linear 160ms;pointer-events:none}
body[data-message-preset="black"] .hypernote-tooltip{border-color:rgba(0,0,0,.18);background:#fff;color:#000;box-shadow:0 18px 42px rgba(0,0,0,.34),inset 0 1px 0 rgba(255,255,255,.8);font:500 15px/1.45 Georgia,'Times New Roman',serif}
body[data-message-preset="white"] .hypernote-tooltip{border-color:rgba(255,255,255,.28);background:#000;color:#fff;box-shadow:0 18px 42px rgba(0,0,0,.45),inset 0 1px 0 rgba(255,255,255,.12);font:500 15px/1.45 Georgia,'Times New Roman',serif}
body[data-message-preset="paper"] .hypernote-tooltip{border-color:rgba(112,81,44,.42);background:#f5ebd2;color:#111;box-shadow:0 18px 42px rgba(0,0,0,.28),inset 0 1px 0 rgba(255,255,255,.55);font:500 18px/1.45 "Brush Script MT","Segoe Script","Apple Chancery","Lucida Handwriting",cursive}
body[data-message-preset="clear"] .hypernote-tooltip{border-color:rgba(255,242,0,.68);background:rgba(255,255,255,.14);color:#fff200;box-shadow:0 18px 42px rgba(0,0,0,.30),0 0 22px rgba(255,242,0,.22),inset 0 1px 0 rgba(255,255,255,.38);backdrop-filter:blur(10px);font:500 15px/1.45 Georgia,'Times New Roman',serif}
.hypernote-tooltip.is-visible{opacity:1;visibility:visible;transform:translateY(0);transition:opacity var(--motion-fast),transform var(--motion-fast),visibility 0s}
.text-wall::-webkit-scrollbar{width:10px}
.text-wall::-webkit-scrollbar-track{background:rgba(0,0,0,.06);border-radius:999px}
.text-wall::-webkit-scrollbar-thumb{background:rgba(92,67,40,.52);border-radius:999px}

.hud-button{width:calc(var(--icon-size) + 10px);height:calc(var(--icon-size) + 10px);padding:5px;border-radius:var(--pill-radius)}
#open-text,#close-text{opacity:0;visibility:hidden;pointer-events:none}
#open-text.is-visible,#close-text.is-visible{opacity:1;visibility:visible;pointer-events:auto;transition-delay:0s}
#open-text{position:absolute;left:var(--corner-offset);bottom:var(--corner-offset);z-index:101;transform:translateY(8px) scale(.96)}
#open-text.is-visible{transform:translateY(0) scale(1)}
#open-text img{width:var(--icon-size);height:var(--icon-size);padding:6px}
#open-text:hover{transform:translateY(0) scale(1.05)}
#close-text{position:absolute;top:clamp(16px,4vw,40px);right:clamp(16px,4vw,40px);width:var(--close-size);height:var(--close-size);border-radius:14px;font-size:clamp(24px,2.8vw,28px);line-height:1;z-index:106;transform:scale(.96)}
#close-text.is-visible{transform:scale(1)}

#volume-control{position:absolute;top:calc(50% + var(--nav-size) + 34px);right:var(--nav-offset);display:flex;align-items:center;gap:10px;padding:8px 10px 8px 8px;border:1px solid rgba(255,255,255,.08);border-radius:var(--pill-radius);background:rgba(42,46,54,.72);box-shadow:var(--hud-shadow);backdrop-filter:blur(10px);z-index:99;opacity:0;visibility:hidden;pointer-events:none;transform:translateY(0);transition:opacity var(--motion-fast),transform var(--motion-medium),visibility 0s linear 220ms}
body.stage-ready #volume-control{opacity:1;visibility:visible;pointer-events:auto;transition-delay:0s}
#volume-control.slider-open{border-color:rgba(155,255,251,.18);background:rgba(50,55,64,.82)}
#volume-icon{flex:none}
#volume-icon-img{width:var(--icon-size);height:var(--icon-size);padding:6px}
#volume-slider{width:0;min-width:0;-webkit-appearance:none;appearance:none;height:4px;opacity:0;pointer-events:none;background:rgba(255,255,255,.30);border-radius:2px;transform:scaleX(.92);transform-origin:right center;transition:width var(--motion-medium),opacity var(--motion-fast),transform var(--motion-fast)}
#volume-control.slider-open #volume-slider{width:var(--slider-width);opacity:1;pointer-events:auto;transform:scaleX(1)}
#volume-slider::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;width:16px;height:16px;background:var(--accent-strong);border-radius:50%;cursor:pointer;border:2px solid #fff;box-shadow:0 0 0 4px rgba(0,255,255,.12)}
#volume-slider::-moz-range-thumb{width:16px;height:16px;background:var(--accent-strong);border:2px solid #fff;border-radius:50%;cursor:pointer;box-shadow:0 0 0 4px rgba(0,255,255,.12)}

#audio-enable-button{position:absolute;left:50%;bottom:calc(var(--corner-offset) + 54px + env(safe-area-inset-bottom));display:none;align-items:center;justify-content:center;min-height:40px;padding:0 16px;border:1px solid rgba(155,255,251,.32);border-radius:var(--pill-radius);background:linear-gradient(180deg,rgba(18,27,41,.88),rgba(7,11,19,.76));color:var(--text-main);box-shadow:var(--hud-shadow);font:700 13px/1 var(--font-ui);transform:translateX(-50%);z-index:130;cursor:pointer}
#audio-enable-button.is-visible{display:inline-flex}

@media (max-width: 900px){:root{--nav-size:clamp(68px,11vw,96px);--wall-gap:clamp(18px,4vw,30px)}.text-wall-content{font-size:clamp(16px,1.8vw,18px)}}
@media (max-width: 640px){:root{--nav-size:clamp(60px,18vw,76px);--icon-size:44px;--close-size:40px;--corner-offset:16px}#mobile-sound-hint{display:block}#mobile-actions{top:calc(var(--corner-offset) + env(safe-area-inset-top));left:50%;transform:translateX(-50%)}#prev,#next{top:auto;bottom:calc(var(--corner-offset) + 8px + env(safe-area-inset-bottom));transform:none}#prev:hover,#next:hover{transform:scale(1.04)}.nav-button[disabled]:hover{transform:none}#volume-control{top:auto;right:var(--corner-offset);bottom:calc(var(--corner-offset) + 88px + env(safe-area-inset-bottom));transform:none}body.stage-ready #volume-control{transform:none}#progress{left:50%;right:auto;bottom:calc(var(--corner-offset) + env(safe-area-inset-bottom));transform:translateX(-50%)}#open-text{left:var(--corner-offset);bottom:calc(var(--corner-offset) + 88px + env(safe-area-inset-bottom))}#close-text{top:16px;right:16px}.text-wall{width:calc(100% - (2 * var(--wall-gap)));max-height:calc(100% - (2 * var(--wall-gap)) - 110px)}#volume-control.slider-open #volume-slider{width:min(34vw,128px)}}
@media (prefers-reduced-motion: reduce){*,*::before,*::after{scroll-behavior:auto !important}#slideshow,#volume-control,#open-text,#close-text,.text-wall,#volume-slider{transition:none}#begin-button{animation:none}#curtain-overlay.is-visible,#curtain-overlay.is-visible #curtain-left,#curtain-overlay.is-visible #curtain-right,#curtain-overlay.is-visible #curtain-left-detail,#curtain-overlay.is-visible #curtain-right-detail{animation-duration:0.01ms !important;animation-iteration-count:1 !important}}
"""

TEMPLATE_JS = r"""
document.addEventListener('DOMContentLoaded', () => {
  const overlay   = document.getElementById('curtain-overlay');
  const cLeft     = document.getElementById('curtain-left');
  const cRight    = document.getElementById('curtain-right');
  const cLeftDetail  = document.getElementById('curtain-left-detail');
  const cRightDetail = document.getElementById('curtain-right-detail');
  const leftCurtainLayers = [cLeft, cLeftDetail].filter(Boolean);
  const rightCurtainLayers = [cRight, cRightDetail].filter(Boolean);
  const curtainLayers = leftCurtainLayers.concat(rightCurtainLayers);
  const beginBtn  = document.getElementById('begin-button');

  const slides    = Array.from(document.querySelectorAll('.slide'));
  const prevBtn   = document.getElementById('prev');
  const nextBtn   = document.getElementById('next');
  const progress  = document.getElementById('progress');
  const fullscreenBtn = document.getElementById('fullscreen-button');
  const shareBtn = document.getElementById('share-button');

  const turn       = document.getElementById('turn');
  const turnShadow = document.getElementById('turnShadow');
  const sheetFront = document.getElementById('sheetFront');
  const sheetBack  = document.getElementById('sheetBack');
  const imgFront   = document.getElementById('turnFrontImg');
  const imgBack    = document.getElementById('turnBackImg');

  const wall       = document.getElementById('textWall');
  const closeText  = document.getElementById('close-text');
  const openText   = document.getElementById('open-text');

  const slideshowEl = document.getElementById('slideshow');
  const volumeControl = document.getElementById('volume-control');
  const volIcon   = document.getElementById('volume-icon');
  const volIconImg = document.getElementById('volume-icon-img');
  const hasMessage = (typeof HAS_MESSAGE === 'boolean') ? HAS_MESSAGE : document.body.dataset.hasMessage === 'true';
  const hasMessageOverlay = hasMessage && !!wall && !!openText && !!closeText;
  const hasMusic = document.body.dataset.hasUserMusic === 'true';
  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const curtainIntroRevealMs = prefersReducedMotion ? 80 : 520;
  const curtainFallbackOpenMs = prefersReducedMotion ? 140 : 2600;
  const curtainCleanupPadMs = prefersReducedMotion ? 20 : 0;
  const glissSafetyPadMs = prefersReducedMotion ? 120 : 450;
  const flipDurationMs = prefersReducedMotion ? 0 : 620;
  const musicFadeMs = prefersReducedMotion ? 120 : 900;
  const messageOverlayLockMs = hasMessageOverlay ? (prefersReducedMotion ? 80 : 1000) : 0;

  const TOTAL = slides.length;
  let started = false;
  let introControlsLocked = false;
  let idx = 0;
  let flipping = false;
  let wallClosedByUser = false;
  let messageOverlayLocked = false;
  let messageOverlayLockTimer = null;
  let slider = null;
  let stageReady = false;
  let introStarted = false;
  let deferredWarmStarted = false;
  let audioPrimed = false;
  let musicPrimed = false;
  let musicPrimePromise = null;
  let musicEnableButton = null;

  const BUILD_ID = "{{BUILD_ID}}";
  const playlistConfig = {{PLAYLIST_JSON}};
  const VOLUME_STORAGE_KEY = 'lettersmith-viewer-volume';
  const flipPool = Array.from({length: 10}, (_, i) => `gallery/sounds/flip${i+1}.mp3`);
  const glissSrc = 'gallery/sounds/glissando.mp3';

  function clamp(n, a, b){ return Math.max(a, Math.min(b, n)); }
  function withBuildId(url){
    if (url.startsWith('data:') || url.startsWith('blob:')) return url;
    const sep = url.includes('?') ? '&' : '?';
    return `${url}${sep}v=${encodeURIComponent(BUILD_ID)}`;
  }
  function makeAudio(url, { loop=false, volume=1 } = {}){
    const a = new Audio(withBuildId(url));
    a.preload = 'auto';
    a.loop = !!loop;
    a.volume = clamp(volume, 0, 1);
    a.playsInline = true;
    return a;
  }
  async function safePlay(audio, label){
    if (!audio) return false;
    try {
      const p = audio.play();
      if (p && typeof p.catch === 'function') {
        await p;
      }
      return true;
    } catch (err) {
      console.warn(`Audio play failed: ${label}`, err);
      return false;
    }
  }

  const playlistSources = Array.isArray(playlistConfig.tracks)
    ? playlistConfig.tracks.map((track) => String(track.src || '')).filter(Boolean)
    : [];
  const playlistRepeat = playlistConfig.repeat !== false;
  const playlistCrossfadeMs = 1000;
  const musicPlayers = (hasMusic && playlistSources.length)
    ? Array.from({length: 2}, () => makeAudio(playlistSources[0], { loop: false, volume: 0 }))
    : [];
  let musicActiveSlot = 0;
  let musicIndex = 0;
  let music = musicPlayers[0] || null;
  let musicTargetVolume = 0;
  let playlistFade = null;

  function nextMusicIndex(){
    if (!playlistSources.length) return null;
    if (musicIndex + 1 < playlistSources.length) return musicIndex + 1;
    return playlistRepeat ? 0 : null;
  }
  function applyPlaylistVolumes(progress=null){
    if (!musicPlayers.length) return;
    if (playlistFade && progress !== null){
      const t = clamp(progress, 0, 1);
      musicPlayers[playlistFade.fromSlot].volume = musicTargetVolume * (1 - t);
      musicPlayers[playlistFade.toSlot].volume = musicTargetVolume * t;
      return;
    }
    musicPlayers.forEach((player, slot) => {
      player.volume = slot === musicActiveSlot ? musicTargetVolume : 0;
    });
  }
  function configureMusicSlot(slot, index){
    const player = musicPlayers[slot];
    if (!player || !playlistSources[index]) return null;
    try{
      player.pause();
      player.src = withBuildId(playlistSources[index]);
      player.currentTime = 0;
      player.loop = false;
      player.muted = musicTargetVolume === 0;
      player.load();
    }catch(err){
      console.warn('Playlist track setup failed', err);
      return null;
    }
    return player;
  }
  async function beginPlaylistCrossfade(){
    if (playlistFade || musicPlayers.length < 2) return;
    const nextIndex = nextMusicIndex();
    if (nextIndex === null || nextIndex === musicIndex) return;
    const fromSlot = musicActiveSlot;
    const toSlot = 1 - fromSlot;
    const incoming = configureMusicSlot(toSlot, nextIndex);
    if (!incoming) return;
    incoming.volume = 0;
    playlistFade = { fromSlot, toSlot, nextIndex, startedAt: 0, pending: true };
    const ok = await safePlay(incoming, `playlist-${nextIndex + 1}`);
    if (!ok){
      playlistFade = null;
      return;
    }
    playlistFade.startedAt = performance.now();
    playlistFade.pending = false;
    function crossfadeStep(now){
      if (!playlistFade || playlistFade.pending) return;
      const progress = clamp((now - playlistFade.startedAt) / playlistCrossfadeMs, 0, 1);
      applyPlaylistVolumes(progress);
      if (progress < 1){
        requestAnimationFrame(crossfadeStep);
        return;
      }
      const completed = playlistFade;
      const outgoing = musicPlayers[completed.fromSlot];
      try{ outgoing.pause(); outgoing.currentTime = 0; }catch(_err){ }
      musicActiveSlot = completed.toSlot;
      musicIndex = completed.nextIndex;
      music = musicPlayers[musicActiveSlot];
      playlistFade = null;
      applyPlaylistVolumes();
    }
    requestAnimationFrame(crossfadeStep);
  }
  function handleMusicTimeUpdate(player, slot){
    if (slot !== musicActiveSlot || playlistFade || playlistSources.length < 2) return;
    if (!Number.isFinite(player.duration) || player.duration <= 1) return;
    const remainingMs = (player.duration - player.currentTime) * 1000;
    if (remainingMs <= playlistCrossfadeMs) beginPlaylistCrossfade();
  }
  function handleMusicEnded(slot){
    if (slot !== musicActiveSlot || playlistFade) return;
    const nextIndex = nextMusicIndex();
    if (nextIndex === null) return;
    if (nextIndex === musicIndex){
      try{ music.currentTime = 0; }catch(_err){ }
      safePlay(music, 'playlist-repeat');
      return;
    }
    musicIndex = nextIndex;
    configureMusicSlot(musicActiveSlot, musicIndex);
    music = musicPlayers[musicActiveSlot];
    applyPlaylistVolumes();
    safePlay(music, `playlist-${musicIndex + 1}`);
  }
  musicPlayers.forEach((player, slot) => {
    player.addEventListener('timeupdate', () => handleMusicTimeUpdate(player, slot));
    player.addEventListener('ended', () => handleMusicEnded(slot));
  });

  const gliss = makeAudio(glissSrc, { volume: 0.10 });
  const flipSounds = flipPool.map((href) => makeAudio(href, { volume: 0.5 }));
  const deferredAssets = [
    { as: 'image', href: 'gallery/pages/letter.png' },
    { as: 'image', href: 'gallery/pages/wall.png' },
    { as: 'image', href: 'gallery/pages/back.png' },
    { as: 'image', href: 'gallery/controls/ppage.png' },
    { as: 'image', href: 'gallery/controls/npage.png' },
    { as: 'image', href: 'gallery/controls/volon.png' },
    { as: 'image', href: 'gallery/controls/voloff.png' },
    ...(hasMessage ? [{ as: 'image', href: 'gallery/controls/showmessageicon.png' }] : []),
    ...playlistSources.map((href) => ({ as: 'audio', href, type: 'audio/mpeg' })),
    ...flipPool.map((href) => ({ as: 'audio', href, type: 'audio/mpeg' })),
  ];

  function setHiddenState(el, hidden){ if (el) el.setAttribute('aria-hidden', hidden ? 'true' : 'false'); }
  function setExpandedState(el, expanded){ if (el) el.setAttribute('aria-expanded', expanded ? 'true' : 'false'); }

  function bindPress(el, handler){
    if (!el) return;
    el.addEventListener('click', handler);
    if (el instanceof HTMLButtonElement) return;
    el.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      e.preventDefault();
      handler(e);
    });
  }

  function installHypernotes(){
    const content = document.getElementById('textWallContent');
    if (!content) return;
    const links = Array.from(content.querySelectorAll('a[href^="hypernote:"]'));
    if (!links.length) return;

    const tooltip = document.createElement('div');
    tooltip.className = 'hypernote-tooltip';
    tooltip.setAttribute('role', 'tooltip');
    document.body.appendChild(tooltip);

    let activeLink = null;
    const prefix = 'hypernote:';

    function noteFor(link){
      const href = link.getAttribute('href') || '';
      const raw = href.startsWith(prefix) ? href.slice(prefix.length) : '';
      try { return decodeURIComponent(raw); }
      catch (_err) { return raw; }
    }

    function place(link){
      const gap = 10;
      const rect = link.getBoundingClientRect();
      const width = tooltip.offsetWidth;
      const height = tooltip.offsetHeight;
      const left = clamp(rect.left + (rect.width / 2) - (width / 2), 16, window.innerWidth - width - 16);
      let top = rect.bottom + gap;
      if (top + height > window.innerHeight - 16){
        top = Math.max(16, rect.top - height - gap);
      }
      tooltip.style.left = `${left}px`;
      tooltip.style.top = `${top}px`;
    }

    function show(link){
      const note = link.dataset.hypernote || noteFor(link);
      if (!note) return;
      activeLink = link;
      tooltip.textContent = note;
      tooltip.style.visibility = 'hidden';
      tooltip.classList.add('is-visible');
      place(link);
      tooltip.style.visibility = '';
    }

    function hide(link){
      if (link && activeLink !== link) return;
      activeLink = null;
      tooltip.classList.remove('is-visible');
    }

    links.forEach((link) => {
      const note = noteFor(link);
      link.dataset.hypernote = note;
      link.setAttribute('role', 'button');
      link.setAttribute('tabindex', '0');
      link.setAttribute('aria-describedby', 'hypernote-tooltip');
      link.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (activeLink === link){ hide(link); return; }
        show(link);
      });
      link.addEventListener('mouseenter', () => show(link));
      link.addEventListener('mouseleave', () => hide(link));
      link.addEventListener('focus', () => show(link));
      link.addEventListener('blur', () => hide(link));
      link.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        e.preventDefault();
        if (activeLink === link){ hide(link); return; }
        show(link);
      });
    });

    tooltip.id = 'hypernote-tooltip';
    window.addEventListener('scroll', () => activeLink && place(activeLink), true);
    window.addEventListener('resize', () => activeLink && place(activeLink));
    document.addEventListener('pointerdown', (e) => {
      if (!activeLink) return;
      if (e.target === activeLink || activeLink.contains(e.target)) return;
      hide(activeLink);
    });
  }

  function warmDeferredAssets(){
    if (deferredWarmStarted) return;
    deferredWarmStarted = true;
    const warm = () => {
      deferredAssets.forEach((asset) => {
        const link = document.createElement('link');
        link.rel = 'preload';
        link.as = asset.as;
        link.href = asset.as === 'audio' ? withBuildId(asset.href) : asset.href;
        if (asset.type) link.type = asset.type;
        document.head.appendChild(link);
      });
    };
    if (typeof window.requestIdleCallback === 'function'){
      window.requestIdleCallback(warm, { timeout: prefersReducedMotion ? 120 : 900 });
      return;
    }
    setTimeout(warm, prefersReducedMotion ? 60 : 180);
  }

  function activeSlide(){ return slides[idx]; }
  function slideImageEl(slide){ return slide ? slide.querySelector('img') : null; }
  function slideImageSrc(slide){
    const img = slideImageEl(slide);
    return img ? img.getAttribute('src') : '';
  }
  function markSlideAssetFailed(slide, img){
    if (!slide || slide.classList.contains('asset-failed')) return;
    slide.classList.add('asset-failed');
    slide.dataset.fallbackLabel = img?.getAttribute('alt') || 'Page image unavailable';
  }

  function installImageFallbacks(){
    slides.forEach((slide) => {
      const img = slideImageEl(slide);
      if (!img) return;
      const handleError = () => markSlideAssetFailed(slide, img);
      img.addEventListener('error', handleError, { once: true });
      if (img.complete && img.naturalWidth === 0) handleError();
    });
    curtainLayers.forEach((img) => {
      if (!img) return;
      const handleError = () => {
        img.style.display = 'none';
        if (img === cLeft || img === cRight) overlay.classList.add('curtain-fallback');
      };
      img.addEventListener('error', handleError, { once: true });
      if (img.complete && img.naturalWidth === 0) handleError();
    });
  }

  function waitForImageReady(img){
    if (!img) return Promise.resolve(false);
    if (img.complete){
      if (img.naturalWidth === 0) return Promise.resolve(false);
      if (typeof img.decode === 'function') return img.decode().then(() => true).catch(() => true);
      return Promise.resolve(true);
    }
    return new Promise((resolve) => {
      img.addEventListener('load', () => resolve(true), { once: true });
      img.addEventListener('error', () => resolve(false), { once: true });
    });
  }

  function waitForCriticalAssets(){
    const criticalImages = curtainLayers.concat([slideImageEl(slides[0])]).filter(Boolean);
    const assetWait = Promise.allSettled(criticalImages.map(waitForImageReady));
    const timeoutWait = new Promise((resolve) => setTimeout(resolve, prefersReducedMotion ? 120 : 1600));
    return Promise.race([assetWait, timeoutWait]);
  }

  function revealStage(){
    if (stageReady) return;
    stageReady = true;
    setHiddenState(slideshowEl, false);
    setHiddenState(volumeControl, false);
    document.body.classList.add('stage-ready');
    setActiveIndex(0, { playSound: false });
    setTurnVisible(false);
  }

  function startCurtainIntro(){
    if (introStarted) return;
    introStarted = true;
    warmDeferredAssets();
    function onIntroEnd(e){
      if (e.animationName !== 'curtainIntroFadeIn') return;
      overlay.removeEventListener('animationend', onIntroEnd);
      revealStage();
    }
    overlay.addEventListener('animationend', onIntroEnd);
    setTimeout(revealStage, curtainIntroRevealMs);
    requestAnimationFrame(() => overlay.classList.add('is-visible'));
  }

  function updateProgress(){ progress.textContent = `Page ${idx + 1} of ${TOTAL}`; }
  function isWallPage(){ return idx === 2; }
  function setDisabled(btn, disabled){
    btn.disabled = !!disabled;
    btn.setAttribute('aria-disabled', disabled ? 'true' : 'false');
  }
  function syncButtons(){
    const locked = !started || introControlsLocked || flipping || messageOverlayLocked;
    setDisabled(prevBtn, locked || idx === 0);
    setDisabled(nextBtn, locked || idx === TOTAL - 1);
  }

  function clearMessageOverlayLock(){
    if (messageOverlayLockTimer !== null){
      clearTimeout(messageOverlayLockTimer);
      messageOverlayLockTimer = null;
    }
    messageOverlayLocked = false;
    syncButtons();
  }
  function setWallOpen(open){
    if (!hasMessageOverlay) return;
    wall.classList.toggle('is-open', open);
    setHiddenState(wall, !open);
    openText.classList.toggle('is-visible', !open);
    setHiddenState(openText, open);
    closeText.classList.toggle('is-visible', open);
    setHiddenState(closeText, !open);
    setExpandedState(openText, open);
  }
  function hideWallBeforeOverlayOpen(){
    if (!hasMessageOverlay) return;
    wall.classList.remove('is-open');
    openText.classList.remove('is-visible');
    closeText.classList.remove('is-visible');
    setHiddenState(wall, true);
    setHiddenState(openText, true);
    setHiddenState(closeText, true);
    setExpandedState(openText, false);
  }
  function beginMessageOverlayLock(){
    if (!hasMessageOverlay) return;
    if (messageOverlayLockTimer !== null) clearTimeout(messageOverlayLockTimer);
    messageOverlayLocked = true;
    syncButtons();
    hideWallBeforeOverlayOpen();
    messageOverlayLockTimer = setTimeout(() => {
      messageOverlayLockTimer = null;
      messageOverlayLocked = false;
      if (!isWallPage() || wallClosedByUser){ syncButtons(); return; }
      setWallOpen(true);
      syncButtons();
    }, messageOverlayLockMs);
  }
  function syncWallUI(){
    if (!hasMessageOverlay){
      clearMessageOverlayLock();
      if (wall) wall.classList.remove('is-open');
      if (openText) openText.classList.remove('is-visible');
      if (closeText) closeText.classList.remove('is-visible');
      setHiddenState(wall, true);
      setHiddenState(openText, true);
      setHiddenState(closeText, true);
      setExpandedState(openText, false);
      syncButtons();
      return;
    }

    if (!isWallPage()){
      clearMessageOverlayLock();
      wall.classList.remove('is-open');
      openText.classList.remove('is-visible');
      closeText.classList.remove('is-visible');
      setHiddenState(wall, true);
      setHiddenState(openText, true);
      setHiddenState(closeText, true);
      setExpandedState(openText, false);
      syncButtons();
      return;
    }
    if (wallClosedByUser){
      clearMessageOverlayLock();
      setWallOpen(false);
      syncButtons();
      return;
    }
    beginMessageOverlayLock();
  }

  function playFlip(){
    if (!flipSounds.length) return;
    const pick = flipSounds[Math.floor(Math.random() * flipSounds.length)];
    const vol = music ? clamp(musicTargetVolume || music.volume, 0, 1) : 0.5;
    try{
      pick.pause();
      pick.currentTime = 0;
      pick.volume = vol;
      pick.muted = false;
    }catch(err){
      console.warn('Audio reset failed: flip', err);
    }
    safePlay(pick, 'flip');
  }

  function rectForActiveImage(){
    const img = slideImageEl(activeSlide());
    if (!img) return null;
    const r = img.getBoundingClientRect();
    if (r.width <= 2 || r.height <= 2) return null;
    return r;
  }
  function placeTurnToRect(r){
    turn.style.left = `${r.left}px`;
    turn.style.top = `${r.top}px`;
    turn.style.width = `${r.width}px`;
    turn.style.height = `${r.height}px`;
    turnShadow.style.left = `${r.left}px`;
    turnShadow.style.top = `${r.top}px`;
    turnShadow.style.width = `${r.width}px`;
    turnShadow.style.height = `${r.height}px`;
  }
  function easeInOutCubic(t){
    return t < 0.5 ? 4*t*t*t : 1 - Math.pow(-2*t + 2, 3)/2;
  }
  function setTurnVisible(on){
    if (!turn || !turnShadow) return;
    turn.style.opacity = on ? '1' : '0';
    turnShadow.style.opacity = on ? '1' : '0';
  }
  function setTurnRotationDeg(deg){
    turn.style.transformOrigin = '0% 50%';
    turn.style.transform = `rotateY(${deg}deg)`;

    const a = Math.abs(deg);
    const t = clamp(a / 180, 0, 1);
    const edge = Math.pow(Math.sin(t * Math.PI), 1.2);
    const glint = Math.pow(Math.sin(t * Math.PI), 2.0);

    sheetFront.style.setProperty('--edgeA', String(0.28 * edge));
    sheetFront.style.setProperty('--glintA', String(0.22 * glint));

    const dir = (deg < 0) ? 1 : -1;
    turnShadow.style.setProperty('--sx', `${dir > 0 ? 26 : 16}%`);
    turnShadow.style.setProperty('--sd', `${0.14 + 0.22 * edge}`);
    turnShadow.style.setProperty('--sb', `${10 + 10 * edge}px`);
  }
  function cleanupTransient(curSlide, tgtSlide){
    if (curSlide) curSlide.classList.remove('ghost');
    if (tgtSlide) tgtSlide.classList.remove('peek');
  }
  function resetTurnLayer(){
    setTurnVisible(false);
    if (turn){
      turn.style.width = '0px';
      turn.style.height = '0px';
    }
    if (turnShadow){
      turnShadow.style.width = '0px';
      turnShadow.style.height = '0px';
    }
  }

  function setActiveIndex(newIdx, opts = {}){
    const target = clamp(newIdx, 0, TOTAL - 1);
    if (target === idx && opts.force !== true){
      updateProgress();
      syncButtons();
      syncWallUI();
      return;
    }
    clearMessageOverlayLock();
    idx = target;
    slides.forEach((s, i) => {
      s.classList.toggle('active', i === idx);
      s.classList.remove('peek');
      s.classList.remove('ghost');
    });
    if (idx === 2) wallClosedByUser = false;
    if (opts.playSound !== false) playFlip();
    updateProgress();
    syncButtons();
    syncWallUI();
  }
  function flipTo(targetIdx){
    if (!started || introControlsLocked || flipping || messageOverlayLocked) return;

    const tIdx = clamp(targetIdx, 0, TOTAL - 1);
    if (tIdx === idx) return;

    const r = rectForActiveImage();
    if (!r || !turn || !turnShadow || !sheetFront || !sheetBack || !imgFront || !imgBack){
      setActiveIndex(tIdx);
      return;
    }

    flipping = true;
    clearMessageOverlayLock();
    syncButtons();

    const goingNext = tIdx > idx;
    const curSlide = slides[idx];
    const tgtSlide = slides[tIdx];
    const curSrc = slideImageSrc(curSlide);
    const tgtSrc = slideImageSrc(tgtSlide);

    placeTurnToRect(r);
    sheetBack.classList.add('hidden');
    sheetBack.classList.remove('visible');
    imgBack.src = '';
    sheetFront.classList.remove('hidden');
    sheetFront.classList.add('visible');

    if (goingNext){
      tgtSlide.classList.add('peek');
      curSlide.classList.add('ghost');
      imgFront.src = curSrc;
      setTurnVisible(true);
      setTurnRotationDeg(0);
    } else {
      imgFront.src = tgtSrc;
      setTurnVisible(true);
      setTurnRotationDeg(-180);
    }

    playFlip();

    if (flipDurationMs <= 0){
      cleanupTransient(curSlide, tgtSlide);
      setActiveIndex(tIdx, { playSound: false });
      resetTurnLayer();
      flipping = false;
      syncButtons();
      return;
    }

    const t0 = performance.now();
    function step(now){
      const raw = clamp((now - t0) / flipDurationMs, 0, 1);
      const e = easeInOutCubic(raw);
      const deg = goingNext ? -180 * e : -180 + (180 * e);
      setTurnRotationDeg(deg);

      if (raw < 1){
        requestAnimationFrame(step);
        return;
      }

      cleanupTransient(curSlide, tgtSlide);
      setActiveIndex(tIdx, { playSound: false });
      resetTurnLayer();
      flipping = false;
      syncButtons();
    }

    requestAnimationFrame(step);
  }
  function go(delta){
    if (!started || introControlsLocked || messageOverlayLocked) return;
    flipTo(idx + delta);
  }

  window.addEventListener('resize', () => {
    if (!flipping) return;
    const r = rectForActiveImage();
    if (r) placeTurnToRect(r);
  });

  function ensureSlider(){
    if (slider) return slider;
    slider = document.createElement('input');
    slider.type = 'range';
    slider.id = 'volume-slider';
    slider.min = '0';
    slider.max = '100';
    slider.value = String(Math.round(loadVolume0to100()));
    slider.title = 'Volume';
    slider.setAttribute('aria-label', 'Volume level');
    setHiddenState(slider, true);
    volumeControl.appendChild(slider);
    slider.addEventListener('input', () => setVolume0to100(clamp(parseInt(slider.value || '0', 10), 0, 100)));
    return slider;
  }
  function loadVolume0to100(){
    const v0 = (typeof INITIAL_VOLUME === 'number') ? INITIAL_VOLUME : 50;
    try{
      const saved = Number.parseInt(window.localStorage.getItem(VOLUME_STORAGE_KEY) || '', 10);
      if (Number.isFinite(saved)) return clamp(saved, 0, 100);
    }catch(_err){ }
    return clamp(Math.round(v0), 0, 100);
  }
  function setVolume0to100(v){
    const vv = clamp(Math.round(v), 0, 100);
    const vol01 = vv / 100;
    const muted = vv === 0;

    if (music && hasMusic){
      musicTargetVolume = vol01;
      musicPlayers.forEach((player) => { player.muted = muted; });
      applyPlaylistVolumes(
        playlistFade && !playlistFade.pending
          ? clamp((performance.now() - playlistFade.startedAt) / playlistCrossfadeMs, 0, 1)
          : null
      );
    }

    volIconImg.src = muted ? 'gallery/controls/voloff.png' : 'gallery/controls/volon.png';
    volIcon.setAttribute('aria-label', muted ? 'Volume muted. Toggle volume slider' : 'Toggle volume slider');

    if (slider){
      slider.value = String(vv);
    }
    try{ window.localStorage.setItem(VOLUME_STORAGE_KEY, String(vv)); }
    catch(_err){ }
  }
  function setSliderOpen(open){
    const shouldOpen = !!open;
    volumeControl.classList.toggle('slider-open', shouldOpen);
    setExpandedState(volIcon, shouldOpen);
    if (slider) setHiddenState(slider, !shouldOpen);
  }

  function hideMusicEnableButton(){
    if (!musicEnableButton) return;
    musicEnableButton.classList.remove('is-visible');
    setHiddenState(musicEnableButton, true);
  }
  function showMusicEnableButton(){
    if (!music || !hasMusic) return;
    if (!musicEnableButton){
      musicEnableButton = document.createElement('button');
      musicEnableButton.id = 'audio-enable-button';
      musicEnableButton.type = 'button';
      musicEnableButton.textContent = 'Tap to enable music';
      musicEnableButton.setAttribute('aria-hidden', 'true');
      musicEnableButton.addEventListener('click', async () => {
        const v = loadVolume0to100();
        const target = clamp(v / 100, 0, 1);
        musicTargetVolume = target;
        music.volume = 0;
        music.muted = target === 0;
        const ok = await startMusicPlayback('music-enable');
        if (!ok) return;
        fadeMusicToVolume(target);
      });
      document.body.appendChild(musicEnableButton);
    }
    musicEnableButton.classList.add('is-visible');
    setHiddenState(musicEnableButton, false);
  }
  async function startMusicPlayback(label){
    if (!music || !hasMusic) return false;
    try{
      if (music.paused) music.currentTime = 0;
    }catch(err){
      console.warn('Audio reset failed: music', err);
    }
    const ok = await safePlay(music, label);
    if (ok) hideMusicEnableButton();
    return ok;
  }
  function fadeMusicToVolume(target){
    if (!music || !hasMusic) return;
    target = clamp(target, 0, 1);
    musicTargetVolume = target;
    const startVolume = clamp(music.volume || 0, 0, 1);
    const start = performance.now();
    music.muted = target === 0;
    function fadeStep(now){
      const t = clamp((now - start) / musicFadeMs, 0, 1);
      const e = t < 0.5 ? 2*t*t : 1 - Math.pow(-2*t + 2, 2)/2;
      music.volume = startVolume + ((target - startVolume) * e);
      if (t < 1) requestAnimationFrame(fadeStep);
      else applyPlaylistVolumes();
    }
    requestAnimationFrame(fadeStep);
  }
  function primeAudioProbe(url, label){
    try{
      const probe = makeAudio(url, { volume: 0 });
      probe.muted = true;
      safePlay(probe, label).then(() => {
        try{
          probe.pause();
          probe.currentTime = 0;
        }catch(_err){ }
      });
    }catch(err){
      console.warn(`Audio prime failed: ${label}`, err);
    }
  }
  function primeAudioOnGesture(){
    if (audioPrimed) return;
    audioPrimed = true;

    if (music && hasMusic){
      try{
        music.currentTime = 0;
        music.volume = 0;
        music.muted = false;
      }catch(err){
        console.warn('Audio reset failed: music-prime', err);
      }
      musicPrimePromise = safePlay(music, 'music-prime').then((ok) => {
        musicPrimed = ok;
        return ok;
      });
    }

    try{ gliss.load(); }catch(_err){ }
    flipSounds.forEach((sound) => {
      try{ sound.load(); }catch(_err){ }
    });
    primeAudioProbe(glissSrc, 'glissando-prime');
    flipPool.forEach((href, i) => primeAudioProbe(href, `flip${i + 1}-prime`));
  }

  function glissDurationMs(audioEl){
    const d = audioEl && Number.isFinite(audioEl.duration) ? audioEl.duration : 0;
    if (d > 0.25) return Math.round(d * 1000);
    return curtainFallbackOpenMs;
  }
  function runCurtainMotion(durationMs, onDone){
    const openMs = prefersReducedMotion ? 140 : Math.max(500, Math.round(durationMs || curtainFallbackOpenMs));
    overlay.style.opacity = '1';
    overlay.style.animation = 'none';
    overlay.style.background = 'transparent';
    leftCurtainLayers.forEach((layer) => {
      layer.style.opacity = '1';
      layer.style.transform = 'translateX(0)';
      layer.style.animation = 'none';
    });
    rightCurtainLayers.forEach((layer) => {
      layer.style.opacity = '1';
      layer.style.transform = 'translateX(0)';
      layer.style.animation = 'none';
    });
    void (cLeft || overlay).offsetWidth;
    leftCurtainLayers.forEach((layer) => {
      layer.style.animation = `curtainLeftOut ${openMs}ms cubic-bezier(.2,.9,.1,1) forwards`;
    });
    rightCurtainLayers.forEach((layer) => {
      layer.style.animation = `curtainRightOut ${openMs}ms cubic-bezier(.2,.9,.1,1) forwards`;
    });
    setTimeout(() => {
      overlay.style.pointerEvents = 'none';
      overlay.setAttribute('aria-hidden', 'true');
      overlay.remove();
      if (typeof onDone === 'function') onDone();
    }, openMs + curtainCleanupPadMs);
    return openMs;
  }
  function openCurtain(){
    if (started) return;
    started = true;
    introControlsLocked = true;
    syncButtons();
    revealStage();
    primeAudioOnGesture();
    beginBtn.disabled = true;
    beginBtn.style.opacity = '0';
    beginBtn.style.pointerEvents = 'none';

    let musicStarted = false;
    let glissDone = false;
    let curtainDone = false;
    let introMotionStarted = false;
    let safetyTimer = null;

    function tryUnlockIntroControls(){
      if (!glissDone || !curtainDone) return;
      introControlsLocked = false;
      syncButtons();
    }
    function startMusicAfterGliss(){
      if (musicStarted) return;
      musicStarted = true;
      glissDone = true;
      if (safetyTimer !== null){ clearTimeout(safetyTimer); safetyTimer = null; }

      const v = loadVolume0to100();
      setVolume0to100(v);

      if (music && hasMusic){
        const target = clamp(v / 100, 0, 1);
        music.volume = 0;
        music.muted = target === 0;
        const prime = musicPrimePromise || Promise.resolve(musicPrimed);
        prime
          .then((primed) => (primed && !music.paused) ? true : startMusicPlayback('music-start'))
          .then((ok) => {
            if (!ok && target > 0){
              showMusicEnableButton();
              return;
            }
            fadeMusicToVolume(target);
          });
      }

      tryUnlockIntroControls();
    }
    function beginGlissAndCurtain(g){
      if (introMotionStarted) return;
      introMotionStarted = true;
      const openMs = runCurtainMotion(glissDurationMs(g), () => {
        curtainDone = true;
        tryUnlockIntroControls();
      });
      try{
        g.currentTime = 0;
        g.volume = 0.10;
        g.muted = false;
        safePlay(g, 'glissando').then((ok) => {
          if (!ok) startMusicAfterGliss();
        });
      }catch(_){ startMusicAfterGliss(); }
      safetyTimer = setTimeout(startMusicAfterGliss, openMs + glissSafetyPadMs);
    }
    try{
      const g = gliss;
      g.volume = 0.10;
      g.muted = false;
      g.addEventListener('ended', startMusicAfterGliss, { once: true });
      g.addEventListener('error', () => { beginGlissAndCurtain(g); startMusicAfterGliss(); }, { once: true });
      g.addEventListener('loadedmetadata', () => beginGlissAndCurtain(g), { once: true });
      g.load();
      setTimeout(() => beginGlissAndCurtain(g), 250);
    }catch(err){
      console.warn('Audio setup failed: glissando', err);
      const openMs = runCurtainMotion(curtainFallbackOpenMs, () => { curtainDone = true; tryUnlockIntroControls(); });
      setTimeout(startMusicAfterGliss, openMs);
    }
  }

  bindPress(beginBtn, (e) => { e.preventDefault(); openCurtain(); });
  prevBtn.addEventListener('click', () => go(-1));
  nextBtn.addEventListener('click', () => go(1));

  let swipeStart = null;
  let suppressTapUntil = 0;
  slideshowEl.addEventListener('pointerdown', (e) => {
    if (e.pointerType !== 'touch') return;
    if (wall && wall.classList.contains('is-open')) return;
    const target = e.target instanceof Element ? e.target : null;
    if (target && target.closest('button,a,input,[role="button"]')) return;
    swipeStart = { x: e.clientX, y: e.clientY, pointerId: e.pointerId };
  }, { passive: true });
  slideshowEl.addEventListener('pointerup', (e) => {
    if (!swipeStart || swipeStart.pointerId !== e.pointerId) return;
    const start = swipeStart;
    swipeStart = null;
    if (wall && wall.classList.contains('is-open')) return;
    const dx = e.clientX - start.x;
    const dy = e.clientY - start.y;
    if (Math.abs(dx) < 48 || Math.abs(dx) <= Math.abs(dy) * 1.25) return;
    suppressTapUntil = performance.now() + 500;
    go(dx < 0 ? 1 : -1);
  }, { passive: true });
  slideshowEl.addEventListener('pointercancel', () => { swipeStart = null; }, { passive: true });
  slideshowEl.addEventListener('click', (e) => {
    if (document.body.dataset.tapNavigation !== 'true') return;
    if (performance.now() < suppressTapUntil) return;
    if (wall && wall.classList.contains('is-open')) return;
    const target = e.target instanceof Element ? e.target : null;
    if (target && target.closest('button,a,input,[role="button"]')) return;
    const rect = slideshowEl.getBoundingClientRect();
    const relativeX = e.clientX - rect.left;
    if (relativeX < rect.width * 0.24) go(-1);
    else if (relativeX > rect.width * 0.76) go(1);
  });

  if (fullscreenBtn){
    if (!document.fullscreenEnabled){
      fullscreenBtn.hidden = true;
    }else{
      fullscreenBtn.addEventListener('click', async () => {
        try{
          if (document.fullscreenElement) await document.exitFullscreen();
          else await document.documentElement.requestFullscreen();
        }catch(err){ console.warn('Fullscreen request failed', err); }
      });
      document.addEventListener('fullscreenchange', () => {
        fullscreenBtn.textContent = document.fullscreenElement ? 'Exit fullscreen' : 'Fullscreen';
      });
    }
  }
  if (shareBtn && typeof navigator.share === 'function'){
    shareBtn.hidden = false;
    shareBtn.addEventListener('click', async () => {
      try{ await navigator.share({ title: document.title, url: window.location.href }); }
      catch(err){ if (err && err.name !== 'AbortError') console.warn('Share failed', err); }
    });
  }

  window.addEventListener('keydown', (e) => {
    if (!started || introControlsLocked) return;
    if (e.key === 'ArrowLeft'){
      e.preventDefault();
      go(-1);
    } else if (e.key === 'ArrowRight'){
      e.preventDefault();
      go(1);
    } else if (e.key === 'Escape'){
      if (hasMessage && isWallPage() && wall && wall.classList.contains('is-open')){
        setWallOpen(false);
        wallClosedByUser = true;
        if (openText) openText.focus({preventScroll:true});
      }
    }
  });
  if (closeText){
    closeText.addEventListener('click', () => {
      if (!isWallPage()) return;
      clearMessageOverlayLock();
      setWallOpen(false);
      wallClosedByUser = true;
      syncButtons();
      if (openText) openText.focus({preventScroll:true});
    });
  }
  if (openText){
    bindPress(openText, () => {
      if (!isWallPage()) return;
      clearMessageOverlayLock();
      setWallOpen(true);
      wallClosedByUser = false;
      syncButtons();
      if (closeText) closeText.focus({preventScroll:true});
    });
  }
  bindPress(volIcon, () => {
    const s = ensureSlider();
    const shouldOpen = !volumeControl.classList.contains('slider-open');
    setSliderOpen(shouldOpen);
    if (shouldOpen) s.focus({preventScroll:true});
  });

  ensureSlider();
  setSliderOpen(false);
  if (!hasMusic && volumeControl){
    volumeControl.style.display = 'none';
    setHiddenState(volumeControl, true);
  }
  setVolume0to100(loadVolume0to100());
  setHiddenState(wall, true);
  setHiddenState(openText, true);
  setHiddenState(closeText, true);
  setExpandedState(openText, false);
  try{
    installHypernotes();
  }catch(err){
    console.error('Hypernote setup failed', err);
  }
  installImageFallbacks();
  waitForCriticalAssets().finally(startCurtainIntro);
});
"""
