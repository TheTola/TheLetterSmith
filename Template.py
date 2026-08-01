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
  <link rel="preload" as="audio" href="gallery/sounds/glissando.mp3">
  {{MUSIC_PRELOAD_HTML}}
</head>

<body>
  <main id="letter-preview">
  <div id="curtain-overlay" aria-hidden="false" role="dialog" aria-modal="false" aria-labelledby="begin-button">
    <img id="curtain-left"  src="gallery/controls/cleft.png"  alt="" aria-hidden="true" decoding="async" fetchpriority="high">
    <img id="curtain-right" src="gallery/controls/cright.png" alt="" aria-hidden="true" decoding="async" fetchpriority="high">
    <button id="begin-button" type="button">Tap to Begin</button>
  </div>

  <button
    id="fullscreen-button"
    class="hud-button viewer-action"
    type="button"
    title="Enter fullscreen"
    aria-label="Enter fullscreen"
  >Fullscreen</button>

  <div id="slideshow" aria-hidden="true">
    <div id="turn" aria-hidden="true">
      <div class="sheet sheet-front visible" id="sheetFront">
        <img id="turnFrontImg" alt="">
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
      <div class="text-wall" id="textWall" role="dialog" aria-modal="false" aria-label="Message text" aria-hidden="true" tabindex="-1" style="{{MESSAGE_OVERLAY_STYLE}}">
        <button id="close-text" class="hud-button hud-button-close" type="button" title="Close Text" aria-label="Close message" aria-controls="textWall">&times;</button>
        <div class="text-wall-content" id="textWallContent">{{MESSAGE_HTML}}</div>
      </div>
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

    <div id="viewer-actions" aria-label="Letter controls">
      <div id="viewer-actions-left">
        <button
          id="restart-button"
          class="hud-button viewer-action"
          type="button"
          title="Restart letter"
          aria-label="Restart letter"
        >Restart</button>
        <button
          id="mute-button"
          class="hud-button viewer-action"
          type="button"
          title="Mute letter audio"
          aria-label="Mute letter audio"
          aria-pressed="false"
        >Mute</button>
      </div>
    </div>

    <button
      id="open-text"
      class="hud-button hud-button-icon"
      type="button"
      title="Show Message"
      aria-label="Show message"
      aria-controls="textWall"
      aria-expanded="false"
      aria-hidden="true"
    >
      <img src="gallery/controls/showmessageicon.png" alt="" aria-hidden="true" decoding="async">
    </button>
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
  </main>

  <audio id="bg-music" preload="auto" playsinline></audio>
  <audio id="bg-music-standby" preload="auto" playsinline></audio>

  <script>
    const INITIAL_VOLUME = {{INITIAL_VOLUME}};
    const MUSIC_PLAYLIST = {{MUSIC_PLAYLIST_JSON}};
    const MUSIC_CROSSFADE_MS = {{MUSIC_CROSSFADE_MS}};
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
  --message-overlay-surface-opacity:.68;
  --message-overlay-blur:0px;
  --message-overlay-texture-opacity:0;
  --message-ink:#221710;
  --wall-fade-ms:900ms;
  --stage-backdrop:
    radial-gradient(900px 600px at 30% 25%, var(--bg-rim), transparent 60%),
    radial-gradient(900px 600px at 80% 70%, var(--bg-depth), transparent 60%),
    linear-gradient(180deg, var(--bg-top), var(--bg-bottom));
  --shadow-page:0 18px 40px rgba(0,0,0,.45);
  --motion-fast:180ms ease;
  --motion-medium:240ms cubic-bezier(.2,.8,.2,1);
  --nav-offset:clamp(14px,3vw,36px);
  --nav-size:clamp(52px,6vw,78px);
  --nav-pad:clamp(5px,.7vw,8px);
  --icon-size:clamp(46px,5vw,56px);
  --close-size:clamp(42px,4.8vw,48px);
  --corner-offset:clamp(14px,3vw,20px);
  --control-rail:66px;
  --bottom-control-rail:96px;
  --page-side-rail:calc(var(--nav-offset) + var(--nav-size) + (2 * var(--nav-pad)) + 8px);
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
#letter-preview{position:relative;width:100%;height:100%;overflow:hidden;background:var(--stage-backdrop);isolation:isolate}
#letter-preview:fullscreen{width:100vw;height:100vh}

#slideshow{
  position:relative;width:100%;height:100%;
  opacity:0;visibility:hidden;pointer-events:none;
  transition:opacity var(--motion-fast);
  background:var(--stage-backdrop);
  perspective:6000px;
  transform-style:preserve-3d;
}
body.stage-ready #slideshow{opacity:1;visibility:visible;pointer-events:auto}

#curtain-overlay{position:absolute;inset:0;z-index:9999;overflow:hidden;pointer-events:all;opacity:0;background:var(--stage-backdrop);will-change:opacity}
#curtain-overlay.is-visible{animation:curtainIntroFadeIn 520ms ease-out forwards}
#curtain-left,#curtain-right{position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover;z-index:10000;pointer-events:none;opacity:0;will-change:opacity,transform}
#curtain-overlay.is-visible #curtain-left,#curtain-overlay.is-visible #curtain-right{animation:curtainPanelFadeIn 420ms ease-out forwards}
#curtain-overlay.curtain-fallback #curtain-left,#curtain-overlay.curtain-fallback #curtain-right{display:none}
#begin-button{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);min-width:min(80vw,280px);padding:14px 30px;border:1px solid rgba(155,255,251,.42);border-radius:var(--pill-radius);background:linear-gradient(180deg,rgba(15,24,38,.88),rgba(6,10,18,.72));color:var(--text-main);font-size:clamp(22px,3.2vw,42px);font-weight:700;letter-spacing:.02em;cursor:pointer;z-index:10001;animation:pulse 2s infinite;box-shadow:var(--hud-shadow),0 0 0 1px rgba(0,255,255,.14)}
#curtain-overlay:not(.is-visible) #begin-button{opacity:0}
#begin-button:hover{background:linear-gradient(180deg,rgba(20,31,48,.94),rgba(8,12,20,.84))}
@keyframes curtainIntroFadeIn{from{opacity:0}to{opacity:1}}
@keyframes curtainPanelFadeIn{from{opacity:0}to{opacity:1}}
@keyframes pulse{0%,100%{opacity:.68;transform:translate(-50%,-50%) scale(1)}50%{opacity:1;transform:translate(-50%,-50%) scale(1.04)}}
@keyframes curtainLeftOut{from{transform:translateX(0)}to{transform:translateX(-100vw)}}
@keyframes curtainRightOut{from{transform:translateX(0)}to{transform:translateX(100vw)}}

