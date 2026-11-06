"""Deterministic answers for standard application fields.

The stable facts (contact, work authorization, EEO, salary, consents,
employment, education) are identical on every application, so they are
answered here by matching the field's label — no model call, no run-to-run
variance. Every value comes from profile/profile.json (the source of truth),
including the structured employment/education entries.

resolve(field) -> value (shaped for the field's kind) or None when no rule
matches, leaving it to the model/answer API. Anything a rule can't map on a
choice field (option text doesn't match) also returns None.
"""
import datetime as dt
import re

from . import config
from .config import paths

PROF = paths.profile()
_I, _W, _PREF = PROF["identity"], PROF["work_auth"], PROF["preferences"]
_S, EEO = PROF.get("standing_answers", {}), PROF.get("eeo", {})

# Canonical scalars, all from profile/profile.json.
P = {
    "first": _I["first"], "last": _I["last"], "full": f"{_I['first']} {_I['last']}",
    "preferred": _I.get("preferred_name") or "",
    "email": _I["email"], "phone": _I["phone"], "phone_type": _I.get("phone_type", "Mobile"),
    "addr1": _I.get("addr1", ""), "city": _I.get("city", ""), "state": _I.get("state", ""),
    "zip": _I.get("zip", ""), "country": _I.get("country", "United States"),
    "linkedin": _I.get("linkedin", ""), "github": _I.get("github", ""), "website": _I.get("website", ""),
    "current_company": PROF.get("current_company", ""),
    "salary": _PREF.get("salary", ""), "start_date": _PREF.get("start_date", ""),
    "how_heard": _PREF.get("how_did_you_hear", "LinkedIn"), "skill": _PREF.get("default_skill", ""),
    "relocate": _PREF.get("willing_to_relocate", "Yes"), "willing": _PREF.get("willing_general", "Yes"),
    "authorized": _W.get("authorized_to_work", "Yes"), "sponsor": _W.get("needs_sponsorship", "No"),
    "reside": _W.get("resides_in_country", "Yes"), "visa": _W.get("visa_status", ""),
    "clearance": _W.get("security_clearance", "No"), "us_person": _W.get("us_person_itar", "No"),
    "relatives": _S.get("relatives_at_company", "No"), "prev_employed": _S.get("previously_employed", "No"),
}
EMPLOYMENT, EDUCATION = PROF.get("employment", []), PROF.get("educationEntries", [])


def _yn_not(v):
    return {"yes": "No", "no": "Yes"}.get(str(v).strip().lower(), None)


DECLINE_RX = re.compile(r"decline|not wish|don.t wish|prefer not|choose not|not to (say|answer|disclose|identify)", re.I)


def _eeo(key, f, yes_no=False):
    """Pick the option for an EEO item. The profile value is a string or a
    list of preferred option names (tried in order, anchored at the start so
    "Male" never matches "Female"); "decline" picks the decline option."""
    want = EEO.get(key)
    if want is None:
        return None
    opts = f.get("options") or []
    wants = want if isinstance(want, list) else [want]
    if any(str(w).lower() == "decline" for w in wants):
        return next((o for o in opts if DECLINE_RX.search(o)), None)
    if not opts:
        return str(wants[0])
    for w in wants:
        hit = next((o for o in opts if o.strip().lower() == str(w).strip().lower()), None) or \
            next((o for o in opts if re.match(rf"\s*{re.escape(str(w))}\b", o, re.I)), None)
        if hit:
            return hit
    return pick_option(str(wants[0]), opts) if yes_no else None


AFFIRM = re.compile(r"\byes\b|i (consent|agree|certify|acknowledge|authorize)|"
                    r"i am (currently )?(legally )?authorized|i will require|i have read", re.I)
DENY = re.compile(r"\bno\b|not|don't|do not|decline|neither", re.I)


def pick_option(value, options):
    """Map a canonical value onto one of a choice field's option strings."""
    if not options:
        return value  # typeahead / free combobox: type the value
    low = {o.lower().strip(): o for o in options}
    v = value.lower().strip()
    if v in low:
        return low[v]
    if v in ("yes", "no"):  # intent match (incl. full-sentence consent options)
        want_yes = v == "yes"
        for o in options:
            aff, den = bool(AFFIRM.search(o)), bool(DENY.search(o))
            if want_yes and aff and not den:
                return o
            if not want_yes and den and not aff:
                return o
    for o in options:  # substring either way
        ol = o.lower()
        if v and (v in ol or ol in v):
            return o
    return None


