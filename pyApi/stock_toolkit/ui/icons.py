"""Central icon vocabulary for the Streamlit dashboard.

Two-layer indirection so a future restyle is a one-file change:

  semantic name  ─►  internal token  ─►  glyph
  (per UI element)   (per concept)       (the character rendered)

Why two layers?
- ``GLYPHS`` defines the visual vocabulary: 9 concepts, one glyph
  each. Restyle the whole app by editing this dict.
- ``SEMANTIC`` maps each on-screen element to one of those concepts.
  Refactor what something *means* (e.g. demote "alerts" from a
  warning concept to a notification concept) without touching every
  callsite.

Usage::

    from stock_toolkit.ui.icons import icon, heading, tab_label

    st.tabs([tab_label("tab.score", "Score"), ...])
    st.markdown(heading("watchlist", "Watchlist"))
    st.button(f"{icon('buy')}  Buy")

Unknown semantic names return "?" rather than raising — this keeps a
stray typo from breaking the dashboard. Tests cover the known set.
"""


# ─── Layer 2: concept → glyph ──────────────────────────────────────────
# Edit any value here to restyle every UI element using that concept.
GLYPHS: dict[str, str] = {
    "achievement":  "◉",      # ranking, award, featured
    "data":         "◆",      # charts, stats, analytics
    "execute":      "▶",      # run, start, buy
    "reverse":      "◀",      # exit, sell
    "alert":        "▲",      # warning, attention
    "ai":           "✦",      # AI, intelligence
    "ingest":       "↓",      # download, fetch, collect
    "list":         "▪",      # inventory, log, history
    "commit":       "✓",      # save, confirm
    "key":          "🔑",     # secrets, API keys
    "add":          "➕",     # new, create
    "remove":       "✕",      # close, delete
    "settings":     "⚙️",     # admin, settings (user kept)
    "game":         "🎮",     # paper-trading (user kept)
    "help":         "❓",     # help (user kept)
    "chat":         "🤖",     # assistant chat avatar (Streamlit idiom)
}


# ─── Layer 1: UI element → concept ─────────────────────────────────────
# One entry per place the dashboard renders an icon. Multiple entries
# can share a concept — e.g. "watchlist", "inventory", "trade_history"
# all map to the same "list" concept.
SEMANTIC: dict[str, str] = {
    # main tabs (rendered in ui/app.py)
    "tab.score":         "achievement",
    "tab.analysis":      "data",
    "tab.backtest":      "execute",
    "tab.alerts":        "alert",
    "tab.briefing":      "ai",
    "tab.collect":       "ingest",

    # sidebar pages
    "page.admin":        "settings",
    "page.game":         "game",
    "page.help":         "help",

    # admin page sections + buttons
    "watchlist":         "list",
    "api_keys":          "key",
    "collect":           "ingest",
    "inventory":         "list",
    "suppressed":        "alert",
    "save":              "commit",
    "summary":           "data",
    "preview":           "execute",

    # game page sections + buttons
    "positions":         "data",
    "buy":               "execute",
    "sell":              "reverse",
    "portfolio_chart":   "data",
    "compare":           "data",
    "trade_history":     "list",
    "download":          "ingest",
    "outcome_stats":     "achievement",
    "new_strategy":      "add",
    "sell_all":          "remove",
    "settings_strategy": "settings",

    # briefing tab sections + buttons
    "claude_strategy":   "ai",
    "claude_propose":    "ai",
    "seven_step":        "data",
    "paper_trade":       "game",
    "chat_avatar":       "chat",
}


def icon(name: str) -> str:
    """Return the glyph for a semantic name, or ``"?"`` if unknown."""
    token = SEMANTIC.get(name)
    if token is None:
        return "?"
    return GLYPHS.get(token, "?")


def tab_label(name: str, text: str) -> str:
    """Compose a tab label: ``"◉  Score"`` from ``tab_label("tab.score", "Score")``."""
    return f"{icon(name)}  {text}"


def heading(name: str, text: str, level: int = 3) -> str:
    """Compose a markdown heading: ``"### ▪  Watchlist"``."""
    return f"{'#' * level} {icon(name)}  {text}"
