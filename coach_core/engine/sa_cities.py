"""
South African city lookup table for TRUEPACE weather integration.

Covers the 30 largest cities/towns by population.
Supports primary names, official alternatives, and common abbreviations.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class SACity:
    name: str           # primary display name
    latitude: float
    longitude: float
    province: str
    aliases: tuple[str, ...]   # alternative names / abbreviations


# ── Master city list ──────────────────────────────────────────────────────

SA_CITIES: list[SACity] = [
    SACity("Johannesburg",    -26.2041, 28.0473, "Gauteng",        ("jozi", "egoli", "joburg", "jhb", "johanesburg")),
    SACity("Cape Town",       -33.9249, 18.4241, "Western Cape",   ("kaapstad", "mother city", "ct", "cpt", "capetown")),
    SACity("Durban",          -29.8587, 31.0218, "KwaZulu-Natal",  ("ethekwini", "dbn")),
    SACity("Pretoria",        -25.7479, 28.2293, "Gauteng",        ("tshwane", "pta")),
    SACity("Gqeberha",        -33.9608, 25.6022, "Eastern Cape",   ("port elizabeth", "pe", "nelson mandela bay")),
    SACity("Soweto",          -26.2678, 27.8585, "Gauteng",        ("orlando",)),
    SACity("Bloemfontein",    -29.1167, 26.2167, "Free State",     ("mangaung", "bloem", "bfn")),
    SACity("East London",     -33.0153, 27.9116, "Eastern Cape",   ("emonti", "el", "east london")),
    SACity("Polokwane",       -23.8962, 29.4486, "Limpopo",        ("pietersburg", "pok")),
    SACity("Pietermaritzburg",-29.6006, 30.3794, "KwaZulu-Natal",  ("maritzburg", "umgungundlovu", "pmb")),
    SACity("Benoni",          -26.1885, 28.3207, "Gauteng",        ("ekurhuleni",)),
    SACity("Tembisa",         -26.0059, 28.2100, "Gauteng",        ()),
    SACity("Vereeniging",     -26.6736, 27.9319, "Gauteng",        ("vaal",)),
    SACity("Rustenburg",      -25.6676, 27.2421, "North West",     ()),
    SACity("Nelspruit",       -25.4660, 30.9707, "Mpumalanga",     ("mbombela",)),
    SACity("Kimberley",       -28.7300, 24.7620, "Northern Cape",  ("kim",)),
    SACity("Roodepoort",      -26.1625, 27.8725, "Gauteng",        ()),
    SACity("Boksburg",        -26.2120, 28.2597, "Gauteng",        ()),
    SACity("Krugersdorp",     -26.0940, 27.7753, "Gauteng",        ("mogale city",)),
    SACity("Newcastle",       -27.7577, 29.9319, "KwaZulu-Natal",  ()),
    SACity("Uitenhage",       -33.7652, 25.4008, "Eastern Cape",   ("kariega",)),
    SACity("George",          -33.9631, 22.4617, "Western Cape",   ()),
    SACity("Randburg",        -26.0929, 28.0012, "Gauteng",        ()),
    SACity("Brakpan",         -26.2363, 28.3696, "Gauteng",        ()),
    SACity("Witbank",         -25.8660, 29.2330, "Mpumalanga",     ("emalahleni",)),
    SACity("Richards Bay",    -28.7809, 32.0373, "KwaZulu-Natal",  ("richardsbay",)),
    SACity("Vanderbijlpark",  -26.7000, 27.8333, "Gauteng",        ("vaal triangle",)),
    SACity("Centurion",       -25.8598, 28.1865, "Gauteng",        ("verwoerdburg",)),
    SACity("Midrand",         -25.9991, 28.1286, "Gauteng",        ()),
    SACity("Springs",         -26.2500, 28.4500, "Gauteng",        ()),
]


# ── Lookup functions ──────────────────────────────────────────────────────

def find_city(query: str) -> SACity | None:
    """
    Find a city by name or alias. Case-insensitive, strips whitespace.
    Returns None if no match found.

    Matching order:
    1. Exact primary name match
    2. Exact alias match
    3. Primary name starts with query (prefix)
    4. Any alias starts with query (prefix)
    """
    q = query.strip().lower()
    if not q:
        return None

    # 1. Exact primary name
    for city in SA_CITIES:
        if city.name.lower() == q:
            return city

    # 2. Exact alias
    for city in SA_CITIES:
        if q in city.aliases:
            return city

    # 3. Primary prefix
    for city in SA_CITIES:
        if city.name.lower().startswith(q):
            return city

    # 4. Alias prefix
    for city in SA_CITIES:
        for alias in city.aliases:
            if alias.startswith(q):
                return city

    return None


def get_all_city_names() -> list[str]:
    """Return primary names of all 30 cities, sorted alphabetically."""
    return sorted(c.name for c in SA_CITIES)


def cities_by_province() -> dict[str, list[SACity]]:
    """Return cities grouped by province, sorted by name within each province."""
    result: dict[str, list[SACity]] = {}
    for city in SA_CITIES:
        result.setdefault(city.province, []).append(city)
    for prov in result:
        result[prov].sort(key=lambda c: c.name)
    return result


# ── Telegram keyboard helpers ─────────────────────────────────────────────

def city_keyboard_rows(cols: int = 2) -> list[list[str]]:
    """
    Return city names as button rows for a Telegram ReplyKeyboardMarkup.
    Sorted alphabetically, laid out in `cols` columns.
    """
    names = get_all_city_names()
    rows = []
    for i in range(0, len(names), cols):
        rows.append(names[i : i + cols])
    return rows
