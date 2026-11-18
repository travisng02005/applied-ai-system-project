"""Light and dark colour schemes for the PawPal+ Streamlit app.

Light: a soft lavender page with violet accents and a coral touch in the header.
Dark: deep midnight purple with the same violet accent, lightened so it stays readable.
Text, button and header colours were checked for contrast (WCAG AA, 4.5:1 for normal text).
Edit a value here and both the app and its cards follow.
"""

PALETTES = {
    "light": {
        "bg": "#F7F4FF",          # page background (pale lavender)
        "surface": "#E6DEFA",     # cards, inputs, metrics
        "surface-2": "#F1ECFE",   # lighter lavender for inputs / hover
        "border": "#C4B7EE",
        "text": "#2B2350",
        "muted": "#5A5483",
        "accent": "#7C5CE0",      # buttons, highlights (violet)
        "accent-strong": "#5636B8",   # time labels / arrows: readable on lavender
        "on-accent": "#FFFFFF",
        "hero-from": "#6D4AD6",
        "hero-to": "#C5472F",     # coral
        "hero-text": "#FFFFFF",
        "shadow": "rgba(43, 35, 80, .16)",
        "high-bg": "#F8C9C4", "high-fg": "#8E1F1F",
        "med-bg": "#FBE3A6", "med-fg": "#7A4A00",
        "low-bg": "#CDEBC8", "low-fg": "#1E5B2A",
        "cat-bg": "#D3E6F5", "cat-fg": "#1B5578",
        "rep-bg": "#FAD3E6", "rep-fg": "#8A1F5A",
        "ok-bg": "#CDEBC8", "ok-fg": "#1E5B2A",
        "bad-bg": "#F8C9C4", "bad-fg": "#8E1F1F",
    },
    "dark": {
        "bg": "#14111F",
        "surface": "#211C33",
        "surface-2": "#2C2646",
        "border": "#4A3F73",
        "text": "#EFEBFB",
        "muted": "#ABA3D1",
        "accent": "#9B7BFF",
        "accent-strong": "#C7B5FF",
        "on-accent": "#120E1F",
        "hero-from": "#3D2C85",
        "hero-to": "#B4503F",
        "hero-text": "#FFFFFF",
        "shadow": "rgba(0, 0, 0, .5)",
        "high-bg": "#4A2327", "high-fg": "#FFB9B5",
        "med-bg": "#4A3A17", "med-fg": "#FFD98A",
        "low-bg": "#1F4128", "low-fg": "#9FE0A8",
        "cat-bg": "#1E3A4B", "cat-fg": "#9CD3F0",
        "rep-bg": "#4A2340", "rep-fg": "#FFB3D9",
        "ok-bg": "#1F4128", "ok-fg": "#9FE0A8",
        "bad-bg": "#4A2327", "bad-fg": "#FFB9B5",
    },
}

