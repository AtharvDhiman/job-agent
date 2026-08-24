"""Country/location resolution good enough for filtering, honest about limits.

We never claim a location we cannot parse: unknown stays empty rather than
guessing, because location drives work-authorization decisions.
"""

from __future__ import annotations

import re

from app.utils.text import fold

COUNTRY_ALIASES: dict[str, str] = {
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "u.s.": "US",
    "u.s.a.": "US",
    "us": "US",
    "america": "US",
    "united kingdom": "GB",
    "uk": "GB",
    "england": "GB",
    "scotland": "GB",
    "wales": "GB",
    "great britain": "GB",
    "northern ireland": "GB",
    "canada": "CA",
    "ca": "CA",
    "germany": "DE",
    "deutschland": "DE",
    "france": "FR",
    "spain": "ES",
    "espana": "ES",
    "portugal": "PT",
    "italy": "IT",
    "netherlands": "NL",
    "the netherlands": "NL",
    "holland": "NL",
    "belgium": "BE",
    "ireland": "IE",
    "poland": "PL",
    "sweden": "SE",
    "norway": "NO",
    "denmark": "DK",
    "finland": "FI",
    "switzerland": "CH",
    "austria": "AT",
    "czechia": "CZ",
    "czech republic": "CZ",
    "romania": "RO",
    "greece": "GR",
    "india": "IN",
    "singapore": "SG",
    "japan": "JP",
    "china": "CN",
    "hong kong": "HK",
    "south korea": "KR",
    "korea": "KR",
    "taiwan": "TW",
    "vietnam": "VN",
    "indonesia": "ID",
    "philippines": "PH",
    "malaysia": "MY",
    "thailand": "TH",
    "australia": "AU",
    "new zealand": "NZ",
    "brazil": "BR",
    "mexico": "MX",
    "argentina": "AR",
    "chile": "CL",
    "colombia": "CO",
    "south africa": "ZA",
    "nigeria": "NG",
    "kenya": "KE",
    "egypt": "EG",
    "israel": "IL",
    "united arab emirates": "AE",
    "uae": "AE",
    "saudi arabia": "SA",
    "turkey": "TR",
    "ukraine": "UA",
    "estonia": "EE",
    "latvia": "LV",
    "lithuania": "LT",
}

US_STATES = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
    "district of columbia": "DC",
}

MAJOR_CITIES = {
    "san francisco": "US",
    "new york": "US",
    "nyc": "US",
    "seattle": "US",
    "austin": "US",
    "boston": "US",
    "chicago": "US",
    "los angeles": "US",
    "denver": "US",
    "atlanta": "US",
    "london": "GB",
    "manchester": "GB",
    "edinburgh": "GB",
    "cambridge": "GB",
    "berlin": "DE",
    "munich": "DE",
    "hamburg": "DE",
    "paris": "FR",
    "lyon": "FR",
    "amsterdam": "NL",
    "rotterdam": "NL",
    "dublin": "IE",
    "madrid": "ES",
    "barcelona": "ES",
    "lisbon": "PT",
    "milan": "IT",
    "rome": "IT",
    "warsaw": "PL",
    "krakow": "PL",
    "stockholm": "SE",
    "oslo": "NO",
    "copenhagen": "DK",
    "helsinki": "FI",
    "zurich": "CH",
    "geneva": "CH",
    "vienna": "AT",
    "prague": "CZ",
    "bucharest": "RO",
    "toronto": "CA",
    "vancouver": "CA",
    "montreal": "CA",
    "ottawa": "CA",
    "bangalore": "IN",
    "bengaluru": "IN",
    "hyderabad": "IN",
    "mumbai": "IN",
    "delhi": "IN",
    "pune": "IN",
    "chennai": "IN",
    "gurgaon": "IN",
    "noida": "IN",
    "singapore": "SG",
    "tokyo": "JP",
    "seoul": "KR",
    "sydney": "AU",
    "melbourne": "AU",
    "auckland": "NZ",
    "sao paulo": "BR",
    "mexico city": "MX",
    "buenos aires": "AR",
    "tel aviv": "IL",
    "dubai": "AE",
    "cape town": "ZA",
    "johannesburg": "ZA",
    "lagos": "NG",
    "nairobi": "KE",
    "warszawa": "PL",
    "tallinn": "EE",
}

REGION_GROUPS = {
    "emea": {
        "GB",
        "DE",
        "FR",
        "NL",
        "IE",
        "ES",
        "PT",
        "IT",
        "PL",
        "SE",
        "NO",
        "DK",
        "FI",
        "CH",
        "AT",
        "CZ",
        "RO",
        "GR",
        "BE",
        "IL",
        "AE",
        "ZA",
        "NG",
        "KE",
        "EG",
        "TR",
        "UA",
        "EE",
        "LV",
        "LT",
        "SA",
    },
    "eu": {
        "DE",
        "FR",
        "NL",
        "IE",
        "ES",
        "PT",
        "IT",
        "PL",
        "SE",
        "DK",
        "FI",
        "AT",
        "BE",
        "CZ",
        "RO",
        "GR",
        "EE",
        "LV",
        "LT",
    },
    "apac": {"IN", "SG", "JP", "CN", "HK", "KR", "TW", "VN", "ID", "PH", "MY", "TH", "AU", "NZ"},
    "latam": {"BR", "MX", "AR", "CL", "CO"},
    "namer": {"US", "CA"},
    "north america": {"US", "CA"},
    "americas": {"US", "CA", "BR", "MX", "AR", "CL", "CO"},
}

_SPLIT = re.compile(r"[,/|;()\-]| and | or ")


def resolve_country(location: str | None) -> str:
    """Best-effort ISO-3166 alpha-2. Empty string when genuinely unknown."""
    if not location:
        return ""
    folded = fold(location)
    for token in reversed([t.strip() for t in _SPLIT.split(folded) if t.strip()]):
        if token in COUNTRY_ALIASES:
            return COUNTRY_ALIASES[token]
        if len(token) == 2 and token.upper() in set(COUNTRY_ALIASES.values()):
            return token.upper()
        if token in US_STATES or token.upper() in set(US_STATES.values()):
            return "US"
        if token in MAJOR_CITIES:
            return MAJOR_CITIES[token]
    for name, code in COUNTRY_ALIASES.items():
        if len(name) > 3 and re.search(rf"\b{re.escape(name)}\b", folded):
            return code
    return ""


def resolve_city(location: str | None) -> str:
    if not location:
        return ""
    parts = [p.strip() for p in _SPLIT.split(location) if p.strip()]
    for part in parts:
        if fold(part) in MAJOR_CITIES:
            return part
    return parts[0][:160] if parts else ""


def expand_country_preferences(values: list[str] | None) -> set[str]:
    """Turn ['US', 'EMEA', 'Germany'] into a flat set of ISO codes."""
    out: set[str] = set()
    for value in values or []:
        folded = fold(value)
        if folded in REGION_GROUPS:
            out |= REGION_GROUPS[folded]
            continue
        if folded in ("anywhere", "worldwide", "global", "any"):
            return set()  # empty set means "no country restriction"
        code = resolve_country(value) or (value.upper() if len(value) == 2 else "")
        if code:
            out.add(code)
    return out
