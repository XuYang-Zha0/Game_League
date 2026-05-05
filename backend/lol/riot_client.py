from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests


PLATFORM_TO_REGIONAL_ROUTE = {
    "br1": "americas",
    "la1": "americas",
    "la2": "americas",
    "na1": "americas",
    "eun1": "europe",
    "euw1": "europe",
    "tr1": "europe",
    "ru": "europe",
    "jp1": "asia",
    "kr": "asia",
    "oc1": "sea",
    "ph2": "sea",
    "sg2": "sea",
    "th2": "sea",
    "tw2": "sea",
    "vn2": "sea",
}


class RiotApiError(RuntimeError):
    """Raised when Riot returns a non-success response."""

    def __init__(self, status_code: int, url: str, message: str) -> None:
        super().__init__(f"Riot API error {status_code}: {message} ({url})")
        self.status_code = status_code
        self.url = url
        self.message = message


def load_local_env() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return

    with env_path.open("r", encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


class RiotClient:
    """Small Riot API client for LoL account, summoner, league, and match data."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        timeout: int = 20,
        max_retries: int = 3,
    ) -> None:
        load_local_env()
        self.api_key = api_key or os.getenv("RIOT_API_KEY", "").strip()
        if not self.api_key:
            raise ValueError("Missing RIOT_API_KEY. Add it to backend/.env or export it.")

        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "Game-League/0.1",
                "X-Riot-Token": self.api_key,
            }
        )

    def _get(self, route: str, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        route = route.lower()
        url = f"https://{route}.api.riotgames.com{path}"
        last_error = ""

        for attempt in range(1, self.max_retries + 1):
            response = self.session.get(url, params=params, timeout=self.timeout)
            if response.status_code == 200:
                return response.json()
            if response.status_code == 204:
                return None

            body = self._response_message(response)
            last_error = body

            if response.status_code == 429 and attempt < self.max_retries:
                retry_after = int(response.headers.get("Retry-After", "2"))
                time.sleep(max(retry_after, 1))
                continue

            if response.status_code in {500, 502, 503, 504} and attempt < self.max_retries:
                time.sleep(1.5 * attempt)
                continue

            raise RiotApiError(response.status_code, response.url, body)

        raise RiotApiError(0, url, last_error or "request failed")

    @staticmethod
    def _response_message(response: requests.Response) -> str:
        try:
            data = response.json()
        except ValueError:
            return response.text[:500]
        status = data.get("status") if isinstance(data, dict) else None
        if isinstance(status, dict):
            return str(status.get("message") or data)
        return str(data)

    @staticmethod
    def match_route_for_platform(platform_route: str) -> str:
        route = platform_route.lower()
        if route not in PLATFORM_TO_REGIONAL_ROUTE:
            valid = ", ".join(sorted(PLATFORM_TO_REGIONAL_ROUTE))
            raise ValueError(f"Unknown platform route '{platform_route}'. Valid routes: {valid}")
        return PLATFORM_TO_REGIONAL_ROUTE[route]

    def account_by_riot_id(
        self,
        game_name: str,
        tag_line: str,
        *,
        regional_route: str = "asia",
    ) -> Dict[str, Any]:
        return self._get(
            regional_route,
            "/riot/account/v1/accounts/by-riot-id/"
            f"{quote(game_name, safe='')}/{quote(tag_line, safe='')}",
        )

    def summoner_by_puuid(self, puuid: str, *, platform_route: str = "kr") -> Dict[str, Any]:
        return self._get(
            platform_route,
            f"/lol/summoner/v4/summoners/by-puuid/{puuid}",
        )

    def ranked_entries_by_summoner_id(
        self,
        encrypted_summoner_id: str,
        *,
        platform_route: str = "kr",
    ) -> List[Dict[str, Any]]:
        data = self._get(
            platform_route,
            f"/lol/league/v4/entries/by-summoner/{encrypted_summoner_id}",
        )
        return data or []

    def ranked_entries_by_puuid(
        self,
        puuid: str,
        *,
        platform_route: str = "kr",
    ) -> List[Dict[str, Any]]:
        data = self._get(
            platform_route,
            f"/lol/league/v4/entries/by-puuid/{puuid}",
        )
        return data or []

    def match_ids_by_puuid(
        self,
        puuid: str,
        *,
        platform_route: str = "kr",
        start: int = 0,
        count: int = 20,
        queue: Optional[int] = None,
        match_type: Optional[str] = None,
    ) -> List[str]:
        params: Dict[str, Any] = {"start": start, "count": count}
        if queue is not None:
            params["queue"] = queue
        if match_type:
            params["type"] = match_type

        regional_route = self.match_route_for_platform(platform_route)
        data = self._get(
            regional_route,
            f"/lol/match/v5/matches/by-puuid/{puuid}/ids",
            params=params,
        )
        return data or []

    def match_by_id(self, match_id: str, *, platform_route: str = "kr") -> Dict[str, Any]:
        regional_route = self.match_route_for_platform(platform_route)
        return self._get(regional_route, f"/lol/match/v5/matches/{match_id}")

    def champion_rotations(self, *, platform_route: str = "kr") -> Dict[str, Any]:
        return self._get(platform_route, "/lol/platform/v3/champion-rotations")

    def versions(self) -> List[str]:
        # Data Dragon is public and does not use the Riot API gateway.
        url = "https://ddragon.leagueoflegends.com/api/versions.json"
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        return response.json()


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
