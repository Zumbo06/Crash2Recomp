"""Dark theme for the launcher.

Three layers, in order:

1. **Fusion + QPalette** (`apply_theme`). The platform style draws light-theme
   combo arrows, checkmarks and focus rings that look wrong on a dark surface.
   Fusion draws all of those from the palette instead, so setting the palette
   once gets them right everywhere - including widgets no stylesheet reaches.
2. **Tokens** below. Every colour, spacing and radius the UI uses lives here.
   Pages must not hard-code a hex value or a pixel margin; the previous theme
   had ~13 hex literals baked into the stylesheet string where no palette
   change could reach them, and repeated `(28, 24, 28, 24)` in five files.
3. **QSS**, for the things a palette cannot express: cards, the nav rail, the
   primary/play buttons.

Deliberately NOT styled here: `QCheckBox::indicator` and
`QComboBox::down-arrow`. Styling either one makes Qt stop drawing the native
glyph and draw only what the rule says - which is how the old theme ended up
with a checkbox that had no checkmark and a blank 20px drop-down zone. Fusion
draws both correctly from the palette, using ACCENT as Highlight.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette

# --- palette --------------------------------------------------------------
BG            = "#0f1114"   # window
BG_RAISED     = "#16191e"   # cards, sidebar
BG_INPUT      = "#1d2127"   # inputs, hover fills
BG_SUNKEN     = "#0b0d10"   # log console
BORDER        = "#262b33"   # hairline, the default
BORDER_STRONG = "#333a44"   # only where a card must separate from a card

TEXT          = "#e8eaee"
TEXT_DIM      = "#8d95a3"
TEXT_FAINT    = "#5c6472"   # disabled text

ACCENT        = "#f07e1e"   # Crash orange
ACCENT_HOVER  = "#ff9236"
ACCENT_DEEP   = "#c25f0d"   # selections, slider sub-page
ACCENT_INK    = "#17120c"   # text ON accent - near-black, not white
ACCENT_MUTED  = "#4a3a28"   # disabled accent fill

OK            = "#4ac97e"
WARN          = "#e8b339"
ERROR         = "#e8595b"

# Play-page artwork: cool metal panels against the warm wooden action.
PLAY_BG       = "#061321"
PLAY_PANEL    = "#0b1c2e"
PLAY_EDGE     = "#4d89b4"
PLAY_GOLD     = "#ffe648"
PLAY_ORANGE   = "#ff9b20"
PLAY_GREEN    = "#42ef85"

SCROLL        = "#2c333d"
SCROLL_HOVER  = "#3c4552"

# --- spacing scale --------------------------------------------------------
SPACE_1, SPACE_2, SPACE_3 = 4, 8, 12
SPACE_4, SPACE_5, SPACE_6 = 16, 24, 32

RADIUS_SM, RADIUS_MD, RADIUS_LG = 6, 10, 14

# Layout metrics that were previously literals repeated across the pages.
PAGE_MARGINS = (SPACE_6, SPACE_5, SPACE_6, SPACE_5)   # was (28, 24, 28, 24)
CARD_MARGINS = (SPACE_4 + 2, SPACE_4, SPACE_4 + 2, SPACE_4)
LABEL_COL = 180          # the settings row label column
SIDEBAR_W = 208


def apply_theme(app) -> None:
    """Install Fusion + the dark palette, then the stylesheet."""
    app.setStyle("Fusion")

    c = QColor
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, c(BG))
    p.setColor(QPalette.ColorRole.WindowText, c(TEXT))
    p.setColor(QPalette.ColorRole.Base, c(BG_INPUT))
    p.setColor(QPalette.ColorRole.AlternateBase, c(BG_RAISED))
    p.setColor(QPalette.ColorRole.Text, c(TEXT))
    p.setColor(QPalette.ColorRole.Button, c(BG_INPUT))
    p.setColor(QPalette.ColorRole.ButtonText, c(TEXT))
    p.setColor(QPalette.ColorRole.BrightText, c(ERROR))
    p.setColor(QPalette.ColorRole.ToolTipBase, c(BG_INPUT))
    p.setColor(QPalette.ColorRole.ToolTipText, c(TEXT))
    p.setColor(QPalette.ColorRole.PlaceholderText, c(TEXT_FAINT))
    p.setColor(QPalette.ColorRole.Link, c(ACCENT))
    # Highlight drives the checkbox tick, combo selection and focus ring.
    p.setColor(QPalette.ColorRole.Highlight, c(ACCENT))
    p.setColor(QPalette.ColorRole.HighlightedText, c(ACCENT_INK))

    dis = QPalette.ColorGroup.Disabled
    p.setColor(dis, QPalette.ColorRole.Text, c(TEXT_FAINT))
    p.setColor(dis, QPalette.ColorRole.ButtonText, c(TEXT_FAINT))
    p.setColor(dis, QPalette.ColorRole.WindowText, c(TEXT_FAINT))
    p.setColor(dis, QPalette.ColorRole.Highlight, c(ACCENT_MUTED))

    app.setPalette(p)
    app.setStyleSheet(QSS)


QSS = f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: "Segoe UI", "Inter", system-ui, sans-serif;
    font-size: 13px;
}}
QScrollArea, QStackedWidget {{ background: transparent; border: none; }}

/* ---- sidebar ---------------------------------------------------------- */
#Sidebar {{
    background: {BG_RAISED};
    border-right: 1px solid {BORDER};
}}
#SidebarTitle {{
    color: {TEXT};
    font-size: 16px;
    font-weight: 700;
    letter-spacing: 1px;
    padding: {SPACE_5}px {SPACE_4}px 0 {SPACE_4}px;
}}
#SidebarSubtitle {{
    color: {TEXT_FAINT};
    font-size: 11px;
    padding: 2px {SPACE_4}px {SPACE_5}px {SPACE_4}px;
}}
/* Group captions in the nav rail - the structural change that lets one rail
   carry what used to need two. */
#NavGroup {{
    color: {TEXT_FAINT};
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1.2px;
    padding: {SPACE_4}px {SPACE_4}px {SPACE_1}px {SPACE_4}px;
}}
QPushButton#NavButton {{
    background: transparent;
    border: none;
    border-radius: {RADIUS_SM}px;
    margin: 1px {SPACE_2}px;
    padding: 9px {SPACE_3}px;
    text-align: left;
    color: {TEXT_DIM};
    font-size: 13px;
}}
QPushButton#NavButton:hover {{ background: {BG_INPUT}; color: {TEXT}; }}
QPushButton#NavButton:checked {{
    background: {BG_INPUT};
    color: {ACCENT};
    font-weight: 600;
}}

/* ---- headings --------------------------------------------------------- */
QLabel#PageTitle   {{ font-size: 22px; font-weight: 700; }}
QLabel#PageHint    {{ color: {TEXT_DIM}; font-size: 12px; }}
QLabel#SectionTitle{{
    font-size: 11px; font-weight: 700; color: {TEXT_DIM};
    letter-spacing: 1px;
}}
QLabel#Dim         {{ color: {TEXT_DIM}; }}
QLabel#Ok          {{ color: {OK}; font-weight: 600; }}
QLabel#Warn        {{ color: {WARN}; font-weight: 600; }}
QLabel#Error       {{ color: {ERROR}; font-weight: 600; }}

/* ---- cards ------------------------------------------------------------ */
/* tone is set with setProperty("tone", ...) + style().polish(); see
   common.card(). A widget-level setStyleSheet would break inheritance for the
   whole subtree, which is what the old warning cards did. */
QFrame#Card {{
    background: {BG_RAISED};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_MD}px;
}}
QFrame#Card[tone="warn"]  {{ border-color: {WARN}; }}
QFrame#Card[tone="error"] {{ border-color: {ERROR}; }}
QFrame#Card[tone="flat"]  {{ background: transparent; border-color: transparent; }}

/* ---- buttons ---------------------------------------------------------- */
QPushButton {{
    background: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    padding: 7px {SPACE_4}px;
    color: {TEXT};
}}
QPushButton:hover    {{ border-color: {ACCENT}; color: {TEXT}; }}
QPushButton:disabled {{ color: {TEXT_FAINT}; border-color: {BORDER}; background: {BG}; }}

QPushButton#Primary {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
    color: {ACCENT_INK};
    font-weight: 700;
}}
QPushButton#Primary:hover    {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton#Primary:disabled {{
    background: {ACCENT_MUTED}; border-color: {ACCENT_MUTED}; color: {TEXT_FAINT};
}}

QPushButton#PlayButton {{
    background: {ACCENT};
    border: none;
    border-radius: {RADIUS_MD}px;
    color: {ACCENT_INK};
    font-size: 18px;
    font-weight: 800;
    letter-spacing: 1px;
    padding: {SPACE_4}px 0;
}}
QPushButton#PlayButton:hover    {{ background: {ACCENT_HOVER}; }}
QPushButton#PlayButton:disabled {{ background: {ACCENT_MUTED}; color: {TEXT_FAINT}; }}

QPushButton#Danger {{ border-color: {ERROR}; color: {ERROR}; }}
QPushButton#Danger:hover {{ background: {ERROR}; color: {ACCENT_INK}; }}

/* Quiet button for secondary actions - no border until hovered. */
QPushButton#Ghost {{ background: transparent; border-color: transparent; color: {TEXT_DIM}; }}
QPushButton#Ghost:hover {{ background: {BG_INPUT}; color: {TEXT}; }}

/* ---- inputs ----------------------------------------------------------- */
QLineEdit, QComboBox, QSpinBox {{
    background: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    padding: 6px 9px;
    min-height: 18px;
    selection-background-color: {ACCENT_DEEP};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {ACCENT}; }}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover {{ border-color: {BORDER_STRONG}; }}
QLineEdit[readOnly="true"] {{ color: {TEXT_DIM}; background: {BG}; }}
QComboBox QAbstractItemView {{
    background: {BG_INPUT};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT_DEEP};
    selection-color: {TEXT};
    outline: none;
    padding: {SPACE_1}px;
}}
QCheckBox, QRadioButton {{ spacing: {SPACE_2}px; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {TEXT_FAINT}; }}

QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {ACCENT}; width: 14px; height: 14px;
    margin: -6px 0; border-radius: 7px;
}}
QSlider::sub-page:horizontal {{ background: {ACCENT_DEEP}; border-radius: 2px; }}

/* ---- progress --------------------------------------------------------- */
QProgressBar {{
    background: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    height: 18px;
    text-align: center;
    color: {TEXT};
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}

/* ---- log console ------------------------------------------------------ */
QPlainTextEdit#LogConsole {{
    background: {BG_SUNKEN};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 11px;
    color: #c3cad6;
}}

/* ---- lists ------------------------------------------------------------ */
QListWidget {{
    background: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    outline: none;
}}
QListWidget::item {{ padding: {SPACE_2}px; border-bottom: 1px solid {BORDER}; }}
QListWidget::item:selected {{ background: {ACCENT_DEEP}; color: {TEXT}; }}

/* ---- scrollbars ------------------------------------------------------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {SCROLL}; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {SCROLL_HOVER}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {SCROLL}; border-radius: 5px; min-width: 24px; }}

QToolTip {{
    background: {BG_INPUT};
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    border-radius: {RADIUS_SM}px;
    padding: {SPACE_1}px 7px;
}}

/* Play keeps the existing navigation and gives its scene a cool metal HUD. */
#PlayScene, #PlayScene QWidget {{ background: transparent; }}
#PlayScene #PlaySubtitle {{
    background: {PLAY_BG}; color: #e5edf6; border-radius: 5px;
    font-size: 12px; padding: 1px 4px;
}}
#PlayScene QPushButton#ArtworkPlayButton {{
    background: transparent; border: none; padding: 0;
}}
#PlayScene QPushButton#PlaySecondary {{
    background: rgba(6, 19, 33, 225); border: 1px solid #35516c;
    color: #dce8f5; font-size: 12px; padding: 6px 10px;
}}
#PlayScene QPushButton#PlaySecondary:hover {{
    background: #173149; border-color: {PLAY_GOLD}; color: white;
}}
#PlayScene QPushButton#PlaySecondary:focus {{ border-color: {PLAY_GOLD}; }}
#PlayScene QFrame#PlayStatusPanel {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #142c43, stop:0.12 {PLAY_PANEL}, stop:1 #071422);
    border: 2px solid {PLAY_EDGE}; border-radius: 12px;
}}
#PlayScene #PlayDivider {{ background: #315777; }}
#PlayScene #PlayMetaLabel {{ color: #a6b9ce; font-size: 12px; }}
#PlayScene #PlayMetaValue {{ color: #eff5ff; font-size: 13px; font-weight: 600; }}
#PlayScene QLabel[playHeading="true"] {{ font-size: 21px; font-weight: 700; }}
#PlayScene QLabel#Ok {{ color: {PLAY_GREEN}; }}
#PlayScene QProgressBar#PlayReadyBar {{
    background: #14304a; border: 1px solid #315777; border-radius: 4px;
}}
#PlayScene QProgressBar#PlayReadyBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #21bc75, stop:1 {PLAY_GREEN}); border-radius: 3px;
}}
#PlayScene #PlayFooter {{ color: #8da4bb; font-size: 10px; letter-spacing: 0.3px; }}
#PlayScene QFrame#Card[tone="warn"] {{ background: #2a2518; border-color: {WARN}; }}
#PlayScene #PlaySubtitle[compact="true"] {{ font-size: 10px; padding: 0 3px; }}
QDialog#PlayDetails {{ background: {BG}; }}
#PlayDetails QWidget {{ background: transparent; }}
#PlayDetails QFrame#Card {{ background: {BG_RAISED}; }}
#PlayDetails QPushButton {{ background: {BG_INPUT}; }}
"""
