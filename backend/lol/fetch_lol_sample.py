"""Fetch a small LoL data sample from Riot API.

Examples:
    python backend/lol/fetch_lol_sample.py --check-key --platform kr
    python backend/lol/fetch_lol_sample.py --game-name "Faker 페이커" --tag-line KR1 --platform kr
    python backend/lol/fetch_lol_sample.py --puuid <PUUID> --platform kr --match-count 5
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from riot_client import RiotApiError, RiotClient, save_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch LoL sample data through Riot API.")
    parser.add_argument("--game-name", help="Riot ID gameName, for example Faker")
    parser.add_argument("--tag-line", help="Riot ID tagLine, for example KR1")
    parser.add_argument("--puuid", help="Known PUUID. If provided, Riot ID lookup is skipped.")
    parser.add_argument(
        "--platform",
        default="kr",
        help="LoL platform route such as kr, jp1, na1, euw1, tw2. Default: kr",
    )
    parser.add_argument(
        "--account-region",
        default="asia",
        help="Account-V1 regional route: asia, americas, europe, or sea. Default: asia",
    )
    parser.add_argument("--match-count", type=int, default=5, help="Number of match IDs to fetch.")
    parser.add_argument(
        "--check-key",
        action="store_true",
        help="Only call a simple platform endpoint to verify RIOT_API_KEY and platform route.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parent / "lol_data"),
        help="Directory for JSON output files.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = RiotClient()

    if args.check_key:
        rotations = client.champion_rotations(platform_route=args.platform)
        print("[DONE] Riot API key works.")
        print(f"[DONE] platform route: {args.platform}")
        print(f"[DONE] free champion ids: {len(rotations.get('freeChampionIds', []))}")
        return 0

    if not args.puuid and (not args.game_name or not args.tag_line):
        raise SystemExit("Provide --check-key, --puuid, or both --game-name and --tag-line.")

    output_dir = Path(args.output_dir)
    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    account: Dict[str, Any]
    if args.puuid:
        account = {"puuid": args.puuid}
    else:
        account = client.account_by_riot_id(
            args.game_name,
            args.tag_line,
            regional_route=args.account_region,
        )

    puuid = account["puuid"]
    summoner = client.summoner_by_puuid(puuid, platform_route=args.platform)
    ranked = client.ranked_entries_by_puuid(puuid, platform_route=args.platform)
    match_ids = client.match_ids_by_puuid(
        puuid,
        platform_route=args.platform,
        count=max(0, min(args.match_count, 100)),
    )
    matches = [
        client.match_by_id(match_id, platform_route=args.platform)
        for match_id in match_ids[: args.match_count]
    ]

    payload = {
        "fetchedAt": fetched_at,
        "platformRoute": args.platform,
        "accountRegion": args.account_region,
        "account": account,
        "summoner": summoner,
        "ranked": ranked,
        "matchIds": match_ids,
        "matches": matches,
    }

    filename_hint = args.puuid[:8] if args.puuid else f"{args.game_name}_{args.tag_line}"
    output_path = output_dir / f"lol_sample_{filename_hint}.json"
    save_json(output_path, payload)

    print(f"[DONE] account puuid: {puuid}")
    print(f"[DONE] summoner level: {summoner.get('summonerLevel')}")
    print(f"[DONE] ranked queues: {len(ranked)}")
    print(f"[DONE] matches saved: {len(matches)}")
    print(f"[DONE] output: {output_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RiotApiError as exc:
        print(f"[FAILED] {exc}")
        if exc.status_code in {401, 403}:
            print("[HINT] API key 无效、过期，或没有权限。开发 key 通常 24 小时过期。")
        elif exc.status_code == 404 and "/riot/account/v1/accounts/by-riot-id/" in exc.url:
            print("[HINT] Riot ID 没查到。请确认 gameName 和 tagLine 完全正确。")
            print("[HINT] 例如韩服玩家可能不是 Faker#KR1，而是带空格/韩文的完整 Riot ID。")
            print('[HINT] 有空格或中文/韩文时要加引号：--game-name "Faker 페이커" --tag-line KR1')
        elif exc.status_code == 429:
            print("[HINT] 触发 Riot 开发 key 限速了，等一会儿再试。")
        raise SystemExit(1)
