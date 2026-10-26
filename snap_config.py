"""Everything that is specific to one product, in one place.

Change the values here and the whole project follows: the buyer, the
locator and the tests all read from this file.

    ACTIVITY_URL   the page that carries the flash-sale module
    SECKILL_TITLE  the section title that holds the cards
    SPEC_TEXT      the spec printed on the card ("4核4G")
    MARKER_TEXT    the traffic quota printed on the card ("300GB")
    PRICE_NUMBER   the price, without the unit ("38")
    PRICE_TEXT     the price, as it is written on the page ("38元")
    REGION_WORDS   the regions the card offers; the buy dialog may ask
                   for one of them
    BUY_MARKERS    words that identify a panel as ours. Keep the price in
                   here -- a panel that shows a different price is not ours.

Note: the test files contain sample pages written with these values. If
you change the product, the samples in test_snap_target.py and
test_dialog.py have to change with it.
"""

ACTIVITY_URL = "https://cloud.tencent.com/act/pro/featured-202607"

# the section title above the module that holds the cards
SECKILL_TITLE = "限时秒杀"

# the spec printed on the card
SPEC_TEXT = "4核4G"

# the traffic quota printed on the card
MARKER_TEXT = "300GB"

# the price we want
PRICE_NUMBER = "38"
PRICE_TEXT = "38元"

# "38元" but not "138元" or "38.5元"; also "¥38"
PRICE_RE = (r"(?<![\d.])" + PRICE_NUMBER + r"(?:\.\d+)?\s*元|¥\s*"
            + PRICE_NUMBER + r"(?!\d)")
PRICE_38_RE = (r"(?<![\d.])" + PRICE_NUMBER + r"(?:\.\d+)?\s*元|¥\s*"
               + PRICE_NUMBER + r"(?:\.\d+)?(?!\d)")

# the regions the card offers -- the buy dialog may ask for one
REGION_WORDS = ("广州", "上海", "北京")

# a panel is "ours" only if it names our product
BUY_MARKERS = ("4核4G3M", "4核4G", "38元", "300GB", "LH Agent")
