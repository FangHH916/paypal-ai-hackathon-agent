"""Conservative checks for requests this immediate, one-currency demo cannot execute faithfully."""
from app.config import settings
import re

_CANCEL = re.compile(r"\b(?:do not|don't|dont|never)\s+(?:send|pay|buy|invest|transfer|prepare)\b|"
                     r"\bcancel\s+(?:that|it|all|the payment|the transfer)|^\s*stop\b|"
                     r"^\s*(?:please\s+)?(?:cancel|never\s?mind|forget\s+(?:it|that|about it))\b(?:\s+please)?[\s.!]*$", re.I)


_NUMBER_WORD = (r"(?:a |one )?(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
                r"sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|grand)")


def cancelled(text):
    # "Stop! I want my money back", "stop that payment": those are requests of their own, not a cancelled draft
    if re.search(r"\bmoney back\b|\brefund\b|\bstop (?:that|the|my) (?:payment|payout|transfer)\b|\bfreeze\b|\b(?:stop|cancel) everything\b", text, re.I):
        return False
    return bool(_CANCEL.search(text))


_STOPS_CARD = re.compile(r"\b(?:stop|cancel|halt|call off|kill)\b\s+(?:(?:that|the|this|my|it)\b|(?:payment|transfer))|"
                         r"\bcall (?:it|that|this) off\b|\b(?:don'?t|do not|never)\s+(?:send|pay|transfer)\s+(?:it|that|this|the money)\b|"
                         r"^\W*(?:please\s+)?(?:stop|cancel)\b", re.I)


def stops_open_card(text):
    """With a card open, "stop that payment", "please stop it", "call it off" mean THAT card. Cancelling is always allowed:
    it only makes her safer (it is never refused by a scam alert). "Stop everything" / "freeze" is the freeze instead."""
    if re.search(r"\bfreeze\b|\b(?:stop|cancel|block) (?:everything|all)\b|\bmoney back\b|\brefund\b", text, re.I):
        return False
    return cancelled(text) or bool(_STOPS_CARD.search(text))


def correction(text):
    return bool(re.search(r"\b(?:change|correct|revise)\s+(?:the\s+)?amount\s+to\b|\bmake\s+(?:it|that)\b", text, re.I))


def problem(text):
    if cancelled(text):
        return "No payment was prepared. Please tell me when you want to make a new request."
    if re.search(r"\b(?:tomorrow|next week)\b|\bif\b.+\b(?:send|pay|buy|invest|transfer)\b", text, re.I):
        return "This demo supports immediate payments only. Please make a new request when you are ready to pay."
    if re.search(r"\b(?:euros?|EUR|GBP|pounds?|RMB|CNY|yen|JPY|SGD|Singapore dollars?)\b|[€£]", text, re.I) and settings.wallet_currency == "USD":
        return "Star only pays in US dollars. Please tell me the amount in dollars."
    if re.search(r"\bhow (?:would|could|can|do) I\b", text, re.I):
        return "To pay, tell me who and how much, then check and sign the confirmation card. Nothing is prepared yet."
    if re.search(r"\b(?:fifteen|fifty|\d+|one|two|three|four|five|six|seven|eight|nine|ten|twenty|thirty|forty|sixty|seventy|eighty|ninety)\s+or\s+(?:\d+|" + _NUMBER_WORD + r")\b", text, re.I):
        # "1500 or I'll be arrested" is one amount: only a second number (or number word) after "or" is a second amount
        return "I heard two possible amounts. Which exact amount would you like to use?"
    if re.search(r"\b(?:fivety|hundread)\b", text, re.I):
        return "I could not reliably recognise the amount. Could you repeat it or type the exact amount?"
    if re.search(r"\b(?:half|a quarter)\s+(?:of\s+)?(?:\d|a grand|a thousand)", text, re.I):
        from app.agent.textparse import parse_amount
        if parse_amount(text) is None:
            return "That fraction does not give an exact amount in cents. What exact amount should I use?"
    if re.search(r"(?<!\w)-\s*\d|\d+\.\d{3,}|\d\s*[eE][+-]?\d|\d+\s+or\s+\d+", text):
        return "Please state one positive amount in dollars and cents, with at most two decimal places."
    for token in re.findall(r"\d[\d,]*,\d[\d,]*", text):
        if not re.fullmatch(r"\d{1,3}(?:,\d{3})+", token):
            return "I could not read that amount reliably. Please say the amount again."
    if re.search(r"\b(?:one|two|three|four|five|six|seven|eight|nine)\s+(?:one|two|three|four|five|six|seven|eight|nine)\b", text, re.I):
        return "Please say the full amount, for example one hundred and twenty-three dollars."
    return None