.slide{position:absolute;inset:0;opacity:0;pointer-events:none;z-index:5}
.slide.active{opacity:1;pointer-events:auto}
.slide.peek{opacity:1;pointer-events:none;z-index:4}
.slide.ghost{opacity:0;pointer-events:none}
.slide.asset-failed img{opacity:0}
.slide.asset-failed::after{content:attr(data-fallback-label);position:absolute;inset:50% auto auto 50%;transform:translate(-50%,-50%);width:min(520px,72%);padding:18px 22px;color:var(--text-main);text-align:center;font:700 20px/1.4 var(--font-ui);background:rgba(0,0,0,.58);border:1px solid var(--hud-border);border-radius:var(--page-radius);box-shadow:var(--shadow-page);z-index:6}
.slide img{max-width:calc(100% - (2 * var(--page-side-rail)));max-height:calc(100% - var(--control-rail) - var(--bottom-control-rail));position:absolute;inset:var(--control-rail) var(--page-side-rail) var(--bottom-control-rail);margin:auto;object-fit:contain;border-radius:var(--page-radius);box-shadow:inset 0 0 30px rgba(0,0,0,.20),var(--shadow-page)}

.nav-button,.hud-button{display:inline-flex;align-items:center;justify-content:center;border:1px solid var(--hud-border);background:linear-gradient(180deg,var(--hud-surface-top),var(--hud-surface-bottom));color:var(--text-main);box-shadow:var(--hud-shadow);backdrop-filter:blur(10px);cursor:pointer;transition:transform var(--motion-fast),background var(--motion-fast),border-color var(--motion-fast),box-shadow var(--motion-fast),opacity var(--motion-fast),visibility 0s linear 220ms}
.nav-button:hover,.hud-button:hover{background:linear-gradient(180deg,rgba(25,37,55,.88),rgba(10,14,24,.76));border-color:var(--hud-border-strong);box-shadow:var(--hud-shadow-strong)}
.nav-button:active,.hud-button:active{transform:scale(.98)}
.nav-button img,.hud-button img{display:block;width:100%;height:100%;object-fit:contain;pointer-events:none;filter:drop-shadow(0 6px 12px rgba(0,0,0,.34))}
#begin-button:focus-visible,.nav-button:focus-visible,.hud-button:focus-visible,#volume-slider:focus-visible{outline:3px solid rgba(255,255,255,.94);outline-offset:4px}

.nav-button{position:absolute;top:calc(50% + 27px);width:calc(var(--nav-size) + (2 * var(--nav-pad)));height:calc(var(--nav-size) + (2 * var(--nav-pad)));padding:var(--nav-pad);border-radius:50%;z-index:100}
#prev{left:var(--nav-offset);transform:translateY(-50%)}
#next{right:var(--nav-offset);transform:translateY(-50%)}
.nav-button:hover{transform:translateY(-50%) scale(1.04)}
.nav-button img{width:var(--nav-size);height:var(--nav-size)}
.nav-button[disabled]{opacity:.35;cursor:not-allowed;filter:grayscale(1);box-shadow:none}
.nav-button[disabled]:hover{transform:translateY(-50%)}

#viewer-actions{position:absolute;top:var(--corner-offset);left:var(--corner-offset);display:flex;align-items:center;pointer-events:none;z-index:107}
#viewer-actions-left{display:flex;align-items:center;gap:8px}
#viewer-actions .viewer-action{width:auto;height:38px;padding:0 12px;font:700 12px/1 var(--font-ui);letter-spacing:.03em;pointer-events:auto}
#fullscreen-button{position:absolute;top:var(--corner-offset);right:var(--corner-offset);width:auto;height:38px;padding:0 12px;font:700 12px/1 var(--font-ui);letter-spacing:.03em;z-index:10002;pointer-events:auto}
#mute-button[aria-pressed="true"]{border-color:rgba(155,255,251,.55);background:linear-gradient(180deg,rgba(20,55,64,.92),rgba(8,25,34,.86));color:#eaffff}

