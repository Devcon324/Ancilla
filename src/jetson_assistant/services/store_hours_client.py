"""
Store hours via OpenStreetMap (default) or Google Places (optional).

OSM: Nominatim + Overpass + opening_hours tag — no API key.
Google: set GOOGLE_PLACES_API_KEY in .env for Places API (New) text search.
"""
import logging
import math
import re
import time
from datetime import datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import requests
from opening_hours import OpeningHours

from jetson_assistant.config import (
    TIMEZONE,
    TIME_FORMAT,
    WEATHER_LAT,
    WEATHER_LON,
    STORE_SEARCH_RADIUS_KM,
    GOOGLE_PLACES_API_KEY,
    LOCATION_CITY,
    LOCATION_PROVINCE_OR_STATE,
    LOCATION_COUNTRY,
    has_default_location,
    default_location_geocode_query,
)
from jetson_assistant.services import weather_client
from jetson_assistant.services.http import SESSION
from jetson_assistant.log_fmt import info as log_line, warning as log_warn

log = logging.getLogger("assistant.store")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
GOOGLE_PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
OVERPASS_ENDPOINTS = (
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
)
USER_AGENT = "jarvis-voice-assistant/0.1 (local hobby project)"
HTTP_TIMEOUT = 10
OVERPASS_QUERY_TIMEOUT = 8

NO_DEFAULT_LOCATION_MSG = (
    "You don't have a default location set. "
    "Add one in defaults.json so I can find nearby stores."
)

_LOCATION_SPLIT = re.compile(r"\s+(?:in|at|near)\s+", re.IGNORECASE)
# Shared with intent_router so store text is cleaned identically on both sides.
FILLER_RE = re.compile(
    r"\b(today|tonight|tomorrow|right now|near me|currently)\b|^the\s+",
    re.IGNORECASE,
)

# Expand Overpass radius only when a quick query returns zero results
_RADIUS_MULTIPLIERS = (1.0, 2.5, 5.0)


def is_store_error(message: str) -> bool:
    """True for store_client messages that should be spoken as-is."""
    return (
        message == NO_DEFAULT_LOCATION_MSG
        or message.startswith("I couldn't find")
        or message.startswith("I couldn't resolve")
        or message.startswith("I found ")
        or message.startswith("Store lookup")
    )


def _clean(text: str) -> str:
    text = text.strip().rstrip("?.!,")
    return FILLER_RE.sub("", text).strip()


def _split_store_location(store_query: str) -> tuple[str, str | None]:
    """Split 'Eaton Center in Toronto, Ontario' -> ('Eaton Center', 'Toronto, Ontario')."""
    store_query = _clean(store_query)
    match = _LOCATION_SPLIT.search(store_query)
    if not match:
        return store_query, None
    store = store_query[: match.start()].strip()
    place = _clean(store_query[match.end() :])
    if store and place:
        return store, place
    return store_query, None


def _home_coords() -> tuple[float, float] | None:
    if WEATHER_LAT is not None and WEATHER_LON is not None:
        return WEATHER_LAT, WEATHER_LON
    if not has_default_location():
        return None
    query = default_location_geocode_query()
    if not query:
        return None
    geo = weather_client.geocode_place(query)
    if geo:
        return geo[0], geo[1]
    return None


def _resolve_search_coords(
    store_name: str, place: str | None
) -> tuple[float, float, str] | None:
    """Return (lat, lon, location_label) for the search center."""
    if place:
        home = _home_coords()
        if home:
            geo = weather_client.geocode_place_near(
                place, home[0], home[1], LOCATION_COUNTRY
            )
        else:
            geo = weather_client.geocode_place(place)
        if geo:
            return geo[0], geo[1], geo[2]
        return None
    coords = _home_coords()
    if coords:
        return coords[0], coords[1], "your location"
    return None


def _location_from_address(address: dict) -> str | None:
    """Build 'City, Province' from a Nominatim address block."""
    if not address:
        return None
    city = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("hamlet")
    )
    province = address.get("state") or address.get("province")
    if city and province:
        return f"{city}, {province}"
    if city:
        return city
    if province:
        return province
    return None


def _requested_city(place_label: str) -> str | None:
    if place_label == "your location":
        return None
    return place_label.split(",")[0].strip()


