"""Small Open-Meteo client used by the Aurora Monitor weather panel.

Network calls are deliberately synchronous here so the UI can run ``refresh``
on its worker thread and keep ``snapshot`` cheap and non-blocking.
"""

from __future__ import annotations

import copy
import json
import math
import os
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
_REQUEST_TIMEOUT_SECONDS = 8
_MAX_RESPONSE_BYTES = 1_000_000
_REFRESH_INTERVAL_SECONDS = 15 * 60
_FAILURE_BACKOFF_SECONDS = 60
_MAX_OBSERVATION_AGE_SECONDS = 3 * 60 * 60
_MAX_FUTURE_CLOCK_SKEW_SECONDS = 5 * 60
_KNOWN_WEATHER_CODES = frozenset(
    (0, 1, 2, 3, 45, 48, 51, 53, 55, 56, 57, 61, 63, 65, 66, 67,
     71, 73, 75, 77, 80, 81, 82, 85, 86, 95, 96, 99)
)

_WEATHER_DESCRIPTIONS = {
    0: "Ясно",
    1: "Преимущественно ясно",
    2: "Переменная облачность",
    3: "Пасмурно",
    45: "Туман",
    48: "Изморозь и туман",
    51: "Лёгкая морось",
    53: "Морось",
    55: "Сильная морось",
    56: "Слабая ледяная морось",
    57: "Сильная ледяная морось",
    61: "Небольшой дождь",
    63: "Дождь",
    65: "Сильный дождь",
    66: "Слабый ледяной дождь",
    67: "Сильный ледяной дождь",
    71: "Небольшой снег",
    73: "Снег",
    75: "Сильный снег",
    77: "Снежная крупа",
    80: "Небольшой ливень",
    81: "Ливень",
    82: "Сильный ливень",
    85: "Небольшой снегопад",
    86: "Сильный снегопад",
    95: "Гроза",
    96: "Гроза с небольшим градом",
    99: "Гроза с сильным градом",
}


