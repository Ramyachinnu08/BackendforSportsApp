"""Leagues, matches, points, roster, recommendations (spec §4.2–4.4, §5)."""
from datetime import datetime, timedelta, timezone

from tests.conftest import create_league, onboard_coach, onboard_player


async def test_create_league_mints_unique_code(client):
    coach = await onboard_coach(client)
    league = await create_league(client, coach)
    assert league["league_code"].startswith("FALC-")
    assert len(league["teams"]) == 2
    assert league["cricket_type"] == "professional"

    second = await create_league(client, coach)
    assert second["league_code"] != league["league_code"]


async def test_create_league_teams_count_mismatch(client):
    coach = await onboard_coach(client)
    r = await client.post("/v1/leagues", data={
        "name": "Bad League", "cricket_type": "gully", "gender": "Men's",
        "teams_count": "3", "team_names": "One,Two",
    }, headers=coach["headers"])
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "TEAMS_COUNT_MISMATCH"


async def test_league_create_requires_coach(client):
    player = await onboard_player(client)
    r = await client.post("/v1/leagues", data={
        "name": "X", "cricket_type": "gully", "gender": "Men's",
        "teams_count": "2", "team_names": "A,B",
    }, headers=player["headers"])
    assert r.status_code == 403


async def test_join_league_flow(client):
    coach = await onboard_coach(client)
    league = await create_league(client, coach)
    player = await onboard_player(client)
    team_id = league["teams"][0]["id"]

    r = await client.post("/v1/leagues/join",
                          json={"league_code": league["league_code"], "team_id": team_id},
                          headers=player["headers"])
    assert r.status_code == 200
    assert r.json()["membership_status"] == "active"
    assert r.json()["team"]["id"] == team_id

    dup = await client.post("/v1/leagues/join",
                            json={"league_code": league["league_code"], "team_id": team_id},
                            headers=player["headers"])
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "ALREADY_MEMBER"

    bad = await client.post("/v1/leagues/join",
                            json={"league_code": "NOPE-00-99", "team_id": team_id},
                            headers=player["headers"])
    assert bad.status_code == 404
    assert bad.json()["error"]["code"] == "INVALID_CODE"

    # coach got a player_joined notification
    notifs = await client.get("/v1/notifications", headers=coach["headers"])
    types = [n["type"] for n in notifs.json()["items"]]
    assert "player_joined" in types

    # league detail shows the member's team
    detail = await client.get(f"/v1/leagues/{league['id']}", headers=player["headers"])
    assert detail.status_code == 200
    assert detail.json()["my_team"]["id"] == team_id

    # exit team
    out = await client.delete(f"/v1/leagues/{league['id']}/membership", headers=player["headers"])
    assert out.status_code == 204


async def test_league_code_owner_only(client):
    coach = await onboard_coach(client)
    league = await create_league(client, coach)
    other = await onboard_player(client)

    ok = await client.get(f"/v1/leagues/{league['id']}/code", headers=coach["headers"])
    assert ok.status_code == 200
    assert ok.json()["share_url"].endswith(league["league_code"])

    nope = await client.get(f"/v1/leagues/{league['id']}/code", headers=other["headers"])
    assert nope.status_code == 403
    assert nope.json()["error"]["code"] == "NOT_LEAGUE_OWNER"


async def _setup_match(client):
    coach = await onboard_coach(client)
    league = await create_league(client, coach)
    p1 = await onboard_player(client, name="Rahul Sharma")
    p2 = await onboard_player(client, name="Vikram Rao")
    team_a, team_b = league["teams"][0]["id"], league["teams"][1]["id"]
    for player, team in ((p1, team_a), (p2, team_b)):
        r = await client.post("/v1/leagues/join",
                              json={"league_code": league["league_code"], "team_id": team},
                              headers=player["headers"])
        assert r.status_code == 200
    starts = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    r = await client.post(f"/v1/leagues/{league['id']}/matches",
                          json={"team_a_id": team_a, "team_b_id": team_b,
                                "starts_at": starts, "venue": "Green Field Arena"},
                          headers=coach["headers"])
    assert r.status_code == 201, r.text
    return coach, league, p1, p2, team_a, team_b, r.json()


async def test_match_points_idempotent(client):
    coach, league, p1, p2, team_a, team_b, match = await _setup_match(client)
    payload = {
        "result": "team_a_won",
        "player_stats": [
            {"user_id": p1["user"]["id"], "runs": 78, "balls": 45, "wickets": 0,
             "catches": 1, "is_mom": True},
            {"user_id": p2["user"]["id"], "runs": 12, "balls": 20, "wickets": 2,
             "catches": 0, "is_mom": False},
        ],
    }

    missing = await client.post(f"/v1/matches/{match['id']}/points", json=payload,
                                headers=coach["headers"])
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "MISSING_FIELD"

    headers = coach["headers"] | {"Idempotency-Key": "sub-001"}
    first = await client.post(f"/v1/matches/{match['id']}/points", json=payload, headers=headers)
    assert first.status_code == 200, first.text
    awarded = first.json()["qo_points_awarded"]
    # p1: 5 participation + 39 (runs*0.5) + 10 catch + 25 MOM + 15 win = 94
    assert awarded[p1["user"]["id"]] == 94
    # p2: 5 + 6 + 40 wickets = 51 (lost, no bonus)
    assert awarded[p2["user"]["id"]] == 51

    # exact replay → identical response, no double-award
    replay = await client.post(f"/v1/matches/{match['id']}/points", json=payload, headers=headers)
    assert replay.status_code == 200
    assert replay.json()["qo_points_awarded"] == awarded

    # different key on a completed match → conflict
    other = await client.post(f"/v1/matches/{match['id']}/points", json=payload,
                              headers=coach["headers"] | {"Idempotency-Key": "sub-002"})
    assert other.status_code == 409

    # scores materialised from the ledger exactly once
    score = await client.get("/v1/players/me/qo-score", headers=p1["headers"])
    assert score.json()["score"] == 94

    # standings updated: winner 2 pts
    detail = await client.get(f"/v1/leagues/{league['id']}", headers=coach["headers"])
    standings = {s["team_id"]: s for s in detail.json()["standings"]}
    assert standings[team_a]["points"] == 2
    assert standings[team_a]["won"] == 1
    assert standings[team_b]["lost"] == 1
    assert detail.json()["standings"][0]["team_id"] == team_a  # position 1

    # player performance shows the match with badges
    perf = await client.get("/v1/players/me/performance", headers=p1["headers"])
    recent = perf.json()["recent_matches"]
    assert recent[0]["badges"] == ["Won Match", "MOM"]
    assert recent[0]["qo_points"] == 94
    assert perf.json()["ranking"]["rank"] == 1

    # notifications fired
    notifs = await client.get("/v1/notifications", headers=p1["headers"])
    types = [n["type"] for n in notifs.json()["items"]]
    assert "points_added" in types and "match_result" in types


async def test_points_owner_only(client):
    coach, league, p1, p2, team_a, team_b, match = await _setup_match(client)
    other_coach = await onboard_coach(client, name="Other Coach")
    r = await client.post(f"/v1/matches/{match['id']}/points",
                          json={"result": "draw",
                                "player_stats": [{"user_id": p1["user"]["id"], "runs": 1}]},
                          headers=other_coach["headers"] | {"Idempotency-Key": "x"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "NOT_LEAGUE_OWNER"


async def test_roster_and_recommendation(client):
    coach = await onboard_coach(client)
    player = await onboard_player(client, name="Rahul Sharma")

    missing = await client.post("/v1/coaches/me/players", json={"player_id": "P99999"},
                                headers=coach["headers"])
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "PLAYER_NOT_FOUND"

    added = await client.post("/v1/coaches/me/players", json={"player_id": player["player_id"]},
                              headers=coach["headers"])
    assert added.status_code == 201
    assert added.json()["name"] == "Rahul Sharma"

    dup = await client.post("/v1/coaches/me/players", json={"player_id": player["player_id"]},
                            headers=coach["headers"])
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "ALREADY_ADDED"

    roster = await client.get("/v1/coaches/me/players", headers=coach["headers"])
    assert len(roster.json()["items"]) == 1

    reco = await client.post("/v1/recommendations",
                             json={"player_ids": [player["user"]["id"]],
                                   "note": "Ready for the state squad.", "rating": 5.0,
                                   "target": "club"},
                             headers=coach["headers"])
    assert reco.status_code == 201

    # +25 points through the ledger
    score = await client.get("/v1/players/me/qo-score", headers=player["headers"])
    assert score.json()["score"] == 25

    # cooldown: same coach-player pair within 30 days → conflict
    again = await client.post("/v1/recommendations",
                              json={"player_ids": [player["user"]["id"]]},
                              headers=coach["headers"])
    assert again.status_code == 409

    # recommendation shows on the player's playbook + milestone achieved
    playbook = await client.get("/v1/players/me/playbook", headers=player["headers"])
    recos = playbook.json()["coach_recommendations"]
    assert recos[0]["quote"] == "Ready for the state squad."
    perf = await client.get("/v1/players/me/performance", headers=player["headers"])
    milestones = {m["key"]: m["done"] for m in perf.json()["journey_milestones"]}
    assert milestones["coach_recommended"] is True
    assert milestones["started_playing"] is True


async def test_coach_dashboard(client):
    coach = await onboard_coach(client)
    await create_league(client, coach)
    r = await client.get("/v1/coaches/me/dashboard", headers=coach["headers"])
    assert r.status_code == 200
    body = r.json()
    assert body["league_count"] == 1
    assert body["certification_status"] == "not_submitted"
    assert body["quick_stats"] == {"players": 0, "matches": 0, "win_rate": 0.0}


async def test_league_lookup_by_code(client):
    coach = await onboard_coach(client)
    league = await create_league(client, coach)
    player = await onboard_player(client)

    r = await client.get(f"/v1/leagues/by-code/{league['league_code'].lower()}",
                         headers=player["headers"])
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == league["id"]
    assert body["name"] == league["name"]
    assert len(body["teams"]) == 2
    assert body["teams"][0]["player_count"] == 0
    # no member identities leak pre-join
    assert "players" not in body and "members" not in body

    missing = await client.get("/v1/leagues/by-code/NOPE-00-00", headers=player["headers"])
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "INVALID_CODE"