def _address_locality(address: dict) -> str:
    parts: list[str] = []
    for key in ("city", "town", "village", "hamlet", "municipality"):
        value = address.get(key)
        if value:
            parts.append(str(value).lower())
    return " ".join(parts)


def _matches_requested_city(
    requested_city: str,
    address: dict,
    location: str | None,
) -> bool:
    req = requested_city.lower()
    locality = _address_locality(address)
    if req in locality or any(req in part or part in req for part in locality.split()):
        return True
    if location and req in location.lower():
        return True
    return False


def _location_from_overpass_tags(tags: dict) -> str | None:
    city = tags.get("addr:city") or tags.get("addr:town") or tags.get("addr:village")
    province = tags.get("addr:province") or tags.get("addr:state")
    if city and province:
        return f"{city}, {province}"
    if city:
        return city
    if province:
        return province
    return None


def _reverse_geocode_location(lat: float, lon: float) -> str | None:
    return _location_from_address(_reverse_geocode_address(lat, lon))


def _labeled_store_name(name: str, location: str | None) -> str:
    if location:
        return f"{name} in {location}"
    return name


def _resolve_store_location(hit: dict) -> str | None:
    location = hit.get("location")
    if location:
        return location
    lat, lon = hit.get("lat"), hit.get("lon")
    if lat is not None and lon is not None:
        return _reverse_geocode_location(lat, lon)
    return None


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return weather_client.haversine_km(lat1, lon1, lat2, lon2) * 1000


def _element_coords(element: dict) -> tuple[float, float] | None:
    if "lat" in element and "lon" in element:
        return element["lat"], element["lon"]
    center = element.get("center")
    if center and "lat" in center and "lon" in center:
        return center["lat"], center["lon"]
    return None


def _escape_overpass_regex(name: str) -> str:
    return re.sub(r'([\\.*+?^${}()|[\]])', r"\\\1", name.strip())


def _viewbox(lat: float, lon: float, radius_km: float) -> str:
    """Nominatim viewbox: left, top, right, bottom (lon/lat)."""
    lat_delta = radius_km / 111.0
    lon_delta = radius_km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
    left = lon - lon_delta
    right = lon + lon_delta
    top = lat + lat_delta
    bottom = lat - lat_delta
    return f"{left},{top},{right},{bottom}"


def _search_radius_km(place_label: str) -> float:
    """How far from the search center to look for stores."""
    if place_label == "your location":
        return max(50.0, STORE_SEARCH_RADIUS_KM * 5.0)
    return max(15.0, STORE_SEARCH_RADIUS_KM * 1.5)


def _name_matches(store_lower: str, name: str | None, display: str) -> bool:
    name_l = (name or "").lower()
    display_l = display.lower()
    return (
        store_lower in name_l
        or store_lower in display_l
        or any(w in display_l for w in store_lower.split() if len(w) > 3)
    )


def _reverse_geocode_address(lat: float, lon: float) -> dict:
    try:
        response = SESSION.get(
            NOMINATIM_REVERSE_URL,
            params={"lat": lat, "lon": lon, "format": "json", "addressdetails": 1},
            headers={"User-Agent": USER_AGENT},
            timeout=HTTP_TIMEOUT,
        )
        response.raise_for_status()
        return response.json().get("address") or {}
    except requests.RequestException:
        return {}


@lru_cache(maxsize=8)
def _home_area_places_cached(lat_key: float, lon_key: float) -> tuple[str, ...]:
    """Home doesn't move, so its area names (and the reverse-geocode HTTP call)
    are memoized per rounded coordinate."""
    places: list[str] = []
    if LOCATION_CITY and LOCATION_PROVINCE_OR_STATE:
        places.append(f"{LOCATION_CITY}, {LOCATION_PROVINCE_OR_STATE}")
    elif LOCATION_CITY:
        places.append(LOCATION_CITY)

    addr = _reverse_geocode_address(lat_key, lon_key)
    province = addr.get("state") or LOCATION_PROVINCE_OR_STATE or ""
    for key in ("town", "city", "municipality", "county"):
        name = addr.get(key)
        if not name:
            continue
        label = f"{name}, {province}" if province else name
        if label not in places:
            places.append(label)

    return tuple(places)


