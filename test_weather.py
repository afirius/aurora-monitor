"""Tests for the weather service using mocked HTTP responses only."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from weather import WeatherService


class FakeResponse:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self, limit=-1):
        return self.body if limit < 0 else self.body[:limit]


def forecast_payload():
    return {
        "utc_offset_seconds": 10800,
        "current": {
            "time": "2026-09-28T15:00",
            "temperature_2m": 17.5,
            "relative_humidity_2m": 62,
            "apparent_temperature": 16.8,
            "is_day": 1,
            "weather_code": 2,
            "wind_speed_10m": 12.2,
        },
        "daily": {
            "time": ["2026-09-28", "2026-09-29"],
            "temperature_2m_min": [10.2, 8.0],
            "temperature_2m_max": [18.6, 15.1],
            "weather_code": [2, 61],
            "precipitation_probability_max": [10, 45],
        },
    }


class WeatherServiceTests(unittest.TestCase):
    def make_service(self, path=None, now=1_790_596_800):
        service = WeatherService(path)
        service._clock = lambda: now
        return service

    def test_unconfigured_service_is_explicit_and_does_not_guess_a_city(self):
        service = self.make_service()

        snapshot = service.refresh()

        self.assertEqual(snapshot["city"], "Город не настроен")
        self.assertEqual(snapshot["description"], "Город не настроен")
        self.assertIsNone(snapshot["temperature"])
        self.assertFalse(snapshot["stale"])
        self.assertEqual(snapshot["daily"], [])

    def test_search_city_localizes_and_shapes_geocoding_results(self):
        payload = {
            "results": [
                {
                    "name": "Тула",
                    "admin1": "Тульская область",
                    "country": "Россия",
                    "latitude": 54.1961,
                    "longitude": 37.6182,
                }
            ]
        }
        service = self.make_service()
        with patch("weather.urlopen", return_value=FakeResponse(payload)) as request:
            results = service.search_city("Тула")

        request_args = request.call_args.args
        url = urlparse(request_args[0].full_url)
        query = parse_qs(url.query)
        self.assertEqual(url.netloc, "geocoding-api.open-meteo.com")
        self.assertEqual(query["language"], ["ru"])
        self.assertEqual(query["name"], ["Тула"])
        self.assertEqual(
            results,
            [
                {
                    "name": "Тула",
                    "label": "Тула, Тульская область, Россия",
                    "latitude": 54.1961,
                    "longitude": 37.6182,
                }
            ],
        )

    def test_refresh_parses_open_meteo_values_and_persists_them(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "weather.json"
            service = self.make_service(cache_path)
            service.set_location("Тула", 54.1961, 37.6182)
            with patch("weather.urlopen", return_value=FakeResponse(forecast_payload())) as request:
                snapshot = service.refresh()

            query = parse_qs(urlparse(request.call_args.args[0].full_url).query)
            self.assertEqual(query["wind_speed_unit"], ["kmh"])
            self.assertEqual(query["temperature_unit"], ["celsius"])
            self.assertEqual(snapshot["city"], "Тула")
            self.assertEqual(snapshot["temperature"], 17.5)
            self.assertEqual(snapshot["feels_like"], 16.8)
            self.assertEqual(snapshot["humidity"], 62)
            self.assertEqual(snapshot["wind_kmh"], 12.2)
            self.assertEqual(snapshot["description"], "Переменная облачность")
            self.assertTrue(snapshot["is_day"])
            self.assertEqual(snapshot["updated_at"], 1_790_596_800)
            self.assertFalse(snapshot["stale"])
            self.assertIsNone(snapshot["error"])
            self.assertEqual(snapshot["daily"][1]["precipitation_probability"], 45)
            self.assertTrue(cache_path.exists())

    def test_refresh_uses_success_cache_for_fifteen_minutes(self):
        service = self.make_service()
        service.set_location("Тула", 54.1961, 37.6182)
        now = [1_790_596_800]
        service._clock = lambda: now[0]
        with patch("weather.urlopen", return_value=FakeResponse(forecast_payload())) as request:
            first = service.refresh()
            now[0] += 14 * 60
            second = service.refresh()

        self.assertEqual(request.call_count, 1)
        self.assertEqual(second, first)

    def test_failed_refresh_marks_cached_weather_stale_and_backs_off(self):
        service = self.make_service()
        service.set_location("Тула", 54.1961, 37.6182)
        now = [1_790_596_800]
        service._clock = lambda: now[0]
        with patch(
            "weather.urlopen",
            side_effect=[FakeResponse(forecast_payload()), TimeoutError("timed out")],
        ) as request:
            good = service.refresh()
            now[0] += 16 * 60
            stale = service.refresh()
            backed_off = service.refresh()

        self.assertEqual(request.call_count, 2)
        self.assertEqual(stale["temperature"], good["temperature"])
        self.assertEqual(stale["updated_at"], good["updated_at"])
        self.assertTrue(stale["stale"])
        self.assertIn("погод", stale["error"].lower())
        self.assertEqual(backed_off, stale)

    def test_failed_first_refresh_has_error_without_invented_weather_and_backs_off(self):
        service = self.make_service()
        service.set_location("Тула", 54.1961, 37.6182)
        with patch("weather.urlopen", side_effect=TimeoutError("timed out")) as request:
            failed = service.refresh()
            second = service.refresh()

        self.assertEqual(request.call_count, 1)
        self.assertIsNone(failed["temperature"])
        self.assertIsNone(failed["updated_at"])
        self.assertTrue(failed["stale"])
        self.assertIsNotNone(failed["error"])
        self.assertEqual(second, failed)

    def test_location_and_last_weather_are_restored_from_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "weather.json"
            now = [1_790_596_800]
            service = self.make_service(cache_path)
            service._clock = lambda: now[0]
            service.set_location("Тула", 54.1961, 37.6182)
            with patch("weather.urlopen", return_value=FakeResponse(forecast_payload())):
                original = service.refresh()

            with patch("weather.time.time", return_value=now[0]):
                restored = WeatherService(cache_path)
            restored._clock = lambda: now[0]
            self.assertEqual(restored.snapshot()["city"], "Тула")
            self.assertEqual(restored.snapshot()["temperature"], original["temperature"])
            self.assertFalse(restored.snapshot()["stale"])
            with patch("weather.urlopen") as request:
                cached = restored.refresh()
            request.assert_not_called()
            self.assertEqual(cached["updated_at"], original["updated_at"])

    def test_setting_same_location_preserves_fresh_weather_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "weather.json"
            service = self.make_service(cache_path)
            service.set_location("Тула", 54.1961, 37.6182)
            with patch("weather.urlopen", return_value=FakeResponse(forecast_payload())):
                original = service.refresh()
            saved_cache = cache_path.read_bytes()

            service.set_location("Тула", 54.1961, 37.6182)

            self.assertEqual(service.snapshot(), original)
            self.assertEqual(cache_path.read_bytes(), saved_cache)
            with patch("weather.urlopen") as request:
                cached = service.refresh()
            request.assert_not_called()
            self.assertEqual(cached, original)

    def test_set_location_rejects_invalid_coordinates(self):
        service = self.make_service()
        with self.assertRaises(ValueError):
            service.set_location("Тула", 91, 37.6)
        with self.assertRaises(ValueError):
            service.set_location("Тула", 54, float("nan"))

    def test_cache_sanitizes_invalid_optional_and_daily_values(self):
        now = int(time.time())
        payload = {
            "location": {"city": "Тула", "latitude": 54.1961, "longitude": 37.6182},
            "last_successful_fetch_at": now,
            "snapshot": {
                "city": ["renderer should never see this"],
                "temperature": 17.5,
                "feels_like": {"bad": "type"},
                "humidity": 101,
                "wind_kmh": -4,
                "description": ["invalid"],
                "weather_code": 2,
                "is_day": "yes",
                "updated_at": now,
                "stale": "false",
                "error": ["invalid"],
                "daily": [
                    {
                        "date": ["2026-09-28"],
                        "min": "cold",
                        "max": None,
                        "weather_code": {"bad": 1},
                        "precipitation_probability": [],
                    },
                    {
                        "date": "2026-09-28",
                        "min": 10,
                        "max": 18,
                        "weather_code": 2,
                        "precipitation_probability": 110,
                    },
                    {
                        "date": "2026-09-29",
                        "min": -2,
                        "max": 7,
                        "weather_code": 61,
                        "precipitation_probability": 40,
                    },
                ],
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "weather.json"
            cache_path.write_text(json.dumps(payload), encoding="utf-8")
            with patch("weather.time.time", return_value=now):
                service = WeatherService(cache_path)

        snapshot = service.snapshot()
        self.assertEqual(snapshot["city"], "Тула")
        self.assertEqual(snapshot["temperature"], 17.5)
        self.assertEqual(snapshot["description"], "Переменная облачность")
        self.assertIsNone(snapshot["feels_like"])
        self.assertIsNone(snapshot["humidity"])
        self.assertIsNone(snapshot["wind_kmh"])
        self.assertIsNone(snapshot["is_day"])
        self.assertIsNone(snapshot["error"])
        self.assertFalse(snapshot["stale"])
        self.assertEqual(snapshot["daily"], [
            {
                "date": "2026-09-29",
                "min": -2.0,
                "max": 7.0,
                "weather_code": 61,
                "precipitation_probability": 40.0,
            }
        ])

    def test_cache_with_invalid_core_values_does_not_reach_renderer(self):
        now = int(time.time())
        payload = {
            "location": {"city": "Тула", "latitude": 54.1961, "longitude": 37.6182},
            "last_successful_fetch_at": now,
            "snapshot": {
                "temperature": "warm",
                "weather_code": 2,
                "updated_at": now,
                "daily": "not a list",
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "weather.json"
            cache_path.write_text(json.dumps(payload), encoding="utf-8")
            with patch("weather.time.time", return_value=now):
                service = WeatherService(cache_path)

        snapshot = service.snapshot()
        self.assertEqual(snapshot["city"], "Тула")
        self.assertIsNone(snapshot["temperature"])
        self.assertIsNone(snapshot["weather_code"])
        self.assertIsNone(snapshot["updated_at"])
        self.assertEqual(snapshot["daily"], [])
        self.assertTrue(snapshot["stale"])
        self.assertIsNone(service._last_successful_fetch_at)

    def test_recent_fetch_with_old_observation_is_marked_stale(self):
        now = int(time.time())
        payload = {
            "location": {"city": "Тула", "latitude": 54.1961, "longitude": 37.6182},
            "last_successful_fetch_at": now - 60,
            "snapshot": {
                "temperature": 17,
                "weather_code": 0,
                "updated_at": now - 4 * 60 * 60,
                "daily": [],
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "weather.json"
            cache_path.write_text(json.dumps(payload), encoding="utf-8")
            with patch("weather.time.time", return_value=now):
                service = WeatherService(cache_path)

        self.assertEqual(service.snapshot()["temperature"], 17)
        self.assertTrue(service.snapshot()["stale"])
        self.assertIsNotNone(service._last_successful_fetch_at)


if __name__ == "__main__":
    unittest.main()
