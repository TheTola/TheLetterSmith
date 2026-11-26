# ===============================
# File: Template.py
# Purpose: HTML/CSS/JS templates for the eLetter viewer
# ===============================

import json

from desktop_viewer import DESKTOP_VIEWER_CSS, DESKTOP_VIEWER_JS
from page_turn import PAGE_TURN_JS
from page_transitions import PAGE_EFFECTS_JS, TRANSITION_SOUNDS

TEMPLATE_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-{{CSP_NONCE}}'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self'; font-src 'self' data:; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'; worker-src 'none'">
  <title>{{TITLE}}</title>
  <link rel="stylesheet" href="styles.css">

  <link rel="preload" as="image" href="gallery/pages/cover.png" fetchpriority="high">
  <link rel="preload" as="image" href="gallery/controls/cleft.png" fetchpriority="high">
  <link rel="preload" as="image" href="gallery/controls/cright.png" fetchpriority="high">
  <link rel="preload" as="image" href="gallery/controls/bannerman.png">
  <link rel="preload" as="audio" href="gallery/sounds/glissando.mp3">
  {{MUSIC_PRELOAD_HTML}}
</head>

<body>
  <main id="letter-preview">
  <div id="curtain-overlay" aria-hidden="false" role="button" tabindex="0" aria-labelledby="begin-button">
    <img id="curtain-left"  src="gallery/controls/cleft.png"  alt="" aria-hidden="true" decoding="async" fetchpriority="high">
    <img id="curtain-right" src="gallery/controls/cright.png" alt="" aria-hidden="true" decoding="async" fetchpriority="high">
    <span id="begin-button" contenteditable="false">Tap to Begin</span>
    <div id="phone-start-controls" hidden>
      <button id="phone-open" class="hud-button" type="button">Open Letter</button>
      <button id="phone-start-sound" class="hud-button" type="button" aria-label="Sound" aria-pressed="false">Sound Off</button>
      <button id="phone-start-over" class="hud-button" type="button" hidden>Start Over</button>
    </div>
  </div>
  <div
    id="title-banner"
    aria-hidden="true"
    style="--title-banner-text-rgb:{{TITLE_BANNER_TEXT_RGB}}"
  >
    <img
      id="title-banner-art"
      src="gallery/controls/bannerman.png"
      alt=""
      aria-hidden="true"
      decoding="async"
    >
    <span>{{TITLE}}</span>
  </div>

  <button
    id="fullscreen-button"
    inert
    class="hud-button viewer-action"
    type="button"
    title="Enter fullscreen"
    aria-label="Enter fullscreen"
  >Fullscreen</button>

  <div id="slideshow" aria-hidden="true">
    <canvas id="turn" aria-hidden="true" hidden></canvas>

    <section class="slide active" data-index="0" id="slide-0">
      <img src="gallery/pages/cover.png" alt="Cover Page" decoding="async" fetchpriority="high">
    </section>

    <section class="slide" data-index="1" id="slide-1">
      <img src="gallery/pages/letter.png" alt="Main Letter" decoding="async">
    </section>

    <section class="slide" data-index="2" data-page-type="wall" id="slide-2">
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

    <div id="phone-controls" hidden>
      <div id="phone-page-actions">
        <button id="phone-finish" class="hud-button" type="button" hidden>Finish</button>
      </div>
      <div id="phone-navigation" role="group" aria-label="Letter navigation and sound"></div>
    </div>
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

  <script nonce="{{CSP_NONCE}}">
    const INITIAL_VOLUME = {{INITIAL_VOLUME}};
    const INITIAL_MUTED = {{INITIAL_MUTED}};
    const MUSIC_PLAYLIST = {{MUSIC_PLAYLIST_JSON}};
    const MUSIC_CROSSFADE_MS = {{MUSIC_CROSSFADE_MS}};
    const IMAGE_ANIMATIONS = {{IMAGE_ANIMATIONS_JSON}};
    const HAS_MESSAGE = {{HAS_MESSAGE_JSON}};
  </script>

  <script nonce="{{CSP_NONCE}}" src="script.js"></script>
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
  --message-overlay-center-rgb:248,240,220;
  --message-overlay-edge-rgb:241,228,198;
  --message-overlay-opacity:.68;
  --message-overlay-surface-opacity:.68;
  --message-overlay-border:rgba(104,76,43,.18);
  --message-overlay-inner-border:rgba(104,76,43,.10);
  --message-overlay-shadow:0 18px 52px rgba(0,0,0,.20),inset 0 1px 0 rgba(255,255,255,.32);
  --message-ink:#221710;
  --adaptive-max-lightness:.03;
  --duration-hover:150ms;
  --duration-overlay:180ms;
  --duration-major:640ms;
  --duration-page-turn:1250ms;
  --duration-curtain:1500ms;
  --duration-attention:2000ms;
  --ease-standard:cubic-bezier(.2,.8,.2,1);
  --ease-emphasized:cubic-bezier(.65,0,.35,1);
  --motion-hover:var(--duration-hover) var(--ease-standard);
  --motion-overlay:var(--duration-overlay) var(--ease-standard);
  --motion-major:var(--duration-major) var(--ease-emphasized);
  --stage-backdrop:
    radial-gradient(900px 600px at 30% 25%, var(--bg-rim), transparent 60%),
    radial-gradient(900px 600px at 80% 70%, var(--bg-depth), transparent 60%),
    linear-gradient(180deg, var(--bg-top), var(--bg-bottom));
  --shadow-page:0 18px 40px rgba(0,0,0,.45);
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
  transition:opacity var(--motion-major);
  background:var(--stage-backdrop);
  isolation:isolate;
}
body.stage-ready #slideshow{opacity:1;visibility:visible;pointer-events:auto}

#curtain-overlay{position:absolute;inset:0;z-index:9999;overflow:hidden;pointer-events:all;opacity:0;background:var(--stage-backdrop);will-change:opacity;cursor:default;user-select:none;-webkit-user-select:none;caret-color:transparent;touch-action:manipulation}
#curtain-overlay[tabindex]{z-index:10003}
#curtain-overlay.is-visible{animation:curtainIntroFadeIn var(--motion-major) forwards}
#curtain-left,#curtain-right{position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover;z-index:10000;pointer-events:none;opacity:0;will-change:opacity,transform}
#curtain-overlay.is-visible #curtain-left,#curtain-overlay.is-visible #curtain-right{animation:curtainPanelFadeIn var(--motion-major) forwards}
#curtain-overlay.curtain-fallback #curtain-left,#curtain-overlay.curtain-fallback #curtain-right{display:none}
#begin-button{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);min-width:min(80vw,280px);padding:14px 30px;border:1px solid rgba(155,255,251,.42);border-radius:var(--pill-radius);background:linear-gradient(180deg,rgba(15,24,38,.88),rgba(6,10,18,.72));color:var(--text-main);font-size:clamp(22px,3.2vw,42px);font-weight:700;letter-spacing:.02em;text-align:center;cursor:default;pointer-events:none;user-select:none;-webkit-user-select:none;-webkit-touch-callout:none;caret-color:transparent;z-index:10001;animation:pulse var(--duration-attention) infinite;box-shadow:var(--hud-shadow),0 0 0 1px rgba(0,255,255,.14)}
#curtain-overlay:not(.is-visible) #begin-button{opacity:0}
#begin-button.is-dismissed{animation:none;opacity:0;visibility:hidden;transition:opacity 160ms ease-out,visibility 0s linear 160ms}
#title-banner{position:absolute;top:50%;left:50%;z-index:10002;width:min(94vw,1000px,92vh);aspect-ratio:1/1;container-type:inline-size;display:grid;place-items:center;visibility:hidden;opacity:0;pointer-events:none;transform:translate(-50%,-50%) scale(.92);color:rgb(var(--title-banner-text-rgb));filter:drop-shadow(0 12px 20px rgba(0,0,0,.5));will-change:opacity,transform}
#title-banner-art{grid-area:1/1;width:100%;height:100%;object-fit:contain;pointer-events:none;user-select:none}
#title-banner span{grid-area:1/1;position:relative;z-index:1;width:68%;max-width:68%;font-family:var(--font-letter);font-size:6.8cqw;font-weight:700;font-style:italic;line-height:1.08;letter-spacing:.025em;text-align:center;overflow-wrap:anywhere;text-shadow:0 1px 0 rgba(255,255,255,.4),0 4px 9px rgba(0,0,0,.72);-webkit-text-stroke:clamp(1px,.18cqw,2px) rgba(0,0,0,.68);paint-order:stroke fill}
#title-banner.is-showing{visibility:visible;animation:titleBannerFadeIn 280ms cubic-bezier(.18,.82,.2,1) forwards}
#title-banner.is-hiding{visibility:visible;animation:titleBannerFadeOut 360ms ease-in forwards}
@keyframes curtainIntroFadeIn{from{opacity:0}to{opacity:1}}
@keyframes curtainPanelFadeIn{from{opacity:0}to{opacity:1}}
@keyframes pulse{0%,100%{opacity:.68;transform:translate(-50%,-50%) scale(1)}50%{opacity:1;transform:translate(-50%,-50%) scale(1.04)}}
@keyframes titleBannerFadeIn{from{opacity:0;transform:translate(-50%,-50%) scale(.92)}to{opacity:1;transform:translate(-50%,-50%) scale(1)}}
@keyframes titleBannerFadeOut{from{opacity:1;transform:translate(-50%,-50%) scale(1)}to{opacity:0;transform:translate(-50%,-50%) scale(1.035)}}
@keyframes curtainLeftOut{from{transform:translateX(0)}to{transform:translateX(-100vw)}}
@keyframes curtainRightOut{from{transform:translateX(0)}to{transform:translateX(100vw)}}

.slide{position:absolute;inset:0;opacity:0;pointer-events:none;z-index:5}
.slide.active{opacity:1;pointer-events:auto}
.slide.peek{opacity:1;pointer-events:none;z-index:4}
.slide.ghost{opacity:0;pointer-events:none}
.slide.asset-failed > img{opacity:0}
.slide.asset-failed::after{content:attr(data-fallback-label);position:absolute;inset:50% auto auto 50%;transform:translate(-50%,-50%);width:min(520px,72%);padding:18px 22px;color:var(--text-main);text-align:center;font:700 20px/1.4 var(--font-ui);background:rgba(0,0,0,.58);border:1px solid var(--hud-border);border-radius:var(--page-radius);box-shadow:var(--shadow-page);z-index:6}
.slide > img{max-width:calc(100% - (2 * var(--page-side-rail)));max-height:calc(100% - var(--control-rail) - var(--bottom-control-rail));position:absolute;inset:var(--control-rail) var(--page-side-rail) var(--bottom-control-rail);margin:auto;object-fit:contain;border-radius:var(--page-radius);box-shadow:inset 0 0 30px rgba(0,0,0,.20),var(--shadow-page)}
.canvas-bounds{position:absolute;inset:var(--control-rail) var(--page-side-rail) var(--bottom-control-rail);pointer-events:none}
.slide.has-canvas > img{inset:auto;left:var(--canvas-left);top:var(--canvas-top);width:var(--canvas-width);height:var(--canvas-height);max-width:none;max-height:none;margin:0;object-fit:var(--artwork-fit);object-position:var(--artwork-x) var(--artwork-y)}

.nav-button,.hud-button{display:inline-flex;align-items:center;justify-content:center;border:1px solid var(--hud-border);background:linear-gradient(180deg,var(--hud-surface-top),var(--hud-surface-bottom));color:var(--text-main);box-shadow:var(--hud-shadow);backdrop-filter:blur(10px);cursor:pointer;transition:transform var(--motion-hover),background var(--motion-hover),border-color var(--motion-hover),box-shadow var(--motion-hover),opacity var(--motion-overlay),visibility 0s linear var(--duration-overlay)}
.nav-button:hover,.hud-button:hover{background:linear-gradient(180deg,rgba(25,37,55,.88),rgba(10,14,24,.76));border-color:var(--hud-border-strong);box-shadow:var(--hud-shadow-strong)}
.nav-button:active,.hud-button:active{transform:scale(.98)}
.nav-button img,.hud-button img{display:block;width:100%;height:100%;object-fit:contain;pointer-events:none;filter:drop-shadow(0 6px 12px rgba(0,0,0,.34))}
.nav-button:focus-visible,.hud-button:focus-visible,#volume-slider:focus-visible{outline:3px solid rgba(255,255,255,.94);outline-offset:4px}

.nav-button{position:absolute;top:calc(50% + 27px);width:calc(var(--nav-size) + (2 * var(--nav-pad)));height:calc(var(--nav-size) + (2 * var(--nav-pad)));padding:var(--nav-pad);border-radius:50%;z-index:100}
#prev{left:var(--nav-offset);transform:translateY(-50%)}
#next{right:var(--nav-offset);transform:translateY(-50%)}
.nav-button:hover{transform:translateY(-50%) scale(1.04)}
.nav-button img{width:var(--nav-size);height:var(--nav-size)}
.nav-button[disabled]{opacity:.35;cursor:not-allowed;filter:grayscale(1);box-shadow:none}
.nav-button[disabled]:hover{transform:translateY(-50%)}
#slideshow.page-turning .nav-button{opacity:0;pointer-events:none}

#viewer-actions{position:absolute;top:var(--corner-offset);left:var(--corner-offset);display:flex;align-items:center;pointer-events:none;z-index:107}
#viewer-actions-left{display:flex;align-items:center;gap:8px}
#viewer-actions .viewer-action{width:auto;height:38px;padding:0 12px;font:700 12px/1 var(--font-ui);letter-spacing:.03em;pointer-events:auto}
#fullscreen-button{position:absolute;top:var(--corner-offset);right:var(--corner-offset);width:auto;height:38px;padding:0 12px;font:700 12px/1 var(--font-ui);letter-spacing:.03em;z-index:10002;pointer-events:auto}
#mute-button[aria-pressed="true"]{border-color:rgba(155,255,251,.55);background:linear-gradient(180deg,rgba(20,55,64,.92),rgba(8,25,34,.86));color:#eaffff}

