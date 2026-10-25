"""Everything that changes from one campaign to the next.

The buyer does not hardcode a product, a price or a page: it reads them
from ``config.json`` next to this file. Copy ``config.example.json`` to
``config.json`` and edit the values -- the file is git-ignored, so your
campaign never ends up in a public repository.

If ``config.json`` is missing, the DEFAULTS below are used, so the scripts
still run out of the box against the example campaign.
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_NAME = "config.json"
EXAMPLE_NAME = "config.example.json"

# The values below describe the campaign this project was written against.
# They are only a starting point -- everything is overridable in config.json.
DEFAULTS = {
    # ---- where to go --------------------------------------------------
    "page_url": "https://cloud.tencent.com/act/pro/featured-202607",
    "cookie_file": "cookies.json",
    "log_file": "snap_browser.log",

    # ---- what to buy --------------------------------------------------
    # "spec"    the configuration printed on the card (e.g. "4核4G")
    # "marker"  a second string that must be on the same card (e.g. "300GB")
    # "price"   the flash price in yuan, as a bare number (e.g. "38")
    "spec": "4核4G",
    "marker": "300GB",
    "price": "38",

    # ---- where to look ------------------------------------------------
    # a keyword of the section that holds the flash-sale card
    "seckill_title": "限时秒杀",
    # the goods id of that card; the page's own data lists one per product
    "act_id": "1996855673581677",

    # ---- the buy dialog -----------------------------------------------
    # the regions the card offers, in the order they should be tried
    "regions": ["广州", "上海", "北京"],
    # extra strings that identify our dialog; the spec, the price and the
    # marker are added automatically
    "dialog_markers": [],
}


def load(path=None):
    """Read config.json (if it exists) on top of DEFAULTS.

    The first time the tool runs, config.json is created from
    config.example.json, so there is always a file to edit.
    """
    cfg = dict(DEFAULTS)
    cfg["regions"] = list(DEFAULTS["regions"])
    cfg["dialog_markers"] = list(DEFAULTS["dialog_markers"])
    path = path or os.path.join(HERE, CONFIG_NAME)
    if not os.path.isfile(path):
        example = os.path.join(HERE, EXAMPLE_NAME)
        if os.path.isfile(example):
            try:
                with open(example, encoding="utf-8") as fh:
                    template = fh.read()
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(template)
            except OSError:
                pass          # a read-only directory is not a reason to fail
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        for key, value in data.items():
            cfg[key] = value
    return cfg


CFG = load()

SPEC = ""
MARKER = ""
PRICE = ""
PAGE_URL = ""
COOKIE_FILE = "cookies.json"
LOG_FILE = "snap_browser.log"
SECKILL_TITLE = ""
ACT_ID = ""
REGIONS = []


def use(path=None):
    """Load the configuration and publish it as this module's globals.

    Everything else reads snap_config.SPEC and friends at call time, so
    calling this again (with a different file) re-targets the whole tool.
    """
    global CFG, SPEC, MARKER, PRICE, PAGE_URL, COOKIE_FILE, LOG_FILE
    global SECKILL_TITLE, ACT_ID, REGIONS
    CFG = load(path)
    SPEC = str(CFG.get("spec") or "")
    MARKER = str(CFG.get("marker") or "")
    PRICE = str(CFG.get("price") or "")
    PAGE_URL = str(CFG.get("page_url") or "")
    COOKIE_FILE = str(CFG.get("cookie_file") or "cookies.json")
    LOG_FILE = str(CFG.get("log_file") or "snap_browser.log")
    SECKILL_TITLE = str(CFG.get("seckill_title") or "")
    ACT_ID = str(CFG.get("act_id") or "")
    REGIONS = list(CFG.get("regions") or [])
    return CFG


use()


def price_pattern(price=None):
    """A regex that matches the flash price -- and nothing that merely
    contains it.

    ``38`` must match "38元" and "¥38", but not "138元" and not "388元".
    The pattern is built here (in Python) and handed to the page as a plain
    string, so no escaping survives the trip into JavaScript.
    """
    p = re.escape(str(price if price is not None else PRICE))
    return r"(?<![\d.])%s(?:\.\d+)?\s*元|¥\s*%s(?!\d)" % (p, p)


def price_re(price=None):
    """The same thing, compiled."""
    return re.compile(price_pattern(price))


def dialog_markers(cfg=None):
    """Strings that identify our buy dialog.

    The spec, the price and the marker are added automatically; anything in
    ``dialog_markers`` is appended.
    """
    cfg = cfg or CFG
    out = []
    for value in (cfg.get("spec"), cfg.get("marker"),
                  "%s元" % cfg.get("price")):
        value = str(value or "").strip()
        if value and value not in out:
            out.append(value)
    for value in cfg.get("dialog_markers") or []:
        value = str(value or "").strip()
        if value and value not in out:
            out.append(value)
    return out
