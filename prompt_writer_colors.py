"""Display-only identities shared by Prompt Writer and page destinations.

Colors are compared in Oklab (https://bottosson.github.io/posts/oklab/).
Saved identities stay stable; display shades adapt to the active theme.
Nothing here belongs in prompt_settings.
"""
from colorsys import hls_to_rgb
from functools import lru_cache
from math import dist, hypot


SURFACE = '#171b22'
INPUT_SURFACE = '#202630'
SELECTED_SURFACE = '#242b36'
TEXT = '#dde2ea'
BORDER = '#657184'
CATEGORY_KEYS = ('subject', 'type', 'scheme', 'helpful', 'style_bias',
                 'clean_output', 'framing', 'global')
REGISTRY_KEY = 'prompt_writer_colors'
MIN_CONTRAST = 4.5
MIN_IDENTITY_DISTANCE = .075
MIN_THEME_DISTANCE = .10


def _linear(color):
    rgb = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    return tuple(c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in rgb)


@lru_cache(maxsize=4096)
def oklab(color):
    r, g, b = _linear(color)
    l = (.4122214708*r + .5363325363*g + .0514459929*b) ** (1/3)
    m = (.2119034982*r + .6806995451*g + .1073969566*b) ** (1/3)
    s = (.0883024619*r + .2817188376*g + .6299787005*b) ** (1/3)
    return (.2104542553*l + .7936177850*m - .0040720468*s,
            1.9779984951*l - 2.4285922050*m + .4505937099*s,
            .0259040371*l + .7827717662*m - .8086757660*s)


def contrast(first, second):
    def luminance(color):
        r, g, b = _linear(color)
        return .2126*r + .7152*g + .0722*b
    a, b = sorted((luminance(first), luminance(second)))
    return (b + .05) / (a + .05)


@lru_cache(maxsize=1)
def theme_colors():
    from ui_theme import THEMES
    return tuple(sorted({getattr(theme.tokens, key) for theme in THEMES.values()
                         for key in ('primary', 'text', 'heading_text')}))


@lru_cache(maxsize=1)
def _candidates():
    reserved = [oklab(color) for color in (*theme_colors(), TEXT)]
    candidates = set()
    for hue in range(0, 360, 5):
        for lightness in (.45, .55, .65, .75, .85):
            for saturation in (.45, .65, .85, 1.):
                color = '#' + ''.join(f'{round(c * 255):02x}' for c in hls_to_rgb(hue/360, lightness, saturation))
                lab = oklab(color)
                if (contrast(color, SELECTED_SURFACE) >= MIN_CONTRAST
                        and hypot(lab[1], lab[2]) >= .07
                        and min(dist(lab, other) for other in reserved) >= MIN_THEME_DISTANCE):
                    candidates.add(color)
    return tuple(sorted(candidates))


def allocate_color(used):
    """Choose the most perceptually separated unused color; never cycle."""
    occupied = [oklab(color) for color in (*theme_colors(), TEXT, *used)]
    color = max(_candidates(), key=lambda candidate: min(dist(oklab(candidate), other) for other in occupied))
    if any(dist(oklab(color), oklab(other)) < MIN_IDENTITY_DISTANCE for other in used):
        raise ValueError('No distinct Prompt Writer color remains for this project.')
    return color


@lru_cache(maxsize=1)
def category_colors():
    colors = {}
    # Preferred hues retain the familiar subject/style/palette relationship.
    anchors = ('#ff6d73', '#9580ff', '#28bd35', '#4dd2ff',
               '#ee6c2b', '#ecff1a', '#28bd98', '#ff80ff')
    for key, anchor in zip(CATEGORY_KEYS, anchors):
        available = [candidate for candidate in _candidates()
                     if all(dist(oklab(candidate), oklab(other)) >= .10 for other in colors.values())]
        colors[key] = min(available, key=lambda candidate: dist(oklab(candidate), oklab(anchor)))
    return colors


def ensure_page_colors(project):
    """Persist only UI identity in the existing extensible project metadata."""
    extensions = project.to_dict()['extensions']
    saved = extensions.get(REGISTRY_KEY, {})
    saved = saved.get('pages', {}) if isinstance(saved, dict) else {}
    saved = saved if isinstance(saved, dict) else {}
    pages = project.pages
    colors = {page['id']: saved[page['id']] for page in pages
              if isinstance(saved.get(page['id']), str) and saved[page['id']] in _candidates()}
    used = list(category_colors().values())
    # Keep existing assignments before allocating new ones, regardless of order.
    for page_id, color in tuple(colors.items()):
        if any(dist(oklab(color), oklab(other)) < MIN_IDENTITY_DISTANCE for other in used):
            del colors[page_id]
        else:
            used.append(color)
    for page in pages:
        if page['id'] not in colors:
            color = allocate_color(tuple(used))
            colors[page['id']] = color
            used.append(color)
    registry = {'version': 1, 'pages': colors}
    if extensions.get(REGISTRY_KEY) != registry:
        project.update_project_data('extensions', {REGISTRY_KEY: registry})
    return colors