def _home_area_places(lat: float, lon: float) -> list[str]:
    """Place names to bias store search near home (~110m rounding for cache hits)."""
    return list(_home_area_places_cached(round(lat, 3), round(lon, 3)))


def _pick_nearest_hit(
    results: list[dict],
    store_lower: str,
    lat: float,
    lon: float,
    max_dist_km: float,
    *,
    prefer_scheduled_hours: bool = False,
    required_city: str | None = None,
) -> dict | None:
    """Pick the nearest name-matched result within max_dist_km; prefer closest that has hours."""
    candidates: list[tuple[float, dict]] = []
    max_dist_m = max_dist_km * 1000

    for hit in results:
        hit_lat = float(hit.get("lat", 0))
        hit_lon = float(hit.get("lon", 0))
        dist = _haversine_m(lat, lon, hit_lat, hit_lon)
        if dist > max_dist_m:
            continue
        display = hit.get("display_name", hit.get("name", ""))
        name = hit.get("name") or display.split(",")[0]
        if not _name_matches(store_lower, name, display):
            continue
        address = hit.get("address") or {}
        location = _location_from_address(address)
        if required_city and not _matches_requested_city(required_city, address, location):
            continue
        extratags = hit.get("extratags") or {}
        entry = {
            "name": name or store_lower,
            "opening_hours": extratags.get("opening_hours"),
            "lat": hit_lat,
            "lon": hit_lon,
            "location": location,
            "distance_km": dist / 1000,
        }
        candidates.append((dist, entry))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])
    with_hours = [entry for _, entry in candidates if entry.get("opening_hours")]
    if prefer_scheduled_hours and with_hours:
        scheduled = [
            e for e in with_hours if not _is_always_open(e["opening_hours"])
        ]
        if scheduled:
            return scheduled[0]
    if with_hours:
        return with_hours[0]
    return candidates[0][1]


def _nominatim_queries(store_name: str, place_label: str, lat: float, lon: float) -> list[str]:
    """Build search strings, including British 'centre' and city-first orderings."""
    centre = (
        re.sub(r"center", "centre", store_name, flags=re.I)
        if re.search(r"center", store_name, re.I)
        else None
    )

    if place_label == "your location":
        queries = [store_name]
        if centre:
            queries.append(centre)
        for place in _home_area_places(lat, lon):
            queries.append(f"{store_name}, {place}")
            if centre:
                queries.append(f"{centre}, {place}")
        if LOCATION_PROVINCE_OR_STATE:
            queries.append(f"{store_name}, {LOCATION_PROVINCE_OR_STATE}")
    else:
        city = place_label.split(",")[0].strip()
        queries = [
            f"{store_name}, {place_label}",
            f"{city} {store_name}",
            f"{store_name}, {city}",
        ]
        if centre:
            queries.extend([
                f"{centre}, {place_label}",
                f"{city} {centre}",
            ])
        queries.append(f"{store_name} {city}")

    seen: set[str] = set()
    unique: list[str] = []
    for q in queries:
        key = q.lower()
        if key not in seen:
            seen.add(key)
            unique.append(q)
    # The search loop stops as soon as a viewbox-bounded query yields a match
    # with hours (or a city match), so the common case is 1-2 requests; the cap
    # just bounds the worst case.
    return unique[:5]


