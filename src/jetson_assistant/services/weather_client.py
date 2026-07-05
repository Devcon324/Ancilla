"""
Open-Meteo requires no API key and no signup, which makes it the simplest
option for a hobby project like this. Swap in Environment Canada or
OpenWeatherMap later if you want more detail (alerts, radar, etc).
"""
import math

from jetson_assistant.config import (
    WEATHER_LAT,
    WEATHER_LON,
    TEMPERATURE_UNIT,
    WIND_UNIT,
    has_default_location,
    location_weather_label,
    default_location_geocode_query,
)
from jetson_assistant.services.http import SESSION

NO_DEFAULT_LOCATION_MSG = (
    "You don't have a default location set. "
    "Add one in defaults.json, or ask for weather in a specific city."
)

# Canadian province / territory names and common abbreviations
_CA_REGIONS = {
    "on": "Ontario",
    "ontario": "Ontario",
    "bc": "British Columbia",
    "british columbia": "British Columbia",
    "ab": "Alberta",
    "alberta": "Alberta",
    "qc": "Quebec",
    "quebec": "Quebec",
    "mb": "Manitoba",
    "manitoba": "Manitoba",
    "sk": "Saskatchewan",
    "saskatchewan": "Saskatchewan",
    "ns": "Nova Scotia",
    "nova scotia": "Nova Scotia",
    "nb": "New Brunswick",
    "new brunswick": "New Brunswick",
    "nl": "Newfoundland and Labrador",
    "pe": "Prince Edward Island",
    "nt": "Northwest Territories",
    "yt": "Yukon",
    "nu": "Nunavut",
}

_WMO_CODES = {
    0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog",
    51: "light drizzle", 61: "light rain", 63: "moderate rain", 65: "heavy rain",
    71: "light snow", 73: "moderate snow", 75: "heavy snow",
    80: "rain showers", 95: "thunderstorms",
}

_WIND_API_UNITS = {
    "km/h": "kmh",
    "mph": "mph",
    "m/s": "ms",
    "kn": "kn",
}

_TEMP_SYMBOLS = {
    "celsius": "\u00b0C",
    "fahrenheit": "\u00b0F",
}


def _fetch_forecast(lat: float, lon: float, location_name: str) -> str:
    wind_api = _WIND_API_UNITS.get(WIND_UNIT, "kmh")
    temp_symbol = _TEMP_SYMBOLS.get(TEMPERATURE_UNIT, "\u00b0C")

    resp = SESSION.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,weather_code,wind_speed_10m",
            "temperature_unit": TEMPERATURE_UNIT,
            "wind_speed_unit": wind_api,
        },
        timeout=5,
    )
    resp.raise_for_status()
    current = resp.json()["current"]

    temp = round(current["temperature_2m"])
    wind = round(current["wind_speed_10m"])
    condition = _WMO_CODES.get(current["weather_code"], "unclear conditions")

    return (
        f"It's currently {temp}{temp_symbol} and {condition} in {location_name}, "
        f"with wind around {wind} {WIND_UNIT}."
    )


def _normalize_region_hint(hint: str) -> str:
    key = hint.strip().lower()
    return _CA_REGIONS.get(key, hint.strip())


def _region_matches(admin1: str | None, hint: str | None) -> bool:
    if not admin1 or not hint:
        return False
    normalized = _normalize_region_hint(hint)
    admin1_l = admin1.lower()
    hint_l = normalized.lower()
    return hint_l in admin1_l or admin1_l in hint_l


def _parse_place(place: str) -> tuple[str, str | None]:
    """Split 'Ottawa, Ontario' into city + region hint."""
    place = place.strip().rstrip("?.!")
    if "," in place:
        city, region = (part.strip() for part in place.split(",", 1))
        return city, region or None
    return place, None


