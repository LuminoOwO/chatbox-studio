"""Colour themes and text size for the GUI.

Every colour in the stylesheet is a named token, so a theme only has to fill in the
palette. Text sizes are scaled for the "Text size" setting.
"""

from __future__ import annotations

import re
from string import Template

THEMES = [("dark", "Dark"), ("light", "Light"), ("contrast", "High contrast"),
          ("mocha", "Catppuccin Mocha"), ("macchiato", "Catppuccin Macchiato"),
          ("frappe", "Catppuccin Frappé"), ("latte", "Catppuccin Latte"),
          ("nord", "Nord"), ("dracula", "Dracula"), ("gruvbox", "Gruvbox Dark"),
          ("tokyonight", "Tokyo Night"), ("rosepine", "Rosé Pine")]
SCALES = [(0.9, "Small"), (1.0, "Normal"), (1.15, "Large"), (1.3, "Larger"), (1.5, "Largest")]

PALETTES = {
    "dark": dict(
        text="#e6e8eb", bg="#14161a", panel="#1b1e23", border="#262a31", muted="#9aa2ad",
        field="#23272e", field_border="#3a404a", field_hover="#4a525e", accent="#4c9cff",
        selection="#2d4a6f", button="#2a2f37", button_border="#3a414b", button_hover="#323843",
        primary="#2d5fa8", primary_border="#3a70bd", primary_hover="#356bb8", on_primary="#ffffff",
        sunken="#16181c", log_text="#a8b0ba", faint="#8a929d", disabled="#6a717b",
        header="#1f2329", bubble="#f3f4f6", bubble_text="#111418", warn="#e5a54b",
        focus="#7cb8ff", chip_on_bg="rgba(76,156,255,0.16)", chip_on_border="rgba(76,156,255,0.55)",
        warn_bg="rgba(229,165,75,0.18)", warn_border="rgba(229,165,75,0.6)",
        meter_track="#23272e", meter_idle="#5a626d", meter_voice="#4c9cff",
    ),
    "light": dict(
        text="#1b1f24", bg="#eef0f3", panel="#ffffff", border="#d3d8de", muted="#4f5761",
        field="#ffffff", field_border="#b9c0c8", field_hover="#8f98a3", accent="#1f6feb",
        selection="#cfe1ff", button="#f1f3f5", button_border="#bcc3cb", button_hover="#e3e7eb",
        primary="#1f6feb", primary_border="#1659c7", primary_hover="#2f7cf0", on_primary="#ffffff",
        sunken="#f7f8fa", log_text="#38404a", faint="#565e69", disabled="#8d949c",
        header="#eef0f3", bubble="#e6e9ee", bubble_text="#111418", warn="#9a5b00",
        focus="#0b57d0", chip_on_bg="rgba(31,111,235,0.12)", chip_on_border="rgba(31,111,235,0.55)",
        warn_bg="rgba(154,91,0,0.12)", warn_border="rgba(154,91,0,0.55)",
        meter_track="#dde1e6", meter_idle="#9aa1aa", meter_voice="#1f6feb",
    ),
    "contrast": dict(
        text="#ffffff", bg="#000000", panel="#0a0a0a", border="#ffffff", muted="#e8e8e8",
        field="#000000", field_border="#ffffff", field_hover="#ffd400", accent="#ffd400",
        selection="#0050a0", button="#000000", button_border="#ffffff", button_hover="#1f1f1f",
        primary="#0050a0", primary_border="#ffffff", primary_hover="#0066cc", on_primary="#ffffff",
        sunken="#000000", log_text="#ffffff", faint="#dcdcdc", disabled="#a0a0a0",
        header="#111111", bubble="#ffffff", bubble_text="#000000", warn="#ffb000",
        focus="#ffd400", chip_on_bg="#0050a0", chip_on_border="#ffffff",
        warn_bg="#5a3d00", warn_border="#ffb000",
        meter_track="#333333", meter_idle="#aaaaaa", meter_voice="#ffd400",
    ),
}