def _nominatim_search(
    store_name: str,
    lat: float,
    lon: float,
    place_label: str,
    *,
    prefer_scheduled_hours: bool = False,
) -> dict | None:
    """Fast text search near lat/lon. Returns {name, opening_hours, lat, lon} or None."""
    store_lower = store_name.lower()
    radius_km = _search_radius_km(place_label)
    viewbox = _viewbox(lat, lon, radius_km)
    required_city = _requested_city(place_label)
    best: dict | None = None

    for query in _nominatim_queries(store_name, place_label, lat, lon):
        log_line(log, "Store", f"Nominatim {query!r} within {radius_km:.0f}km of {place_label}")
        t0 = time.perf_counter()
        try:
            response = SESSION.get(
                NOMINATIM_URL,
                params={
                    "q": query,
                    "format": "json",
                    "limit": 50,
                    "extratags": 1,
                    "addressdetails": 1,
                    "viewbox": viewbox,
                    "bounded": 1,
                },
                headers={"User-Agent": USER_AGENT},
                timeout=HTTP_TIMEOUT,
            )
            response.raise_for_status()
            results = response.json()
        except requests.RequestException as exc:
            log_warn(log, "Store", f"Nominatim failed: {exc}")
            continue

        log_line(
            log, "Store",
            f"Nominatim {len(results)} hit(s) for {query!r} ({time.perf_counter() - t0:.2f}s)",
        )
        if not results:
            continue

        picked = _pick_nearest_hit(
            results, store_lower, lat, lon, radius_km,
            prefer_scheduled_hours=prefer_scheduled_hours,
            required_city=required_city,
        )
        if picked and (
            best is None or picked["distance_km"] < best["distance_km"]
        ):
            best = picked
            if best.get("opening_hours") or required_city:
                break

    if best:
        loc = best.get("location") or "unknown"
        log_line(
            log, "Store",
            f"picked {best['name']} in {loc} ({best['distance_km']:.0f} km away)",
        )
    return best


def _overpass_search(
    name: str, lat: float, lon: float, radius_m: int, endpoint: str
) -> list[dict]:
    pattern = _escape_overpass_regex(name)
    query = f"""[out:json][timeout:{OVERPASS_QUERY_TIMEOUT}];
nwr["name"~"{pattern}",i](around:{radius_m},{lat},{lon});
out center tags 20;"""
    response = SESSION.post(
        endpoint,
        data={"data": query},
        headers={"User-Agent": USER_AGENT},
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    return response.json().get("elements", [])


def _overpass_find_store(name: str, lat: float, lon: float, place_label: str) -> dict | None:
    """Overpass fallback — one radius step at a time; stop if all mirrors time out."""
    base_radius_m = int(STORE_SEARCH_RADIUS_KM * 1000)
    best_with_hours: tuple[float, dict] | None = None
    best_any: tuple[float, dict] | None = None

    for mult in _RADIUS_MULTIPLIERS:
        radius_m = max(int(base_radius_m * mult), 2000)
        log_line(
            log, "Store",
            f"Overpass {name!r} within {radius_m // 1000}km of {place_label}",
        )
        t0 = time.perf_counter()
        elements: list[dict] | None = None
        all_timed_out = True
        for endpoint in OVERPASS_ENDPOINTS:
            try:
                elements = _overpass_search(name, lat, lon, radius_m, endpoint)
                all_timed_out = False
                log_line(
                    log, "Store",
                    f"Overpass {len(elements)} hit(s) ({time.perf_counter() - t0:.2f}s)",
                )
                break
            except requests.RequestException as exc:
                log_warn(log, "Store", f"Overpass timeout ({endpoint.split('//')[1].split('/')[0]})")
                continue

        if all_timed_out:
            log_warn(log, "Store", "Overpass mirrors timed out")
            break

        for element in elements or []:
            coords = _element_coords(element)
            if not coords:
                continue
            dist = _haversine_m(lat, lon, coords[0], coords[1])
            tags = element.get("tags") or {}
            if not tags.get("name"):
                continue
            entry = {
                "name": tags["name"],
                "opening_hours": tags.get("opening_hours"),
                "lat": coords[0],
                "lon": coords[1],
                "location": _location_from_overpass_tags(tags),
            }
            if best_any is None or dist < best_any[0]:
                best_any = (dist, entry)
            if entry["opening_hours"]:
                if best_with_hours is None or dist < best_with_hours[0]:
                    best_with_hours = (dist, entry)

        if best_with_hours or best_any:
            break

    if best_with_hours:
        return best_with_hours[1]
    return best_any[1] if best_any else None


def _address_from_google_components(components: list[dict]) -> dict:
    address: dict[str, str] = {}
    type_map = {
        "locality": "city",
        "postal_town": "town",
        "administrative_area_level_3": "town",
        "sublocality": "town",
    }
    for comp in components:
        text = comp.get("longText") or comp.get("shortText") or ""
        for gtype, key in type_map.items():
            if gtype in (comp.get("types") or []):
                address[key] = text
    return address


def _location_from_google_components(components: list[dict]) -> str | None:
    city = province = None
    for comp in components:
        types = comp.get("types") or []
        text = comp.get("longText") or comp.get("shortText") or ""
        if not text:
            continue
        if "locality" in types:
            city = text
        elif "administrative_area_level_1" in types:
            province = text
    if city and province:
        return f"{city}, {province}"
    return city or province


def _parse_google_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            ZoneInfo(TIMEZONE)
        )
    except ValueError:
        return None