# (label regex, handler) — handler(field) returns the canonical value (pre
# option-mapping) or None. First match wins; order matters (specific first).
RULES = [
    # specific phrasings first: the broad rules below would otherwise grab them
    (r"pronounc", lambda f: None),                                     # model / leave to you
    (r"unable to relocate|only work remotely|cannot relocate|can't relocate", lambda f: _yn_not(P["relocate"])),
    (r"country (phone )?code|phone country|dial(l?ing)? code", lambda f: P["country"]),
    (r"phone extension|^ext(ension)?\.?$", lambda f: ""),
    (r"(what|which) city|city and state|city, state", lambda f: f"{P['city']}, {P['state']}"),
    # optional -> blank (Workday pre-suggests from the résumé); required -> one real cv.md skill
    (r"type to add skills|^skills$", lambda f: P["skill"] if f.get("required") else ""),
    (r"^first name|given name|legal first", lambda f: P["first"]),
    (r"^last name|family name|surname|legal last", lambda f: P["last"]),
    (r"preferred (first )?name|preferred name", lambda f: (P["preferred"] or P["first"]) if f.get("required") else P["preferred"]),
    (r"^(full |legal |candidate )?name$|your name", lambda f: P["full"]),
    (r"^e-?mail", lambda f: P["email"]),
    (r"phone (device )?type|is .*(a )?(mobile|landline)|mobile or landline|type of phone",
     lambda f: next((o for o in f.get("options") or [] if re.search(r"cell|mobile", o, re.I)), P["phone_type"])),
    (r"^(?=.{0,40}$).*\b(phone|mobile|cell|telephone|contact number)\b", lambda f: P["phone"]),
    (r"address line ?1|street address|^address$|^address line$", lambda f: P["addr1"]),
    (r"^(?=.{0,40}$).*(address line ?2|\b(apt|suite|unit)\b)", lambda f: ""),
    (r"^city$|^town$|city/town", lambda f: P["city"]),
    (r"location \(city\)|^current location$|^location$", lambda f: P["city"]),
    (r"^(?=.{0,40}$).*\b(state|province|region)\b", lambda f: P["state"]),
    (r"^(?=.{0,40}$).*\b(zip|postal code|post code)\b", lambda f: P["zip"]),
    (r"^country|country/region", lambda f: P["country"]),
    (r"linkedin", lambda f: P["linkedin"]),
    (r"github", lambda f: P["github"]),
    (r"website|portfolio|personal site", lambda f: P["website"]),
    (r"current company|current employer", lambda f: P["current_company"]),
    # work authorization / sponsorship / residency
    # "authorized ... WITHOUT sponsorship" is the inverse of "do you need sponsorship"
    (r"without (requiring |needing |the need for |any )?(visa |employer |future )?sponsorship|"
     r"without (the )?need (of|for) sponsorship", lambda f: _yn_not(P["sponsor"])),
    (r"require (visa )?sponsorship|need sponsorship|sponsorship (for|now)|require.*immigration|"
     r"require .{0,40}\bsponsor\b|\bsponsor (you|me)\b",
     lambda f: P["sponsor"]),
    (r"authorized to work|legally authorized|work authorization|right to work", lambda f: P["authorized"]),
    (r"currently reside|reside in the united states|located in the us", lambda f: P["reside"]),
    (r"visa status|immigration status|work status", lambda f: P["visa"]),
    (r"us person under itar|\bitar\b", lambda f: P["us_person"]),
    (r"security clearance|clearance", lambda f: P["clearance"]),
    # logistics / willingness
    (r"willing to relocate|open to relocation|relocate", lambda f: P["relocate"]),
    (r"relocation assistance", lambda f: ""),                        # optional
    (r"notice period|when (can|could) you start.*notice|availability to start", lambda f: _notice(f)),
    (r"type(s)? of schedule|schedule.*willing|which schedule|shift", lambda f: _schedule(f)),
    (r"willing to (relocate|work|travel|commute)|willingness|are you willing|"
     r"able to work onsite|work onsite|work in (our|the) .*office|in[- ]?office|"
     r"days per week in|commute to", lambda f: P["willing"]),
    (r"desired (annual |base )?salary|salary (expectation|requirement)|compensation expectation|"
     r"expected (salary|compensation)", lambda f: P["salary"]),
    # relatives / prior employment
    (r"relative|personal relation|blood relation|know anyone|family member.*employ", lambda f: P["relatives"]),
    (r"previously (worked|employed)|former employee|current or former|worked (for|at) (us|this)",
     lambda f: P["prev_employed"]),
    # EEO / self-identification
    (r"gender identity|^gender$|^sex$|\bgender\b", lambda f: _gender(f)),
    (r"\brace\b|racial|ethnic", lambda f: _race(f)),
    (r"hispanic|latino|latinx", lambda f: _eeo("hispanic", f, yes_no=True)),
    (r"veteran|protected veteran|military", lambda f: _veteran(f)),
    (r"disabilit|chronic condition", lambda f: _disability(f)),
    # a stand-alone "decline" checkbox: unticked unless the profile declines everything
    (r"^\s*(i do not want to answer|i don.t wish to answer|prefer not to (say|answer|disclose))\s*$",
     lambda f: bool(EEO.get("decline_all")) if f["kind"] == "checkbox" else None),
    (r"sexual orientation", lambda f: _eeo("sexual_orientation", f)),
    (r"transgender", lambda f: _eeo("transgender", f, yes_no=True)),
    (r"consent|i agree|i certify|i acknowledge|terms and conditions|privacy (policy|notice)|"
     r"do you consent", lambda f: _consent(f)),
    (r"text message|sms|opt[- ]?in|newsletter|talent (community|network)|marketing", lambda f: _optout(f)),
    (r"how did you (hear|find out|learn)|^\s*(lead |candidate )?source\b|referral source|where did you (find|hear)", lambda f: P["how_heard"]),
    (r"today'?s date|date of signature|signature date|^date\b", lambda f: _today(f)),
]
COMPILED = [(re.compile(rx, re.I), fn) for rx, fn in RULES]