_TEMPLATE = Template(r"""
* { font-family: "Inter", "Segoe UI", "Noto Sans", "Ubuntu", sans-serif; font-size: 13px; color: $text; }
QMainWindow, #root { background: $bg; }
#inner, QScrollArea > QWidget > QWidget { background: transparent; }
#panel { background: $panel; border: 1px solid $border; border-radius: 10px; }
#title { font-size: 17px; font-weight: 600; }
#section { color: $muted; font-size: 11px; font-weight: 600; letter-spacing: 1px; padding-top: 10px; }
#hint { color: $muted; font-size: 11px; }
QComboBox, QLineEdit, QSpinBox {
    background: $field; border: 1px solid $field_border; border-radius: 6px; padding: 5px 8px; min-height: 18px; }
QComboBox:hover, QLineEdit:hover, QSpinBox:hover { border-color: $field_hover; }
QComboBox:focus, QLineEdit:focus, QSpinBox:focus { border-color: $accent; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView { background: $field; border: 1px solid $field_border; selection-background-color: $selection; }
QPushButton { background: $button; border: 1px solid $button_border; border-radius: 6px; padding: 6px 12px; }
QPushButton:hover { background: $button_hover; }
QPushButton:pressed { background: $border; }
QPushButton#listen { font-size: 14px; font-weight: 600; padding: 8px 18px; border-radius: 8px; }
QPushButton#listen:!checked { background: $primary; border-color: $primary_border; }
QPushButton#primary { background: $primary; border-color: $primary_border; }
QPushButton#primary:hover { background: $primary_hover; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px; border: 1px solid $field_hover; background: $field; }
QCheckBox::indicator:checked { background: $accent; border-color: $accent; }
QSlider::groove:horizontal { height: 4px; background: $field_border; border-radius: 2px; }
QSlider::sub-page:horizontal { background: $accent; border-radius: 2px; }
QSlider::handle:horizontal { background: $text; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }
QTextBrowser, QPlainTextEdit { background: $sunken; border: 1px solid $border; border-radius: 8px; padding: 6px; }
QPlainTextEdit { font-family: "JetBrains Mono", "Cascadia Mono", "Consolas", "DejaVu Sans Mono", monospace; font-size: 11px; color: $log_text; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 8px; }
QScrollBar::handle:vertical { background: $field_border; border-radius: 4px; min-height: 24px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QDialog { background: $bg; }
QTabWidget::pane { border: 1px solid $border; border-radius: 8px; background: $panel; padding: 10px; top: -1px; }
QTabWidget#maintabs::pane { border: none; background: transparent; padding: 0; }
QLabel#bubble { background: $bubble; color: $bubble_text; border-radius: 14px; padding: 12px 16px;
    font-size: 15px; }
QLabel#bubble[empty="true"] { background: $field; color: $faint; font-size: 13px; }
QPushButton#chip { border-radius: 12px; padding: 4px 11px; font-size: 12px; background: $panel;
    color: $faint; text-decoration: line-through; }
QPushButton#chip:checked { background: $chip_on_bg; border-color: $chip_on_border;
    color: $text; text-decoration: none; }
QPushButton#chip:disabled { color: $disabled; }
QPushButton#chipwarn { border-radius: 12px; padding: 4px 11px; font-size: 12px; }
QPushButton#chipwarn:checked { background: $warn_bg; border-color: $warn_border;
    color: $warn; font-weight: 600; }
QListWidget#elements { background: $sunken; border: 1px solid $border; padding: 4px; }
QListWidget#elements::item { padding: 6px 4px; border-radius: 6px; }
QListWidget#elements::item:selected { background: $selection; }
QTableWidget QComboBox { border-radius: 0; padding: 2px 6px; }
QDateTimeEdit, QDoubleSpinBox { background: $field; border: 1px solid $field_border; border-radius: 6px;
    padding: 5px 8px; }
QRadioButton { spacing: 8px; }
QTabBar::tab { background: transparent; color: $muted; padding: 7px 14px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: $text; border-bottom-color: $accent; }
QTabBar::tab:hover { color: $text; }
QListWidget { background: $field; border: 1px solid $field_border; border-radius: 6px; padding: 4px; }
QListWidget::item { padding: 3px 2px; }
QListWidget::item:selected { background: transparent; color: $text; }
QListWidget::indicator { width: 15px; height: 15px; border-radius: 4px; border: 1px solid $field_hover; background: $panel; }
QListWidget::indicator:checked { background: $accent; border-color: $accent; }
QPushButton#icon { padding: 5px 0; }
QLabel#arrow { color: $muted; font-size: 15px; }
QMenu { background: $field; border: 1px solid $field_border; padding: 4px; }
QMenu::item { padding: 6px 14px; border-radius: 4px; }
QMenu::item:selected { background: $selection; }
QComboBox:disabled { color: $disabled; }
QTableWidget { background: $sunken; border: 1px solid $border; border-radius: 6px; gridline-color: $border;
    selection-background-color: $selection; }
QTableWidget QLineEdit { padding: 2px 6px; border-radius: 0; }
QHeaderView::section { background: $header; color: $muted; border: none; border-bottom: 1px solid $border;
    padding: 6px 8px; font-size: 11px; font-weight: 600; }
QTableWidget::indicator { width: 16px; height: 16px; border-radius: 4px; border: 1px solid $field_hover; background: $field; }
QTableWidget::indicator:checked { background: $accent; border-color: $accent; }

QPushButton#primary, QPushButton#listen:!checked { color: $on_primary; }
QPushButton:focus { border: 2px solid $focus; padding: 5px 11px; }
QPushButton#listen:focus { padding: 7px 17px; }
QPushButton#chip:focus, QPushButton#chipwarn:focus { border: 2px solid $focus; padding: 3px 10px; }
QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateTimeEdit:focus,
QPlainTextEdit:focus, QTextBrowser:focus, QListWidget:focus, QTableWidget:focus { border: 2px solid $focus; }
QCheckBox:focus, QRadioButton:focus { color: $focus; }
QCheckBox::indicator:focus { border: 2px solid $focus; }
QTabBar::tab:focus { color: $text; border-bottom-color: $focus; }
QRadioButton::indicator { width: 15px; height: 15px; border-radius: 8px; border: 1px solid $field_hover; background: $field; }
QRadioButton::indicator:checked { background: $accent; border-color: $accent; }
QToolTip { background: $field; color: $text; border: 1px solid $field_border; padding: 6px; }
QTreeWidget { background: $sunken; border: 1px solid $border; border-radius: 6px; padding: 2px; }
QTreeWidget::item { padding: 3px 2px; }
QTreeWidget::item:selected { background: $selection; color: $text; }
""")