def _google_hours_from_place(place: dict) -> dict:
    hours = place.get("currentOpeningHours") or {}
    weekday = hours.get("weekdayDescriptions") or []
    is_24h = any(
        "24 hour" in desc.lower() or "open 24" in desc.lower()
        for desc in weekday
    )
    return {
        "open_now": hours.get("openNow"),
        "next_open": _parse_google_timestamp(hours.get("nextOpenTime")),
        "next_close": _parse_google_timestamp(hours.get("nextCloseTime")),
        "is_24h": is_24h,
    }


def _pick_nearest_google_place(
    places: list[dict],
    store_lower: str,
    lat: float,
    lon: float,
    max_dist_km: float,
    *,
    required_city: str | None = None,
) -> dict | None:
    candidates: list[tuple[float, dict]] = []
    max_dist_m = max_dist_km * 1000

    for place in places:
        loc = place.get("location") or {}
        hit_lat = loc.get("latitude")
        hit_lon = loc.get("longitude")
        if hit_lat is None or hit_lon is None:
            continue
        dist = _haversine_m(lat, lon, hit_lat, hit_lon)
        if dist > max_dist_m:
            continue
        name = (place.get("displayName") or {}).get("text", "")
        if not _name_matches(store_lower, name, name):
            continue
        components = place.get("addressComponents") or []
        location = _location_from_google_components(components)
        if required_city and not _matches_requested_city(
            required_city, _address_from_google_components(components), location
        ):
            continue
        gh = _google_hours_from_place(place)
        entry = {
            "name": name or store_lower,
            "lat": hit_lat,
            "lon": hit_lon,
            "location": location,
            "distance_km": dist / 1000,
            "source": "google",
            "google_hours": gh,
        }
        candidates.append((dist, entry))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])
    with_hours = [
        entry for _, entry in candidates
        if entry["google_hours"].get("open_now") is not None
    ]
    if with_hours:
        return with_hours[0]
    return candidates[0][1]


def _google_find_store(
    store_name: str,
    lat: float,
    lon: float,
    place_label: str,
) -> dict | None:
    """Google Places text search biased to lat/lon. Returns normalized hit dict."""
    radius_km = _search_radius_km(place_label)
    radius_m = min(int(radius_km * 1000), 50_000)
    query = store_name if place_label == "your location" else f"{store_name} {place_label}"

    log_line(log, "Store", f"Google Places {query!r} within {radius_km:.0f}km of {place_label}")
    t0 = time.perf_counter()
    try:
        response = SESSION.post(
            GOOGLE_PLACES_SEARCH_URL,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": GOOGLE_PLACES_API_KEY,
                "X-Goog-FieldMask": (
                    "places.displayName,places.location,places.currentOpeningHours,"
                    "places.addressComponents"
                ),
            },
            json={
                "textQuery": query,
                "locationBias": {
                    "circle": {
                        "center": {"latitude": lat, "longitude": lon},
                        "radius": radius_m,
                    }
                },
                "maxResultCount": 20,
                "rankPreference": "DISTANCE",
            },
            timeout=HTTP_TIMEOUT,
        )
        response.raise_for_status()
        places = response.json().get("places", [])
    except requests.RequestException as exc:
        log_warn(log, "Store", f"Google Places failed: {exc}")
        return None

    log_line(
        log, "Store",
        f"Google Places {len(places)} hit(s) for {query!r} ({time.perf_counter() - t0:.2f}s)",
    )
    if not places:
        return None

    picked = _pick_nearest_google_place(
        places, store_name.lower(), lat, lon, radius_km,
        required_city=_requested_city(place_label),
    )
    if picked:
        loc = picked.get("location") or "unknown"
        log_line(
            log, "Store",
            f"picked {picked['name']} in {loc} ({picked['distance_km']:.0f} km away)",
        )
    return picked


