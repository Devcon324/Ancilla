"""
Looks up whether a named place is currently open via Google Places API
(Text Search + Place Details, "New" API). Needs GOOGLE_PLACES_API_KEY set
as an environment variable — get one from Google Cloud Console and
restrict it to the Places API.
"""
import requests

from config import GOOGLE_PLACES_API_KEY


def is_store_open(store_query: str) -> str:
    if not GOOGLE_PLACES_API_KEY:
        return "I don't have a Google Places API key configured yet."

    search_resp = requests.post(
        "https://places.googleapis.com/v1/places:searchText",
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": GOOGLE_PLACES_API_KEY,
            "X-Goog-FieldMask": "places.id,places.displayName,places.currentOpeningHours",
        },
        json={"textQuery": store_query},
        timeout=5,
    )
    search_resp.raise_for_status()
    places = search_resp.json().get("places", [])

    if not places:
        return f"I couldn't find a place matching '{store_query}'."

    place = places[0]
    name = place["displayName"]["text"]
    hours = place.get("currentOpeningHours", {})
    is_open = hours.get("openNow")

    if is_open is None:
        return f"I found {name}, but don't have hours info for it right now."
    return f"{name} is {'open' if is_open else 'closed'} right now."
