"""Conservative checks for requests this immediate, one-currency demo cannot execute faithfully."""
from app.config import settings
import re

_CANCEL = re.compile(r"\b(?:do not|don't|dont|never)\s+(?:send|pay|buy|invest|transfer|prepare)\b|"
                     r"\bcancel\s+(?:that|it|all|the payment|the transfer)|^\s*stop\b|"
                     r"^\s*(?:please\s+)?(?:cancel|never\s?mind|forget\s+(?:it|that|about it))\b(?:\s+please)?[\s.!]*$", re.I)


def cancelled(text):
    # "Stop! I want my money back", "stop that payment": those are requests of their own, not a cancelled draft
    if re.search(r"\bmoney back\b|\brefund\b|\bstop (?:that|the|my) (?:payment|payout|transfer)\b|\bfreeze\b|\b(?:stop|cancel) everything\b", text, re.I):
        return False
    return bool(_CANCEL.search(text))


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
    if re.search(r"\b(?:fifteen|fifty|\d+|one|two|three|four|five|six|seven|eight|nine|ten|twenty|thirty|forty|sixty|seventy|eighty|ninety)\s+or\s+(?:\d+|[a-z]+)\b", text, re.I):
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