def _reply_closing_google(name: str, gh: dict, now: datetime) -> str:
    open_now = gh.get("open_now")
    if open_now is True:
        if gh.get("is_24h"):
            return f"{name} is open 24 hours — it doesn't close."
        nxt = gh.get("next_close")
        if nxt is None:
            return f"{name} is open right now, and I couldn't determine when it closes."
        if _day_phrase(nxt, now) == "today":
            return f"{name} closes at {_format_clock(nxt)} today."
        return f"{name} closes {_when_phrase(nxt, now)}."
    if open_now is False:
        nxt = gh.get("next_open")
        if nxt is None:
            return f"{name} is closed right now."
        return f"{name} is already closed. It reopens {_when_phrase(nxt, now)}."
    return f"I found {name}, but don't have hours info for it right now."


def _reply_opening_google(name: str, gh: dict, now: datetime) -> str:
    open_now = gh.get("open_now")
    if open_now is False:
        nxt = gh.get("next_open")
        if nxt is None:
            return f"{name} is closed right now."
        if _day_phrase(nxt, now) == "today":
            return f"{name} opens at {_format_clock(nxt)} today."
        return f"{name} opens {_when_phrase(nxt, now)}."
    if open_now is True:
        nxt = gh.get("next_close")
        if nxt is None:
            return f"{name} is open right now."
        return f"{name} is already open, until {_format_clock(nxt)}."
    return f"I found {name}, but don't have hours info for it right now."


def _reply_status_google(name: str, gh: dict, now: datetime) -> str:
    open_now = gh.get("open_now")
    if open_now is True:
        if gh.get("is_24h"):
            return f"{name} is open 24 hours."
        nxt = gh.get("next_close")
        if nxt is None:
            return f"{name} is open right now."
        return f"{name} is open right now, until {_format_clock(nxt)}."
    if open_now is False:
        nxt = gh.get("next_open")
        if nxt is None:
            return f"{name} is closed right now."
        return f"{name} is closed right now. It opens {_when_phrase(nxt, now)}."
        return f"I found {name}, but don't have hours info for it right now."


def _find_store(
    store_name: str,
    lat: float,
    lon: float,
    place_label: str,
    *,
    explicit_place: bool = False,
    intent: str = "status",
) -> tuple[dict | None, str | None]:
    """Return store hit dict or None. Google when API key is set, else OSM."""
    if GOOGLE_PLACES_API_KEY:
        google_hit = _google_find_store(store_name, lat, lon, place_label)
        if google_hit and google_hit.get("google_hours", {}).get("open_now") is not None:
            return google_hit, None
        if google_hit:
            log_line(log, "Store", "Google hit has no hours; trying OpenStreetMap")

    prefer_scheduled = intent in ("closing", "opening")
    hit = _nominatim_search(
        store_name, lat, lon, place_label,
        prefer_scheduled_hours=prefer_scheduled,
    )
    if hit and hit.get("opening_hours"):
        return hit, None

    # Overpass fallback only for home-vicinity searches (explicit places skip it)
    if not explicit_place and (hit is None or not hit.get("opening_hours")):
        overpass_hit = _overpass_find_store(store_name, lat, lon, place_label)
        if overpass_hit and overpass_hit.get("opening_hours"):
            return overpass_hit, None
        if overpass_hit:
            hit = overpass_hit

    return hit, None


def _format_clock(dt: datetime) -> str:
    if TIME_FORMAT == "24h":
        return dt.strftime("%H:%M")
    hour = dt.strftime("%I").lstrip("0") or "12"
    minute = dt.strftime("%M")
    ampm = dt.strftime("%p")
    if minute == "00":
        return f"{hour} {ampm}"
    return f"{hour}:{minute} {ampm}"


def _day_phrase(when: datetime, now: datetime) -> str:
    when_date = when.date()
    now_date = now.date()
    if when_date == now_date:
        return "today"
    if when_date == now_date + timedelta(days=1):
        return "tomorrow"
    return when.strftime("%A")


def _when_phrase(when: datetime, now: datetime) -> str:
    day = _day_phrase(when, now)
    clock = _format_clock(when)
    return f"{day} at {clock}"


def _is_always_open(hours_expr: str) -> bool:
    normalized = hours_expr.strip().lower().replace(" ", "")
    return normalized == "24/7" or normalized.startswith("24/7")


