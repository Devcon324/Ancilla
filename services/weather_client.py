"""
Open-Meteo requires no API key and no signup, which makes it the simplest
option for a hobby project like this. Swap in Environment Canada or
OpenWeatherMap later if you want more detail (alerts, radar, etc).
"""
import requests

from config import WEATHER_LAT, WEATHER_LON, WEATHER_LOCATION_NAME

_WMO_CODES = {
    0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog",
    51: "light drizzle", 61: "light rain", 63: "moderate rain", 65: "heavy rain",
    71: "light snow", 73: "moderate snow", 75: "heavy snow",
    80: "rain showers", 95: "thunderstorms",
}


def get_current_weather() -> str:
    resp = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": WEATHER_LAT,
            "longitude": WEATHER_LON,
            "current": "temperature_2m,weather_code,wind_speed_10m",
            "temperature_unit": "celsius",
        },
        timeout=5,
    )
    resp.raise_for_status()
    current = resp.json()["current"]

    temp = round(current["temperature_2m"])
    wind = round(current["wind_speed_10m"])
    condition = _WMO_CODES.get(current["weather_code"], "unclear conditions")

    return (
        f"It's currently {temp}\u00b0C and {condition} in {WEATHER_LOCATION_NAME}, "
        f"with wind around {wind} km/h."
    )