def _rgba(hex_: str, a: float) -> str:
    h = hex_.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{a})"


def scheme(*, crust, mantle, base, surface0, surface1, surface2, overlay, subtext, text,
           accent, primary, on_primary, warn, focus, bubble, bubble_text, light=False) -> dict:
    """Builds a full palette from a colour scheme's base colours (Catppuccin naming)."""
    return dict(
        text=text, bg=crust if not light else mantle, panel=mantle if not light else base,
        border=surface0, muted=subtext, field=surface0 if not light else base,
        field_border=surface1, field_hover=surface2, accent=accent,
        selection=surface1, button=surface0, button_border=surface1, button_hover=surface1,
        primary=primary, primary_border=primary, primary_hover=accent, on_primary=on_primary,
        sunken=crust, log_text=subtext, faint=overlay, disabled=surface2, header=base,
        bubble=bubble, bubble_text=bubble_text, warn=warn, focus=focus,
        chip_on_bg=_rgba(accent, 0.18), chip_on_border=_rgba(accent, 0.6),
        warn_bg=_rgba(warn, 0.18), warn_border=_rgba(warn, 0.6),
        meter_track=surface0, meter_idle=surface2, meter_voice=accent,
    )


PALETTES.update({
    # Catppuccin - https://catppuccin.com/palette
    "mocha": scheme(crust="#11111b", mantle="#181825", base="#1e1e2e", surface0="#313244",
                    surface1="#45475a", surface2="#585b70", overlay="#9399b2",
                    subtext="#bac2de", text="#cdd6f4", accent="#cba6f7", primary="#cba6f7",
                    on_primary="#11111b", warn="#fab387", focus="#b4befe",
                    bubble="#cdd6f4", bubble_text="#11111b"),
    "macchiato": scheme(crust="#181926", mantle="#1e2030", base="#24273a", surface0="#363a4f",
                        surface1="#494d64", surface2="#5b6078", overlay="#939ab7",
                        subtext="#b8c0e0", text="#cad3f5", accent="#c6a0f6",
                        primary="#c6a0f6", on_primary="#181926", warn="#f5a97f",
                        focus="#b7bdf8", bubble="#cad3f5", bubble_text="#181926"),
    "frappe": scheme(crust="#232634", mantle="#292c3c", base="#303446", surface0="#414559",
                     surface1="#51576d", surface2="#626880", overlay="#949cbb",
                     subtext="#b5bfe2", text="#c6d0f5", accent="#ca9ee6", primary="#ca9ee6",
                     on_primary="#232634", warn="#ef9f76", focus="#babbf1",
                     bubble="#c6d0f5", bubble_text="#232634"),
    "latte": scheme(crust="#dce0e8", mantle="#e6e9ef", base="#eff1f5", surface0="#ccd0da",
                    surface1="#bcc0cc", surface2="#acb0be", overlay="#5c5f77",
                    subtext="#5c5f77", text="#4c4f69", accent="#8839ef", primary="#8839ef",
                    on_primary="#ffffff", warn="#fe640b", focus="#7287fd",
                    bubble="#dce0e8", bubble_text="#4c4f69", light=True),
    "nord": scheme(crust="#242933", mantle="#2e3440", base="#3b4252", surface0="#3b4252",
                   surface1="#434c5e", surface2="#4c566a", overlay="#a0a8b7",
                   subtext="#d8dee9", text="#eceff4", accent="#88c0d0", primary="#88c0d0",
                   on_primary="#2e3440", warn="#ebcb8b", focus="#8fbcbb",
                   bubble="#eceff4", bubble_text="#2e3440"),
    "dracula": scheme(crust="#191a21", mantle="#21222c", base="#282a36", surface0="#343746",
                      surface1="#44475a", surface2="#565a72", overlay="#9aa5ce",
                      subtext="#d6d6e0", text="#f8f8f2", accent="#bd93f9", primary="#bd93f9",
                      on_primary="#191a21", warn="#ffb86c", focus="#ff79c6",
                      bubble="#f8f8f2", bubble_text="#21222c"),
    "gruvbox": scheme(crust="#1d2021", mantle="#282828", base="#32302f", surface0="#3c3836",
                      surface1="#504945", surface2="#665c54", overlay="#a89984",
                      subtext="#d5c4a1", text="#ebdbb2", accent="#fabd2f", primary="#d79921",
                      on_primary="#1d2021", warn="#fe8019", focus="#8ec07c",
                      bubble="#ebdbb2", bubble_text="#1d2021"),
    "tokyonight": scheme(crust="#16161e", mantle="#1a1b26", base="#1f2335", surface0="#292e42",
                         surface1="#3b4261", surface2="#414868", overlay="#9aa5ce",
                         subtext="#a9b1d6", text="#c0caf5", accent="#7aa2f7",
                         primary="#7aa2f7", on_primary="#16161e", warn="#ff9e64",
                         focus="#bb9af7", bubble="#c0caf5", bubble_text="#16161e"),
    "rosepine": scheme(crust="#191724", mantle="#1f1d2e", base="#26233a", surface0="#26233a",
                       surface1="#403d52", surface2="#524f67", overlay="#908caa",
                       subtext="#c4c0d9", text="#e0def4", accent="#c4a7e7", primary="#c4a7e7",
                       on_primary="#191724", warn="#f6c177", focus="#ebbcba",
                       bubble="#e0def4", bubble_text="#191724"),
})


def palette(theme: str) -> dict:
    return PALETTES.get(theme, PALETTES["dark"])


def build_style(theme: str = "dark", scale: float = 1.0) -> str:
    css = _TEMPLATE.substitute(palette(theme))
    if abs(scale - 1.0) > 0.01:
        css = re.sub(r"font-size:\s*(\d+)px",
                     lambda m: f"font-size: {max(8, round(int(m.group(1)) * scale))}px", css)
    return css