def _parse_hours(expr: str) -> OpeningHours | None:
    try:
        return OpeningHours(expr, timezone=ZoneInfo(TIMEZONE))
    except Exception as exc:
        log_warn(log, "Store", f"bad opening_hours {expr!r}: {exc}")
        return None


def _reply_closing(name: str, oh: OpeningHours, now: datetime, hours_expr: str) -> str:
    if oh.is_open(now):
        nxt = oh.next_change(now)
        if nxt is None:
            if _is_always_open(hours_expr):
                return f"{name} is open 24 hours — it doesn't close."
            return f"{name} is open right now, and I couldn't determine when it closes."
        if _day_phrase(nxt, now) == "today":
            return f"{name} closes at {_format_clock(nxt)} today."
        return f"{name} closes {_when_phrase(nxt, now)}."
    nxt = oh.next_change(now)
    if nxt is None:
        return f"{name} is closed right now."
    return f"{name} is already closed. It reopens {_when_phrase(nxt, now)}."


def _reply_opening(name: str, oh: OpeningHours, now: datetime) -> str:
    if oh.is_closed(now):
        nxt = oh.next_change(now)
        if nxt is None:
            return f"{name} is closed right now."
        if _day_phrase(nxt, now) == "today":
            return f"{name} opens at {_format_clock(nxt)} today."
        return f"{name} opens {_when_phrase(nxt, now)}."
    nxt = oh.next_change(now)
    if nxt is None:
        return f"{name} is open right now."
    return f"{name} is already open, until {_format_clock(nxt)}."


def _reply_status(name: str, oh: OpeningHours, now: datetime, hours_expr: str) -> str:
    if oh.is_open(now):
        if _is_always_open(hours_expr):
            return f"{name} is open 24 hours."
        nxt = oh.next_change(now)
        if nxt is None:
            return f"{name} is open right now."
        return f"{name} is open right now, until {_format_clock(nxt)}."
    nxt = oh.next_change(now)
    if nxt is None:
        return f"{name} is closed right now."
    return f"{name} is closed right now. It opens {_when_phrase(nxt, now)}."


def store_hours(store_name: str, intent: str = "status") -> str:
    """
    Look up store hours for the nearest match (Google Places or OpenStreetMap).
    Supports 'Store in City' in the store name (e.g. 'Eaton Center in Toronto').
    intent: closing | opening | status | hours
    """
    store_name = store_name.strip().rstrip("?.!")
    if not store_name:
        return "I didn't catch which store you meant."

    store, place = _split_store_location(store_name)
    coords = _resolve_search_coords(store, place)
    if coords is None:
        if place:
            return f"I couldn't resolve the location '{place}'."
        return NO_DEFAULT_LOCATION_MSG

    lat, lon, place_label = coords
    hit, _ = _find_store(
        store, lat, lon, place_label,
        explicit_place=bool(place),
        intent=intent.lower(),
    )
    if hit is None:
        if place:
            city = _requested_city(place_label) or place
            return f"I couldn't find a place matching '{store}' in {city}."
        return f"I couldn't find a place matching '{store}' near your location."

    display_name = hit["name"]
    location = _resolve_store_location(hit)
    labeled_name = _labeled_store_name(display_name, location)
    now = datetime.now(ZoneInfo(TIMEZONE))
    intent = intent.lower()

    if hit.get("source") == "google":
        gh = hit.get("google_hours") or {}
        if intent == "closing":
            return _reply_closing_google(labeled_name, gh, now)
        if intent == "opening":
            return _reply_opening_google(labeled_name, gh, now)
        return _reply_status_google(labeled_name, gh, now)

    hours_expr = hit.get("opening_hours")
    if not hours_expr:
        return (
            f"I found {labeled_name}, but OpenStreetMap doesn't have hours for it yet."
        )

    oh = _parse_hours(hours_expr)
    if oh is None:
        return f"I found {labeled_name}, but couldn't read its hours."

    if intent == "closing":
        return _reply_closing(labeled_name, oh, now, hours_expr)
    if intent == "opening":
        return _reply_opening(labeled_name, oh, now)
    if intent == "hours":
        return _reply_status(labeled_name, oh, now, hours_expr)
    return _reply_status(labeled_name, oh, now, hours_expr)