def _geocode_results(query: str) -> list[dict]:
    resp = SESSION.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": query, "count": 100, "language": "en", "format": "json"},
        timeout=5,
    )
    resp.raise_for_status()
    return resp.json().get("results") or []


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km (shared by store lookup and geocoding)."""
    r = 6_371
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


_POPULATED_FEATURE_CODES = frozenset(
    {"PPL", "PPLA", "PPLC", "PPLA2", "PPLA3", "PPLA4", "PPLX"}
)


def _name_matches_query(hit_name: str | None, city: str) -> bool:
    if not hit_name or not city:
        return False
    return hit_name.strip().lower() == city.strip().lower()


def _country_matches(hit_country: str | None, home_country: str | None) -> bool:
    if not hit_country or not home_country:
        return False
    hit_l = hit_country.strip().lower()
    home_l = home_country.strip().lower()
    return hit_l == home_l or hit_l in home_l or home_l in hit_l


def _format_location_label(city: str, region: str | None, country: str | None) -> str:
    """Always include city, province/state, and country when available."""
    parts = [p for p in (city, region, country) if p]
    return ", ".join(parts)


def _hit_to_tuple(hit: dict) -> tuple[float, float, str]:
    location_name = _format_location_label(
        hit.get("name", ""),
        hit.get("admin1"),
        hit.get("country"),
    )
    return hit["latitude"], hit["longitude"], location_name


def _pick_result(results: list[dict], region_hint: str | None) -> dict | None:
    if not results:
        return None
    if region_hint:
        for hit in results:
            if _region_matches(hit.get("admin1"), region_hint):
                return hit
    return results[0]


def _pick_result_near(
    results: list[dict],
    city: str,
    region_hint: str | None,
    home_lat: float,
    home_lon: float,
    home_country: str | None,
) -> dict | None:
    """Pick the result closest to home, preferring region hint and home country."""
    if not results:
        return None

    candidates = results
    if region_hint:
        region_matched = [
            hit for hit in results if _region_matches(hit.get("admin1"), region_hint)
        ]
        if region_matched:
            candidates = region_matched

    if home_country:
        country_matched = [
            hit for hit in candidates if _country_matches(hit.get("country"), home_country)
        ]
        if country_matched:
            candidates = country_matched

    if city:
        exact_name = [hit for hit in candidates if _name_matches_query(hit.get("name"), city)]
        if exact_name:
            candidates = exact_name

    populated = [
        hit for hit in candidates if hit.get("feature_code") in _POPULATED_FEATURE_CODES
    ]
    if populated:
        candidates = populated

    return min(
        candidates,
        key=lambda hit: haversine_km(
            home_lat, home_lon, hit["latitude"], hit["longitude"]
        ),
    )


def _geocode(place: str) -> tuple[float, float, str] | None:
    city, region_hint = _parse_place(place)
    queries = []
    for candidate in (place, place.replace(",", " ").strip(), city):
        if candidate and candidate not in queries:
            queries.append(candidate)

    for query in queries:
        hit = _pick_result(_geocode_results(query), region_hint)
        if hit:
            return _hit_to_tuple(hit)
    return None


def geocode_place(place: str) -> tuple[float, float, str] | None:
    """Geocode a place name to (lat, lon, label). Prominence-based (for weather)."""
    return _geocode(place)


def geocode_place_near(
    place: str,
    home_lat: float,
    home_lon: float,
    home_country: str | None = None,
) -> tuple[float, float, str] | None:
    """Geocode a place name biased to the nearest match to home (for store lookup)."""
    city, region_hint = _parse_place(place)
    queries: list[str] = []
    for candidate in (place, place.replace(",", " ").strip(), city):
        if candidate and candidate not in queries:
            queries.append(candidate)

    for query in queries:
        hit = _pick_result_near(
            _geocode_results(query), city, region_hint, home_lat, home_lon, home_country
        )
        if hit:
            return _hit_to_tuple(hit)
    return None


def is_weather_error(message: str) -> bool:
    """True for weather_client messages that should be spoken as-is."""
    return (
        message == NO_DEFAULT_LOCATION_MSG
        or message.startswith("I couldn't find")
        or message.startswith("I couldn't resolve")
    )


def _default_weather() -> str:
    if not has_default_location():
        return NO_DEFAULT_LOCATION_MSG

    if WEATHER_LAT is not None and WEATHER_LON is not None:
        label = location_weather_label() or "your location"
        return _fetch_forecast(WEATHER_LAT, WEATHER_LON, label)

    query = default_location_geocode_query()
    geo = _geocode(query)
    if geo:
        lat, lon, location_name = geo
        return _fetch_forecast(lat, lon, location_name)
    return f"I couldn't resolve your default location '{query}'."


def get_weather_for(place: str | None = None) -> str:
    """Return current weather for a named place, or the configured default."""
    if place:
        place = place.strip().rstrip("?.!")
        if place:
            geo = _geocode(place)
            if geo:
                lat, lon, location_name = geo
                return _fetch_forecast(lat, lon, location_name)
            return f"I couldn't find a location matching '{place}'."
    return _default_weather()