.text-wall{position:absolute;top:var(--control-rail);bottom:var(--bottom-control-rail);left:50%;width:min(var(--wall-max-width),calc(100% - (2 * var(--wall-gap))));height:fit-content;max-height:calc(100% - var(--control-rail) - var(--bottom-control-rail));margin:auto 0;overflow:auto;padding:var(--wall-frame-pad);border:1px solid var(--message-overlay-border);border-radius:var(--panel-radius);background:radial-gradient(ellipse at 50% 43%,rgba(var(--message-overlay-center-rgb),var(--message-overlay-surface-opacity)),rgba(var(--message-overlay-edge-rgb),var(--message-overlay-surface-opacity)));color:var(--message-ink);box-shadow:var(--message-overlay-shadow);z-index:105;opacity:0;visibility:hidden;pointer-events:none;transform:translateX(-50%) scale(.975);transform-origin:center;transition:opacity var(--motion-overlay),transform var(--motion-overlay),visibility 0s linear var(--duration-overlay);will-change:opacity,transform;isolation:isolate;scrollbar-width:thin;scrollbar-color:rgba(92,67,40,.52) rgba(0,0,0,.06)}
.text-wall.is-open{opacity:1;visibility:visible;pointer-events:auto;transform:translateX(-50%) scale(1);transition:opacity var(--motion-overlay),transform var(--motion-overlay),visibility 0s}
.text-wall::before{content:"";position:absolute;inset:10px;border:1px solid var(--message-overlay-inner-border);border-radius:calc(var(--panel-radius) - 4px);pointer-events:none}
.text-wall-content{position:relative;z-index:1;max-width:44rem;margin:0 auto;padding:var(--wall-block-pad) var(--wall-inline-pad);color:var(--message-ink);font-family:var(--font-letter);font-size:clamp(17px,1.25vw,20px);line-height:1.68;text-rendering:optimizeLegibility}
.text-wall-content,.text-wall-content *{max-width:100%;overflow-wrap:anywhere}
.text-wall-content > :first-child{margin-top:0 !important}
.text-wall-content > :last-child{margin-bottom:0 !important}
.text-wall-content :where(p,div,blockquote,ul,ol,h1,h2,h3,h4,table){margin-left:0 !important;margin-right:0 !important}
.text-wall-content :where(img,table){height:auto}
.text-wall.canvas-message{inset:auto;left:var(--message-left);top:var(--message-top);width:var(--message-width);height:var(--message-height);max-height:none;margin:0;padding:0;border:0;overflow:visible;transform:scale(var(--canvas-scale));transform-origin:top left;transition:opacity var(--motion-overlay),visibility 0s linear var(--duration-overlay)}
.text-wall.canvas-message.is-open{transform:scale(var(--canvas-scale));transition-delay:0s}
.canvas-message .text-wall-viewport{position:absolute;inset:0;overflow:auto;overscroll-behavior:contain;border-radius:inherit;scrollbar-width:thin}
.canvas-message .text-wall-content{box-sizing:border-box;width:100%;max-width:none;min-height:100%;margin:0;padding:var(--message-padding);text-align:var(--message-align);font-size:18px}
.canvas-message .close-text{position:absolute;top:calc(-44px / var(--canvas-scale));right:0;width:calc(44px / var(--canvas-scale));height:calc(44px / var(--canvas-scale));margin:0;font-size:calc(24px / var(--canvas-scale))}
.message-overflow-note{position:absolute;right:0;bottom:calc(-24px / var(--canvas-scale));padding:2px 5px;border-radius:4px;background:#15151b;color:#fff;font:500 calc(12px / var(--canvas-scale))/1.4 var(--font-ui);white-space:nowrap;pointer-events:none}
.message-overflow-note[hidden]{display:none}
.text-wall-content :is(a[href^="ultralink:"],a[href^="hypernote:"]){font-style:italic;text-decoration:none !important;cursor:help;outline-offset:3px}
.text-wall-content :is(a[href^="ultralink:"],a[href^="hypernote:"]):focus-visible{outline:2px solid #00d0ff;border-radius:3px}
.ultralink-tooltip{position:fixed;left:0;top:0;max-width:min(22rem,calc(100vw - 32px));padding:11px 14px;border:1px solid transparent;border-radius:9px;box-shadow:0 18px 42px rgba(0,0,0,.34);font:500 15px/1.45 Georgia,'Times New Roman',serif;white-space:pre-wrap;overflow-wrap:anywhere;z-index:220;opacity:0;visibility:hidden;transform:translateY(4px);transition:opacity var(--motion-overlay),transform var(--motion-overlay),visibility 0s linear var(--duration-overlay);pointer-events:none}
.ultralink-tooltip.theme-paper{border-color:rgba(112,81,44,.42);background:linear-gradient(180deg,#fff8df,#f2e3bd);color:#24170d;box-shadow:0 18px 42px rgba(0,0,0,.32),inset 0 1px 0 rgba(255,255,255,.65)}
.ultralink-tooltip.theme-dark{border-color:rgba(255,255,255,.24);background:rgba(7,10,16,.94);color:#fff;box-shadow:0 18px 42px rgba(0,0,0,.46),inset 0 1px 0 rgba(255,255,255,.10)}
.ultralink-tooltip.theme-minimal{border-color:rgba(0,0,0,.30);background:rgba(255,255,255,.20);color:#000;box-shadow:0 10px 28px rgba(0,0,0,.20);backdrop-filter:blur(10px)}
.ultralink-tooltip.is-visible{opacity:1;visibility:visible;transform:translateY(0);transition:opacity var(--motion-overlay),transform var(--motion-overlay),visibility 0s}
.text-wall::-webkit-scrollbar{width:10px}
.text-wall::-webkit-scrollbar-track{background:rgba(0,0,0,.06);border-radius:999px}
.text-wall::-webkit-scrollbar-thumb{background:rgba(92,67,40,.52);border-radius:999px}

.hud-button{width:calc(var(--icon-size) + 10px);height:calc(var(--icon-size) + 10px);padding:5px;border-radius:var(--pill-radius)}
#open-text,#close-text,.close-text{opacity:0;visibility:hidden;pointer-events:none}
#open-text.is-visible,#close-text.is-visible,.close-text.is-visible{opacity:1;visibility:visible;pointer-events:auto;transition-delay:0s}
#open-text{position:absolute;left:var(--corner-offset);bottom:var(--corner-offset);z-index:101;transform:translateY(8px) scale(.96)}
#open-text.is-visible{transform:translateY(0) scale(1)}
#open-text img{width:var(--icon-size);height:var(--icon-size);padding:6px}
#open-text:hover{transform:translateY(0) scale(1.05)}
.close-text,#close-text{position:sticky;top:0;left:auto;bottom:auto;display:flex;margin:0 0 0 auto;width:var(--close-size);height:var(--close-size);border-radius:14px;font-size:clamp(24px,2.8vw,28px);line-height:1;z-index:3;transform:scale(.96)}
#close-text.is-visible,.close-text.is-visible{transform:scale(1)}
#page-navigation{position:absolute;left:50%;bottom:var(--corner-offset);transform:translateX(-50%);display:flex;align-items:center;gap:8px;max-width:calc(100% - 220px);padding:6px 10px;border-radius:var(--panel-radius);background:rgba(14,14,18,.85);color:#fff;font:500 12px/1.4 var(--font-ui);z-index:106}
#page-indicator{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#page-selector{max-width:130px;color:#fff;background:#25252b;border:1px solid #777;border-radius:4px;padding:4px}
@media(max-width:520px){#page-navigation{bottom:calc(var(--bottom-control-rail) + 4px);max-width:calc(100% - 24px)}#page-selector{max-width:100px}}

#volume-control{position:absolute;right:var(--corner-offset);bottom:var(--corner-offset);display:flex;align-items:center;gap:10px;padding:6px;border:1px solid rgba(255,255,255,.08);border-radius:var(--pill-radius);background:rgba(42,46,54,.72);box-shadow:var(--hud-shadow);backdrop-filter:blur(10px);z-index:102;opacity:0;visibility:hidden;pointer-events:none;transform:translateY(0);transition:opacity var(--motion-overlay),transform var(--motion-major),visibility 0s linear var(--duration-overlay)}
body.stage-ready #volume-control{opacity:1;visibility:visible;pointer-events:auto;transition-delay:0s}
#volume-control.slider-open{border-color:rgba(155,255,251,.18);background:rgba(50,55,64,.82)}
#volume-icon{flex:none}
#volume-icon-img{width:var(--icon-size);height:var(--icon-size);padding:6px}
#volume-slider{width:0;min-width:0;-webkit-appearance:none;appearance:none;height:4px;opacity:0;pointer-events:none;background:rgba(255,255,255,.30);border-radius:2px;transform:scaleX(.92);transform-origin:right center;transition:width var(--motion-major),opacity var(--motion-overlay),transform var(--motion-hover)}
#volume-control.slider-open #volume-slider{width:var(--slider-width);opacity:1;pointer-events:auto;transform:scaleX(1)}
#volume-slider::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;width:16px;height:16px;background:var(--accent-strong);border-radius:50%;cursor:pointer;border:2px solid #fff;box-shadow:0 0 0 4px rgba(0,255,255,.12)}
#volume-slider::-moz-range-thumb{width:16px;height:16px;background:var(--accent-strong);border:2px solid #fff;border-radius:50%;cursor:pointer;box-shadow:0 0 0 4px rgba(0,255,255,.12)}

#turn{position:absolute;inset:0;width:100%;height:100%;pointer-events:none;z-index:40;background:var(--stage-backdrop)}
#letter-preview[data-turn-effect="pixelate"] :is(#viewer-actions,#fullscreen-button,#volume-control,#page-navigation,#open-text,#title-banner){visibility:hidden}

@media (max-width: 900px){:root{--nav-size:clamp(48px,8vw,66px);--wall-gap:clamp(18px,4vw,30px)}.text-wall-content{font-size:clamp(16px,1.8vw,18px)}}
@media (max-width: 640px){:root{--nav-offset:4px;--nav-size:34px;--nav-pad:4px;--icon-size:34px;--close-size:38px;--corner-offset:12px;--control-rail:50px;--bottom-control-rail:60px;--page-side-rail:50px}#prev,#next{top:50%}#volume-control{left:var(--corner-offset);right:auto;bottom:calc(5px + env(safe-area-inset-bottom));padding:4px;transform:none}body.stage-ready #volume-control{transform:none}#viewer-actions{top:10px;left:10px}#viewer-actions-left{gap:5px}#viewer-actions .viewer-action,#fullscreen-button{height:32px;padding:0 8px;font-size:10px}#fullscreen-button{top:10px;right:10px}#open-text{left:auto;right:var(--corner-offset);bottom:calc(5px + env(safe-area-inset-bottom));transform:none}.text-wall{width:calc(100% - (2 * var(--wall-gap)));max-height:calc(100% - var(--control-rail) - var(--bottom-control-rail))}#volume-control.slider-open #volume-slider{width:min(28vw,64px)}}
@media (max-width: 220px){:root{--page-side-rail:58px}#viewer-actions .viewer-action,#fullscreen-button{width:34px;padding:0;font-size:0}#restart-button::before{content:"Γå╗";font-size:18px}#mute-button::before{content:"≡ƒöç";font-size:15px}#mute-button[aria-pressed="true"]::before{content:"≡ƒöè"}#fullscreen-button::before{content:"Γ¢╢";font-size:17px}}
@media (max-height: 420px){:root{--nav-offset:4px;--nav-size:34px;--nav-pad:4px;--icon-size:32px;--close-size:34px;--corner-offset:8px;--control-rail:48px;--bottom-control-rail:52px;--page-side-rail:50px;--wall-gap:10px;--wall-frame-pad:8px;--wall-block-pad:14px;--wall-inline-pad:12px}#viewer-actions{top:8px;left:8px}#viewer-actions-left{gap:5px}#viewer-actions .viewer-action,#fullscreen-button{height:30px;padding:0 8px;font-size:10px}#fullscreen-button{top:8px;right:8px}#volume-control{bottom:6px;padding:3px}#open-text{bottom:6px}.text-wall-content{font-size:15px;line-height:1.45}}
@media (prefers-reduced-motion: reduce){*,*::before,*::after{scroll-behavior:auto !important}#slideshow,#volume-control,#open-text,#close-text,.text-wall,#volume-slider{transition:none}#begin-button{animation:none}#curtain-overlay.is-visible,#curtain-overlay.is-visible #curtain-left,#curtain-overlay.is-visible #curtain-right,#title-banner.is-showing,#title-banner.is-hiding{animation-duration:0.01ms !important;animation-iteration-count:1 !important}}

#letter-preview.phone-viewer{
  --phone-top:max(8px,env(safe-area-inset-top));
  --phone-bottom:max(8px,env(safe-area-inset-bottom));
  --phone-left:max(8px,env(safe-area-inset-left));
  --phone-right:max(8px,env(safe-area-inset-right));
  --phone-bar-height:52px;
  --phone-actions-height:50px;
  --control-rail:calc(var(--phone-top) + 48px);
  --bottom-control-rail:calc(var(--phone-bottom) + var(--phone-bar-height) + 6px);
}
@supports (height:100dvh){#letter-preview.phone-viewer{height:100dvh}}
:is(#phone-start-controls,#phone-controls)[hidden],.phone-viewer [hidden]{display:none !important}
.phone-viewer .slide.phone-page-actions{--bottom-control-rail:calc(var(--phone-bottom) + var(--phone-bar-height) + var(--phone-actions-height) + 6px)}
.phone-viewer .slide:not(.has-canvas) > img,.phone-viewer .canvas-bounds{left:var(--phone-left);right:var(--phone-right)}
.phone-viewer .slide:not(.has-canvas) > img{max-width:calc(100% - var(--phone-left) - var(--phone-right))}
.phone-viewer .slide > img{touch-action:pan-y pinch-zoom;user-select:none;-webkit-user-drag:none}
.phone-viewer :is(.hud-button,.nav-button,#page-selector){touch-action:manipulation}
.phone-viewer #begin-button,.phone-viewer .slide.asset-failed::after{display:none}
.phone-viewer #curtain-overlay{z-index:10003}
#phone-start-controls{
  position:absolute;inset:var(--phone-top) var(--phone-right) var(--phone-bottom) var(--phone-left);z-index:10001;
  display:grid;grid-template-columns:repeat(2,max-content);place-content:center;gap:10px;pointer-events:none;
}
#phone-start-controls button{pointer-events:auto}
#phone-start-over{grid-column:1 / -1;justify-self:center}
.phone-viewer #phone-open,.phone-viewer #open-text,.phone-viewer #phone-finish{
  border-color:rgba(155,255,251,.5);background:#142f3a;
}
.phone-viewer #viewer-actions{top:var(--phone-top);left:var(--phone-left)}
.phone-viewer #fullscreen-button{top:var(--phone-top);right:var(--phone-right)}
.phone-viewer :is(#phone-start-controls,#phone-controls,.phone-reader-tools,.phone-artwork-retry) .hud-button,
.phone-viewer #viewer-actions .viewer-action,.phone-viewer #fullscreen-button{
  width:auto;min-width:44px;height:44px;padding:0 12px;font:700 13px/1 var(--font-ui);white-space:nowrap;
}
#phone-controls{position:absolute;left:var(--phone-left);right:var(--phone-right);bottom:var(--phone-bottom);z-index:109;display:flex;flex-direction:column;align-items:center;gap:6px;pointer-events:none}
#phone-page-actions,#phone-navigation{display:flex;align-items:center;justify-content:center;gap:6px;pointer-events:auto}
#phone-navigation{height:var(--phone-bar-height);max-width:100%;padding:4px;border:1px solid var(--hud-border);border-radius:var(--pill-radius);background:#10141e;box-shadow:var(--hud-shadow);gap:4px}
.phone-viewer #phone-navigation .nav-button{position:relative;inset:auto;transform:none;width:44px;height:44px;padding:6px}
.phone-viewer #phone-navigation .nav-button img{width:100%;height:100%}
.phone-viewer #slideshow.page-turning .nav-button{opacity:.35}
.phone-viewer #page-navigation{position:relative;inset:auto;transform:none;max-width:none;min-width:62px;min-height:44px;justify-content:center;padding:0 6px;background:none;white-space:nowrap}
.phone-viewer #page-navigation:focus-within{outline:2px solid #fff}
.phone-viewer #page-navigation:has(#page-selector)::after{content:"";width:5px;height:5px;border-right:1px solid;border-bottom:1px solid;transform:translateY(-2px) rotate(45deg);pointer-events:none}
.phone-viewer #page-selector{position:absolute;inset:0;width:100%;max-width:none;height:100%;opacity:0;cursor:pointer}
.phone-viewer #volume-control,.phone-viewer #volume-control.slider-open{position:relative;inset:auto;transform:none;padding:0;border:0;background:none;box-shadow:none;backdrop-filter:none}
.phone-viewer #volume-icon{width:44px;padding:0}
.phone-viewer #volume-icon-img{width:30px;height:30px;padding:0}
.phone-viewer #volume-slider{display:none}
.phone-viewer #volume-control.slider-open #volume-slider{
  display:block;position:absolute;right:0;bottom:calc(100% + var(--phone-actions-height) + 12px);
  width:112px;height:24px;background:#29313d;box-shadow:0 0 0 8px #10141e;
}
.phone-viewer #open-text{position:relative;inset:auto;transform:none}
.phone-viewer #open-text:not(.is-visible){display:none}
.phone-viewer #phone-finish:disabled{opacity:.5}
.phone-viewer .phone-artwork-retry{position:absolute;inset:var(--control-rail) var(--phone-right) var(--bottom-control-rail) var(--phone-left);display:grid;place-items:center;pointer-events:none}
.phone-viewer .phone-artwork-retry button{pointer-events:auto}
.phone-viewer .slide:not(.active) .phone-artwork-retry{visibility:hidden}
.phone-viewer[data-turn-effect="pixelate"] #phone-navigation :is(#page-navigation,#volume-control){visibility:visible}

.phone-viewer.phone-reading :is(#phone-controls,#viewer-actions,#fullscreen-button,.phone-artwork-retry){display:none}
.phone-viewer .text-wall,.phone-viewer .text-wall.is-open{
  inset:0;z-index:200;display:flex;flex-direction:column;width:auto;height:auto;max-height:none;margin:0;
  padding:var(--phone-top) var(--phone-right) var(--phone-bottom) var(--phone-left);overflow:hidden;
  border:0;border-radius:0;transform:none;--message-overlay-surface-opacity:1 !important;
}
.phone-viewer .text-wall::before,.phone-viewer .message-overflow-note{display:none}
.phone-reader-tools{display:flex;align-items:center;justify-content:flex-end;gap:8px;padding:0 4px 8px;border-bottom:1px solid var(--message-overlay-border)}
.phone-viewer .phone-reader-tools .hud-button:disabled{opacity:.45;cursor:default}
.phone-viewer .phone-reader-tools :is(.close-text,#close-text){position:static;flex:none;margin:0 0 0 4px;font-size:24px}
.phone-viewer .text-wall-viewport{position:relative;inset:auto;flex:1;min-height:0;overflow:auto;overscroll-behavior:contain;touch-action:pan-y pinch-zoom}
.phone-viewer .text-wall-content{
  width:auto;min-height:0;max-width:44rem;padding:16px clamp(8px,4vw,24px) 24px;
  font-size:18px;line-height:1.6;zoom:var(--phone-reading-scale,1);
}
.phone-viewer .text-wall-content *{min-width:0 !important;overflow-wrap:anywhere !important}
.phone-viewer .text-wall-content :is(pre,[style*="white-space:nowrap"],[style*="white-space:pre;"]){white-space:pre-wrap !important}
.phone-viewer .text-wall-content table{table-layout:fixed;width:100% !important}

@media (orientation:landscape) and (min-width:620px){
  #letter-preview.phone-viewer{--phone-actions-height:0px}
  #phone-controls{flex-direction:row-reverse;justify-content:center}
}
""" + DESKTOP_VIEWER_CSS

TRANSITION_SOUNDS_JS = "const TRANSITION_SOUNDS = " + json.dumps({
    style: ["gallery/sounds/" + filename for filename in filenames]
    for style, filenames in TRANSITION_SOUNDS.items()
}) + ";\n"

TEMPLATE_JS = TRANSITION_SOUNDS_JS + PAGE_EFFECTS_JS + PAGE_TURN_JS + DESKTOP_VIEWER_JS + r"""
document.addEventListener('DOMContentLoaded', () => {
  const overlay   = document.getElementById('curtain-overlay');
  const cLeft     = document.getElementById('curtain-left');
  const cRight    = document.getElementById('curtain-right');
  const beginBtn  = document.getElementById('begin-button');
  const titleBanner = document.getElementById('title-banner');

  const slides    = Array.from(document.querySelectorAll('.slide'));
  const prevBtn   = document.getElementById('prev');
  const nextBtn   = document.getElementById('next');
  const restartBtn = document.getElementById('restart-button');
  const muteBtn = document.getElementById('mute-button');
  const fullscreenBtn = document.getElementById('fullscreen-button');
  const letterPreviewEl = document.getElementById('letter-preview');
  const userAgent = navigator.userAgent;
  const forgePreview = window.letterSmithForgePreview;
  // A narrow viewport or a touch screen can also belong to a desktop or tablet.
  const phoneViewer = forgePreview?.phone ?? (
    !/\b(?:iPad|Tablet)\b/i.test(userAgent) && (
      /\b(?:iPhone|iPod|Windows Phone)\b/i.test(userAgent) ||
      (/\bAndroid\b/i.test(userAgent) && (navigator.userAgentData?.mobile || /\bMobile\b/i.test(userAgent)))
    )
  );
  const desktopViewer = !phoneViewer && !navigator.userAgentData?.mobile &&
    !/\b(?:Android|iPad|Tablet|Mobile)\b/i.test(userAgent) && navigator.userAgentData?.platform !== 'Android' &&
    !(navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1) &&
    !window.matchMedia('(pointer: coarse) and (hover: none)').matches;
  const letterTransition = letterPreviewEl.dataset.transition || 'classic';
  const turn = document.getElementById('turn');

  const legacyWall = document.getElementById('textWall');
  const messageWalls = Array.from(document.querySelectorAll('.text-wall'));
  let wall         = legacyWall;
  let closeText    = document.getElementById('close-text');
  const openText   = document.getElementById('open-text');
  let pageIndicator = document.getElementById('page-indicator');
  const pageSelector = desktopViewer ? null : document.getElementById('page-selector');
  const phoneStartControls = document.getElementById('phone-start-controls');
  const phoneOpenBtn = document.getElementById('phone-open');
  const phoneStartSound = document.getElementById('phone-start-sound');
  const phoneStartOver = document.getElementById('phone-start-over');
  const phoneControls = document.getElementById('phone-controls');
  const phonePageActions = document.getElementById('phone-page-actions');
  const phoneFinish = document.getElementById('phone-finish');

  const slideshowEl = document.getElementById('slideshow');
  const volumeControl = document.getElementById('volume-control');
  const volIcon   = document.getElementById('volume-icon');
  const volIconImg = document.getElementById('volume-icon-img');
  let music       = document.getElementById('bg-music');
  let musicStandby = document.getElementById('bg-music-standby');
  const reducedMotionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
  const prefersReducedMotion = reducedMotionQuery.matches;
  const motionStyles = getComputedStyle(document.documentElement);
  function motionDuration(property, fallbackMs){
    const raw = motionStyles.getPropertyValue(property).trim();
    const value = Number.parseFloat(raw);
    if (!raw || !Number.isFinite(value)) return fallbackMs;
    return raw.endsWith('s') && !raw.endsWith('ms') ? value * 1000 : value;
  }
  function motionValue(property, fallback){
    return motionStyles.getPropertyValue(property).trim() || fallback;
  }
  const motion = Object.freeze({
    duration: Object.freeze({
      overlay: motionDuration('--duration-overlay', 180),
      major: motionDuration('--duration-major', 640),
      pageTurn: motionDuration('--duration-page-turn', 1250),
      curtain: motionDuration('--duration-curtain', 1500),
    }),
    easing: Object.freeze({
      emphasized: motionValue('--ease-emphasized', 'cubic-bezier(.65,0,.35,1)'),
    }),
    ease(value){
      const t = clamp(value, 0, 1);
      return t * t * (3 - 2 * t);
    },
  });
  const curtainIntroRevealMs = prefersReducedMotion ? 80 : motion.duration.major;
  const curtainOpenMs = prefersReducedMotion ? 140 : motion.duration.curtain;
  const curtainCleanupPadMs = prefersReducedMotion ? 20 : 0;
  const titleBannerDelayMs = prefersReducedMotion ? 80 : 500;
  const titleBannerFadeInMs = prefersReducedMotion ? 10 : 280;
  const titleBannerHoldMs = 3500;
  const titleBannerFadeOutMs = prefersReducedMotion ? 10 : 360;
  const glissSafetyPadMs = prefersReducedMotion ? 120 : 450;
  const musicFadeMs = prefersReducedMotion ? 120 : 900;
  const wallRevealDelayMs = prefersReducedMotion ? 80 : 2200;
  const wallRevealFadeMs = prefersReducedMotion ? 120 : motion.duration.overlay;

  const TOTAL = slides.length;
  let started = false;
  let introControlsLocked = false;
  let idx = 0;
  const imageAnimationStates = new Map();
  let wallClosedByUser = false;
  let wallRevealLocked = false;
  let wallRevealTimer = null;
  let wallRevealUnlockTimer = null;
  let slider = null;
  let desktopControls = null;
  let stageReady = false;
  let introStarted = false;
  let titleBannerStarted = false;
  let titleBannerRevealTimer = null;
  let titleBannerAutoDismissTimer = null;
  let titleBannerCleanupTimer = null;
  let deferredWarmStarted = false;
  let flipping = false;
  let musicPlaylistIndex = 0;
  let requestedPageId = null;
  let playlistTransitioning = false;
  let playlistTransitionTimer = null;
  const musicLoopDelayMs = 1200;
  const playbackIdentity = document.body.dataset.playbackId;
  const muteStorageKey = 'lettersmith.viewerMuted' + (playbackIdentity ? ':' + playbackIdentity : '');
  const phoneProgressKey = 'lettersmith.phoneProgress:' + (playbackIdentity || location.pathname);
  const phoneReadingScales = [1, 1.15, 1.32, 1.52, 1.75, 2];
  let phoneProgress = phoneViewer ? loadPhoneProgress() : null;
  let phoneReadingSize = phoneProgress?.size ?? 0;
  let phoneScrollPositions = phoneProgress?.scroll ?? [];
  let phoneProgressTimer = null;
  let phoneFinishing = false;
  let phoneFinishTimer = null;
  let phoneReplay = false;
  const phoneReaderViewports = new Map();
  const recoveredImageSources = new Map();
  let viewerMuted = loadViewerMuted();
  let currentVolume = loadVolume0to100();
  let playbackVisible = true;
  let playbackRequested = false;
  let playbackDisposed = false;
  let playbackGeneration = 0;
  const playbackTimers = new Set();
  const playbackFrames = new Set();
  const ownedAudio = new Set([music, musicStandby].filter(Boolean));
  const musicGains = new Map();
  let phoneAudioContext = null;

  const glissSrc = 'gallery/sounds/glissando.mp3';
  const deferredAssets = [
    ...slides.slice(1).map((slide) => ({ as: 'image', href: slide.querySelector('img').getAttribute('src') })),
    { as: 'image', href: 'gallery/controls/ppage.png' },
    { as: 'image', href: 'gallery/controls/npage.png' },
    { as: 'image', href: 'gallery/controls/volon.png' },
    { as: 'image', href: 'gallery/controls/voloff.png' },
    { as: 'image', href: 'gallery/controls/showmessageicon.png' },
    ...((Array.isArray(MUSIC_PLAYLIST) ? MUSIC_PLAYLIST : []).map((href) => ({ as: 'audio', href, type: 'audio/mpeg' }))),
    ...Object.values(TRANSITION_SOUNDS).flat().map((href) => ({ as: 'audio', href, type: 'audio/mpeg' })),
    ...(phoneViewer && prefersReducedMotion ? [] : Object.values(IMAGE_ANIMATIONS).flatMap((config) => (
      Array.isArray(config.frames)
        ? config.frames.map((href) => ({ as: 'image', href }))
        : []
    ))),
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

  function loadPhoneProgress(){
    try{
      const saved = JSON.parse(localStorage.getItem(phoneProgressKey));
      if (!Number.isInteger(saved?.page) || saved.page < 0 || saved.page >= TOTAL) return null;
      return {
        page: saved.page,
        reading: saved.reading === true,
        size: Number.isInteger(saved.size) ? clamp(saved.size, 0, phoneReadingScales.length - 1) : 0,
        scroll: Array.isArray(saved.scroll) ? saved.scroll.slice(0, TOTAL).map(position => (
          Number.isFinite(position?.top) && Number.isFinite(position.range) && Number.isFinite(position.width)
            ? position : null
        )) : [],
      };
    }catch(_){ return null; }
  }
  function savePhoneProgress(){
    clearTimeout(phoneProgressTimer);
    phoneProgressTimer = null;
    if (!phoneViewer || !started || phoneFinishing) return;
    const reading = letterPreviewEl.classList.contains('phone-reading');
    if (reading) rememberPhoneScroll(phoneReaderViewports.get(wall));
    phoneProgress = {page:idx, reading, size:phoneReadingSize, scroll:phoneScrollPositions};
    try{ localStorage.setItem(phoneProgressKey, JSON.stringify(phoneProgress)); }catch(_){ }
  }
  function clearPhoneProgress(){
    clearTimeout(phoneProgressTimer);
    phoneProgressTimer = null;
    phoneProgress = null;
    phoneScrollPositions = [];
    try{ localStorage.removeItem(phoneProgressKey); }catch(_){ }
  }
  function syncPhoneStartControls(replay = false){
    phoneOpenBtn.textContent = replay ? 'Replay' : phoneProgress ? 'Continue' : 'Open Letter';
    phoneStartOver.hidden = !phoneProgress;
    phoneStartControls.hidden = false;
  }
  function hasWallMessage(item){
    return !!item && (item.dataset.hasMessage === undefined ? HAS_MESSAGE : item.dataset.hasMessage === 'true');
  }
  function rememberPhoneScroll(viewport){
    phoneScrollPositions[idx] = {
      top: viewport.scrollTop,
      range: viewport.scrollHeight - viewport.clientHeight,
      width: viewport.clientWidth,
    };
  }
  function syncPhoneReading(open){
    const viewport = phoneReaderViewports.get(wall);
    const wasOpen = letterPreviewEl.classList.contains('phone-reading');
    if (wasOpen && !open) rememberPhoneScroll(viewport);
    letterPreviewEl.classList.toggle('phone-reading', open);
    wall.inert = !open;
    if (open && !wasOpen){
      setSliderOpen(false);
      const position = phoneScrollPositions[idx];
      viewport.scrollTop = position?.width === viewport.clientWidth ? position.top :
        position?.range > 0 ? (position.top / position.range) * (viewport.scrollHeight - viewport.clientHeight) : 0;
    }
  }
  function installPhoneReader(){
    const sizeButtons = [];
    function applySize(){
      letterPreviewEl.style.setProperty('--phone-reading-scale', phoneReadingScales[phoneReadingSize]);
      for (const [smaller, larger] of sizeButtons){
        smaller.disabled = phoneReadingSize === 0;
        larger.disabled = phoneReadingSize === phoneReadingScales.length - 1;
      }
    }
    function changeSize(step){
      const viewport = phoneReaderViewports.get(wall);
      const range = viewport.scrollHeight - viewport.clientHeight;
      const scrollRatio = range > 0 ? viewport.scrollTop / range : 0;
      phoneReadingSize = clamp(phoneReadingSize + step, 0, phoneReadingScales.length - 1);
      applySize();
      viewport.scrollTop = scrollRatio * (viewport.scrollHeight - viewport.clientHeight);
      savePhoneProgress();
    }
    function sizeButton(label, name, step){
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'hud-button';
      button.textContent = label;
      button.setAttribute('aria-label', name);
      button.addEventListener('click', () => changeSize(step));
      return button;
    }
    for (const item of messageWalls){
      let viewport = item.querySelector('.text-wall-viewport');
      if (!viewport){
        viewport = document.createElement('div');
        viewport.className = 'text-wall-viewport';
        viewport.tabIndex = 0;
        viewport.setAttribute('role', 'region');
        viewport.setAttribute('aria-label', 'Message text');
        viewport.append(item.querySelector('.text-wall-content'));
        item.append(viewport);
      }
      phoneReaderViewports.set(item, viewport);
      item.inert = true;
      item.setAttribute('aria-modal', 'true');
      const toolbar = document.createElement('div');
      toolbar.className = 'phone-reader-tools';
      const smaller = sizeButton('A−', 'Decrease text size', -1);
      const larger = sizeButton('A+', 'Increase text size', 1);
      sizeButtons.push([smaller, larger]);
      toolbar.append(smaller, larger, item.querySelector('.hud-button-close'));
      item.prepend(toolbar);
      viewport.addEventListener('scroll', () => {
        if (item !== wall || !item.classList.contains('is-open') || phoneProgressTimer !== null) return;
        phoneProgressTimer = setTimeout(savePhoneProgress, 150);
      }, {passive:true});
    }
    applySize();
  }
  function installPhoneControls(){
    letterPreviewEl.classList.add('phone-viewer');
    document.querySelector('meta[name="viewport"]').content += ', viewport-fit=cover';
    overlay.removeAttribute('tabindex');
    overlay.removeAttribute('role');
    overlay.removeAttribute('aria-labelledby');
    if (fullscreenBtn && !document.fullscreenEnabled) fullscreenBtn.hidden = true;
    slideshowEl.inert = true;

    let pageNavigation = document.getElementById('page-navigation');
    if (!pageNavigation){
      pageNavigation = document.createElement('div');
      pageNavigation.id = 'page-navigation';
      pageIndicator = document.createElement('span');
      pageIndicator.id = 'page-indicator';
      pageIndicator.setAttribute('role', 'status');
      pageIndicator.setAttribute('aria-live', 'polite');
      pageNavigation.append(pageIndicator);
    }
    document.getElementById('phone-navigation').append(prevBtn, pageNavigation, nextBtn, muteBtn, volumeControl);
    muteBtn.setAttribute('aria-label', 'Sound');
    phonePageActions.prepend(openText);
    openText.textContent = 'Read Message';
    openText.title = 'Read Message';
    openText.setAttribute('aria-label', 'Read message');
    for (const [index, slide] of slides.entries()){
      const message = messageWalls.find(item => item.dataset.pageId && item.dataset.pageId === slide.dataset.pageId) ||
        (slide.dataset.pageType === 'wall' ? legacyWall : null);
      slide.classList.toggle('phone-page-actions', index === TOTAL - 1 || hasWallMessage(message));
    }
    phoneControls.hidden = false;
    syncPhoneStartControls();
    installPhoneReader();

    let swipe = null;
    slideshowEl.addEventListener('pointerdown', event => {
      swipe = event.pointerType === 'touch' && event.isPrimary && event.target === slideImageEl(slides[idx])
        ? {id:event.pointerId, x:event.clientX, y:event.clientY} : null;
    }, {passive:true});
    slideshowEl.addEventListener('pointerup', event => {
      if (!swipe || event.pointerId !== swipe.id) return;
      const dx = event.clientX - swipe.x;
      const dy = event.clientY - swipe.y;
      swipe = null;
      if (Math.abs(dx) >= 44 && Math.abs(dx) > 1.4 * Math.abs(dy)) go(dx < 0 ? 1 : -1);
    }, {passive:true});
    slideshowEl.addEventListener('pointercancel', () => { swipe = null; }, {passive:true});
    reducedMotionQuery.addEventListener('change', () => {
      if (started && !phoneFinishing) activateImageAnimation(idx);
    });
  }
  if (phoneViewer) installPhoneControls();

  function installAdaptiveMicroContrast(wall){
    const content = wall.querySelector('.text-wall-content');
    const ownerSlide = wall.closest('.slide') || slides.find((slide) => wall.dataset.pageId && slide.dataset.pageId === wall.dataset.pageId);
    let wallImage = ownerSlide?.querySelector('img') || document.querySelector('[data-page-type="wall"] > img');
    if (!content || !wallImage){
      return { schedule(){} };
    }

    const textNodes = [];
    const walker = document.createTreeWalker(content, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()){
      if (walker.currentNode.nodeValue && walker.currentNode.nodeValue.trim()){
        textNodes.push(walker.currentNode);
      }
    }
    const segmenter = typeof Intl.Segmenter === 'function'
      ? new Intl.Segmenter(undefined, {granularity:'grapheme'})
      : null;
    const supportsHighlights=!!(window.CSS && CSS.highlights && typeof Highlight!=='undefined');
    const fallbackGlyphs=[];
    function graphemeParts(text){
      let fallbackOffset=0;
      return segmenter
        ? Array.from(segmenter.segment(text),(entry)=>[entry.segment,entry.index])
        : Array.from(text,(character)=>{ const entry=[character,fallbackOffset]; fallbackOffset+=character.length; return entry; });
    }
    if (!supportsHighlights){
      for(const node of textNodes){
        const fragment=document.createDocumentFragment();
        const intended=getComputedStyle(node.parentElement).color;
        for(const [character] of graphemeParts(node.nodeValue||'')){
          if (!character.trim()){
            fragment.appendChild(document.createTextNode(character));
            continue;
          }
          const glyph=document.createElement('span');
          glyph.className='ls-amc-glyph';
          glyph.dataset.intendedColor=intended;
          glyph.textContent=character;
          fragment.appendChild(glyph);
          fallbackGlyphs.push(glyph);
        }
        node.replaceWith(fragment);
      }
    }
    content.dataset.adaptiveContrastMode=supportsHighlights?'highlights':'glyph-spans';
    const style = document.createElement('style');
    style.id = `lettersmith-adaptive-micro-contrast-${wall.id}`;
    document.head.appendChild(style);
    let highlightNames = [];
    let imageSample = null;
    let frame = 0;

    function cssRgb(name, fallback){
      const raw = getComputedStyle(wall).getPropertyValue(name).match(/[\d.]+/g);
      return raw && raw.length >= 3 ? raw.slice(0, 3).map(Number) : fallback;
    }
    function parseColor(raw){
      const values = String(raw || '').match(/[\d.]+/g);
      if (!values || values.length < 3) return [238,234,226,1];
      return [Number(values[0]),Number(values[1]),Number(values[2]),values[3] === undefined ? 1 : Number(values[3])];
    }
    function linear(value){
      const channel = clamp(value, 0, 255) / 255;
      return channel <= .04045 ? channel / 12.92 : Math.pow((channel + .055) / 1.055, 2.4);
    }
    function luminance(rgb){
      return (.2126 * linear(rgb[0])) + (.7152 * linear(rgb[1])) + (.0722 * linear(rgb[2]));
    }
    function ratio(first, second){
      const a = luminance(first), b = luminance(second);
      return (Math.max(a,b) + .05) / (Math.min(a,b) + .05);
    }
    function rgbToHsl(rgb){
      const r=rgb[0]/255,g=rgb[1]/255,b=rgb[2]/255,max=Math.max(r,g,b),min=Math.min(r,g,b);
      let h=0,s=0;
      const l=(max+min)/2, delta=max-min;
      if (delta){
        s=delta/(1-Math.abs((2*l)-1));
        if (max===r) h=((g-b)/delta)%6;
        else if (max===g) h=((b-r)/delta)+2;
        else h=((r-g)/delta)+4;
        h=(h*60+360)%360;
      }
      return [h,s,l];
    }
    function hslToRgb(hsl){
      const h=hsl[0],s=hsl[1],l=hsl[2],c=(1-Math.abs((2*l)-1))*s;
      const x=c*(1-Math.abs(((h/60)%2)-1)),m=l-(c/2);
      let rgb;
      if (h<60) rgb=[c,x,0]; else if (h<120) rgb=[x,c,0]; else if (h<180) rgb=[0,c,x];
      else if (h<240) rgb=[0,x,c]; else if (h<300) rgb=[x,0,c]; else rgb=[c,0,x];
      return rgb.map((value)=>Math.round((value+m)*255));
    }
    function adjustedColor(intended, background, maximum){
      const original = intended.slice(0,3);
      const currentRatio = ratio(original, background);
      if (currentRatio >= 7 || maximum <= 0) return original;
      let shift = Math.min(.05, maximum) * clamp((7-currentRatio)/6,0,1);
      shift = Math.floor((shift+1e-12)/.0025)*.0025;
      if (shift <= 0) return original;
      const hsl = rgbToHsl(original);
      hsl[2] = luminance(original) <= luminance(background)
        ? Math.max(0,hsl[2]-shift)
        : Math.min(1,hsl[2]+shift);
      const candidate = hslToRgb(hsl);
      return ratio(candidate,background)+1e-9 < currentRatio ? original : candidate;
    }
    function prepareImage(){
      if (!wallImage.complete || !wallImage.naturalWidth || !wallImage.naturalHeight) return null;
      if (imageSample && imageSample.src === wallImage.currentSrc) return imageSample;
      const scale = Math.min(1,1024/wallImage.naturalWidth,1024/wallImage.naturalHeight);
      const canvas = document.createElement('canvas');
      canvas.width = Math.max(1,Math.round(wallImage.naturalWidth*scale));
      canvas.height = Math.max(1,Math.round(wallImage.naturalHeight*scale));
      const context = canvas.getContext('2d',{willReadFrequently:true});
      try{
        context.drawImage(wallImage,0,0,canvas.width,canvas.height);
        imageSample = {src:wallImage.currentSrc,width:canvas.width,height:canvas.height,data:context.getImageData(0,0,canvas.width,canvas.height).data};
      }catch(_error){ imageSample = null; }
      return imageSample;
    }
    function clearHighlights(){
      if (supportsHighlights){
        highlightNames.forEach((name)=>CSS.highlights.delete(name));
      }
      highlightNames=[];
      style.textContent='';
    }
    function run(){
      frame=0;
      if (!wall.classList.contains('is-open')) return;
      wallImage = (ownerSlide || slides[idx]).querySelector('img');
      const sampleImage=prepareImage();
      if (!sampleImage) return;
      const imageRect=wallImage.getBoundingClientRect(), wallRect=wall.getBoundingClientRect();
      if (!imageRect.width || !imageRect.height || !wallRect.width || !wallRect.height) return;
      const resolved=getComputedStyle(wall);
      const alpha=clamp(Number.parseFloat(resolved.getPropertyValue('--message-overlay-surface-opacity'))||0,0,1);
      const maximum=clamp(Number.parseFloat(resolved.getPropertyValue('--adaptive-max-lightness'))||0,0,.05);
      const center=cssRgb('--message-overlay-center-rgb',[0,0,0]);
      const edge=cssRgb('--message-overlay-edge-rgb',center);
      const groups=new Map();
      let minimumContrast=Infinity;
      function recordContrast(ink,background,opacity){
        const visible=background.map((value,index)=>value+(ink[index]-value)*opacity);
        minimumContrast=Math.min(minimumContrast,ratio(visible,background));
      }
      function reportContrast(){
        wall.dataset.minimumContrast=Number.isFinite(minimumContrast)?String(minimumContrast):'';
      }
      const imageStyle=getComputedStyle(wallImage);
      const imageScale=(imageStyle.objectFit==='cover'?Math.max:Math.min)(
        imageRect.width/wallImage.naturalWidth,imageRect.height/wallImage.naturalHeight);
      const drawnWidth=wallImage.naturalWidth*imageScale,drawnHeight=wallImage.naturalHeight*imageScale;
      const position=imageStyle.objectPosition.split(' ').map(value=>Number.parseFloat(value)/100);
      const imageLeft=imageRect.left+(imageRect.width-drawnWidth)*(Number.isFinite(position[0])?position[0]:.5);
      const imageTop=imageRect.top+(imageRect.height-drawnHeight)*(Number.isFinite(position[1])?position[1]:.5);

      function pixelAt(clientX,clientY){
        const inside=clientX>=Math.max(imageRect.left,imageLeft) && clientX<=Math.min(imageRect.right,imageLeft+drawnWidth) &&
          clientY>=Math.max(imageRect.top,imageTop) && clientY<=Math.min(imageRect.bottom,imageTop+drawnHeight);
        let artwork=[14,14,18];
        if (inside){
          const px=clamp(Math.round(((clientX-imageLeft)/drawnWidth)*(sampleImage.width-1)),0,sampleImage.width-1);
          const py=clamp(Math.round(((clientY-imageTop)/drawnHeight)*(sampleImage.height-1)),0,sampleImage.height-1);
          const offset=((py*sampleImage.width)+px)*4;
          artwork=[sampleImage.data[offset],sampleImage.data[offset+1],sampleImage.data[offset+2]];
        }
        if (alpha<=0) return artwork;
        const nx=clamp((clientX-wallRect.left)/wallRect.width,0,1),ny=clamp((clientY-wallRect.top)/wallRect.height,0,1);
        const distance=Math.min(1,Math.hypot(nx-.5,ny-.43)/.76);
        const surface=center.map((value,index)=>value+((edge[index]-value)*distance));
        return artwork.map((value,index)=>Math.round(value+((surface[index]-value)*alpha)));
      }
      function localBackground(rect){
        const expandX=Math.max(2,rect.width*.15),expandY=Math.max(2,rect.height*.10),samples=[];
        for(let row=0;row<5;row++) for(let column=0;column<5;column++){
          const x=(rect.left-expandX)+((rect.width+(2*expandX))*(column/4));
          const y=(rect.top-expandY)+((rect.height+(2*expandY))*(row/4));
          const rgb=pixelAt(x,y); samples.push([luminance(rgb),rgb]);
        }
        samples.sort((a,b)=>a[0]-b[0]);
        const middle=samples.slice(5,-5);
        return [0,1,2].map((channel)=>Math.round(middle.reduce((sum,item)=>sum+item[1][channel],0)/middle.length));
      }
      function textBackgroundLayers(node){
        const layers=[];
        let element=node.parentElement;
        while(element && element!==content.parentElement){
          const color=parseColor(getComputedStyle(element).backgroundColor);
          if (color[3]>0) layers.push(color);
          if (element===content) break;
          element=element.parentElement;
        }
        return layers.reverse();
      }
      function compositeLayers(background,layers){
        return layers.reduce((current,layer)=>current.map(
          (value,index)=>Math.round(value+((layer[index]-value)*clamp(layer[3],0,1)))
        ),background);
      }
      if (!supportsHighlights){
        for(const glyph of fallbackGlyphs){
          const rect=glyph.getBoundingClientRect();
          if (!rect.width || !rect.height || rect.bottom<wallRect.top || rect.top>wallRect.bottom) continue;
          const intended=parseColor(glyph.dataset.intendedColor);
          const background=compositeLayers(
            localBackground(rect),
            textBackgroundLayers(glyph),
          );
          const adjusted=adjustedColor(intended,background,maximum);
          recordContrast(adjusted,background,intended[3]);
          glyph.style.color=`rgba(${adjusted[0]},${adjusted[1]},${adjusted[2]},${intended[3]})`;
        }
        reportContrast();
        return;
      }
      for(const node of textNodes){
        const text=node.nodeValue||'';
        const intended=parseColor(getComputedStyle(node.parentElement).color);
        const formatBackgrounds=textBackgroundLayers(node);
        const parts=graphemeParts(text);
        for(let partIndex=0;partIndex<parts.length;partIndex++){
          const character=parts[partIndex][0],start=parts[partIndex][1];
          if (!character.trim()) continue;
          const end=start+character.length, range=document.createRange();
          range.setStart(node,start); range.setEnd(node,end);
          const rect=range.getBoundingClientRect();
          if (!rect.width || !rect.height || rect.bottom<wallRect.top || rect.top>wallRect.bottom) continue;
          const background=compositeLayers(localBackground(rect),formatBackgrounds);
          const adjusted=adjustedColor(intended,background,maximum);
          recordContrast(adjusted,background,intended[3]);
          if (adjusted[0]===intended[0] && adjusted[1]===intended[1] && adjusted[2]===intended[2]) continue;
          const color=`rgba(${adjusted[0]},${adjusted[1]},${adjusted[2]},${intended[3]})`;
          if (!groups.has(color)) groups.set(color,[]);
          groups.get(color).push(range);
        }
      }
      clearHighlights();
      const rules=[];
      let index=0;
      for(const [color,ranges] of groups){
        const name=`lettersmith-amc-${wall.id}-${index++}`;
        CSS.highlights.set(name,new Highlight(...ranges));
        highlightNames.push(name);
        rules.push(`#${content.id}::highlight(${name}){color:${color}}`);
      }
      style.textContent=rules.join('\n');
      reportContrast();
    }
    function schedule(){
      if (frame) cancelAnimationFrame(frame);
      frame=requestAnimationFrame(run);
    }
    wall.addEventListener('scroll',schedule,{passive:true});
    wall.querySelector('.text-wall-viewport')?.addEventListener('scroll',schedule,{passive:true});
    window.addEventListener('resize',schedule,{passive:true});
    slides.forEach((slide) => slide.querySelector('img').addEventListener('load',()=>{ imageSample=null; schedule(); }));
    return {schedule};
  }

  const contrastControllers = new Map(messageWalls.map((item) => [item, installAdaptiveMicroContrast(item)]));
  const adaptiveMicroContrast = {schedule(){ contrastControllers.get(wall)?.schedule(); }};

  function installUltralinks(wall){
    const content = wall.querySelector('.text-wall-content');
    if (!content) return;
    const links = Array.from(
      content.querySelectorAll('a[href^="ultralink:"],a[href^="hypernote:"]')
    );
    if (!links.length) return;

    const tooltip = document.createElement('div');
    tooltip.id = legacyWall === wall ? 'ultralink-tooltip' : `ultralink-tooltip-${wall.id}`;
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
  function markSlideAssetFailed(slide, img){
    if (!slide || slide.classList.contains('asset-failed')) return;
    slide.classList.add('asset-failed');
    slide.dataset.fallbackLabel = img?.getAttribute('alt') || 'Page image unavailable';
    desktopControls?.updateArtwork();
  }

  function installPhoneImageRetry(slide, img){
    const index = Number(slide.dataset.index);
    const fallback = document.createElement('div');
    fallback.className = 'phone-artwork-retry';
    fallback.hidden = true;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'hud-button';
    button.textContent = 'Retry Image';
    button.setAttribute('aria-label', `Retry Image — ${img.alt}`);
    fallback.append(button);
    slide.append(fallback);
    let failedSource = '';
    let retry = 0;
    function showFailure(){
      failedSource ||= img.getAttribute('src') || '';
      cancelImageAnimation(index, false);
      markSlideAssetFailed(slide, img);
      button.disabled = false;
      button.textContent = 'Retry Image';
      fallback.hidden = false;
    }
    img.addEventListener('error', showFailure);
    img.addEventListener('load', () => {
      if (!failedSource) return;
      recoveredImageSources.set(failedSource, img.getAttribute('src'));
      failedSource = '';
      slide.classList.remove('asset-failed');
      fallback.hidden = true;
      if (started && idx === index && !phoneFinishing) activateImageAnimation(index);
    });
    button.addEventListener('click', () => {
      if (!failedSource) return;
      const url = new URL(failedSource, location.href);
      if (/^(https?|file):$/.test(url.protocol)) url.searchParams.set('retry', `${Date.now()}-${++retry}`);
      button.disabled = true;
      button.textContent = 'Retrying…';
      img.src = url.href;
    });
    if (img.complete && img.naturalWidth === 0) showFailure();
  }
  function installImageFallbacks(){
    slides.forEach((slide) => {
      const img = slideImageEl(slide);
      if (!img) return;
      if (phoneViewer){ installPhoneImageRetry(slide, img); return; }
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

  function imageAnimationConfig(slideIndex){
    const config = IMAGE_ANIMATIONS[String(slideIndex)];
    if (!config) return null;
    if (config.render_mode === 'native_gif'){
      return config.source && config.preview_source ? config : null;
    }
    return Array.isArray(config.frames) && config.frames.length > 1 ? config : null;
  }

  function setAnimationImageSource(image, source){
    if (source) image.src = recoveredImageSources.get(source) || source;
  }
  function cancelImageAnimation(slideIndex, restorePoster = true){
    const state = imageAnimationStates.get(slideIndex);
    if (!state) return;
    state.cancelled = true;
    if (state.timer !== null) clearTimeout(state.timer);
    if (restorePoster && state.config.render_mode === 'native_gif'){
      setAnimationImageSource(state.image, state.config.preview_source);
    }
    state.timer = null;
    imageAnimationStates.delete(slideIndex);
  }

  function showImageAnimationFrame(state, frameIndex){
    if (state.cancelled) return;
    setAnimationImageSource(state.image, state.config.frames[frameIndex]);
  }

  function imageFrameDuration(state, frameIndex){
    const value = Number(state.config.durations_ms?.[frameIndex]);
    const authoredDuration = Number.isFinite(value) && value > 0 ? value : 100;
    const requestedSpeed = Number(state.config.speed_percent);
    const speedPercent = Number.isFinite(requestedSpeed)
      ? Math.min(400, Math.max(25, requestedSpeed))
      : 100;
    return Math.max(1, Math.round(authoredDuration * 100 / speedPercent));
  }

  function scheduleImageAnimation(state, delayMs, callback){
    if (state.cancelled) return;
    state.timer = setTimeout(() => {
      state.timer = null;
      if (!state.cancelled) callback();
    }, Math.max(0, Number(delayMs) || 0));
  }

  function configuredImagePlayCount(config){
    if (config.play_count === 'forever') return Infinity;
    const value = Number.parseInt(String(config.play_count), 10);
    return Number.isFinite(value) && value > 0 ? value : 1;
  }

  function effectiveImagePlayCount(config){
    const configured = configuredImagePlayCount(config);
    if (config.playback_mode !== 'original') return configured;
    const embedded = config.embedded_play_count === 'forever'
      ? Infinity
      : Math.max(1, Number.parseInt(String(config.embedded_play_count), 10) || 1);
    if (configured === Infinity) return embedded;
    return embedded === Infinity ? configured : Math.min(configured, embedded);
  }

  function playImageForward(state, frameIndex){
    const lastFrame = state.config.frames.length - 1;
    showImageAnimationFrame(state, frameIndex);
    scheduleImageAnimation(state, imageFrameDuration(state, frameIndex), () => {
      if (frameIndex < lastFrame){
        playImageForward(state, frameIndex + 1);
        return;
      }
      state.completedForwardPlays += 1;
      if (state.completedForwardPlays >= state.totalForwardPlays){
        // Hard rule: a completed animation stays on its last displayed frame.
        showImageAnimationFrame(state, lastFrame);
        return;
      }
      scheduleImageAnimation(state, state.config.loop_pause_ms, () => {
        if (state.config.playback_mode === 'ping_pong'){
          playImageReverse(state, lastFrame - 1);
        } else {
          playImageForward(state, 0);
        }
      });
    });
  }

  function playImageReverse(state, frameIndex){
    showImageAnimationFrame(state, frameIndex);
    scheduleImageAnimation(state, imageFrameDuration(state, frameIndex), () => {
      if (frameIndex > 0){
        playImageReverse(state, frameIndex - 1);
        return;
      }
      scheduleImageAnimation(
        state,
        state.config.loop_pause_ms,
        () => playImageForward(state, 0),
      );
    });
  }

  function prepareImageAnimation(slideIndex){
    const slide = slides[slideIndex];
    const config = imageAnimationConfig(slideIndex);
    if (!config || (phoneViewer && slide.classList.contains('asset-failed'))) return;
    cancelImageAnimation(slideIndex);
    const image = slideImageEl(slide);
    if (!image) return;
    setAnimationImageSource(image, config.render_mode === 'native_gif' ? config.preview_source : config.frames[0]);
  }

  function activateImageAnimation(slideIndex){
    const slide = slides[slideIndex];
    const config = imageAnimationConfig(slideIndex);
    if (!config || (phoneViewer && slide.classList.contains('asset-failed'))) return;
    if (config.animation_enabled === false || (phoneViewer && reducedMotionQuery.matches)){
      prepareImageAnimation(slideIndex);
      return;
    }
    cancelImageAnimation(slideIndex);
    const image = slideImageEl(slide);
    if (!image) return;
    const state = {
      cancelled: false,
      timer: null,
      image,
      config,
      completedForwardPlays: 0,
      totalForwardPlays: effectiveImagePlayCount(config),
    };
    imageAnimationStates.set(slideIndex, state);
    if (config.render_mode === 'native_gif'){
      setAnimationImageSource(image, config.source);
      return;
    }
    showImageAnimationFrame(state, 0);
    scheduleImageAnimation(
      state,
      config.start_delay_ms,
      () => playImageForward(state, 0),
    );
  }

  function revealStage(){
    if (stageReady) return;
    stageReady = true;
    setHiddenState(slideshowEl, false);
    setHiddenState(volumeControl, false);
    document.body.classList.add('stage-ready');
    const requestedIndex = slides.findIndex((slide) => slide.dataset.pageId === requestedPageId);
    const fallbackIndex = forgePreview ? idx : 0;
    setActiveIndex(requestedIndex < 0 ? fallbackIndex : requestedIndex, { playSound: false });
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

  function isWallPage(){
    const slide = slides[idx];
    return !!slide && (slide.dataset.pageType === "wall" ||
      (slide.dataset.canonical === 'true' && slide.dataset.pageType === "letter"));
  }
  function selectMessageWall(){
    wall = messageWalls.find((item) => item.dataset.pageId && item.dataset.pageId === slides[idx]?.dataset.pageId) || legacyWall;
    closeText = wall?.querySelector('.hud-button-close') || null;
    if (wall) openText.setAttribute('aria-controls', wall.id);
    else openText.removeAttribute('aria-controls');
  }
  const canvasPages = slides.filter((slide) => slide.dataset.layout).map((slide) => ({
    slide, layout:JSON.parse(slide.dataset.layout),
    wall:messageWalls.find((item) => item.dataset.pageId === slide.dataset.pageId),
  }));
  let layoutIssues = [];
  let layoutFrame = 0;
  function updateCanvasLayouts(){
    const issues = [];
    for (const item of canvasPages){
      const {slide, layout, wall:messageWall} = item;
      const bounds = slide.querySelector('.canvas-bounds').getBoundingClientRect();
      const stage = slide.getBoundingClientRect();
      const canvas = layout.artwork.canvas;
      const scale = Math.min(bounds.width / canvas.width, bounds.height / canvas.height);
      if (!(scale > 0)) continue;
      const left = bounds.left - stage.left + (bounds.width - canvas.width * scale) / 2;
      const top = bounds.top - stage.top + (bounds.height - canvas.height * scale) / 2;
      const properties = {
        '--canvas-left':`${left}px`, '--canvas-top':`${top}px`,
        '--canvas-width':`${canvas.width * scale}px`, '--canvas-height':`${canvas.height * scale}px`,
        '--artwork-fit':layout.artwork.fit,
        '--artwork-x':`${layout.artwork.position.x * 100}%`, '--artwork-y':`${layout.artwork.position.y * 100}%`,
      };
      for (const [key,value] of Object.entries(properties)) slide.style.setProperty(key,value);
      if (!messageWall || !layout.message) continue;
      const {box,padding,text_align} = layout.message;
      const messageProperties = {
        '--canvas-scale':String(scale), '--message-left':`${left + box.x * scale}px`,
        '--message-top':`${top + box.y * scale}px`, '--message-width':`${box.width}px`,
        '--message-height':`${box.height}px`, '--message-align':text_align,
        '--message-padding':`${padding.top}px ${padding.right}px ${padding.bottom}px ${padding.left}px`,
      };
      for (const [key,value] of Object.entries(messageProperties)) messageWall.style.setProperty(key,value);
      const viewport = messageWall.querySelector('.text-wall-viewport');
      const overflow = viewport.scrollHeight > viewport.clientHeight + 1 || viewport.scrollWidth > viewport.clientWidth + 1;
      messageWall.querySelector('.message-overflow-note').hidden = !overflow;
      if (overflow) issues.push({code:'message_overflow',page_id:slide.dataset.pageId,
        message:`Page ${Number(slide.dataset.index) + 1} — ${slide.dataset.pageName}: Message continues in the scrollable text area.`,
        severity:'warning',feature:'messages',blocks_output:false,blocks_preview:false});
    }
    desktopControls?.updateArtwork();
    desktopControls?.restorePosition();
    const changed = JSON.stringify(issues) !== JSON.stringify(layoutIssues);
    layoutIssues = issues;
    if (changed) window.dispatchEvent(new CustomEvent('lettersmith-layout', {detail:issues.map((item) => ({...item}))}));
  }
  function scheduleCanvasLayouts(){
    if (layoutFrame) return;
    layoutFrame = requestAnimationFrame(() => { layoutFrame = 0; updateCanvasLayouts(); });
  }
  window.addEventListener('resize', scheduleCanvasLayouts);
  document.addEventListener('load', scheduleCanvasLayouts, true);
  document.fonts?.ready.then(scheduleCanvasLayouts);
  if (typeof ResizeObserver !== 'undefined') new ResizeObserver(scheduleCanvasLayouts).observe(slideshowEl);
  scheduleCanvasLayouts();
  function syncPageNavigation(){
    const slide = slides[idx];
    if (desktopViewer) slides.forEach((item) => setHiddenState(item, item !== slide));
    if (pageIndicator && slide){
      const name = slide.dataset.pageName || slide.querySelector('img')?.alt || '';
      const description = `Page ${idx + 1} of ${TOTAL}${name ? ' — ' + name : ''}`;
      pageIndicator.textContent = phoneViewer ? `${idx + 1} / ${TOTAL}` : description;
      if (phoneViewer) pageIndicator.setAttribute('aria-label', description);
    }
    if (pageSelector) pageSelector.value = String(idx);
  }
  function setDisabled(btn, disabled){
    btn.disabled = !!disabled;
    btn.setAttribute('aria-disabled', disabled ? 'true' : 'false');
  }
  function syncButtons(){
    const locked = !started || introControlsLocked || wallRevealLocked || flipping || phoneFinishing;
    setDisabled(prevBtn, locked || idx === 0);
    setDisabled(nextBtn, locked || idx === TOTAL - 1);
    if (pageSelector) setDisabled(pageSelector, locked);
    desktopControls?.updateArtwork();
    if (phoneViewer){
      phoneFinish.hidden = !started || idx !== TOTAL - 1;
      setDisabled(phoneFinish, locked);
    }
  }

  function clearWallRevealTimers(){
    if (wallRevealTimer !== null){ clearTimeout(wallRevealTimer); wallRevealTimer = null; }
    if (wallRevealUnlockTimer !== null){ clearTimeout(wallRevealUnlockTimer); wallRevealUnlockTimer = null; }
  }
  function setWallOpen(open){
    if (!wall) return;
    wall.classList.toggle('is-open', open);
    setHiddenState(wall, !open);
    openText.classList.toggle('is-visible', !open);
    setHiddenState(openText, open);
    closeText.classList.toggle('is-visible', open);
    setHiddenState(closeText, !open);
    setExpandedState(openText, open);
    if (phoneViewer) syncPhoneReading(open);
    desktopControls?.trackMessage(open ? wall : null);
    if (open){
      adaptiveMicroContrast.schedule();
      setTimeout(()=>adaptiveMicroContrast.schedule(),motion.duration.overlay+24);
    }
  }
  function hideWallDuringRevealDelay(){
    if (!wall) return;
    if (phoneViewer) syncPhoneReading(false);
    desktopControls?.trackMessage(null);
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
    const hasMessage = hasWallMessage(wall) && isWallPage();
    if (phoneViewer) phonePageActions.hidden = !hasMessage && idx !== TOTAL - 1;
    if (!hasMessage){
      clearWallRevealTimers();
      wallRevealLocked = false;
      wall?.classList.remove('is-open');
      openText.classList.remove('is-visible');
      closeText?.classList.remove('is-visible');
      setHiddenState(wall, true);
      setHiddenState(openText, true);
      setHiddenState(closeText, true);
      setExpandedState(openText, false);
      syncButtons();
      return;
    }
    if (phoneViewer || wallClosedByUser){
      clearWallRevealTimers();
      wallRevealLocked = false;
      setWallOpen(false);
      syncButtons();
      return;
    }
    beginWallRevealSequence();
  }

  function playOneShot(src, volume01, offset = 0){
    if (!playbackAllowed() || (phoneViewer && (viewerMuted || currentVolume === 0 || phoneFinishing))) return;
    try{
      const a = new Audio(src);
      a.preload = 'auto';
      a.volume = clamp(volume01, 0, 1);
      a.muted = viewerMuted || currentVolume === 0;
      if (offset > 0) a.currentTime = offset;
      const release = () => ownedAudio.delete(a);
      a.addEventListener('error', release, {once:true});
      ownTransientAudio(a);
      playOwnedAudio(a, release);
    }catch(_){ }
  }
  function playTurnSound(goingNext, animated){
    const sounds = TRANSITION_SOUNDS[letterTransition];
    if (!sounds?.length) return;
    if (letterTransition === 'classic'){
      const pick = sounds[Math.floor(Math.random() * sounds.length)];
      playOneShot(pick, music ? musicLevel(music) : 0.5);
      return;
    }
    // Direct jumps and reduced motion use the closing phrase of the cue.
    playOneShot(sounds[goingNext ? 0 : sounds.length - 1], currentVolume / 100, animated ? 0 : .86);
  }

  function setActiveIndex(newIdx, opts = {}){
    const target = clamp(newIdx, 0, TOTAL - 1);
    if (target === idx && opts.force !== true){
      selectMessageWall();
      syncPageNavigation();
      syncButtons();
      syncWallUI();
      if (phoneViewer) savePhoneProgress();
      return;
    }
    desktopControls?.resetZoom();
    clearWallRevealTimers();
    wallRevealLocked = false;
    hideWallDuringRevealDelay();
    document.querySelectorAll('.ultralink-tooltip.is-visible').forEach((item) => item.classList.remove('is-visible'));
    const previous = idx;
    cancelImageAnimation(previous);
    prepareImageAnimation(target);
    idx = target;
    selectMessageWall();
    syncPageNavigation();
    slides.forEach((s, i) => {
      s.classList.toggle('active', i === idx);
      s.classList.remove('ghost', 'peek');
    });
    if (isWallPage()) wallClosedByUser = false;
    if (opts.playSound !== false) playTurnSound(target > previous, false);
    syncButtons();
    syncWallUI();
    if (started) activateImageAnimation(idx);
    if (phoneViewer) savePhoneProgress();
  }

  function clearTurn(){
    turn.hidden = true;
    turn.width = turn.height = 0;
    slideshowEl.classList.remove('page-turning');
    slideshowEl.setAttribute('aria-busy', 'false');
    delete letterPreviewEl.dataset.turnEffect;
    flipping = false;
  }
  function finishTurn(targetIndex){
    setActiveIndex(targetIndex, { playSound: false });
    // Keep the last frame until the newly active DOM page has had a paint.
    requestAnimationFrame(() => {
      if (!flipping) return;
      clearTurn();
      syncButtons();
    });
  }
  function flipTo(targetIndex, preview = false){
    if (!started || flipping || introControlsLocked || wallRevealLocked || phoneFinishing) return;
    const target = clamp(targetIndex, 0, TOTAL - 1);
    if (target === idx) return;
    desktopControls?.resetZoom();
    const goingNext = target > idx;
    const currentSlide = slides[idx];
    const pageSlide = slides[Math.max(idx, target)];
    let frame = null;
    const reduceMotion = phoneViewer ? reducedMotionQuery.matches : prefersReducedMotion;
    const missingArtwork = phoneViewer && [currentSlide, slides[target]].some(slide => slide.classList.contains('asset-failed'));
    if ((!reduceMotion || preview) && !missingArtwork){
      prepareImageAnimation(target);
      const leafIndex = Math.min(idx, target);
      const leafSlide = slides[leafIndex];
      const isCover = leafSlide.dataset.pageType === 'cover' || leafIndex === 0;
      try{
        frame = createPageTurn(turn, slideshowEl, slideImageEl(leafSlide), slideImageEl(pageSlide), isCover, letterTransition, [cLeft, cRight]);
        if (frame && !frame(goingNext ? 0 : 1)) frame = null;
      }catch(error){
        console.warn('Page turn could not start.', error);
        frame = null;
      }
    }
    playTurnSound(goingNext, !!frame);
    if (!frame){
      setActiveIndex(target, { playSound: false });
      turn.width = turn.height = 0;
      return;
    }

    flipping = true;
    syncButtons();
    hideWallDuringRevealDelay();
    slideshowEl.classList.add('page-turning');
    slideshowEl.setAttribute('aria-busy', 'true');
    letterPreviewEl.dataset.turnEffect = letterTransition;
    currentSlide.classList.add('ghost');
    slides[target].classList.add('peek');
    turn.hidden = false;
    const startedAt = performance.now();
    function animate(now){
      if (!flipping) return;
      const raw = clamp((now - startedAt) / motion.duration.pageTurn, 0, 1);
      let progress = raw;
      if (letterTransition === 'classic'){
        progress = raw < .5 ? motion.ease(raw / .46) / 2 : .5 + motion.ease((raw - .54) / .46) / 2;
      }
      try{
        if (frame(goingNext ? progress : 1 - progress) && raw < 1){
          requestAnimationFrame(animate);
          return;
        }
      }catch(error){
        console.warn('Page turn interrupted.', error);
      }
      finishTurn(target);
    }
    requestAnimationFrame(animate);
  }

  function go(delta){
    if (!started || introControlsLocked || wallRevealLocked || flipping || phoneFinishing) return;
    const target = clamp(idx + delta, 0, TOTAL - 1);
    if (target === idx) return;
    dismissTitleBanner();
    flipTo(target);
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
    const initialMuted = document.body.dataset.initialMuted === undefined
      ? (typeof INITIAL_MUTED === 'boolean' && INITIAL_MUTED)
      : document.body.dataset.initialMuted === 'true';
    if (!playbackIdentity) return initialMuted;
    try{
      const saved = window.sessionStorage.getItem(muteStorageKey);
      return saved === null ? initialMuted : saved === 'true';
    }catch(_){
      return initialMuted;
    }
  }
  function saveViewerMuted(){
    if (!playbackIdentity) return;
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
      setMusicLevel(audio, vol01);
    });
    ownedAudio.forEach(audio => { audio.muted = muted; });
    volIconImg.src = muted ? 'gallery/controls/voloff.png' : 'gallery/controls/volon.png';
    const soundLabel = desktopViewer ? (muted ? 'Sound (muted)' : 'Sound')
      : (muted ? 'Audio muted. Toggle volume slider' : 'Toggle volume slider');
    volIcon.setAttribute('aria-label', soundLabel);
    if (slider) slider.value = String(vv);
    if (phoneViewer) syncMuteButton();
  }

  function syncMuteButton(){
    if (!muteBtn) return;
    if (phoneViewer){
      const soundOn = !viewerMuted && currentVolume > 0;
      for (const button of [phoneStartSound, muteBtn]){
        button.textContent = soundOn ? 'Sound On' : 'Sound Off';
        button.setAttribute('aria-pressed', String(soundOn));
        button.title = soundOn ? 'Mute letter audio' : 'Unmute letter audio';
      }
      return;
    }
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
    if (!phoneViewer) syncMuteButton();
  }
  function togglePhoneSound(){
    const enable = viewerMuted || currentVolume === 0;
    if (enable && currentVolume === 0) currentVolume = loadVolume0to100() || 50;
    setViewerMuted(!enable);
    if (enable && started && !introControlsLocked && !phoneFinishing && music?.paused && ensureInitialMusicSource()){
      preparePhoneAudioGain();
      playbackRequested = true;
      playOwnedAudio(music);
    }
  }

  function playbackAllowed(generation = playbackGeneration){
    return !playbackDisposed && playbackVisible && !document.hidden && generation === playbackGeneration;
  }
  function schedulePlayback(callback, delay){
    const generation = playbackGeneration;
    const timer = setTimeout(() => {
      playbackTimers.delete(timer);
      if (playbackAllowed(generation)) callback();
    }, delay);
    playbackTimers.add(timer);
    return timer;
  }
  function animatePlayback(callback){
    const generation = playbackGeneration;
    const frame = requestAnimationFrame(now => {
      playbackFrames.delete(frame);
      if (playbackAllowed(generation)) callback(now);
    });
    playbackFrames.add(frame);
  }
  function ownTransientAudio(audio){
    ownedAudio.add(audio);
    audio.addEventListener('ended', () => ownedAudio.delete(audio), {once:true});
    return audio;
  }
  function playOwnedAudio(audio, onError){
    const generation = playbackGeneration;
    if (!playbackAllowed(generation)) return;
    const failed = () => {
      if (playbackAllowed(generation) && typeof onError === 'function') onError();
    };
    try{
      Promise.resolve(audio.play()).then(() => {
        if (!playbackAllowed(generation)) audio.pause();
      }).catch(failed);
    }catch(_){ failed(); }
  }
  function stopPlayback(dispose = false){
    playbackGeneration += 1;
    playbackRequested = false;
    playbackDisposed = playbackDisposed || dispose;
    playbackTimers.forEach(timer => clearTimeout(timer));
    playbackFrames.forEach(frame => cancelAnimationFrame(frame));
    playbackTimers.clear();
    playbackFrames.clear();
    playlistTransitionTimer = null;
    playlistTransitioning = false;
    ownedAudio.forEach(audio => {
      try{
        audio.pause();
        if (dispose || (audio !== music && audio !== musicStandby)){
          audio.removeAttribute('src');
          audio.load();
        }
      }catch(_){ }
      if (audio !== music && audio !== musicStandby) ownedAudio.delete(audio);
    });
    document.querySelectorAll('audio,video').forEach(media => {
      try{ media.pause(); }catch(_){ }
    });
    if (started && introControlsLocked){
      introControlsLocked = false;
      overlay.remove();
      if (phoneViewer) slideshowEl.inert = false;
      syncButtons();
    }
    setVolume0to100(currentVolume);
    if (dispose && phoneAudioContext) phoneAudioContext.close().catch(() => {});
  }
  window.letterSmithPlayback = Object.freeze({
    setVisible(visible){
      playbackVisible = !!visible;
      if (!playbackVisible) stopPlayback();
    },
    shutdown(){
      playbackVisible = false;
      stopPlayback(true);
    },
  });
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) return;
    if (phoneViewer) savePhoneProgress();
    stopPlayback();
  });
  window.addEventListener('pagehide', event => {
    if (phoneViewer){
      savePhoneProgress();
      if (event.persisted){ stopPlayback(); return; }
    }
    window.letterSmithPlayback.shutdown();
  });
  window.addEventListener('pageshow', event => {
    if (phoneViewer && event.persisted) resetPhoneLetter(phoneReplay || phoneFinishing);
  });

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
  function nextMusicIndex(sources){
    if (!sources.length) return -1;
    return (musicPlaylistIndex + 1) % sources.length;
  }
  function musicLevel(audio){ return musicGains.get(audio)?.gain.value ?? audio.volume; }
  function setMusicLevel(audio, value){
    const gain = musicGains.get(audio);
    if (gain) gain.gain.value = value;
    else audio.volume = value;
  }
  function preparePhoneAudioGain(){
    // iPhone can lock element volume. Route same-origin or embedded music through a gain node.
    if (!phoneAudioContext && window.CSS?.supports('selector(:volume-locked)') && music.matches(':volume-locked') &&
        (location.protocol !== 'file:' || musicSources().every(source => source.startsWith('data:')))){
      try{
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        phoneAudioContext = new AudioContext();
        for (const audio of [music, musicStandby].filter(Boolean)){
          const gain = phoneAudioContext.createGain();
          gain.gain.value = audio.volume;
          audio.volume = 1;
          phoneAudioContext.createMediaElementSource(audio).connect(gain);
          gain.connect(phoneAudioContext.destination);
          musicGains.set(audio, gain);
        }
      }catch(_){ }
    }
    if (phoneAudioContext && phoneAudioContext.state !== 'running'){
      phoneAudioContext.resume().catch(() => {});
    }
  }
  function primePhoneSoundtrack(){
    if (!ensureInitialMusicSource()) return;
    preparePhoneAudioGain();
    // A click can authorize playback for each element before the curtain cue ends.
    const sources = musicSources();
    const players = [music];
    if (musicStandby && sources.length > 1 && Number(MUSIC_CROSSFADE_MS) > 0){
      musicStandby.src = sources[nextMusicIndex(sources)];
      players.push(musicStandby);
    }
    for (const audio of players){
      audio.muted = true;
      setMusicLevel(audio, 0);
      playOwnedAudio(audio);
    }
  }
  function installPlaylistListeners(audio){
    if (!audio) return;
    audio.addEventListener('timeupdate', () => {
      if (!playbackRequested || !playbackAllowed() || audio !== music || playlistTransitioning) return;
      const sources = musicSources();
      const crossfadeMs = Math.max(0, Number(MUSIC_CROSSFADE_MS) || 0);
      if (sources.length < 2 || crossfadeMs <= 0) return;
      if (!Number.isFinite(audio.duration) || audio.duration <= 0) return;
      const remainingMs = (audio.duration - audio.currentTime) * 1000;
      if (remainingMs > 0 && remainingMs <= Math.max(120, crossfadeMs)) crossfadeToNextTrack();
    });
    audio.addEventListener('ended', () => {
      if (!playbackRequested || !playbackAllowed() || audio !== music || playlistTransitioning) return;
      advanceMusicSequence();
    });
  }
  function advanceMusicSequence(){
    const sources = musicSources();
    if (!playbackRequested || !playbackAllowed() || playlistTransitioning || !sources.length || !music) return;
    if (sources.length > 1 && Number(MUSIC_CROSSFADE_MS) > 0){
      crossfadeToNextTrack();
      return;
    }
    const nextIndex = nextMusicIndex(sources);
    playlistTransitioning = true;
    if (playlistTransitionTimer !== null) clearTimeout(playlistTransitionTimer);
    playlistTransitionTimer = schedulePlayback(() => {
      playlistTransitionTimer = null;
      const currentSources = musicSources();
      if (!music || !currentSources.length){
        playlistTransitioning = false;
        return;
      }
      musicPlaylistIndex = nextIndex % currentSources.length;
      const wanted = currentSources[musicPlaylistIndex];
      try{
        if (!music.getAttribute('src') || !music.src.endsWith(wanted)) music.src = wanted;
        music.currentTime = 0;
        setMusicLevel(music, clamp(currentVolume / 100, 0, 1));
        music.muted = viewerMuted || currentVolume === 0;
        playOwnedAudio(music);
      }catch(_){ }
      playlistTransitioning = false;
    }, musicLoopDelayMs);
  }
  function crossfadeToNextTrack(){
    const sources = musicSources();
    if (!playbackRequested || !playbackAllowed() || playlistTransitioning || !sources.length || !music || !musicStandby) return;
    playlistTransitioning = true;
    const nextIndex = nextMusicIndex(sources);
    const target = clamp(currentVolume / 100, 0, 1);
    const muted = viewerMuted || target === 0;
    musicStandby.src = sources[nextIndex];
    musicStandby.currentTime = 0;
    setMusicLevel(musicStandby, 0);
    musicStandby.muted = muted;
    const duration = Math.max(120, Number(MUSIC_CROSSFADE_MS) || 1000);
    const startedAt = performance.now();
    playOwnedAudio(musicStandby, () => {
      playlistTransitioning = false;
    });
    function step(now){
      if (!playlistTransitioning) return;
      const t = clamp((now - startedAt) / duration, 0, 1);
      const volume = clamp(currentVolume / 100, 0, 1);
      const effectiveMuted = viewerMuted || volume === 0;
      setMusicLevel(music, volume * (1 - t));
      setMusicLevel(musicStandby, volume * t);
      if (t < 1){ animatePlayback(step); return; }
      music.pause();
      music.currentTime = 0;
      const previous = music;
      music = musicStandby;
      musicStandby = previous;
      musicPlaylistIndex = nextIndex;
      setMusicLevel(music, volume);
      music.muted = effectiveMuted;
      setMusicLevel(musicStandby, volume);
      musicStandby.muted = effectiveMuted;
      playlistTransitioning = false;
    }
    animatePlayback(step);
  }
  installPlaylistListeners(music);
  installPlaylistListeners(musicStandby);
  if (!desktopViewer && !musicSources().length && volumeControl) volumeControl.style.display = 'none';
  function setSliderOpen(open){
    const shouldOpen = !!open;
    volumeControl.classList.toggle('slider-open', shouldOpen);
    setExpandedState(volIcon, shouldOpen);
    if (slider) setHiddenState(slider, !shouldOpen);
    desktopControls?.setSoundOpen(shouldOpen);
  }

  function glissDurationMs(audioEl){
    const d = audioEl && Number.isFinite(audioEl.duration) ? audioEl.duration : 0;
    if (d > 0.25) return Math.round(d * 1000);
    return curtainOpenMs;
  }
  function runCurtainMotion(onDone){
    const openMs = curtainOpenMs;
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
    cLeft.style.animation = `curtainLeftOut ${openMs}ms ${motion.easing.emphasized} forwards`;
    cRight.style.animation = `curtainRightOut ${openMs}ms ${motion.easing.emphasized} forwards`;
    schedulePlayback(() => {
      overlay.style.pointerEvents = 'none';
      overlay.setAttribute('aria-hidden', 'true');
      overlay.remove();
      if (typeof onDone === 'function') onDone();
    }, openMs + curtainCleanupPadMs);
    return openMs;
  }
  function runTitleBanner(){
    if (titleBannerStarted) return;
    titleBannerStarted = true;
    titleBannerRevealTimer = setTimeout(() => {
      titleBannerRevealTimer = null;
      beginBtn.classList.add('is-dismissed');
      titleBanner.setAttribute('aria-hidden', 'false');
      titleBanner.classList.add('is-showing');
      titleBannerAutoDismissTimer = setTimeout(() => {
        titleBannerAutoDismissTimer = null;
        dismissTitleBanner();
      }, titleBannerFadeInMs + titleBannerHoldMs);
    }, titleBannerDelayMs);
  }
  function dismissTitleBanner(){
    if (titleBannerRevealTimer !== null){
      clearTimeout(titleBannerRevealTimer);
      titleBannerRevealTimer = null;
    }
    if (titleBannerAutoDismissTimer !== null){
      clearTimeout(titleBannerAutoDismissTimer);
      titleBannerAutoDismissTimer = null;
    }
    if (titleBannerCleanupTimer !== null){
      clearTimeout(titleBannerCleanupTimer);
      titleBannerCleanupTimer = null;
    }
    beginBtn.classList.add('is-dismissed');
    titleBanner.classList.remove('is-showing');
    if (titleBanner.getAttribute('aria-hidden') === 'true') return;
    titleBanner.classList.add('is-hiding');
    titleBannerCleanupTimer = setTimeout(() => {
      titleBannerCleanupTimer = null;
      titleBanner.classList.remove('is-hiding');
      titleBanner.setAttribute('aria-hidden', 'true');
    }, titleBannerFadeOutMs);
  }
  function beginLetter(){
    started = true;
    syncButtons();
    revealStage();
    activateImageAnimation(idx);
    overlay.removeAttribute('tabindex');
    if (fullscreenBtn) fullscreenBtn.inert = false;
    desktopControls?.start();
  }
  function resetPhoneLetter(replay){
    clearTimeout(phoneFinishTimer);
    phoneFinishTimer = null;
    started = false;
    introControlsLocked = false;
    phoneFinishing = false;
    phoneReplay = replay;
    stopPlayback();
    musicPlaylistIndex = 0;
    dismissTitleBanner();
    titleBannerStarted = false;
    clearTurn();
    cancelImageAnimation(idx);
    setActiveIndex(0, {playSound:false, force:true});
    setSliderOpen(false);
    stageReady = false;
    document.body.classList.remove('stage-ready');
    slideshowEl.inert = true;
    setHiddenState(slideshowEl, true);
    setHiddenState(volumeControl, true);
    if (fullscreenBtn) fullscreenBtn.inert = true;
    overlay.style.animation = 'none';
    overlay.style.opacity = '1';
    overlay.style.background = '';
    overlay.style.pointerEvents = '';
    for (const panel of [cLeft, cRight]){
      panel.style.animation = 'none';
      panel.style.opacity = '1';
      panel.style.transform = 'translateX(0)';
    }
    letterPreviewEl.append(overlay);
    setHiddenState(overlay, false);
    syncPhoneStartControls(replay);
    phoneOpenBtn.focus({preventScroll:true});
  }
  function finishPhoneLetter(){
    if (phoneFinish.disabled || phoneFinish.hidden) return;
    phoneFinishing = true;
    clearPhoneProgress();
    dismissTitleBanner();
    setSliderOpen(false);
    syncButtons();
    slideshowEl.inert = true;
    if (fullscreenBtn) fullscreenBtn.inert = true;

    playbackRequested = false;
    playlistTransitioning = false;
    clearTimeout(playlistTransitionTimer);
    playbackTimers.delete(playlistTransitionTimer);
    playlistTransitionTimer = null;
    const duration = reducedMotionQuery.matches ? 140 : motion.duration.curtain;
    const fading = [music, musicStandby].filter(audio => audio && !audio.paused).map(audio => [audio, musicLevel(audio)]);
    const startedAt = performance.now();
    function fade(now){
      const progress = clamp((now - startedAt) / Math.min(duration, musicFadeMs), 0, 1);
      for (const [audio, level] of fading) setMusicLevel(audio, level * (1 - progress));
      if (progress < 1){
        animatePlayback(fade);
      } else {
        for (const [audio] of fading) audio.pause();
      }
    }
    if (fading.length) animatePlayback(fade);

    overlay.style.pointerEvents = '';
    overlay.style.animation = 'none';
    for (const panel of [cLeft, cRight]) panel.style.animation = 'none';
    letterPreviewEl.append(overlay);
    void overlay.offsetWidth;
    cLeft.style.animation = `curtainLeftOut ${duration}ms ${motion.easing.emphasized} reverse both`;
    cRight.style.animation = `curtainRightOut ${duration}ms ${motion.easing.emphasized} reverse both`;
    if (overlay.classList.contains('curtain-fallback')){
      overlay.style.background = '';
      overlay.style.animation = `curtainIntroFadeIn ${duration}ms ${motion.easing.emphasized} both`;
    }
    phoneFinishTimer = setTimeout(() => resetPhoneLetter(true), duration);
  }
  function openCurtain(progress = null, resumePreview = false){
    if (started || phoneFinishing || !playbackAllowed()) return;
    const generation = playbackGeneration;
    introControlsLocked = true;
    beginLetter();
    if (phoneViewer){
      phoneReplay = false;
      phoneStartControls.hidden = true;
      setActiveIndex(progress?.page ?? (forgePreview ? idx : 0), {playSound:false});
      if (progress?.reading && isWallPage() && hasWallMessage(wall)) setWallOpen(true);
      savePhoneProgress();
      primePhoneSoundtrack();
    }
    if (!resumePreview) runTitleBanner();

    let musicStarted = false;
    let glissDone = false;
    let curtainDone = false;
    let introMotionStarted = false;
    let safetyTimer = null;

    function tryUnlockIntroControls(){
      if (!glissDone || !curtainDone) return;
      introControlsLocked = false;
      if (phoneViewer){
        slideshowEl.inert = false;
        if (wall?.classList.contains('is-open')) closeText?.focus({preventScroll:true});
      }
      syncButtons();
    }
    function startMusicAfterGliss(){
      if (musicStarted || !playbackAllowed(generation)) return;
      musicStarted = true;
      glissDone = true;
      if (safetyTimer !== null){ clearTimeout(safetyTimer); playbackTimers.delete(safetyTimer); safetyTimer = null; }
      const v = currentVolume;
      if (phoneViewer) musicStandby?.pause();
      else setVolume0to100(v);
      if (!ensureInitialMusicSource()){
        tryUnlockIntroControls();
        return;
      }
      playbackRequested = true;
      try{
        music.currentTime = 0;
        setMusicLevel(music, 0);
        music.muted = viewerMuted || v === 0;
        playOwnedAudio(music);
      }catch(_){ }
      const start = performance.now();
      function fadeStep(now){
        if (!playbackRequested) return;
        const t = clamp((now - start) / musicFadeMs, 0, 1);
        const e = t < 0.5 ? 2*t*t : 1 - Math.pow(-2*t + 2, 2)/2;
        setMusicLevel(music, clamp(currentVolume / 100, 0, 1) * e);
        if (t < 1) animatePlayback(fadeStep);
      }
      animatePlayback(fadeStep);
      tryUnlockIntroControls();
    }
    function beginGlissAndCurtain(g){
      if (introMotionStarted || !playbackAllowed(generation)) return;
      introMotionStarted = true;
      const openMs = runCurtainMotion(() => {
        curtainDone = true;
        tryUnlockIntroControls();
      });
      if (g){
        try{
          g.currentTime = 0;
          playOwnedAudio(g, startMusicAfterGliss);
        }catch(_){ startMusicAfterGliss(); }
      }
      const glissWaitMs = Math.max(openMs, glissDurationMs(g));
      safetyTimer = schedulePlayback(startMusicAfterGliss, glissWaitMs + glissSafetyPadMs);
    }
    if (resumePreview){
      overlay.remove();
      curtainDone = true;
      startMusicAfterGliss();
      return;
    }
    if (phoneViewer && (viewerMuted || currentVolume === 0)){
      beginGlissAndCurtain(null);
      startMusicAfterGliss();
      return;
    }
    try{
      const g = ownTransientAudio(new Audio(glissSrc));
      g.preload = 'auto';
      g.volume = 0.10;
      g.muted = viewerMuted || currentVolume === 0;
      g.addEventListener('ended', startMusicAfterGliss, { once: true });
      g.addEventListener('error', () => { beginGlissAndCurtain(g); startMusicAfterGliss(); }, { once: true });
      if (phoneViewer){ beginGlissAndCurtain(g); return; }
      g.addEventListener('loadedmetadata', () => beginGlissAndCurtain(g), { once: true });
      g.load();
      schedulePlayback(() => beginGlissAndCurtain(g), 250);
    }catch(_){
      const openMs = runCurtainMotion(() => { curtainDone = true; tryUnlockIntroControls(); });
      schedulePlayback(startMusicAfterGliss, openMs);
    }
  }

  // Suppress native caret placement/selection; only the completed click starts
  // playback, keeping the gesture on the overlay until release (including touch).
  overlay.addEventListener('pointerdown', (e) => {
    if (phoneViewer) return;
    e.preventDefault();
    e.stopPropagation();
  });
  bindPress(overlay, (e) => {
    if (phoneViewer) return;
    e.preventDefault();
    e.stopPropagation();
    if (!e.repeat) openCurtain();
  });
  if (phoneViewer){
    phoneOpenBtn.addEventListener('click', () => openCurtain(phoneProgress));
    phoneStartOver.addEventListener('click', () => { clearPhoneProgress(); openCurtain({page:0}); });
    phoneStartSound.addEventListener('click', togglePhoneSound);
    phoneFinish.addEventListener('click', finishPhoneLetter);
  }
  prevBtn.addEventListener('click', () => go(-1));
  nextBtn.addEventListener('click', () => go(1));
  if (pageSelector){
    pageSelector.addEventListener('change', () => {
      const target = Number(pageSelector.value);
      if (Number.isInteger(target) && target >= 0 && target < TOTAL){
        dismissTitleBanner();
        setActiveIndex(target);
      }
    });
  }
  window.LetterSmithViewer = Object.freeze({
    goToPage(pageId){
      const target = slides.findIndex((slide) => slide.dataset.pageId === String(pageId));
      if (target < 0 || flipping) return false;
      requestedPageId = String(pageId);
      if (started) dismissTitleBanner();
      setActiveIndex(target, {playSound:false});
      return true;
    },
    previewTurn(fromId, toId){
      const from = slides.findIndex(slide => slide.dataset.pageId === String(fromId));
      const to = slides.findIndex(slide => slide.dataset.pageId === String(toId));
      if (from < 0 || to < 0 || Math.abs(from - to) !== 1 || flipping) return false;
      requestAnimationFrame(() => {
        if (!playbackAllowed() || flipping) return;
        if (!started){
          requestedPageId = String(fromId);
          introStarted = true;
          beginLetter();
          overlay.remove();
        }
        dismissTitleBanner();
        setActiveIndex(from, {playSound:false});
        clearWallRevealTimers();
        unlockWallReveal();
        const images = [from, to].map(index => slideImageEl(slides[index]));
        Promise.allSettled(images.map(image => image?.decode())).then(() => {
          if (idx === from && playbackAllowed()) flipTo(to, true);
        });
      });
      return true;
    },
    currentPageId(){ return slides[idx]?.dataset.pageId || null; },
    layoutIssues(){ updateCanvasLayouts(); return layoutIssues.map((item) => ({...item})); },
  });
  if (restartBtn){
    restartBtn.addEventListener('click', () => {
      if (phoneViewer){
        clearPhoneProgress();
        resetPhoneLetter(false);
      } else {
        window.location.reload();
      }
    });
  }
  if (muteBtn){
    muteBtn.addEventListener('click', () => {
      if (phoneViewer) togglePhoneSound();
      else setViewerMuted(!viewerMuted);
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
    if (!started || introControlsLocked || wallRevealLocked || phoneFinishing) return;
    if (phoneViewer){
      if (letterPreviewEl.classList.contains('phone-reading')){
        if (e.key === 'Escape') closeText?.click();
        return;
      }
    }
    if ((phoneViewer || desktopViewer) && e.target instanceof Element && e.target.closest('select,input')) return;
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
  messageWalls.forEach((item) => {
    const button = item.querySelector('.hud-button-close');
    button?.addEventListener('click', () => {
      if (!isWallPage() || item !== wall) return;
      clearWallRevealTimers();
      wallRevealLocked = false;
      setWallOpen(false);
      wallClosedByUser = true;
      syncButtons();
      if (phoneViewer) savePhoneProgress();
      openText.focus({preventScroll:true});
    });
  });
  bindPress(openText, () => {
    if (!isWallPage()) return;
    clearWallRevealTimers();
    wallRevealLocked = false;
    setWallOpen(true);
    wallClosedByUser = false;
    syncButtons();
    if (phoneViewer) savePhoneProgress();
    closeText?.focus({preventScroll:true});
  });
  bindPress(volIcon, () => {
    const s = ensureSlider();
    const shouldOpen = !volumeControl.classList.contains('slider-open');
    setSliderOpen(shouldOpen);
    if (shouldOpen) s.focus({preventScroll:true});
  });

  ensureSlider();
  if (desktopViewer){
    desktopControls = installDesktopViewer(letterPreviewEl, {
      slideshow:slideshowEl, volumeControl, slider, muteButton:muteBtn, soundButton:volIcon,
      fullscreenButton:fullscreenBtn, openText,
      currentArtwork:() => slideImageEl(slides[idx]),
      canZoom:() => started && !introControlsLocked && !flipping,
      closeSound:() => setSliderOpen(false),
    });
    pageIndicator = desktopControls.pageIndicator;
  }
  setSliderOpen(false);
  syncMuteButton();
  setVolume0to100(loadVolume0to100());
  messageWalls.forEach((item) => {
    setHiddenState(item, true);
    setHiddenState(item.querySelector('.hud-button-close'), true);
  });
  setHiddenState(openText, true);
  setHiddenState(closeText, true);
  setExpandedState(openText, false);
  try{
    messageWalls.forEach(installUltralinks);
  }catch(error){
    console.error('Ultralink setup failed', error);
  }
  installImageFallbacks();
  if (forgePreview){
    forgePreview.position = () => [idx, started && !phoneFinishing];
    if (Number.isInteger(forgePreview.page) && forgePreview.page >= 0 && forgePreview.page < TOTAL){
      setActiveIndex(forgePreview.page, {playSound:false});
    }
  }
  if (forgePreview?.opened && playbackAllowed()){
    introStarted = true;
    warmDeferredAssets();
    openCurtain({page:idx}, true);
  } else {
    waitForCriticalAssets().finally(startCurtainIntro);
  }
});
"""


def template_for_pages(pages):
    """Render the selected page order with one shared message overlay."""
    from page_layout import page_labels
    labels = page_labels(pages)
    start = TEMPLATE_HTML.index('    <section class="slide active"')
    end = TEMPLATE_HTML.index('    <button id="prev"', start)
    wall_start = TEMPLATE_HTML.index('      <div class="text-wall"', start)
    wall_end = TEMPLATE_HTML.index('    </section>', wall_start)
    overlay = TEMPLATE_HTML[wall_start:wall_end]
    sections = []
    for index, page in enumerate(pages):
        active = " active" if index == 0 else ""
        priority = ' fetchpriority="high"' if index == 0 else ""
        sections.append(
            f'    <section class="slide{active}" data-index="{index}" '
            f'data-page-type="{page.kind}" id="slide-{index}">\n'
            f'      <img src="gallery/pages/{page.filename}" alt="{labels[page.key]}" '
            f'decoding="async"{priority}>\n    </section>\n'
        )
    return TEMPLATE_HTML[:start] + "\n".join(sections) + overlay + TEMPLATE_HTML[end:]


def template_for_project_pages(pages, *, template_html=None, transition=None):
    """Compose canonical render DTOs without changing authored documents.

    ``message_html`` must already be sanitized and its resources packaged.
    Expand trusted shell placeholders before passing ``template_html`` so
    authored text can never be interpreted as another shell placeholder.
    """
    import hashlib
    import html
    import json
    from pathlib import PurePosixPath
    from urllib.parse import unquote, urlsplit

    from page_transitions import normalize_page_transition
    from project_model import PAGE_TYPES, resolve_page_layout

    pages = tuple(pages)
    base = TEMPLATE_HTML if template_html is None else template_html
    start = base.index('    <section class="slide active"')
    end = base.index('    <button id="prev"', start)
    seen = set()
    sections = []
    overlays = []
    options = []
    for index, page in enumerate(pages):
        page_id = str(page["id"])
        if not page_id or page_id in seen:
            raise ValueError("Rendered pages require unique nonempty page IDs.")
        seen.add(page_id)
        kind = page["type"]
        definition = PAGE_TYPES[kind]
        name = str(page.get("name") or definition.label)
        source = str(page["source"])
        parsed = urlsplit(source)
        relative = PurePosixPath(unquote(parsed.path))
        if (parsed.scheme or parsed.netloc or relative.is_absolute()
                or ".." in relative.parts or "\\" in source or ":" in source
                or not relative.parts):
            raise ValueError("Rendered artwork must use a safe packaged relative path.")
        scope = "page-" + hashlib.sha256(page_id.encode("utf-8")).hexdigest()
        escaped_name = html.escape(name, quote=True)
        active = " active" if index == 0 else ""
        priority = ' fetchpriority="high"' if index == 0 else ""
        supplied_layout = page.get("layout") or {}
        layout = resolve_page_layout({
            "message": {"layout": supplied_layout.get("message") or {}} if kind == "letter" else None,
            "artwork": {"placement": supplied_layout.get("artwork") or {}},
        }, {})
        canvas_data = ""
        canvas_bounds = ""
        if layout["artwork"] is not None:
            canvas_data = ' data-layout="' + html.escape(json.dumps(layout, separators=(",", ":")), quote=True) + '"'
            canvas_bounds = '      <div class="canvas-bounds" aria-hidden="true"></div>\n'
            active += " has-canvas"
        overlay = ""
        if kind == "letter":
            content = str(page.get("message_html", ""))
            overlay_style = html.escape(str(page.get("overlay_style", "")), quote=True)
            message_class = " canvas-message" if layout["message"] is not None else ""
            content_html = f'        <div class="text-wall-content" id="{scope}-content">{content}</div>\n'
            if message_class:
                content_html = (f'<div class="text-wall-viewport" tabindex="0" role="region" '
                                f'aria-label="Message text — {escaped_name}" aria-describedby="{scope}-overflow">'
                                + content_html + '</div>\n'
                                + f'<span class="message-overflow-note" id="{scope}-overflow" hidden>Scroll to read the full message</span>\n')
            overlay = (
                f'      <div class="text-wall{message_class}" id="{scope}-message" '
                f'data-page-id="{html.escape(page_id, quote=True)}" '
                f'data-has-message="{str(bool(content.strip())).lower()}" '
                f'role="dialog" aria-modal="false" aria-label="Message — {escaped_name}" '
                f'aria-hidden="true" tabindex="-1" style="{overlay_style}">\n'
                f'        <button id="{scope}-close" class="hud-button hud-button-close close-text" '
                f'type="button" title="Close Text" aria-label="Close message" '
                f'aria-controls="{scope}-message">&times;</button>\n'
                + content_html +
                '      </div>\n'
            )
            overlays.append(overlay)
        sections.append(
            f'    <section class="slide{active}" data-index="{index}" '
            f'data-canonical="true" data-page-type="{kind}" '
            f'data-page-id="{html.escape(page_id, quote=True)}" '
            f'data-page-name="{escaped_name}" id="{scope}"{canvas_data}>\n'
            + canvas_bounds +
            f'      <img src="{html.escape(source, quote=True)}" alt="{escaped_name}" '
            f'decoding="async"{priority}>\n    </section>\n'
        )
        options.append(f'<option value="{index}">{index + 1} — {escaped_name}</option>')
    navigation = (
        '    <div id="page-navigation" aria-label="Page navigation">'
        '<span id="page-indicator" role="status" aria-live="polite"></span>'
        '<select id="page-selector" aria-label="Go to page" disabled>'
        + "".join(options) + '</select></div>\n'
    )
    prefix = base[:start].replace('<main id="letter-preview">',
                                 f'<main id="letter-preview" data-transition="{normalize_page_transition(transition)}">', 1)
    if pages:
        prefix = prefix.replace('href="gallery/pages/cover.png"',
                                f'href="{html.escape(str(pages[0]["source"]), quote=True)}"')
    else:
        prefix = prefix.replace('  <link rel="preload" as="image" href="gallery/pages/cover.png" fetchpriority="high">', "")
    suffix = base[end:].replace('aria-controls="textWall"', '')
    return prefix + "\n".join(sections) + "\n".join(overlays) + navigation + suffix