_STYLES = """
.stApp, [data-testid="stAppViewContainer"] { background: var(--bg); color: var(--text); }
[data-testid="stHeader"] { background: transparent; }
.block-container { padding-top: 4rem; max-width: 880px; }
.stApp, .stApp p, .stApp label, .stApp span, .stApp li, .stApp h1, .stApp h2, .stApp h3,
.stApp h4, .stApp h5, [data-testid="stMarkdownContainer"], [data-testid="stWidgetLabel"] p {
    color: var(--text);
}
.stApp [data-testid="stCaptionContainer"], .stApp small { color: var(--muted); }

/* hero */
.hero {
    background: linear-gradient(135deg, var(--hero-from) 0%, var(--hero-to) 100%);
    color: var(--hero-text); padding: 1.5rem 1.7rem; border-radius: 20px; margin-bottom: 1.1rem;
    box-shadow: 0 8px 24px var(--shadow);
}
.hero h1, .hero h1 *, .hero p, .hero p * { color: var(--hero-text) !important; }
.hero h1 { margin: 0; font-size: 2.1rem; padding: 0; }
.hero p { margin: .3rem 0 0; opacity: .92; font-size: 1.02rem; }

/* cards */
.card {
    background: var(--surface); border: 1px solid var(--border); border-radius: 14px;
    padding: .75rem 1rem; margin-bottom: .55rem; box-shadow: 0 1px 4px var(--shadow);
}
.card.accent-high { border-left: 5px solid var(--high-fg); }
.card.accent-medium { border-left: 5px solid var(--med-fg); }
.card.accent-low { border-left: 5px solid var(--low-fg); }
.row { display: flex; gap: .9rem; align-items: center; flex-wrap: wrap; }
.time { font-weight: 700; color: var(--accent-strong); min-width: 7.4rem;
        font-variant-numeric: tabular-nums; }
.title { font-weight: 600; color: var(--text); }
.muted { color: var(--muted) !important; font-size: .83rem; }
.grow { flex: 1; min-width: 10rem; }
.pill { display: inline-block; padding: .1rem .6rem; border-radius: 999px;
        font-size: .74rem; font-weight: 600; margin-right: .3rem; }
.p-high { background: var(--high-bg); color: var(--high-fg); }
.p-medium { background: var(--med-bg); color: var(--med-fg); }
.p-low { background: var(--low-bg); color: var(--low-fg); }
.cat { background: var(--cat-bg); color: var(--cat-fg); }
.rep { background: var(--rep-bg); color: var(--rep-fg); }
.petcard { text-align: center; padding: 1rem .6rem; }
.petcard .emoji { font-size: 2.2rem; line-height: 1.1; }
.petcard .name { font-weight: 700; font-size: 1.1rem; }
.banner { border-radius: 14px; padding: .8rem 1rem; margin-bottom: .6rem; font-weight: 600; }
.banner.ok { background: var(--ok-bg); color: var(--ok-fg); }
.banner.bad { background: var(--bad-bg); color: var(--bad-fg); }
.change { font-variant-numeric: tabular-nums; }
.arrow { color: var(--accent-strong); font-weight: 700; padding: 0 .5rem; }

/* metrics, tabs, expanders, forms */
[data-testid="stMetric"] {
    background: var(--surface); border: 1px solid var(--border); border-radius: 14px;
    padding: .7rem 1rem; box-shadow: 0 1px 4px var(--shadow);
}
[data-testid="stMetricLabel"] p { color: var(--muted); }
.stTabs [data-baseweb="tab-list"] { gap: .4rem; border-bottom: 1px solid var(--border); }
.stTabs [data-baseweb="tab"] { border-radius: 10px 10px 0 0; padding: .5rem 1.1rem;
                               font-weight: 600; background: transparent; }
.stTabs [data-baseweb="tab"] p { color: var(--muted); }
.stTabs [aria-selected="true"] p { color: var(--accent-strong); }
.stTabs [data-baseweb="tab-highlight"] { background: var(--accent); }
[data-testid="stExpander"] details { background: var(--surface); border: 1px solid var(--border);
                                      border-radius: 14px; }
[data-testid="stExpander"] summary { background: var(--surface) !important;
                                      border-radius: 14px; }
[data-testid="stExpander"] summary:hover { background: var(--surface-2) !important; }
[data-testid="stExpander"] summary svg { color: var(--text) !important; fill: var(--text) !important; }
[data-testid="stExpander"] summary p { color: var(--text); font-weight: 600; }
[data-testid="stForm"] { background: var(--surface); border: 1px solid var(--border);
                         border-radius: 16px; }
[data-testid="stAlert"] { border-radius: 12px; }
[data-testid="stAlert"] p { color: inherit; }

/* inputs */
[data-baseweb="input"], [data-baseweb="base-input"], [data-baseweb="select"] > div,
[data-baseweb="textarea"], .stNumberInput input, .stTextInput input, .stTimeInput input {
    background: var(--surface-2) !important; color: var(--text) !important;
    border-color: var(--border) !important;
}
input, textarea { color: var(--text) !important; -webkit-text-fill-color: var(--text) !important; }
[data-testid="stSelectbox"] div, [data-testid="stTimeInput"] div,
[data-testid="stMultiSelect"] div { background: var(--surface-2) !important;
                                    color: var(--text) !important; }
[data-testid="stSelectbox"] input, [data-testid="stTimeInput"] input { background: transparent !important; }
[data-testid="stSelectbox"] svg, [data-testid="stTimeInput"] svg { fill: var(--text) !important; color: var(--text) !important; }
[role="listbox"], [role="listbox"] li, [role="option"], [data-testid="stSelectboxVirtualDropdown"] {
    background: var(--surface) !important; color: var(--text) !important; }
[role="option"]:hover, [role="option"][aria-selected="true"] { background: var(--surface-2) !important; }
[data-testid="stSelectbox"] [data-testid="stWidgetLabel"],
[data-testid="stSelectbox"] [data-testid="stWidgetLabel"] div,
[data-testid="stTimeInput"] [data-testid="stWidgetLabel"],
[data-testid="stTimeInput"] [data-testid="stWidgetLabel"] div { background: transparent !important; }
[data-baseweb="select"] div { background: var(--surface-2) !important;
                              color: var(--text) !important; }
[data-baseweb="select"] input { background: transparent !important; }
[data-baseweb="select"] span, [data-baseweb="select"] svg { color: var(--text) !important;
                                                            fill: var(--text) !important; }
[data-baseweb="popover"] ul, [data-baseweb="menu"] { background: var(--surface) !important; }
[data-baseweb="popover"] li, [data-baseweb="menu"] li { color: var(--text) !important; }
[data-baseweb="popover"] li:hover { background: var(--surface-2) !important; }
.stNumberInput button { background: var(--surface-2) !important; color: var(--text) !important; }
[data-testid="stCheckbox"] span, [data-testid="stToggle"] p { color: var(--text); }

/* buttons */
div.stButton > button, div[data-testid="stFormSubmitButton"] > button {
    border-radius: 10px; font-weight: 600; background: var(--surface);
    color: var(--text); border: 1px solid var(--border);
}
div.stButton > button:hover, div[data-testid="stFormSubmitButton"] > button:hover {
    border-color: var(--accent-strong); color: var(--accent-strong);
}
div[data-testid="stFormSubmitButton"] > button[data-testid="stBaseButton-primaryFormSubmit"],
button[data-testid="stBaseButton-primary"], button[data-testid="stBaseButton-primaryFormSubmit"],
div.stButton > button[kind="primary"] {
    background: var(--accent); color: var(--on-accent); border: 1px solid var(--accent);
}
div[data-testid="stFormSubmitButton"] > button[data-testid="stBaseButton-primaryFormSubmit"]:hover,
button[data-testid="stBaseButton-primary"]:hover,
button[data-testid="stBaseButton-primaryFormSubmit"]:hover {
    background: var(--accent-strong); color: var(--on-accent); border-color: var(--accent-strong);
}
[data-testid="stProgress"] > div > div > div { background: var(--accent); }
pre, code { background: var(--surface-2) !important; color: var(--text) !important; }
"""


def build_css(theme: str) -> str:
    """A <style> block for the given theme name ('light' or 'dark')."""
    colors = PALETTES.get(theme, PALETTES["light"])
    variables = "".join(f"--{name}: {value};" for name, value in colors.items())
    return f"<style>:root {{ {variables} color-scheme: {theme}; }}{_STYLES}</style>"
