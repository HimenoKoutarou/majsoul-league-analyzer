"""主项目 HTTP API 客户端。"""
import httpx


class SiteApi:
    def __init__(self, base_url: str, token: str):
        self.http = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(15.0, connect=5.0),
            headers={"X-Bot-Token": token},
        )

    async def close(self):
        await self.http.aclose()

    async def events(self, after_id: int, limit: int = 50) -> dict:
        response = await self.http.get("/api/bot/events",
                                       params={"after_id": after_id, "limit": limit})
        response.raise_for_status()
        return response.json()

    async def games(self, size: int = 5) -> dict:
        response = await self.http.get("/api/games", params={"page": 1, "size": size})
        response.raise_for_status()
        return response.json()

    async def game(self, game_uuid: str) -> dict:
        response = await self.http.get(f"/api/games/{game_uuid}")
        response.raise_for_status()
        return response.json()

    async def stats(self, by: str, target_id: int) -> dict:
        response = await self.http.get("/api/stats",
                                       params={"by": by, "id": target_id})
        response.raise_for_status()
        return response.json()

    async def related_games(self, by: str, target_id: int) -> dict:
        response = await self.http.get("/api/stats/games",
                                       params={"by": by, "id": target_id})
        response.raise_for_status()
        return response.json()

    async def teams(self) -> list[dict]:
        response = await self.http.get("/api/teams")
        response.raise_for_status()
        return response.json()

    async def profile(self, player_id: int) -> dict:
        response = await self.http.get("/api/stats/profile",
                                       params={"by": "player", "id": player_id})
        response.raise_for_status()
        return response.json()

    async def standings(self, by: str = "team") -> dict:
        response = await self.http.get("/api/standings", params={"by": by})
        response.raise_for_status()
        return response.json()