def _notice(f):
    for o in f.get("options") or []:
        if re.search(r"less than|immediate|0|1 ?week|two week|< ?30", o, re.I):
            return o
    return (f.get("options") or [None])[0]


def _schedule(f):
    for pat in (r"hybrid", r"full[- ]?time", r"standard", r"any|flexible|all"):
        for o in f.get("options") or []:
            if re.search(pat, o, re.I):
                return o
    return None


def _gender(f):
    return _eeo("gender", f)


def _race(f):
    """Race/ethnicity. A Hispanic/Latino yes-no labelled "ethnicity" gets the
    hispanic answer; otherwise the preferred race names, start-anchored so a
    plain "Asian" never grabs "East Asian"."""
    label = f.get("label") or ""
    if f.get("options") and re.search(r"hispanic|latin", label, re.I) and not re.search(r"\brace\b|racial", label, re.I):
        return _eeo("hispanic", f, yes_no=True)
    return _eeo("race", f)


def _veteran(f):
    want = str(EEO.get("veteran", "No")).lower()
    opts = f.get("options") or []
    if want == "decline":
        return next((o for o in opts if DECLINE_RX.search(o)), None)
    if want == "no":
        return next((o for o in opts if re.search(r"not a (protected )?veteran|i am not", o, re.I)), "Not a protected veteran")
    return _eeo("veteran", f, yes_no=True)


def _disability(f):
    want = str(EEO.get("disability", "No")).lower()
    no_rx = r"^no,|do not have a disab|don't have a disab|have not had"
    if f["kind"] == "checkbox":  # one checkbox per answer (some Workday tenants): tick only the matching one
        if want == "no":
            return bool(re.search(no_rx, f["label"], re.I))
        if want == "decline":
            return bool(DECLINE_RX.search(f["label"]))
        return bool(re.search(r"^yes", f["label"], re.I))
    if want == "no":
        return next((o for o in f.get("options") or [] if re.search(no_rx.replace("^", ""), o, re.I)), "No")
    return _eeo("disability", f, yes_no=True)


def _consent(f):  # consent/acknowledgment boxes for this application
    return "Yes" if _S.get("agree_to_consents", True) else None


def _optout(f):
    if _S.get("marketing_opt_in"):
        return "Yes"
    if f["kind"] == "checkbox":
        return False
    for o in f.get("options") or []:
        if DENY.search(o):
            return o
    return "No"


def _today(f):
    d = dt.date.today()
    label = f["label"].lower()
    if "month" in label:
        return f"{d:%m}"
    if "day" in label:
        return f"{d:%d}"
    if "year" in label:
        return f"{d:%Y}"
    return f"{d:%m/%d/%Y}"


def resolve(field):
    """Deterministic value for `field`, or None to defer to the model."""
    label = (field.get("label") or "").strip()
    if not label:
        return None
    # Essays are never table-answered: a keyword anywhere in a long prompt ("open
    # source") once put "LinkedIn" into a required 200-word essay.
    if field.get("kind") == "textarea":
        return None
    for rx, fn in COMPILED:
        if rx.search(label):
            val = fn(field)
            if val in (None, ""):
                return val if val == "" else None
            kind, opts = field["kind"], field.get("options") or []
            if kind in ("select", "radio", "yesno", "wd_select") or (kind == "combobox" and opts):
                return pick_option(str(val), opts)
            if kind == "checkbox":
                if isinstance(val, bool):
                    return val
                v = str(val).strip().lower()
                if v in ("yes", "true", "agree") or v.startswith("i consent"):
                    return True
                if v in ("no", "false", "not a protected veteran"):
                    return False
                return None  # a specific value (e.g. "Asian") can't map to a lone checkbox -> defer
            if kind == "checkbox_group":
                picked = pick_option(str(val), opts)
                return [picked] if picked else None
            return str(val)
    return None