.text-wall{position:absolute;top:var(--control-rail);bottom:var(--bottom-control-rail);left:50%;width:min(var(--wall-max-width),calc(100% - (2 * var(--wall-gap))));height:fit-content;max-height:calc(100% - var(--control-rail) - var(--bottom-control-rail));margin:auto 0;overflow:auto;padding:var(--wall-frame-pad);border:1px solid var(--paper-edge);border-radius:var(--panel-radius);background-color:rgba(var(--message-overlay-rgb),var(--message-overlay-surface-opacity));color:var(--message-ink);box-shadow:0 30px 80px var(--paper-shadow),0 12px 28px rgba(0,0,0,.22),inset 0 1px 0 var(--paper-line);backdrop-filter:blur(var(--message-overlay-blur));-webkit-backdrop-filter:blur(var(--message-overlay-blur));z-index:105;opacity:0;visibility:hidden;pointer-events:none;transform:translateX(-50%) translateY(8px);transition:opacity var(--wall-fade-ms) ease,transform var(--motion-medium),visibility 0s linear var(--wall-fade-ms);isolation:isolate;scrollbar-width:thin;scrollbar-color:rgba(92,67,40,.52) rgba(0,0,0,.06);background-image:radial-gradient(circle at 17% 29%,rgba(112,81,44,var(--message-overlay-texture-opacity)) 0 1px,transparent 1.5px),radial-gradient(circle at 73% 64%,rgba(255,255,255,var(--message-overlay-texture-opacity)) 0 1px,transparent 1.5px);background-size:23px 29px,31px 37px}
.text-wall.is-open{opacity:1;visibility:visible;pointer-events:auto;transform:translateX(-50%) translateY(0);transition:opacity var(--wall-fade-ms) ease,transform var(--motion-medium),visibility 0s}
.text-wall::before{content:"";position:absolute;inset:10px;border:1px solid rgba(116,85,47,.16);border-radius:calc(var(--panel-radius) - 4px);box-shadow:inset 0 0 0 1px rgba(255,255,255,.16),inset 0 18px 28px rgba(255,255,255,.08);pointer-events:none}
.text-wall::after{content:"";position:absolute;inset:0;border-radius:inherit;background:linear-gradient(180deg,rgba(255,255,255,.16),rgba(255,255,255,0) 22%,rgba(0,0,0,.10) 100%);mix-blend-mode:soft-light;pointer-events:none}
.text-wall-content{position:relative;z-index:1;max-width:44rem;margin:0 auto;padding:var(--wall-block-pad) var(--wall-inline-pad);color:var(--message-ink);font-family:var(--font-letter);font-size:clamp(17px,1.25vw,20px);line-height:1.68;text-rendering:optimizeLegibility}
.text-wall-content,.text-wall-content *{max-width:100%;overflow-wrap:anywhere}
.text-wall-content > :first-child{margin-top:0 !important}
.text-wall-content > :last-child{margin-bottom:0 !important}
.text-wall-content :where(p,div,blockquote,ul,ol,h1,h2,h3,h4,table){margin-left:0 !important;margin-right:0 !important}
.text-wall-content :where(img,table){height:auto}
.text-wall-content :is(a[href^="ultralink:"],a[href^="hypernote:"]){font-style:italic;text-decoration:none !important;cursor:help;outline-offset:3px}
.text-wall-content :is(a[href^="ultralink:"],a[href^="hypernote:"]):focus-visible{outline:2px solid #00d0ff;border-radius:3px}
.ultralink-tooltip{position:fixed;left:0;top:0;max-width:min(22rem,calc(100vw - 32px));padding:11px 14px;border:1px solid transparent;border-radius:9px;box-shadow:0 18px 42px rgba(0,0,0,.34);font:500 15px/1.45 Georgia,'Times New Roman',serif;white-space:pre-wrap;overflow-wrap:anywhere;z-index:220;opacity:0;visibility:hidden;transform:translateY(4px);transition:opacity var(--motion-fast),transform var(--motion-fast),visibility 0s linear 160ms;pointer-events:none}
.ultralink-tooltip.theme-paper{border-color:rgba(112,81,44,.42);background:linear-gradient(180deg,#fff8df,#f2e3bd);color:#24170d;box-shadow:0 18px 42px rgba(0,0,0,.32),inset 0 1px 0 rgba(255,255,255,.65)}
.ultralink-tooltip.theme-dark{border-color:rgba(255,255,255,.24);background:rgba(7,10,16,.94);color:#fff;box-shadow:0 18px 42px rgba(0,0,0,.46),inset 0 1px 0 rgba(255,255,255,.10)}
.ultralink-tooltip.theme-minimal{border-color:rgba(0,0,0,.30);background:rgba(255,255,255,.20);color:#000;box-shadow:0 10px 28px rgba(0,0,0,.20);backdrop-filter:blur(10px)}
.ultralink-tooltip.is-visible{opacity:1;visibility:visible;transform:translateY(0);transition:opacity var(--motion-fast),transform var(--motion-fast),visibility 0s}
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
#close-text{position:sticky;top:0;left:auto;bottom:auto;display:flex;margin:0 0 0 auto;width:var(--close-size);height:var(--close-size);border-radius:14px;font-size:clamp(24px,2.8vw,28px);line-height:1;z-index:3;transform:scale(.96)}
#close-text.is-visible{transform:scale(1)}

#volume-control{position:absolute;right:var(--corner-offset);bottom:var(--corner-offset);display:flex;align-items:center;gap:10px;padding:6px;border:1px solid rgba(255,255,255,.08);border-radius:var(--pill-radius);background:rgba(42,46,54,.72);box-shadow:var(--hud-shadow);backdrop-filter:blur(10px);z-index:102;opacity:0;visibility:hidden;pointer-events:none;transform:translateY(0);transition:opacity var(--motion-fast),transform var(--motion-medium),visibility 0s linear 220ms}
body.stage-ready #volume-control{opacity:1;visibility:visible;pointer-events:auto;transition-delay:0s}
#volume-control.slider-open{border-color:rgba(155,255,251,.18);background:rgba(50,55,64,.82)}
#volume-icon{flex:none}
#volume-icon-img{width:var(--icon-size);height:var(--icon-size);padding:6px}
#volume-slider{width:0;min-width:0;-webkit-appearance:none;appearance:none;height:4px;opacity:0;pointer-events:none;background:rgba(255,255,255,.30);border-radius:2px;transform:scaleX(.92);transform-origin:right center;transition:width var(--motion-medium),opacity var(--motion-fast),transform var(--motion-fast)}
#volume-control.slider-open #volume-slider{width:var(--slider-width);opacity:1;pointer-events:auto;transform:scaleX(1)}
#volume-slider::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;width:16px;height:16px;background:var(--accent-strong);border-radius:50%;cursor:pointer;border:2px solid #fff;box-shadow:0 0 0 4px rgba(0,255,255,.12)}
#volume-slider::-moz-range-thumb{width:16px;height:16px;background:var(--accent-strong);border:2px solid #fff;border-radius:50%;cursor:pointer;box-shadow:0 0 0 4px rgba(0,255,255,.12)}

#turn{position:absolute;left:0;top:0;width:0;height:0;opacity:0;pointer-events:none;overflow:hidden;border-radius:var(--page-radius);z-index:40;transform-style:preserve-3d;backface-visibility:hidden;will-change:transform,opacity}
.sheet{position:absolute;inset:0;overflow:hidden;border-radius:var(--page-radius);transform-style:preserve-3d;backface-visibility:hidden;transform-origin:0 50%;--edgeA:0;--glintA:0}
.sheet img{display:block;width:100%;height:100%;object-fit:contain;border-radius:var(--page-radius)}
.sheet.hidden{opacity:0;visibility:hidden}
.sheet.visible{opacity:1;visibility:visible}
.sheet::after{content:"";position:absolute;inset:0;pointer-events:none;background:linear-gradient(90deg,rgba(255,255,255,0) 60%,rgba(255,255,255,var(--edgeA)) 86%,rgba(255,255,255,0) 100%);mix-blend-mode:screen}
.sheet::before{content:"";position:absolute;inset:-10%;pointer-events:none;background:radial-gradient(320px 220px at 92% 45%,rgba(255,255,255,var(--glintA)) 0%,rgba(255,255,255,0) 62%);mix-blend-mode:overlay}
#turnShadow{position:absolute;left:0;top:0;width:0;height:0;pointer-events:none;z-index:39;opacity:0;border-radius:var(--page-radius);overflow:hidden;--sx:18%;--sd:.28;--sb:14px;background:radial-gradient(140% 90% at var(--sx) 55%,rgba(0,0,0,var(--sd)) 0%,rgba(0,0,0,0) 62%);filter:blur(var(--sb))}

@media (max-width: 900px){:root{--nav-size:clamp(48px,8vw,66px);--wall-gap:clamp(18px,4vw,30px)}.text-wall-content{font-size:clamp(16px,1.8vw,18px)}}
@media (max-width: 640px){:root{--nav-offset:4px;--nav-size:34px;--nav-pad:4px;--icon-size:34px;--close-size:38px;--corner-offset:12px;--control-rail:50px;--bottom-control-rail:60px;--page-side-rail:50px}#prev,#next{top:50%}#volume-control{left:var(--corner-offset);right:auto;bottom:calc(5px + env(safe-area-inset-bottom));padding:4px;transform:none}body.stage-ready #volume-control{transform:none}#viewer-actions{top:10px;left:10px}#viewer-actions-left{gap:5px}#viewer-actions .viewer-action,#fullscreen-button{height:32px;padding:0 8px;font-size:10px}#fullscreen-button{top:10px;right:10px}#open-text{left:auto;right:var(--corner-offset);bottom:calc(5px + env(safe-area-inset-bottom));transform:none}.text-wall{width:calc(100% - (2 * var(--wall-gap)));max-height:calc(100% - var(--control-rail) - var(--bottom-control-rail))}#volume-control.slider-open #volume-slider{width:min(28vw,64px)}}
@media (max-width: 220px){:root{--page-side-rail:58px}#viewer-actions .viewer-action,#fullscreen-button{width:34px;padding:0;font-size:0}#restart-button::before{content:"Γå╗";font-size:18px}#mute-button::before{content:"≡ƒöç";font-size:15px}#mute-button[aria-pressed="true"]::before{content:"≡ƒöè"}#fullscreen-button::before{content:"Γ¢╢";font-size:17px}}
@media (max-height: 420px){:root{--nav-offset:4px;--nav-size:34px;--nav-pad:4px;--icon-size:32px;--close-size:34px;--corner-offset:8px;--control-rail:48px;--bottom-control-rail:52px;--page-side-rail:50px;--wall-gap:10px;--wall-frame-pad:8px;--wall-block-pad:14px;--wall-inline-pad:12px}#viewer-actions{top:8px;left:8px}#viewer-actions-left{gap:5px}#viewer-actions .viewer-action,#fullscreen-button{height:30px;padding:0 8px;font-size:10px}#fullscreen-button{top:8px;right:8px}#volume-control{bottom:6px;padding:3px}#open-text{bottom:6px}.text-wall-content{font-size:15px;line-height:1.45}}
@media (prefers-reduced-motion: reduce){*,*::before,*::after{scroll-behavior:auto !important}#slideshow,#volume-control,#open-text,#close-text,.text-wall,#volume-slider{transition:none}#begin-button{animation:none}#curtain-overlay.is-visible,#curtain-overlay.is-visible #curtain-left,#curtain-overlay.is-visible #curtain-right{animation-duration:0.01ms !important;animation-iteration-count:1 !important}}
"""

TEMPLATE_JS = r"""
document.addEventListener('DOMContentLoaded', () => {
  const overlay   = document.getElementById('curtain-overlay');
  const cLeft     = document.getElementById('curtain-left');
  const cRight    = document.getElementById('curtain-right');
  const beginBtn  = document.getElementById('begin-button');

  const slides    = Array.from(document.querySelectorAll('.slide'));
  const prevBtn   = document.getElementById('prev');
  const nextBtn   = document.getElementById('next');
  const restartBtn = document.getElementById('restart-button');
  const muteBtn = document.getElementById('mute-button');
  const fullscreenBtn = document.getElementById('fullscreen-button');
  const letterPreviewEl = document.getElementById('letter-preview');
  const turn = document.getElementById('turn');
  const turnShadow = document.getElementById('turnShadow');
  const sheetFront = document.getElementById('sheetFront');
  const imgFront = document.getElementById('turnFrontImg');

  const wall       = document.getElementById('textWall');
  const closeText  = document.getElementById('close-text');
  const openText   = document.getElementById('open-text');

  const slideshowEl = document.getElementById('slideshow');
  const volumeControl = document.getElementById('volume-control');
  const volIcon   = document.getElementById('volume-icon');
  const volIconImg = document.getElementById('volume-icon-img');
  let music       = document.getElementById('bg-music');
  let musicStandby = document.getElementById('bg-music-standby');
  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const curtainIntroRevealMs = prefersReducedMotion ? 80 : 520;
  const curtainFallbackOpenMs = prefersReducedMotion ? 140 : 2600;
  const curtainCleanupPadMs = prefersReducedMotion ? 20 : 0;
  const glissSafetyPadMs = prefersReducedMotion ? 120 : 450;
  const musicFadeMs = prefersReducedMotion ? 120 : 900;
  const wallRevealDelayMs = prefersReducedMotion ? 80 : 2200;
  const wallRevealFadeMs = prefersReducedMotion ? 120 : 900;

  const TOTAL = slides.length;
  let started = false;
  let introControlsLocked = false;
  let idx = 0;
  let wallClosedByUser = false;
  let wallRevealLocked = false;
  let wallRevealTimer = null;
  let wallRevealUnlockTimer = null;
  let slider = null;
  let stageReady = false;
  let introStarted = false;
  let deferredWarmStarted = false;
  let flipping = false;
  let musicPlaylistIndex = 0;
  let playlistTransitioning = false;
  const muteStorageKey = 'lettersmith.viewerMuted';
  let viewerMuted = loadViewerMuted();
  let currentVolume = loadVolume0to100();

  const flipPool = Array.from({length: 10}, (_, i) => `gallery/sounds/flip${i+1}.mp3`);
  const glissSrc = 'gallery/sounds/glissando.mp3';
  const deferredAssets = [
    { as: 'image', href: 'gallery/pages/letter.png' },
    { as: 'image', href: 'gallery/pages/wall.png' },
    { as: 'image', href: 'gallery/pages/back.png' },
    { as: 'image', href: 'gallery/controls/ppage.png' },
    { as: 'image', href: 'gallery/controls/npage.png' },
    { as: 'image', href: 'gallery/controls/volon.png' },
    { as: 'image', href: 'gallery/controls/voloff.png' },
    { as: 'image', href: 'gallery/controls/showmessageicon.png' },
    ...((Array.isArray(MUSIC_PLAYLIST) ? MUSIC_PLAYLIST : []).map((href) => ({ as: 'audio', href, type: 'audio/mpeg' }))),
    ...flipPool.map((href) => ({ as: 'audio', href, type: 'audio/mpeg' })),
  ];

  function clamp(n, a, b){ return Math.max(a, Math.min(b, n)); }
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

  function installUltralinks(){
    const content = document.getElementById('textWallContent');
    if (!content) return;
    const links = Array.from(
      content.querySelectorAll('a[href^="ultralink:"],a[href^="hypernote:"]')
    );
    if (!links.length) return;

    const tooltip = document.createElement('div');
    tooltip.id = 'ultralink-tooltip';
    tooltip.className = 'ultralink-tooltip';
    tooltip.setAttribute('role', 'tooltip');
    document.body.appendChild(tooltip);

    function tooltipTheme(){
      if (!wall) return 'theme-dark';
      const resolved = window.getComputedStyle(wall);
      const opacity = Number.parseFloat(
        resolved.getPropertyValue('--message-overlay-opacity')
      );
      if (Number.isFinite(opacity) && opacity <= 0.05){
        return 'theme-minimal';
      }
      const raw = resolved.getPropertyValue('--message-overlay-rgb').trim();
      const channels = raw.match(/[\d.]+/g);
      if (!channels || channels.length < 3) return 'theme-dark';
      const rgb = channels.slice(0, 3).map((value) => clamp(Number(value), 0, 255) / 255);
      const linear = rgb.map((value) => (
        value <= 0.04045
          ? value / 12.92
          : Math.pow((value + 0.055) / 1.055, 2.4)
      ));
      const luminance = (0.2126 * linear[0]) + (0.7152 * linear[1]) + (0.0722 * linear[2]);
      return luminance < 0.42 ? 'theme-paper' : 'theme-dark';
    }
    tooltip.classList.add(tooltipTheme());

    function messageFor(link){
      const href = link.getAttribute('href') || '';
      const match = href.match(/^(?:ultralink|hypernote):(.*)$/i);
      if (!match) return '';
      try { return decodeURIComponent(match[1]); }
      catch (_error) { return match[1]; }
    }

    let activeLink = null;

    function place(link){
      const gap = 10;
      const margin = 16;
      const rect = link.getBoundingClientRect();
      const width = tooltip.offsetWidth;
      const height = tooltip.offsetHeight;
      const left = clamp(
        rect.left + (rect.width / 2) - (width / 2),
        margin,
        Math.max(margin, window.innerWidth - width - margin)
      );
      let top = rect.bottom + gap;
      if (top + height > window.innerHeight - margin){
        top = Math.max(margin, rect.top - height - gap);
      }
      tooltip.style.left = `${left}px`;
      tooltip.style.top = `${top}px`;
    }

    function show(link){
      const message = link.dataset.ultralinkMessage || messageFor(link);
      if (!message) return;
      activeLink = link;
      tooltip.textContent = message;
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
      link.dataset.ultralinkMessage = messageFor(link);
      link.setAttribute('role', 'button');
      link.setAttribute('tabindex', '0');
      link.setAttribute('aria-describedby', tooltip.id);
      link.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        if (activeLink === link) hide(link);
        else show(link);
      });
      link.addEventListener('mouseenter', () => show(link));
      link.addEventListener('mouseleave', () => {
        if (document.activeElement !== link) hide(link);
      });
      link.addEventListener('focus', () => show(link));
      link.addEventListener('blur', () => hide(link));
      link.addEventListener('keydown', (event) => {
        if (event.key === 'Escape'){
          hide(link);
          return;
        }
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        if (activeLink === link) hide(link);
        else show(link);
      });
    });

    window.addEventListener('scroll', () => activeLink && place(activeLink), true);
    window.addEventListener('resize', () => activeLink && place(activeLink));
    document.addEventListener('pointerdown', (event) => {
      if (!activeLink || activeLink.contains(event.target)) return;
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
        link.href = asset.href;
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

  function slideImageEl(slide){ return slide ? slide.querySelector('img') : null; }
  function slideImageSrc(slide){
    const image = slideImageEl(slide);
    return image ? image.getAttribute('src') || '' : '';
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
    [cLeft, cRight].forEach((img) => {
      if (!img) return;
      const handleError = () => {
        img.style.display = 'none';
        overlay.classList.add('curtain-fallback');
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
    const criticalImages = [cLeft, cRight, slideImageEl(slides[0])].filter(Boolean);
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

  function isWallPage(){ return idx === 2; }
  function setDisabled(btn, disabled){
    btn.disabled = !!disabled;
    btn.setAttribute('aria-disabled', disabled ? 'true' : 'false');
  }
  function syncButtons(){
    const locked = !started || introControlsLocked || wallRevealLocked || flipping;
    setDisabled(prevBtn, locked || idx === 0);
    setDisabled(nextBtn, locked || idx === TOTAL - 1);
  }

  function clearWallRevealTimers(){
    if (wallRevealTimer !== null){ clearTimeout(wallRevealTimer); wallRevealTimer = null; }
    if (wallRevealUnlockTimer !== null){ clearTimeout(wallRevealUnlockTimer); wallRevealUnlockTimer = null; }
  }
  function setWallOpen(open){
    wall.classList.toggle('is-open', open);
    setHiddenState(wall, !open);
    openText.classList.toggle('is-visible', !open);
    setHiddenState(openText, open);
    closeText.classList.toggle('is-visible', open);
    setHiddenState(closeText, !open);
    setExpandedState(openText, open);
  }
  function hideWallDuringRevealDelay(){
    wall.classList.remove('is-open');
    openText.classList.remove('is-visible');
    closeText.classList.remove('is-visible');
    setHiddenState(wall, true);
    setHiddenState(openText, true);
    setHiddenState(closeText, true);
    setExpandedState(openText, false);
  }
  function unlockWallReveal(){
    wallRevealLocked = false;
    syncButtons();
  }
  function beginWallRevealSequence(){
    clearWallRevealTimers();
    wallRevealLocked = true;
    syncButtons();
    hideWallDuringRevealDelay();
    wallRevealTimer = setTimeout(() => {
      wallRevealTimer = null;
      if (!isWallPage() || wallClosedByUser){ unlockWallReveal(); return; }
      setWallOpen(true);
      wallRevealUnlockTimer = setTimeout(() => {
        wallRevealUnlockTimer = null;
        unlockWallReveal();
      }, wallRevealFadeMs + 120);
    }, wallRevealDelayMs);
  }
  function syncWallUI(){
    if (!isWallPage()){
      clearWallRevealTimers();
      wallRevealLocked = false;
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
      clearWallRevealTimers();
      wallRevealLocked = false;
      setWallOpen(false);
      syncButtons();
      return;
    }
    beginWallRevealSequence();
  }

  function playOneShot(src, volume01){
    try{
      const a = new Audio(src);
      a.preload = 'auto';
      a.volume = clamp(volume01, 0, 1);
      a.muted = viewerMuted;
      a.play().catch(()=>{});
    }catch(_){ }
  }
  function playFlip(){
    const pick = flipPool[Math.floor(Math.random() * flipPool.length)];
    const vol = music ? clamp(music.volume, 0, 1) : 0.5;
    playOneShot(pick, vol);
  }

  function setActiveIndex(newIdx, opts = {}){
    const target = clamp(newIdx, 0, TOTAL - 1);
    if (target === idx && opts.force !== true){
      syncButtons();
      syncWallUI();
      return;
    }
    clearWallRevealTimers();
    wallRevealLocked = false;
    idx = target;
    slides.forEach((s, i) => {
      s.classList.toggle('active', i === idx);
      s.classList.remove('peek');
      s.classList.remove('ghost');
    });
    if (idx === 2) wallClosedByUser = false;
    if (opts.playSound !== false) playFlip();
    syncButtons();
    syncWallUI();
  }

  function activeSlide(){ return slides[idx]; }
  function activeImageRect(){
    const image = slideImageEl(activeSlide());
    if (!image) return null;
    const rect = image.getBoundingClientRect();
    return rect.width > 2 && rect.height > 2 ? rect : null;
  }
  function placeTurn(rect){
    for (const element of [turn, turnShadow]){
      element.style.left = `${rect.left}px`;
      element.style.top = `${rect.top}px`;
      element.style.width = `${rect.width}px`;
      element.style.height = `${rect.height}px`;
    }
  }
  function setTurnVisible(visible){
    turn.style.opacity = visible ? '1' : '0';
    turnShadow.style.opacity = visible ? '1' : '0';
  }
  function setTurnRotation(degrees){
    turn.style.transformOrigin = '0% 50%';
    turn.style.transform = `rotateY(${degrees}deg)`;
    const amount = clamp(Math.abs(degrees) / 180, 0, 1);
    const edge = Math.pow(Math.sin(amount * Math.PI), 1.2);
    const glint = Math.pow(Math.sin(amount * Math.PI), 2);
    sheetFront.style.setProperty('--edgeA', String(0.28 * edge));
    sheetFront.style.setProperty('--glintA', String(0.22 * glint));
    turnShadow.style.setProperty('--sx', degrees < 0 ? '26%' : '16%');
    turnShadow.style.setProperty('--sd', String(0.14 + (0.22 * edge)));
    turnShadow.style.setProperty('--sb', `${10 + (10 * edge)}px`);
  }
  function finishTurn(currentSlide, targetSlide, targetIndex){
    currentSlide.classList.remove('ghost');
    targetSlide.classList.remove('peek');
    setActiveIndex(targetIndex, { playSound: false });
    setTurnVisible(false);
    for (const element of [turn, turnShadow]){
      element.style.width = '0px';
      element.style.height = '0px';
    }
    slideshowEl.classList.remove('page-turning');
    slideshowEl.setAttribute('aria-busy', 'false');
    flipping = false;
    syncButtons();
  }
  function flipTo(targetIndex){
    if (!started || flipping || introControlsLocked || wallRevealLocked) return;
    const target = clamp(targetIndex, 0, TOTAL - 1);
    if (target === idx) return;
    const rect = activeImageRect();
    if (!rect || prefersReducedMotion){
      playFlip();
      setActiveIndex(target, { playSound: false });
      return;
    }

    flipping = true;
    syncButtons();
    slideshowEl.classList.add('page-turning');
    slideshowEl.setAttribute('aria-busy', 'true');

    const goingNext = target > idx;
    const currentSlide = slides[idx];
    const targetSlide = slides[target];
    placeTurn(rect);
    sheetFront.classList.remove('hidden');
    sheetFront.classList.add('visible');
    imgFront.src = goingNext
      ? slideImageSrc(currentSlide)
      : slideImageSrc(targetSlide);

    if (goingNext){
      targetSlide.classList.add('peek');
      currentSlide.classList.add('ghost');
      setTurnRotation(0);
    } else {
      setTurnRotation(-180);
    }
    setTurnVisible(true);
    playFlip();

    const duration = 620;
    const startedAt = performance.now();
    function animate(now){
      const raw = clamp((now - startedAt) / duration, 0, 1);
      const eased = raw < 0.5
        ? 4 * raw * raw * raw
        : 1 - (Math.pow((-2 * raw) + 2, 3) / 2);
      const degrees = goingNext
        ? -180 * eased
        : -180 + (180 * eased);
      setTurnRotation(degrees);
      if (raw < 1){
        requestAnimationFrame(animate);
        return;
      }
      finishTurn(currentSlide, targetSlide, target);
    }
    requestAnimationFrame(animate);
  }
  window.addEventListener('resize', () => {
    if (!flipping) return;
    const rect = activeImageRect();
    if (rect) placeTurn(rect);
  });

  function go(delta){
    if (!started || introControlsLocked || wallRevealLocked || flipping) return;
    flipTo(idx + delta);
  }

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
    return clamp(Math.round(v0), 0, 100);
  }
  function loadViewerMuted(){
    try{
      return window.sessionStorage.getItem(muteStorageKey) === 'true';
    }catch(_){
      return false;
    }
  }
  function saveViewerMuted(){
    try{
      window.sessionStorage.setItem(muteStorageKey, viewerMuted ? 'true' : 'false');
    }catch(_){ }
  }
  function setVolume0to100(v){
    const vv = clamp(Math.round(v), 0, 100);
    currentVolume = vv;
    const vol01 = vv / 100;
    const muted = viewerMuted || vv === 0;
    [music, musicStandby].forEach((audio) => {
      if (!audio) return;
      audio.volume = vol01;
      audio.muted = muted;
    });
    volIconImg.src = muted ? 'gallery/controls/voloff.png' : 'gallery/controls/volon.png';
    volIcon.setAttribute('aria-label', muted ? 'Audio muted. Toggle volume slider' : 'Toggle volume slider');
    if (slider) slider.value = String(vv);
  }

  function syncMuteButton(){
    if (!muteBtn) return;
    const label = viewerMuted ? 'Unmute' : 'Mute';
    const description = viewerMuted ? 'Unmute letter audio' : 'Mute letter audio';
    muteBtn.textContent = label;
    muteBtn.title = description;
    muteBtn.setAttribute('aria-label', description);
    muteBtn.setAttribute('aria-pressed', viewerMuted ? 'true' : 'false');
  }

  function setViewerMuted(muted){
    viewerMuted = !!muted;
    saveViewerMuted();
    setVolume0to100(currentVolume);
    syncMuteButton();
  }

  function musicSources(){
    return Array.isArray(MUSIC_PLAYLIST) ? MUSIC_PLAYLIST.filter((value) => typeof value === 'string' && value) : [];
  }
  function ensureInitialMusicSource(){
    const sources = musicSources();
    if (!sources.length || !music) return false;
    musicPlaylistIndex = clamp(musicPlaylistIndex, 0, sources.length - 1);
    const wanted = sources[musicPlaylistIndex];
    if (!music.getAttribute('src') || !music.src.endsWith(wanted)) music.src = wanted;
    return true;
  }
  function installPlaylistListeners(audio){
    if (!audio) return;
    audio.addEventListener('timeupdate', () => {
      if (audio !== music || playlistTransitioning) return;
      const sources = musicSources();
      if (musicPlaylistIndex + 1 >= sources.length) return;
      if (!Number.isFinite(audio.duration) || audio.duration <= 0) return;
      const remainingMs = (audio.duration - audio.currentTime) * 1000;
      if (remainingMs > 0 && remainingMs <= Math.max(120, MUSIC_CROSSFADE_MS || 1000)) crossfadeToNextTrack();
    });
    audio.addEventListener('ended', () => {
      if (audio !== music || playlistTransitioning) return;
      const sources = musicSources();
      if (musicPlaylistIndex + 1 < sources.length) crossfadeToNextTrack();
    });
  }
  function crossfadeToNextTrack(){
    const sources = musicSources();
    if (playlistTransitioning || musicPlaylistIndex + 1 >= sources.length || !music || !musicStandby) return;
    playlistTransitioning = true;
    const nextIndex = musicPlaylistIndex + 1;
    const target = clamp(currentVolume / 100, 0, 1);
    const muted = viewerMuted || target === 0;
    musicStandby.src = sources[nextIndex];
    musicStandby.currentTime = 0;
    musicStandby.volume = 0;
    musicStandby.muted = muted;
    const duration = Math.max(120, Number(MUSIC_CROSSFADE_MS) || 1000);
    const startedAt = performance.now();
    musicStandby.play().catch(() => {
      playlistTransitioning = false;
    });
    function step(now){
      if (!playlistTransitioning) return;
      const t = clamp((now - startedAt) / duration, 0, 1);
      music.volume = target * (1 - t);
      musicStandby.volume = target * t;
      if (t < 1){ requestAnimationFrame(step); return; }
      music.pause();
      music.currentTime = 0;
      const previous = music;
      music = musicStandby;
      musicStandby = previous;
      musicPlaylistIndex = nextIndex;
      music.volume = target;
      music.muted = muted;
      musicStandby.volume = target;
      musicStandby.muted = muted;
      playlistTransitioning = false;
    }
    requestAnimationFrame(step);
  }
  installPlaylistListeners(music);
  installPlaylistListeners(musicStandby);
  if (!musicSources().length && volumeControl) volumeControl.style.display = 'none';
  function setSliderOpen(open){
    const shouldOpen = !!open;
    volumeControl.classList.toggle('slider-open', shouldOpen);
    setExpandedState(volIcon, shouldOpen);
    if (slider) setHiddenState(slider, !shouldOpen);
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
    cLeft.style.opacity = '1';
    cRight.style.opacity = '1';
    cLeft.style.transform = 'translateX(0)';
    cRight.style.transform = 'translateX(0)';
    cLeft.style.animation = 'none';
    cRight.style.animation = 'none';
    void cLeft.offsetWidth;
    cLeft.style.animation = `curtainLeftOut ${openMs}ms cubic-bezier(.2,.9,.1,1) forwards`;
    cRight.style.animation = `curtainRightOut ${openMs}ms cubic-bezier(.2,.9,.1,1) forwards`;
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
      const v = currentVolume;
      setVolume0to100(v);
      if (!ensureInitialMusicSource()){
        tryUnlockIntroControls();
        return;
      }
      try{
        music.currentTime = 0;
        music.volume = 0;
        music.muted = viewerMuted || v === 0;
        music.play().catch(()=>{});
      }catch(_){ }
      const target = clamp(v / 100, 0, 1);
      const start = performance.now();
      function fadeStep(now){
        const t = clamp((now - start) / musicFadeMs, 0, 1);
        const e = t < 0.5 ? 2*t*t : 1 - Math.pow(-2*t + 2, 2)/2;
        music.volume = target * e;
        if (t < 1) requestAnimationFrame(fadeStep);
      }
      requestAnimationFrame(fadeStep);
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
        g.play().catch(() => startMusicAfterGliss());
      }catch(_){ startMusicAfterGliss(); }
      safetyTimer = setTimeout(startMusicAfterGliss, openMs + glissSafetyPadMs);
    }
    try{
      const g = new Audio(glissSrc);
      g.preload = 'auto';
      g.volume = 0.10;
      g.muted = viewerMuted;
      g.addEventListener('ended', startMusicAfterGliss, { once: true });
      g.addEventListener('error', () => { beginGlissAndCurtain(g); startMusicAfterGliss(); }, { once: true });
      g.addEventListener('loadedmetadata', () => beginGlissAndCurtain(g), { once: true });
      g.load();
      setTimeout(() => beginGlissAndCurtain(g), 250);
    }catch(_){
      const openMs = runCurtainMotion(curtainFallbackOpenMs, () => { curtainDone = true; tryUnlockIntroControls(); });
      setTimeout(startMusicAfterGliss, openMs);
    }
  }

  bindPress(beginBtn, (e) => { e.preventDefault(); openCurtain(); });
  prevBtn.addEventListener('click', () => go(-1));
  nextBtn.addEventListener('click', () => go(1));
  if (restartBtn){
    restartBtn.addEventListener('click', () => {
      window.location.reload();
    });
  }
  if (muteBtn){
    muteBtn.addEventListener('click', () => {
      setViewerMuted(!viewerMuted);
    });
  }
  if (fullscreenBtn){
    fullscreenBtn.addEventListener('click', async () => {
      try{
        if (document.fullscreenElement === letterPreviewEl){
          await document.exitFullscreen();
        } else {
          await letterPreviewEl.requestFullscreen();
        }
      }catch(err){
        console.warn('Fullscreen request failed', err);
      }
    });
    document.addEventListener('fullscreenchange', () => {
      const active = document.fullscreenElement === letterPreviewEl;
      const label = active ? 'Exit fullscreen' : 'Fullscreen';
      fullscreenBtn.textContent = label;
      fullscreenBtn.title = active ? 'Exit fullscreen' : 'Enter fullscreen';
      fullscreenBtn.setAttribute('aria-label', fullscreenBtn.title);
    });
  }
  window.addEventListener('keydown', (e) => {
    if (!started || introControlsLocked || wallRevealLocked) return;
    if (e.key === 'ArrowLeft'){
      e.preventDefault();
      go(-1);
    } else if (e.key === 'ArrowRight'){
      e.preventDefault();
      go(1);
    } else if (e.key === 'Escape'){
      if (isWallPage() && wall.classList.contains('is-open')){
        setWallOpen(false);
        wallClosedByUser = true;
        openText.focus({preventScroll:true});
      }
    }
  });
  closeText.addEventListener('click', () => {
    if (!isWallPage()) return;
    clearWallRevealTimers();
    wallRevealLocked = false;
    setWallOpen(false);
    wallClosedByUser = true;
    syncButtons();
    openText.focus({preventScroll:true});
  });
  bindPress(openText, () => {
    if (!isWallPage()) return;
    clearWallRevealTimers();
    wallRevealLocked = false;
    setWallOpen(true);
    wallClosedByUser = false;
    syncButtons();
    closeText.focus({preventScroll:true});
  });
  bindPress(volIcon, () => {
    const s = ensureSlider();
    const shouldOpen = !volumeControl.classList.contains('slider-open');
    setSliderOpen(shouldOpen);
    if (shouldOpen) s.focus({preventScroll:true});
  });

  ensureSlider();
  setSliderOpen(false);
  syncMuteButton();
  setVolume0to100(loadVolume0to100());
  setHiddenState(wall, true);
  setHiddenState(openText, true);
  setHiddenState(closeText, true);
  setExpandedState(openText, false);
  try{
    installUltralinks();
  }catch(error){
    console.error('Ultralink setup failed', error);
  }
  installImageFallbacks();
  waitForCriticalAssets().finally(startCurtainIntro);
});
"""
