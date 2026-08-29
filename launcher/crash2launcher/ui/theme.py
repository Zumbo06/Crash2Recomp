"""Dark theme for the launcher.

One stylesheet, applied to the whole app. Colours are defined once here so the
pages never hard-code a hex value; the accent is Crash's orange.
"""

from __future__ import annotations

# --- palette --------------------------------------------------------------
BG          = "#14161a"   # window
BG_RAISED   = "#1c1f25"   # cards, sidebar
BG_INPUT    = "#22262e"
BORDER      = "#2e343e"
TEXT        = "#e6e8ec"
TEXT_DIM    = "#9aa2b1"
ACCENT      = "#f07e1e"   # Crash orange
ACCENT_HOVER= "#ff9236"
ACCENT_DEEP = "#c25f0d"
OK          = "#4ac97e"
WARN        = "#e8b339"
ERROR       = "#e8595b"

QSS = f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: "Segoe UI", "Inter", system-ui, sans-serif;
    font-size: 13px;
}}

/* ---- sidebar ---------------------------------------------------------- */
#Sidebar {{
    background: {BG_RAISED};
    border-right: 1px solid {BORDER};
}}
#SidebarTitle {{
    color: {ACCENT};
    font-size: 15px;
    font-weight: 700;
    padding: 18px 16px 4px 16px;
}}
#SidebarSubtitle {{
    color: {TEXT_DIM};
    font-size: 11px;
    padding: 0 16px 14px 16px;
}}
QPushButton#NavButton {{
    background: transparent;
    border: none;
    border-left: 3px solid transparent;
    padding: 10px 16px;
    text-align: left;
    color: {TEXT_DIM};
    font-size: 13px;
}}
QPushButton#NavButton:hover {{
    background: {BG_INPUT};
    color: {TEXT};
}}
QPushButton#NavButton:checked {{
    background: {BG_INPUT};
    border-left: 3px solid {ACCENT};
    color: {TEXT};
    font-weight: 600;
}}

/* ---- headings --------------------------------------------------------- */
QLabel#PageTitle   {{ font-size: 20px; font-weight: 700; }}
QLabel#PageHint    {{ color: {TEXT_DIM}; font-size: 12px; }}
QLabel#SectionTitle{{ font-size: 13px; font-weight: 700; color: {TEXT}; }}
QLabel#Dim         {{ color: {TEXT_DIM}; }}
QLabel#Ok          {{ color: {OK}; font-weight: 600; }}
QLabel#Warn        {{ color: {WARN}; font-weight: 600; }}
QLabel#Error       {{ color: {ERROR}; font-weight: 600; }}

/* ---- cards ------------------------------------------------------------ */
QFrame#Card {{
    background: {BG_RAISED};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}

/* ---- buttons ---------------------------------------------------------- */
QPushButton {{
    background: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 7px 14px;
    color: {TEXT};
}}
QPushButton:hover  {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: #5c6472; border-color: {BORDER}; background: #1a1d23; }}

QPushButton#Primary {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
    color: #17120c;
    font-weight: 700;
}}
QPushButton#Primary:hover    {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton#Primary:disabled {{ background: #4a3a28; border-color: #4a3a28; color: #8d8175; }}

QPushButton#PlayButton {{
    background: {ACCENT};
    border: none;
    border-radius: 10px;
    color: #17120c;
    font-size: 19px;
    font-weight: 800;
    padding: 18px 0;
}}
QPushButton#PlayButton:hover    {{ background: {ACCENT_HOVER}; }}
QPushButton#PlayButton:disabled {{ background: #3a332b; color: #7d7266; }}

QPushButton#Danger {{ border-color: {ERROR}; color: {ERROR}; }}

/* ---- inputs ----------------------------------------------------------- */
QLineEdit, QComboBox, QSpinBox {{
    background: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 9px;
    selection-background-color: {ACCENT_DEEP};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {ACCENT}; }}
QLineEdit[readOnly="true"] {{ color: {TEXT_DIM}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background: {BG_INPUT};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT_DEEP};
    outline: none;
}}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {BORDER};
    border-radius: 4px;
    background: {BG_INPUT};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

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
    border-radius: 6px;
    height: 18px;
    text-align: center;
    color: {TEXT};
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}

/* ---- log console ------------------------------------------------------ */
QPlainTextEdit#LogConsole {{
    background: #0e1013;
    border: 1px solid {BORDER};
    border-radius: 6px;
    font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 11px;
    color: #c3cad6;
}}

/* ---- lists ------------------------------------------------------------ */
QListWidget {{
    background: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    outline: none;
}}
QListWidget::item {{ padding: 8px; border-bottom: 1px solid {BORDER}; }}
QListWidget::item:selected {{ background: {ACCENT_DEEP}; color: {TEXT}; }}

/* ---- scrollbars ------------------------------------------------------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #39414e; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: #48525f; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: #39414e; border-radius: 5px; min-width: 24px; }}

QToolTip {{
    background: {BG_INPUT};
    color: {TEXT};
    border: 1px solid {ACCENT};
    padding: 4px 7px;
}}
"""