class WeatherService:
    """Fetch and cache weather for one explicitly selected location."""

    def __init__(self, cache_path: str | os.PathLike[str] | None):
        self.cache_path = Path(cache_path) if cache_path is not None else None
        self._lock = threading.RLock()
        self._clock = time.time
        self._location: dict[str, Any] | None = None
        self._snapshot = self._empty_snapshot("Город не настроен", error="Город не настроен")
        self._last_successful_fetch_at: float | None = None
        self._next_retry_at: float | None = None
        self._refreshing = False
        self._generation = 0
        self._load_cache()

    def set_location(self, city: str, lat: float, lon: float) -> None:
        """Save the selected city and coordinates; fetching is left to refresh."""
        city = str(city).strip()
        latitude = self._coordinate(lat, -90.0, 90.0, "latitude")
        longitude = self._coordinate(lon, -180.0, 180.0, "longitude")
        if not city:
            raise ValueError("city must not be empty")

        with self._lock:
            selected_location = {
                "city": city,
                "latitude": latitude,
                "longitude": longitude,
            }
            if self._location == selected_location:
                return
            self._generation += 1
            self._location = selected_location
            self._snapshot = self._empty_snapshot(city, error="Погода ещё не загружена")
            self._last_successful_fetch_at = None
            self._next_retry_at = None
            self._persist_locked()

    def search_city(self, query: str) -> list[dict[str, Any]]:
        """Find city candidates from Open-Meteo's public geocoding service."""
        query = str(query).strip()
        if not query:
            return []

        params = urlencode({"name": query, "count": 10, "language": "ru", "format": "json"})
        payload = self._get_json(f"{_GEOCODING_URL}?{params}")
        results = payload.get("results", []) if isinstance(payload, Mapping) else []
        if not isinstance(results, list):
            raise ValueError("Geocoding response has an invalid results field")

        cities: list[dict[str, Any]] = []
        for item in results:
            if not isinstance(item, Mapping):
                continue
            name = item.get("name")
            latitude = self._optional_number(item.get("latitude"))
            longitude = self._optional_number(item.get("longitude"))
            if not isinstance(name, str) or not name.strip() or latitude is None or longitude is None:
                continue
            if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                continue

            label_parts = [name.strip()]
            for field in ("admin1", "country"):
                value = item.get(field)
                if isinstance(value, str) and value.strip() and value.strip() not in label_parts:
                    label_parts.append(value.strip())
            cities.append(
                {
                    "name": name.strip(),
                    "label": ", ".join(label_parts),
                    "latitude": latitude,
                    "longitude": longitude,
                }
            )
        return cities

    def refresh(self) -> dict[str, Any]:
        """Refresh when due, preserving the last good values on network errors."""
        with self._lock:
            now = self._clock()
            if self._location is None:
                return copy.deepcopy(self._snapshot)
            if self._last_successful_fetch_at is not None:
                age = max(0.0, now - self._last_successful_fetch_at)
                if age < _REFRESH_INTERVAL_SECONDS:
                    self._snapshot["stale"] = self._observation_is_stale(
                        self._snapshot.get("updated_at"), now
                    )
                    return copy.deepcopy(self._snapshot)
            if self._next_retry_at is not None and now < self._next_retry_at:
                return copy.deepcopy(self._snapshot)
            if self._refreshing:
                return copy.deepcopy(self._snapshot)

            location = dict(self._location)
            generation = self._generation
            self._refreshing = True

        try:
            fresh_snapshot = self._fetch_snapshot(location)
        except Exception as exc:  # Keep a known-good snapshot when the service is unreachable.
            with self._lock:
                self._refreshing = False
                if generation == self._generation:
                    self._snapshot["stale"] = True
                    self._snapshot["error"] = self._error_message(exc)
                    self._next_retry_at = self._clock() + _FAILURE_BACKOFF_SECONDS
                    self._persist_locked()
                return copy.deepcopy(self._snapshot)

        with self._lock:
            self._refreshing = False
            if generation == self._generation:
                fresh_snapshot["stale"] = self._observation_is_stale(
                    fresh_snapshot.get("updated_at"), self._clock()
                )
                self._snapshot = fresh_snapshot
                self._last_successful_fetch_at = self._clock()
                self._next_retry_at = None
                self._persist_locked()
            return copy.deepcopy(self._snapshot)

    def snapshot(self) -> dict[str, Any]:
        """Return a thread-safe copy of the latest values without network I/O."""
        with self._lock:
            return copy.deepcopy(self._snapshot)

    @staticmethod
    def _coordinate(value: Any, minimum: float, maximum: float, label: str) -> float:
        number = WeatherService._optional_number(value)
        if number is None or not minimum <= number <= maximum:
            raise ValueError(f"{label} must be between {minimum:g} and {maximum:g}")
        return number

    @staticmethod
    def _optional_number(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        number = float(value)
        return number if math.isfinite(number) else None

    @classmethod
    def _observation_is_stale(cls, updated_at: Any, now: float) -> bool:
        timestamp = cls._optional_number(updated_at)
        if timestamp is None:
            return True
        age = now - timestamp
        return age < -_MAX_FUTURE_CLOCK_SKEW_SECONDS or age > _MAX_OBSERVATION_AGE_SECONDS

    @classmethod
    def _cached_number(
        cls,
        value: Any,
        minimum: float,
        maximum: float,
    ) -> float | None:
        number = cls._optional_number(value)
        return number if number is not None and minimum <= number <= maximum else None

    @classmethod
    def _cached_weather_code(cls, value: Any) -> int | None:
        code = cls._optional_int(value)
        return code if code in _KNOWN_WEATHER_CODES else None

    @staticmethod
    def _cached_text(value: Any, maximum_length: int) -> str | None:
        if not isinstance(value, str):
            return None
        cleaned = " ".join(value.split())
        if not cleaned or len(cleaned) > maximum_length:
            return None
        return cleaned

    @classmethod
    def _sanitize_cached_daily(cls, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        forecasts: list[dict[str, Any]] = []
        seen_dates: set[str] = set()
        for day in value[:7]:
            if not isinstance(day, Mapping):
                continue
            date = day.get("date")
            if not isinstance(date, str):
                continue
            try:
                parsed_date = datetime.strptime(date, "%Y-%m-%d").date()
            except ValueError:
                continue
            normalized_date = parsed_date.isoformat()
            if normalized_date != date or normalized_date in seen_dates:
                continue
            low = cls._cached_number(day.get("min"), -100.0, 70.0)
            high = cls._cached_number(day.get("max"), -100.0, 70.0)
            code = cls._cached_weather_code(day.get("weather_code"))
            rain = day.get("precipitation_probability")
            probability = None if rain is None else cls._cached_number(rain, 0.0, 100.0)
            if low is None or high is None or low > high or code is None:
                continue
            if rain is not None and probability is None:
                continue
            forecasts.append(
                {
                    "date": normalized_date,
                    "min": low,
                    "max": high,
                    "weather_code": code,
                    "precipitation_probability": probability,
                }
            )
            seen_dates.add(normalized_date)
        return forecasts

    @classmethod
    def _sanitize_cached_snapshot(cls, city: str, stored: Mapping[str, Any]) -> dict[str, Any]:
        candidate = cls._empty_snapshot(city)
        temperature = cls._cached_number(stored.get("temperature"), -100.0, 70.0)
        code = cls._cached_weather_code(stored.get("weather_code"))
        updated_at = cls._cached_number(stored.get("updated_at"), 946684800.0, 4102444800.0)
        if temperature is None or code is None or updated_at is None:
            candidate["stale"] = True
            candidate["error"] = "Нет допустимых данных погоды в кэше"
            return candidate

        candidate.update(
            temperature=temperature,
            feels_like=cls._cached_number(stored.get("feels_like"), -100.0, 70.0),
            humidity=cls._cached_number(stored.get("humidity"), 0.0, 100.0),
            wind_kmh=cls._cached_number(stored.get("wind_kmh"), 0.0, 500.0),
            description=_WEATHER_DESCRIPTIONS[code],
            weather_code=code,
            is_day=stored.get("is_day") if isinstance(stored.get("is_day"), bool) else None,
            updated_at=int(updated_at),
            stale=False,
            error=cls._cached_text(stored.get("error"), 200),
            daily=cls._sanitize_cached_daily(stored.get("daily")),
        )
        return candidate

    @classmethod
    def _empty_snapshot(cls, city: str, *, error: str | None = None) -> dict[str, Any]:
        return {
            "city": city,
            "temperature": None,
            "feels_like": None,
            "humidity": None,
            "wind_kmh": None,
            "description": "Город не настроен" if city == "Город не настроен" else "Нет данных",
            "weather_code": None,
            "is_day": None,
            "updated_at": None,
            "stale": False,
            "error": error,
            "daily": [],
        }

    def _fetch_snapshot(self, location: Mapping[str, Any]) -> dict[str, Any]:
        params = urlencode(
            {
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "current": ",".join(
                    (
                        "temperature_2m",
                        "relative_humidity_2m",
                        "apparent_temperature",
                        "is_day",
                        "weather_code",
                        "wind_speed_10m",
                    )
                ),
                "daily": ",".join(
                    (
                        "weather_code",
                        "temperature_2m_max",
                        "temperature_2m_min",
                        "precipitation_probability_max",
                    )
                ),
                "temperature_unit": "celsius",
                "wind_speed_unit": "kmh",
                "timezone": "auto",
                "forecast_days": 7,
            }
        )
        payload = self._get_json(f"{_FORECAST_URL}?{params}")
        if not isinstance(payload, Mapping):
            raise ValueError("Weather response is not an object")

        current = payload.get("current")
        daily = payload.get("daily")
        if not isinstance(current, Mapping) or not isinstance(daily, Mapping):
            raise ValueError("Weather response is missing current or daily data")

        temperature = self._required_number(current.get("temperature_2m"), "temperature")
        weather_code = self._required_int(current.get("weather_code"), "weather code")
        is_day_value = self._required_int(current.get("is_day"), "daylight flag")
        if is_day_value not in (0, 1):
            raise ValueError("Weather response has an invalid daylight flag")

        observation_time = current.get("time")
        if not isinstance(observation_time, str):
            raise ValueError("Weather response is missing the observation time")
        updated_at = self._unix_timestamp(observation_time, payload.get("utc_offset_seconds", 0))
        daily_forecast = self._parse_daily(daily)

        return {
            "city": location["city"],
            "temperature": temperature,
            "feels_like": self._optional_number(current.get("apparent_temperature")),
            "humidity": self._optional_number(current.get("relative_humidity_2m")),
            "wind_kmh": self._optional_number(current.get("wind_speed_10m")),
            "description": _WEATHER_DESCRIPTIONS.get(weather_code, "Неизвестное состояние погоды"),
            "weather_code": weather_code,
            "is_day": bool(is_day_value),
            "updated_at": updated_at,
            "stale": False,
            "error": None,
            "daily": daily_forecast,
        }

    @classmethod
    def _parse_daily(cls, daily: Mapping[str, Any]) -> list[dict[str, Any]]:
        dates = daily.get("time")
        minimums = daily.get("temperature_2m_min")
        maximums = daily.get("temperature_2m_max")
        weather_codes = daily.get("weather_code")
        precipitation = daily.get("precipitation_probability_max", [])
        if not all(isinstance(values, list) for values in (dates, minimums, maximums, weather_codes)):
            raise ValueError("Weather response has invalid daily forecast arrays")
        if not isinstance(precipitation, list):
            precipitation = []
        lengths = {len(dates), len(minimums), len(maximums), len(weather_codes)}
        if len(lengths) != 1:
            raise ValueError("Weather response has mismatched daily forecast arrays")

        forecasts: list[dict[str, Any]] = []
        for index, date in enumerate(dates):
            if not isinstance(date, str) or not date.strip():
                raise ValueError("Weather response has an invalid forecast date")
            low = cls._optional_number(minimums[index])
            high = cls._optional_number(maximums[index])
            code = cls._optional_int(weather_codes[index])
            if low is None or high is None or code is None:
                raise ValueError("Weather response has incomplete daily forecast data")
            rain_probability = (
                cls._optional_number(precipitation[index]) if index < len(precipitation) else None
            )
            forecasts.append(
                {
                    "date": date,
                    "min": low,
                    "max": high,
                    "weather_code": code,
                    "precipitation_probability": rain_probability,
                }
            )
        return forecasts

    @classmethod
    def _get_json(cls, url: str) -> Any:
        request = Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "AuroraMonitor/1.0"},
        )
        with urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise ValueError("Weather service returned an unexpectedly large response")
        return json.loads(raw)

    @classmethod
    def _required_number(cls, value: Any, label: str) -> float:
        number = cls._optional_number(value)
        if number is None:
            raise ValueError(f"Weather response has an invalid {label}")
        return number

    @classmethod
    def _optional_int(cls, value: Any) -> int | None:
        number = cls._optional_number(value)
        if number is None or not number.is_integer():
            return None
        return int(number)

    @classmethod
    def _required_int(cls, value: Any, label: str) -> int:
        number = cls._optional_int(value)
        if number is None:
            raise ValueError(f"Weather response has an invalid {label}")
        return number

    @classmethod
    def _unix_timestamp(cls, local_time: str, utc_offset: Any) -> int:
        try:
            value = datetime.fromisoformat(local_time.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Weather response has an invalid observation time") from exc
        if value.tzinfo is None:
            offset = cls._optional_int(utc_offset)
            if offset is None:
                raise ValueError("Weather response has an invalid time zone offset")
            value = value.replace(tzinfo=timezone.utc) - timedelta(seconds=offset)
        return int(value.timestamp())

    @staticmethod
    def _error_message(exc: Exception) -> str:
        if isinstance(exc, HTTPError):
            detail = f"HTTP {exc.code}"
        elif isinstance(exc, URLError):
            detail = str(exc.reason) or "нет соединения"
        elif isinstance(exc, TimeoutError):
            detail = "превышено время ожидания"
        else:
            detail = str(exc) or exc.__class__.__name__
        return f"Не удалось обновить погоду: {detail}"

    def _load_cache(self) -> None:
        if self.cache_path is None:
            return
        try:
            with self.cache_path.open("r", encoding="utf-8") as cache_file:
                payload = json.load(cache_file)
            if not isinstance(payload, Mapping):
                return
            location = payload.get("location")
            if not isinstance(location, Mapping):
                return
            city = location.get("city")
            latitude = self._optional_number(location.get("latitude"))
            longitude = self._optional_number(location.get("longitude"))
            clean_city = self._cached_text(city, 100)
            if (
                clean_city is None
                or latitude is None
                or longitude is None
                or not -90 <= latitude <= 90
                or not -180 <= longitude <= 180
            ):
                return

            city = clean_city
            self._location = {"city": city, "latitude": latitude, "longitude": longitude}
            self._snapshot = self._empty_snapshot(city, error="Погода ещё не загружена")
            stored_snapshot = payload.get("snapshot")
            if isinstance(stored_snapshot, Mapping):
                self._snapshot = self._sanitize_cached_snapshot(city, stored_snapshot)

            successful_fetch_at = self._optional_number(payload.get("last_successful_fetch_at"))
            now = self._clock()
            if (
                successful_fetch_at is None
                or successful_fetch_at < 946684800
                or successful_fetch_at > now + _MAX_FUTURE_CLOCK_SKEW_SECONDS
                or self._snapshot.get("temperature") is None
            ):
                self._last_successful_fetch_at = None
            else:
                self._last_successful_fetch_at = successful_fetch_at
            self._snapshot["stale"] = self._snapshot["stale"] or (
                self._last_successful_fetch_at is None
                or max(0.0, now - self._last_successful_fetch_at) >= _REFRESH_INTERVAL_SECONDS
                or self._observation_is_stale(self._snapshot.get("updated_at"), now)
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            # A missing or damaged cache is recoverable; the user can select a city again.
            return

    def _persist_locked(self) -> None:
        if self.cache_path is None:
            return
        payload = {
            "location": self._location,
            "snapshot": self._snapshot,
            "last_successful_fetch_at": self._last_successful_fetch_at,
        }
        temporary_path: str | None = None
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.cache_path.parent,
                prefix=f".{self.cache_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as cache_file:
                temporary_path = cache_file.name
                json.dump(payload, cache_file, ensure_ascii=False, separators=(",", ":"))
                cache_file.flush()
                os.fsync(cache_file.fileno())
            os.replace(temporary_path, self.cache_path)
        except OSError:
            # Disk caching is best-effort. Live weather remains usable without it.
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass
