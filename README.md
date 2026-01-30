# SpyTON TON BuyBot (FAST + Accurate + Auto-Rank) — Python (aiogram)

✅ Group buy posts (style #2)  
✅ Trending channel buy posts (style #1)  
✅ Leaderboard posts ONCE and EDITS every 10s (no spam)  
✅ Auto-rank: buy post shows current leaderboard rank (e.g. [6])  
✅ Accurate amounts via TonAPI trace parsing (when available)  
✅ Holders + Liquidity + MarketCap via TonAPI + DexScreener (fallbacks)

## Install
pip install -r requirements.txt

## Run
python main.py

## Setup
1) Add bot to a project group
2) /groupid
3) /addtoken SYMBOL JETTON_ADDRESS
4) /setpool stonfi POOL_ADDRESS
5) /setpool dedust POOL_ADDRESS
6) /minbuy 0.3
7) /on

## Owner
(no approval needed) <GROUP_ID>  -> allow cross-posts into @SpyTonTrending
/pinleaderboard      -> post leaderboard once + pin
/leaderboardnow      -> force update

## Notes about speed
- Set POLL_INTERVAL_SECONDS=1 (default) for faster detection.
- TonAPI rate limits apply: use TONAPI_KEY for best speed.



## New in v3
- /addtoken can take only the jetton address and auto-detect symbol + pools.
- /autopools refreshes STONfi/DeDust pools from DexScreener.


## Railway build fix
If Railway fails building wheels (e.g. pydantic-core), keep the included `Dockerfile` in the repo. Railway will detect it and build using Debian `python:3.11-slim`.
