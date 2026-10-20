"""User-clicked artwork actions in an isolated WebEngine script world."""
import json

from PySide6 import QtCore
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineScript


class ImageActions(QtCore.QObject):
    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self.context = None
        self.tokens = set()
        self.channel = QWebChannel(self)
        self.channel.registerObject('letterSmithImages', self)
        browser.view.page().setWebChannel(self.channel, QWebEngineScript.ApplicationWorld)
        library = QtCore.QFile(':/qtwebchannel/qwebchannel.js')
        if not library.open(QtCore.QIODevice.ReadOnly):
            raise RuntimeError('The image selection bridge is unavailable.')
        self.library = bytes(library.readAll()).decode('utf-8')
        library.close()
        self.script = IMAGE_ACTION_SCRIPT
        browser.view.loadFinished.connect(self.install)

    def set_target(self, token, label):
        self.tokens.add(token)
        self.context = {'token': token, 'label': label}
        self.install()

    def install(self, *_):
        if self.context is None:
            return
        palette = self.browser.view.palette()
        context = dict(self.context, background=palette.button().color().name(),
                       foreground=palette.buttonText().color().name(),
                       accent=palette.highlight().color().name())
        code = self.library + '\n' + self.script + '\nwindow.installLetterSmithImages(' + json.dumps(context) + ');'
        self.browser.view.page().runJavaScript(code, QWebEngineScript.ApplicationWorld)

    @QtCore.Slot(str, str)
    def useImage(self, url, token):
        host = self.browser.view.url().host().lower()
        expected = {'ChatGPT': 'chatgpt.com', 'Gemini': 'gemini.google.com'}.get(self.browser.provider)
        if token not in self.tokens or host != expected:
            return
        self.browser._approve_image_url(url, self.browser.view.page(), target=token)


IMAGE_ACTION_SCRIPT = r"""/* Runs in Qt's isolated ApplicationWorld; only an actual user click imports. */
window.installLetterSmithImages = function (context) {
  if (!['chatgpt.com', 'gemini.google.com'].includes(location.hostname)) return;
  if (window.__letterSmithImages) {
    window.__letterSmithImages.scan();
    window.__letterSmithImages.context = context;
    return;
  }
  const state = {context, bridge: null, responses: new WeakMap(), images: new WeakMap()};
  window.__letterSmithImages = state;
  const responseSelector = '[data-message-author-role="assistant"], model-response, .model-response-text';
  state.scan = function () {
    document.querySelectorAll(responseSelector).forEach(response => {
      if (!state.responses.has(response)) state.responses.set(response, {...state.context});
      response.querySelectorAll('img').forEach(img => {
        if ((state.images.has(img) && state.images.get(img).isConnected) || img.naturalWidth < 256 || img.naturalHeight < 256) return;
        const target = state.responses.get(response);
        const button = document.createElement('button');
        button.type = 'button';
        button.dataset.lettersmithImageAction = 'true';
        button.textContent = 'Use image for ' + target.label;
        button.setAttribute('aria-label', button.textContent);
        button.style.cssText = 'display:block;position:relative;z-index:2;margin:10px 0;padding:10px 16px;' +
          'border:1px solid ' + target.accent + ';border-radius:6px;cursor:pointer;font:600 14px system-ui;' +
          'background:' + target.background + ';color:' + target.foreground + ';';
        button.addEventListener('click', event => {
          event.preventDefault(); event.stopPropagation();
          if (!event.isTrusted || !state.bridge) return;
          // currentSrc retains the provider's authenticated full image URL.
          state.bridge.useImage(img.currentSrc || img.src, target.token);
        });
        // Place outside any link/button used to open the provider's image viewer.
        const wrapper = img.closest('button, a') || img;
        wrapper.insertAdjacentElement('afterend', button);
        state.images.set(img, button);
      });
    });
  };
  new QWebChannel(qt.webChannelTransport, channel => {
    state.bridge = channel.objects.letterSmithImages;
    state.scan();
  });
  let scheduled = false;
  new MutationObserver(() => {
    if (scheduled) return;
    scheduled = true;
    setTimeout(() => { scheduled = false; state.scan(); }, 80);
  }).observe(document.documentElement, {childList:true, subtree:true, attributes:true, attributeFilter:['src', 'srcset']});
  document.addEventListener('load', event => { if (event.target.tagName === 'IMG') state.scan(); }, true);
  state.scan();
};
"""