def surface_colors(tokens=None):
    return {
        'surface': getattr(tokens, 'panel_background', SURFACE),
        'input': getattr(tokens, 'control_background', INPUT_SURFACE),
        'selected': getattr(tokens, 'selected_background', SELECTED_SURFACE),
        'text': getattr(tokens, 'text', TEXT),
        'border': getattr(tokens, 'control_border', BORDER),
        'focus': getattr(tokens, 'accent', TEXT),
        'disabled': getattr(tokens, 'disabled_text', BORDER),
    }


def display_color(color, tokens=None):
    """Adjust only the display shade; never rewrite a saved page identity."""
    palette = surface_colors(tokens)
    return _readable_shade(color, tuple(palette[key] for key in ('surface', 'input', 'selected')))


@lru_cache(maxsize=512)
def _readable_shade(color, backgrounds):
    endpoint, amount = _theme_color_mix(backgrounds)
    return _mix_color(color, endpoint, amount)


def _mix_color(color, endpoint, amount):
    rgb = tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))
    return '#' + ''.join(f'{round(channel + (endpoint - channel) * amount):02x}' for channel in rgb)


@lru_cache(maxsize=32)
def _theme_color_mix(backgrounds):
    # One transform preserves differences between identities sharing a hue.
    colors = sorted(_candidates(), key=lambda color: min(contrast(color, bg) for bg in backgrounds))
    for step in range(101):
        for endpoint in (0, 255):
            if all(min(contrast(_mix_color(color, endpoint, step / 100), bg)
                       for bg in backgrounds) >= MIN_CONTRAST for color in colors):
                return endpoint, step / 100
    raise ValueError('The theme surfaces cannot support readable Prompt Writer colors.')


def surface_stylesheet(tokens=None):
    """Scoped to the writer content or a small destination/management surface."""
    from ui_theme import COMPACT_RADIUS, CONTROL_RADIUS, PANEL_RADIUS
    palette = surface_colors(tokens)
    surface, input_surface, selected, text, border, focus, disabled = (
        palette[key] for key in ('surface', 'input', 'selected', 'text', 'border', 'focus', 'disabled'))
    return f'''
        QWidget {{ color:{text}; background-color:{surface}; }}
        QGroupBox {{ border:1px solid {border}; border-radius:{PANEL_RADIUS}px;
                    margin-top:10px; padding:14px 10px 10px; }}
        QGroupBox::title {{ subcontrol-origin:margin; left:10px; padding:0 4px; color:{text}; }}
        QLineEdit, QComboBox, QPlainTextEdit, QTextEdit, QListWidget, QAbstractItemView {{
            background-color:{input_surface}; color:{text};
            border:1px solid {border}; border-radius:{CONTROL_RADIUS}px;
            selection-background-color:{selected}; selection-color:{text}; }}
        QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
            border-color:{focus}; }}
        QPlainTextEdit, QTextEdit {{ padding:8px; }}
        QComboBox QAbstractItemView::item:selected, QListWidget::item:selected {{ background:{selected}; }}
        QPushButton, QToolButton {{ background-color:{input_surface}; color:{text};
            border:1px solid {border}; border-radius:{CONTROL_RADIUS}px; }}
        QPushButton:hover, QToolButton:hover, QPushButton:pressed, QToolButton:pressed {{
            background-color:{selected}; }}
        QPushButton:focus, QToolButton:focus {{ border-color:{focus}; }}
        QPushButton:disabled, QToolButton:disabled, QComboBox:disabled, QLineEdit:disabled {{
            color:{disabled}; border-color:{border}; }}
        QComboBox::drop-down {{ border:none; border-left:1px solid {border}; }}
        QToolTip {{ color:{text}; background:{input_surface}; border:1px solid {border};
            border-radius:{COMPACT_RADIUS}px; padding:4px 7px; }}
        QCheckBox {{ spacing:7px; padding:2px 0; }}
        QWidget[themeIndependent="true"] QCheckBox::indicator {{ background:{input_surface}; border:1px solid {border};
            border-radius:{COMPACT_RADIUS}px; }}
        QWidget[themeIndependent="true"] QCheckBox::indicator:checked {{ background:{text}; border-color:{focus}; }}
        QWidget[themeIndependent="true"] QCheckBox::indicator:hover,
        QWidget[themeIndependent="true"] QCheckBox::indicator:focus {{ border-color:{focus}; }}
        QWidget[themeIndependent="true"] QCheckBox::indicator:disabled {{ border-color:{border}; }}
    '''
