# SportyQo — Backend API Documentation

**Version:** 1.0.0
**Base URL:** `https://api.sportyqo.com/v1`
**Content-Type:** `application/json` (unless stated otherwise)
**Auth scheme:** `Authorization: Bearer <access_token>`

Derived from the Flutter app (`lib/screens/**`). Every screen that touches server state is listed with its endpoint contract, DB dependencies, upload needs, realtime needs, and notification triggers.

---

## Table of Contents

1. [Conventions](#1-conventions)
2. [Screens That Need Backend (Summary Matrix)](#2-screens-that-need-backend-summary-matrix)
3. [Auth & Onboarding](#3-auth--onboarding)
4. [Player APIs](#4-player-apis)
5. [Coach APIs](#5-coach-apis)
6. [Social / Dugout APIs](#6-social--dugout-apis)
7. [Media & File Uploads](#7-media--file-uploads)
8. [Notifications](#8-notifications)
9. [Realtime (WebSocket) Channels](#9-realtime-websocket-channels)
10. [Database Schema](#10-database-schema)
11. [Error Format](#11-error-format)

---

## 1. Conventions

| Item | Value |
|---|---|
| Auth header | `Authorization: Bearer <jwt>` |
| Access token TTL | 15 min |
| Refresh token TTL | 30 days |
| Pagination | `?page=1&limit=20` → `{ items, page, limit, total, has_more }` |
| Timestamps | ISO 8601 UTC (`2026-05-24T18:00:00Z`) |
| IDs | UUID v4 for internal, `P26001` format for public player IDs |
| Roles | `player` \| `coach` |

**Screens with NO backend need** (pure UI/navigation):
`brand_splash_screen`, `splash_screen`, `choose_role_screen`, `verification_sent_screen`, `generating_id_screen` (display only — ID comes from `select_sport` response).

---

## 2. Screens That Need Backend (Summary Matrix)

| Screen | Endpoints | Auth | Uploads | Realtime | Push |
|---|---|:--:|:--:|:--:|:--:|
| `create_account_screen` | `POST /auth/register` | ✗ | ✗ | ✗ | ✗ |
| `login_screen` | `POST /auth/login` | ✗ | ✗ | ✗ | ✗ |
| `create_profile_screen` | `PATCH /users/me` | ✓ | ✓ avatar | ✗ | ✗ |
| `select_sport_screen` | `POST /users/me/sport` | ✓ | ✗ | ✗ | ✗ |
| `player_id_ready_screen` | `GET /users/me` | ✓ | ✗ | ✗ | ✗ |
| `create_coach_account_screen` | `POST /auth/register` | ✗ | ✗ | ✗ | ✗ |
| `enter_mobile_screen` | `POST /auth/otp/send` | ✗ | ✗ | ✗ | SMS |
| `access_code_screen` | `POST /auth/otp/verify` | ✗ | ✗ | ✗ | ✗ |
| `complete_coach_profile_screen` | `PATCH /coaches/me` | ✓ | ✓ avatar | ✗ | ✗ |
| `home_screen` (player) | `GET /players/me/dashboard` | ✓ | ✗ | ✓ score | ✓ |
| `join_league_screen` | `POST /leagues/join` | ✓ | ✗ | ✗ | ✓ |
| `qo_score_card_screen` | `GET /players/me/qo-score` | ✓ | ✗ | ✓ | ✗ |
| `dugout_screen` | `GET /feed`, `POST /posts/:id/like` | ✓ | ✗ | ✓ likes | ✓ |
| `_ProfileDetailScreen` | `GET /users/:id/profile` | ✓ | ✗ | ✗ | ✗ |
| `playbook_screen` | `GET /players/me/playbook` | ✓ | ✓ video/img | ✗ | ✗ |
| `_SettingsScreen` | `GET/PATCH /users/me/settings` | ✓ | ✗ | ✗ | ✗ |
| `_EditProfileScreen` | `PATCH /users/me` | ✓ | ✓ avatar | ✗ | ✗ |
| `_NotificationScreen` | `GET /notifications` | ✓ | ✗ | ✓ | ✓ |
| `performance_screen` | `GET /players/me/performance` | ✓ | ✗ | ✗ | ✗ |
| `coach_home_screen` | `GET /coaches/me/dashboard` | ✓ | ✗ | ✗ | ✓ |
| `create_league_screen` | `POST /leagues` | ✓ | ✓ logo | ✗ | ✓ |
| `coach_leagues_screen` | `GET /coaches/me/leagues` | ✓ | ✗ | ✗ | ✗ |
| `select_match_screen` | `GET /leagues/:id/matches` | ✓ | ✗ | ✓ live | ✗ |
| `select_players_screen` | `GET /leagues/:id/players` | ✓ | ✗ | ✗ | ✗ |
| `share_league_code_screen` | `GET /leagues/:id/code` | ✓ | ✗ | ✗ | ✗ |
| `coach_certification_screen` | `POST /coaches/me/certification` | ✓ | ✓ docs | ✗ | ✓ |
| `coach_performance_screen` | `GET /coaches/me/players` | ✓ | ✗ | ✗ | ✗ |
| `coach_playbook_screen` | `GET /coaches/me/playbook` | ✓ | ✓ video | ✗ | ✗ |
| Recommend Players sheet | `POST /recommendations` | ✓ | ✗ | ✗ | ✓ |

---

## 3. Auth & Onboarding

### 3.1 Register

`POST /auth/register` — **Auth:** none
Screens: `create_account_screen`, `create_coach_account_screen`

**Request**
```json
{
  "full_name": "Aarav Mehta",
  "email": "aarav@example.com",
  "password": "••••••••",
  "role": "player"
}
```

**Response `201`**
```json
{
  "user": {
    "id": "9f3c...",
    "full_name": "Aarav Mehta",
    "email": "aarav@example.com",
    "role": "player",
    "onboarding_stage": "profile",
    "created_at": "2026-07-09T10:00:00Z"
  },
  "access_token": "eyJhbGc...",
  "refresh_token": "def502..."
}
```

**Errors:** `409 EMAIL_TAKEN`, `422 WEAK_PASSWORD`
**Tables:** `users`

---

### 3.2 Login

`POST /auth/login` — **Auth:** none
Screen: `login_screen` (role toggle → `role` field)

**Request**
```json
{ "email": "aarav@example.com", "password": "••••••••", "role": "player" }
```

**Response `200`**
```json
{
  "user": {
    "id": "9f3c...",
    "full_name": "Aarav Mehta",
    "role": "player",
    "player_id": "P26001",
    "avatar_url": "https://cdn.sportyqo.com/av/9f3c.jpg",
    "onboarding_complete": true
  },
  "access_token": "eyJhbGc...",
  "refresh_token": "def502..."
}
```

**Errors:** `401 INVALID_CREDENTIALS`, `403 ROLE_MISMATCH`
**Tables:** `users`, `sessions`

---

### 3.3 Send OTP

`POST /auth/otp/send` — **Auth:** none
Screens: `enter_mobile_screen` → `verification_sent_screen`

**Request**
```json
{ "phone": "+919876543210", "country_code": "IN", "purpose": "coach_verification" }
```

**Response `200`**
```json
{ "request_id": "otp_7f2a...", "expires_in": 300, "masked_phone": "+91 98••••3210" }
```

**Side effect:** SMS delivery (Twilio / MSG91)
**Rate limit:** 3 / 15 min per phone
**Tables:** `otp_requests`

---

### 3.4 Verify OTP

`POST /auth/otp/verify` — **Auth:** none
Screen: `access_code_screen`

**Request**
```json
{ "request_id": "otp_7f2a...", "code": "482913" }
```

**Response `200`**
```json
{ "verified": true, "phone_verified_at": "2026-07-09T10:05:00Z", "next_step": "complete_coach_profile" }
```

**Errors:** `400 INVALID_CODE`, `410 CODE_EXPIRED`, `429 TOO_MANY_ATTEMPTS`
**Tables:** `otp_requests`, `users`

---

### 3.5 Complete Player Profile

`PATCH /users/me` — **Auth:** ✓
Screens: `create_profile_screen`, `_EditProfileScreen`

**Request** (`multipart/form-data` if avatar present, else JSON)
```json
{
  "full_name": "Aarav Mehta",
  "dob": "2008-05-16",
  "role_position": "Batter",
  "team": "Alpha Warriors",
  "location": "Mumbai, India",
  "school": "St. Xavier's School",
  "bio": "Right-handed batter with a love for the game."
}
```
Multipart part: `avatar` — `image/jpeg|png`, ≤ 5 MB

**Response `200`**
```json
{
  "id": "9f3c...",
  "full_name": "Aarav Mehta",
  "avatar_url": "https://cdn.sportyqo.com/av/9f3c.jpg",
  "dob": "2008-05-16",
  "location": "Mumbai, India",
  "updated_at": "2026-07-09T10:10:00Z"
}
```

**Tables:** `users`, `user_profiles`, `media`

---

### 3.6 Select Sport → Generate Player ID

`POST /users/me/sport` — **Auth:** ✓
Screens: `select_sport_screen` → `generating_id_screen` → `player_id_ready_screen`

**Request**
```json
{ "sport": "Cricket", "sub_role": "Batsman", "age_group": "U16" }
```

**Response `201`**
```json
{
  "player_id": "P26001",
  "sport": "Cricket",
  "sub_role": "Batsman",
  "qo_score": 0,
  "card_tier": "purple",
  "issued_at": "2026-07-09T10:12:00Z"
}
```

> **Note:** the app currently generates `P26001` client-side from `DateTime.now()`. This **must** move server-side — IDs must be unique and sequential per year (`P{YY}{NNN}`), allocated inside a transaction.

**Tables:** `users`, `player_ids` (sequence table), `qo_scores`

---

### 3.7 Coach Profile + Sport

`PATCH /coaches/me` — **Auth:** ✓
Screens: `complete_coach_profile_screen`, `select_coach_sport_screen`, `_CoachEditProfileScreen`

**Request**
```json
{
  "full_name": "Coach Suneeth",
  "role_title": "Head Coach",
  "academy": "Falcons Cricket Academy",
  "location": "Bangalore, Karnataka",
  "certification": "BCCI Level 3",
  "experience_years": 6,
  "sport": "Cricket",
  "bio": "Building champions on and off the field."
}
```

**Response `200`** — mirrors request + `coach_id`, `verified: false`, `coach_score: 0`
**Tables:** `users`, `coach_profiles`, `media`

---

### 3.8 Refresh / Logout

| Endpoint | Method | Auth | Body | Response |
|---|---|:--:|---|---|
| `/auth/refresh` | POST | ✗ | `{ refresh_token }` | `{ access_token, refresh_token }` |
| `/auth/logout` | POST | ✓ | `{ refresh_token }` | `204 No Content` |

Screen: `_SettingsScreen` → Logout → `ChooseRoleScreen`
**Tables:** `sessions`

---

## 4. Player APIs

### 4.1 Home Dashboard

`GET /players/me/dashboard` — **Auth:** ✓
Screen: `home_screen` → `_HomeTab`

**Response `200`**
```json
{
  "player": {
    "first_name": "Alex",
    "player_id": "P26001",
    "age_group": "U16",
    "sub_role": "Batsman",
    "avatar_url": "https://cdn.sportyqo.com/av/9f3c.jpg"
  },
  "qo_score": {
    "current": 720,
    "card_tier": "purple",
    "delta_month": 35,
    "trend": [0.85, 0.70, 0.62, 0.50, 0.42, 0.30, 0.18, 0.05]
  },
  "active_league": {
    "id": "lg_88a1",
    "team_name": "Falcons FC",
    "league_name": "U16 Division • Division 1",
    "status": "active"
  },
  "upcoming_match": {
    "id": "mt_4412",
    "team_a": { "name": "Alpha Warriors", "logo_url": null },
    "team_b": { "name": "Thunder Strikers", "logo_url": null },
    "starts_at": "2026-05-24T18:00:00Z",
    "venue": "Green Field Arena"
  },
  "unread_notifications": 3
}
```

**Realtime:** subscribe `player:{id}:qo_score` for live score updates.
**Tables:** `users`, `qo_scores`, `league_members`, `leagues`, `teams`, `matches`, `notifications`

---

### 4.2 Join League

`POST /leagues/join` — **Auth:** ✓
Screen: `join_league_screen`

**Request**
```json
{ "league_code": "FALC-16-24", "team_id": "tm_9931" }
```

**Response `200`**
```json
{
  "league": { "id": "lg_88a1", "name": "Falcons U16 Premier League", "cricket_type": "Professional Cricket" },
  "team": { "id": "tm_9931", "name": "Falcons FC" },
  "joined_at": "2026-07-09T10:20:00Z",
  "membership_status": "active"
}
```

**Errors:** `404 INVALID_CODE`, `409 ALREADY_MEMBER`, `403 LEAGUE_FULL`, `410 LEAGUE_CLOSED`
**Push:** to league owner (coach) — `"New Player Joined!"`
**Tables:** `leagues`, `teams`, `league_members`, `notifications`

---

### 4.3 Exit Team

`DELETE /leagues/:league_id/membership` — **Auth:** ✓
Screen: `_LeagueDetailScreen` → Exit Team

**Response `204 No Content`**
**Push:** to coach — `"Player left {team}"`
**Tables:** `league_members`, `notifications`

---

### 4.4 League Detail

`GET /leagues/:id` — **Auth:** ✓
Screen: `_LeagueDetailScreen`

**Response `200`**
```json
{
  "id": "lg_88a1",
  "name": "U16 Division • Division 1",
  "season": "2024-25",
  "cricket_type": "Professional Cricket",
  "gender": "Men's",
  "status": "active",
  "stats": { "teams": 8, "matches": 12, "my_rank": 3, "my_points": 24 },
  "my_team": { "id": "tm_9931", "name": "Falcons FC", "player_count": 28 },
  "standings": [
    { "position": 1, "team_id": "tm_1", "team_name": "Alpha Warriors", "points": 14, "is_me": false },
    { "position": 3, "team_id": "tm_9931", "team_name": "Falcons FC", "points": 10, "is_me": true }
  ]
}
```

**Tables:** `leagues`, `teams`, `league_members`, `standings`, `matches`

---

### 4.5 Qo Score Card

`GET /players/me/qo-score` — **Auth:** ✓
Screen: `qo_score_card_screen`

**Response `200`**
```json
{
  "score": 242,
  "card": {
    "tier": "purple",
    "level": 1,
    "label": "Purple Card",
    "hex": "#7B2FFF",
    "threshold": 1000,
    "next_tier": { "label": "Green Card", "threshold": 2500, "points_needed": 758 }
  },
  "breakdown": [
    { "category": "Match Performance", "points": 128, "max": 400 },
    { "category": "Consistency",       "points": 54,  "max": 200 },
    { "category": "Coach Rating",      "points": 40,  "max": 200 },
    { "category": "Community",         "points": 20,  "max": 200 }
  ],
  "improve_tips": [
    "Play 2 more matches this month",
    "Get recommended by a verified coach"
  ],
  "all_tiers": [
    { "level": 1, "label": "Purple Card", "threshold": 1000,   "hex": "#7B2FFF", "unlocked": true },
    { "level": 2, "label": "Green Card",  "threshold": 2500,   "hex": "#00C853", "unlocked": false },
    { "level": 3, "label": "Yellow Card", "threshold": 5000,   "hex": "#FFEB3B", "unlocked": false },
    { "level": 4, "label": "Orange Card", "threshold": 15000,  "hex": "#FF9800", "unlocked": false },
    { "level": 5, "label": "Red Card",    "threshold": 30000,  "hex": "#FF3B30", "unlocked": false },
    { "level": 6, "label": "Bronze Pro",  "threshold": 50000,  "hex": "#CD7F32", "unlocked": false },
    { "level": 7, "label": "Silver Pro",  "threshold": 75000,  "hex": "#9E9E9E", "unlocked": false },
    { "level": 8, "label": "Golden Pro",  "threshold": 100000, "hex": "#FFB300", "unlocked": false }
  ]
}
```

> Card tiers are **server-owned** config, not hardcoded in Flutter. Ship them via this endpoint (or `GET /config/card-tiers`) so thresholds can change without an app release.

**Realtime:** `player:{id}:qo_score` push on every score mutation.
**Tables:** `qo_scores`, `qo_score_events`, `card_tiers`

---

### 4.6 Performance

`GET /players/me/performance` — **Auth:** ✓
Screen: `performance_screen`

**Response `200`**
```json
{
  "qo_score": { "current": 242, "card_tier": "purple", "delta_week": 63, "label": "Elite Performer" },
  "card_progress": { "current": 758, "target": 1000, "next_tier": "Blue Card", "points_needed": 242 },
  "ranking": { "rank": 14, "total_players": 280, "percentile": "Top 5%", "category": "U16 Cricket" },
  "journey_graph": {
    "period": "this_season",
    "labels": ["Jan","Feb","Mar","Apr","May","Jun"],
    "values": [80, 120, 150, 180, 210, 242]
  },
  "journey_milestones": [
    { "key": "started_playing",   "title": "Started Playing",      "subtitle": "Joined SportyQo • Jan 2024", "done": true },
    { "key": "first_league",      "title": "Joined First League",  "subtitle": "Alpha Warriors • Feb 2024",  "done": true },
    { "key": "first_tournament",  "title": "First Tournament Win", "subtitle": "U16 Summer Cup • Mar 2024",  "done": true },
    { "key": "purple_card",       "title": "Reached Purple Card",  "subtitle": "242 Qo Points • May 2024",   "done": true },
    { "key": "green_card",        "title": "Reach Green Card",     "subtitle": "2500 Qo Points needed",      "done": false },
    { "key": "coach_recommended", "title": "Get Coach Recommended","subtitle": "By a verified coach",        "done": false }
  ],
  "recent_matches": [
    {
      "id": "mt_4410",
      "opponent": "Thunder Strikers",
      "played_at": "2025-05-18",
      "result": "won",
      "stats": { "runs": 78, "catches": 1 },
      "badges": ["Won Match", "MOM"],
      "qo_points": 63
    }
  ]
}
```

**Query params:** `?period=this_season|last_season|all_time`
**Tables:** `qo_scores`, `qo_score_events`, `match_participants`, `matches`, `rankings`, `player_milestones`

---

### 4.7 Recent Matches (paginated)

`GET /players/me/matches?page=1&limit=20` — **Auth:** ✓
Screen: `_AllMatchesScreen`, `performance_screen` → View All

**Response `200`** → `{ items: [Match], page, limit, total, has_more }`
**Tables:** `matches`, `match_participants`, `teams`

---

### 4.8 Player Playbook

`GET /players/me/playbook` — **Auth:** ✓
Screen: `playbook_screen`

**Response `200`**
```json
{
  "profile": {
    "full_name": "Aarav Mehta",
    "sport": "Cricket",
    "sub_role": "Batter",
    "dob": "2008-05-16",
    "location": "Mumbai, India",
    "school": "St. Xavier's School",
    "avatar_url": "...",
    "verified": true,
    "about": "Right-handed batter with a love for the game."
  },
  "qo_score": { "current": 92, "rank": 1, "category": "U16 Cricket" },
  "stats": { "followers": 156, "following": 89, "teams": 24, "tournaments": 18 },
  "is_tracking": false,
  "coach_recommendations": [
    {
      "coach_id": "co_112",
      "name": "Rahul Dravid",
      "title": "Head Coach • India U19",
      "verified": true,
      "rating": 5.0,
      "quote": "Aarav is a dedicated and hard-working player…",
      "recommended_at": "2025-05-12"
    }
  ],
  "tabs": {
    "playing":      [ { "id": "md_1", "title": "Century vs DSO Academy", "subtitle": "125 Runs", "date": "2025-05-12", "thumbnail_url": "...", "media_url": "...", "type": "video" } ],
    "certificates": [],
    "team":         [],
    "trophies":     []
  }
}
```

**Query:** `?tab=playing|certificates|team|trophies`
**Tables:** `users`, `user_profiles`, `qo_scores`, `follows`, `media`, `recommendations`

---

### 4.9 Track / Untrack (Follow)

Screen: playbook "Track / Tracking" button, `_ProfileDetailScreen` "Follow / Following"

| Endpoint | Method | Auth | Request | Response |
|---|---|:--:|---|---|
| `/users/:id/track` | POST | ✓ | — | `{ tracking: true, follower_count: 157 }` |
| `/users/:id/track` | DELETE | ✓ | — | `{ tracking: false, follower_count: 156 }` |

**Push:** target user — `"New Follower"`
**Tables:** `follows`, `notifications`

---

## 5. Coach APIs

### 5.1 Coach Dashboard

`GET /coaches/me/dashboard` — **Auth:** ✓ (`role=coach`)
Screen: `coach_home_screen` → `_CoachHomeTab`

**Response `200`**
```json
{
  "coach": {
    "full_name": "Coach Suneeth",
    "role_title": "Head Coach",
    "academy": "Falcons Cricket Academy",
    "verified": true,
    "avatar_url": "..."
  },
  "quick_stats": { "players": 24, "matches": 12, "win_rate": 0.75 },
  "league_count": 3,
  "certification_status": "under_review",
  "unread_notifications": 3
}
```

**Tables:** `users`, `coach_profiles`, `leagues`, `league_members`, `matches`, `certifications`

---

### 5.2 Create League

`POST /leagues` — **Auth:** ✓ (`role=coach`)
Screen: `create_league_screen` (Step 1 → Step 2)

**Request** (`multipart/form-data` when logo present)
```json
{
  "name": "Falcons U16 Premier League",
  "cricket_type": "Professional Cricket",
  "location": "Bangalore, Karnataka",
  "gender": "Men's",
  "teams_count": 8,
  "team_names": ["Falcons FC","Warriors United","Royal Strikers","Blaze Cricket Club","Titans Academy","Rising Stars","Victory XI","Eagle Hearts"]
}
```
Multipart part: `logo` — `image/png|jpeg`, ≤ 2 MB

**`cricket_type` enum:** `gully` · `professional` · `box` · `tennis_ball` · `hard_ball` · `corporate` · `beach`

**Response `201`**
```json
{
  "id": "lg_88a1",
  "name": "Falcons U16 Premier League",
  "league_code": "FALC-16-24",
  "cricket_type": "professional",
  "gender": "Men's",
  "location": "Bangalore, Karnataka",
  "logo_url": "https://cdn.sportyqo.com/lg/88a1.png",
  "teams": [
    { "id": "tm_1", "name": "Falcons FC" },
    { "id": "tm_2", "name": "Warriors United" }
  ],
  "created_at": "2026-07-09T10:30:00Z"
}
```

> **League code** must be generated server-side. The client builds `FALC-16-24` from `name.substring(0,4) + day + year` — collision-prone. Server should mint a unique code, retry on conflict, and enforce a `UNIQUE` index.

**Errors:** `409 CODE_COLLISION` (retry), `422 TEAMS_COUNT_MISMATCH`
**Tables:** `leagues`, `teams`, `media`

---

### 5.3 List Coach Leagues

`GET /coaches/me/leagues?status=active` — **Auth:** ✓
Screen: `coach_leagues_screen`

**Response `200`** → `{ items: [ { id, name, league_code, logo_url, teams_count, players_count, status, created_at } ] }`
**Tables:** `leagues`, `teams`, `league_members`

---

### 5.4 Get League Code

`GET /leagues/:id/code` — **Auth:** ✓ (owner only)
Screen: `share_league_code_screen`

**Response `200`**
```json
{ "league_code": "FALC-16-24", "share_url": "https://sportyqo.app/join/FALC-16-24", "qr_url": "https://cdn.sportyqo.com/qr/lg_88a1.png" }
```

**Tables:** `leagues`

---

### 5.5 League Matches

`GET /leagues/:id/matches` — **Auth:** ✓
Screen: `select_match_screen` (receives `leagueName` prop)

**Response `200`**
```json
{
  "league": { "id": "lg_88a1", "name": "Falcons U16 Premier League" },
  "items": [
    {
      "id": "mt_4412",
      "team_a": { "id": "tm_1", "name": "Alpha Warriors" },
      "team_b": { "id": "tm_2", "name": "Thunder Strikers" },
      "starts_at": "2026-05-24T18:00:00Z",
      "venue": "Green Field Arena",
      "status": "scheduled",
      "score": null
    }
  ]
}
```

**`status` enum:** `scheduled` · `live` · `completed` · `abandoned`
**Realtime:** `league:{id}:matches` for live score ticks.
**Tables:** `matches`, `teams`, `leagues`

---

### 5.6 Create Match

`POST /leagues/:id/matches` — **Auth:** ✓ (owner)

**Request**
```json
{ "team_a_id": "tm_1", "team_b_id": "tm_2", "starts_at": "2026-05-24T18:00:00Z", "venue": "Green Field Arena" }
```

**Response `201`** → Match object
**Push:** all league members — `"Match Scheduled"`
**Tables:** `matches`, `notifications`

---

### 5.7 League Players / Select Players

`GET /leagues/:id/players?team_id=tm_1` — **Auth:** ✓
Screen: `select_players_screen`

**Response `200`**
```json
{
  "items": [
    { "id": "u_1", "player_id": "P26001", "name": "Rahul Sharma", "sub_role": "Batsman", "qo_score": 242, "avatar_url": "...", "selected": false }
  ]
}
```

---

### 5.8 Submit Match Points

`POST /matches/:id/points` — **Auth:** ✓ (owner)
Screen: `select_players_screen` → Submit

**Request**
```json
{
  "result": "team_a_won",
  "player_stats": [
    { "user_id": "u_1", "runs": 78, "balls": 45, "wickets": 0, "catches": 1, "is_mom": true }
  ]
}
```

**Response `200`**
```json
{ "match_id": "mt_4412", "status": "completed", "qo_points_awarded": { "u_1": 63 } }
```

**Side effects:** writes `qo_score_events`, recomputes `qo_scores` + `rankings`, emits realtime + push (`"Points Added!"`, `"Rank Improved!"`).
**Idempotency:** require `Idempotency-Key` header — points must never double-award.
**Tables:** `matches`, `match_participants`, `qo_score_events`, `qo_scores`, `rankings`, `notifications`

---

### 5.9 Coach Performance / My Players

`GET /coaches/me/players?status=active` — **Auth:** ✓
Screen: `coach_performance_screen`

**Response `200`** → `{ items: [ { user_id, player_id, name, sub_role, team, qo_score, card_tier, last_active_at, status } ] }`

---

### 5.10 Add Player by SportyQo ID

`POST /coaches/me/players` — **Auth:** ✓
Screen: `coach_performance_screen` → Add Player

**Request** `{ "player_id": "P26001" }`
**Response `201`** → player object
**Errors:** `404 PLAYER_NOT_FOUND`, `409 ALREADY_ADDED`
**Push:** player — `"Coach {name} added you"`

---

### 5.11 Recommend Players

`POST /recommendations` — **Auth:** ✓ (`role=coach`)
Screens: `coach_playbook_screen` → Recommend Players sheet, `coach_performance_screen`

**Request**
```json
{ "player_ids": ["u_1", "u_4"], "note": "Ready for the state squad.", "target": "club" }
```

**Response `201`**
```json
{ "recommendations": [ { "id": "rc_1", "player_id": "u_1", "status": "sent", "created_at": "..." } ] }
```

**Push:** each player — `"Coach Recommended You"` (+ Qo Score event)
**Tables:** `recommendations`, `qo_score_events`, `notifications`

---

### 5.12 Coach Certification

`POST /coaches/me/certification` — **Auth:** ✓ — `multipart/form-data`
Screen: `coach_certification_screen`

**Request parts**

| Part | Type | Notes |
|---|---|---|
| `certification_level` | text | e.g. `BCCI Level 3` |
| `issuing_body` | text | |
| `issued_on` | text | `YYYY-MM-DD` |
| `documents[]` | file | `image/*` or `application/pdf`, ≤ 10 MB each, max 5 files |

**Response `202 Accepted`**
```json
{ "certification_id": "ct_77", "status": "under_review", "submitted_at": "2026-07-09T10:40:00Z", "eta_days": 5 }
```

`GET /coaches/me/certification` → current status
**`status` enum:** `not_submitted` · `under_review` · `approved` · `rejected`
**Push:** on status change — `"Certification Update"`
**Tables:** `certifications`, `media`, `notifications`

---

### 5.13 Coach Playbook

`GET /coaches/me/playbook?tab=coaching` — **Auth:** ✓
Screen: `coach_playbook_screen`

Same shape as [4.8](#48-player-playbook), with:
- `coach_score` instead of `qo_score`
- `stats: { players_trained, tournaments, awards, experience_years }`
- tabs: `coaching` · `certificates` · `teams` · `trophies`

---

## 6. Social / Dugout APIs

### 6.1 Feed

`GET /feed?tab=all&page=1&limit=20` — **Auth:** ✓
Screens: `dugout_screen`, `coach_dugout_screen`

**`tab` enum:** `all` · `players` · `coaches` · `teams` · `following`
**`sort` enum:** `latest` · `top`
**Search:** `?q=Rahul`

**Response `200`**
```json
{
  "items": [
    {
      "id": "ps_991",
      "author": {
        "id": "u_1",
        "name": "Aarav Mehta",
        "type": "player",
        "verified": true,
        "role_line": "Cricket • Batter • U16",
        "avatar_url": "...",
        "qo_score": 92
      },
      "content": "Another day, another step forward. 🏏",
      "hashtags": ["WorkInProgress", "TeamFirst"],
      "media": [ { "type": "image", "url": "...", "width": 1200, "height": 800 } ],
      "qo_points_earned": 12,
      "counts": { "likes": 124, "comments": 18, "shares": 4 },
      "viewer": { "liked": false, "bookmarked": false },
      "created_at": "2026-07-09T08:00:00Z"
    }
  ],
  "page": 1, "limit": 20, "total": 240, "has_more": true
}
```

**`author.type` enum:** `player` · `coach` · `team` (drives theme colour client-side)
**Tables:** `posts`, `post_media`, `users`, `follows`, `post_likes`, `post_bookmarks`

---

### 6.2 Post Interactions

| Endpoint | Method | Auth | Response |
|---|---|:--:|---|
| `/posts/:id/like` | POST | ✓ | `{ liked: true, like_count: 125 }` |
| `/posts/:id/like` | DELETE | ✓ | `{ liked: false, like_count: 124 }` |
| `/posts/:id/bookmark` | POST | ✓ | `{ bookmarked: true }` |
| `/posts/:id/bookmark` | DELETE | ✓ | `{ bookmarked: false }` |
| `/posts/:id/comments` | GET | ✓ | `{ items: [Comment], … }` |
| `/posts/:id/comments` | POST | ✓ | `{ id, body, author, created_at }` |
| `/posts/:id/share` | POST | ✓ | `{ share_url }` |

**Realtime:** `post:{id}:counts` — like/comment counters.
**Push:** post author on like/comment — `"Post Liked"`
**Tables:** `post_likes`, `post_comments`, `post_bookmarks`, `notifications`

---

### 6.3 Create Post

`POST /posts` — **Auth:** ✓ — `multipart/form-data`
Screen: `playbook_screen` / `coach_playbook_screen` → Add New

| Part | Type | Notes |
|---|---|---|
| `content` | text | ≤ 2000 chars |
| `media[]` | file | `image/*` ≤ 10 MB, `video/mp4` ≤ 200 MB, max 10 |
| `category` | text | `playing` \| `certificates` \| `team` \| `trophies` |

**Response `201`** → Post object
Large videos → use [presigned upload](#7-media--file-uploads) instead.
**Tables:** `posts`, `post_media`, `media`

---

### 6.4 Public Profile

`GET /users/:id/profile` — **Auth:** ✓
Screen: `_ProfileDetailScreen` (both player & coach dugout)

**Response `200`**
```json
{
  "id": "u_1",
  "name": "Arjun Sharma",
  "type": "player",
  "verified": true,
  "sport_line": "Cricket Player",
  "location": "Bengaluru, India",
  "avatar_url": "...",
  "bio": "Aspiring cricketer | All-rounder 🏏",
  "hashtags": ["Believe", "Perform", "Achieve"],
  "counts": { "posts": 124, "followers": 2845, "following": 512 },
  "viewer": { "following": false, "is_friend": false, "can_message": true },
  "tabs": {
    "playing":     { "count": 42, "items": [ { "id": "md_1", "thumbnail_url": "...", "type": "video" } ] },
    "certificate": { "count": 6,  "items": [] },
    "teams":       { "count": 3,  "items": [] },
    "trophies":    { "count": 9,  "items": [] },
    "update":      { "count": 12, "items": [] }
  }
}
```

**Tables:** `users`, `user_profiles`, `follows`, `posts`, `media`

---

### 6.5 Messaging (Message button)

| Endpoint | Method | Auth | Notes |
|---|---|:--:|---|
| `/conversations` | POST | ✓ | `{ participant_id }` → `{ conversation_id }` |
| `/conversations/:id/messages` | GET | ✓ | paginated |
| `/conversations/:id/messages` | POST | ✓ | `{ body }` |

**Realtime:** `conversation:{id}` — required, not optional.
**Tables:** `conversations`, `conversation_participants`, `messages`

---

### 6.6 Friend Request (Add Friend icon)

| Endpoint | Method | Auth |
|---|---|:--:|
| `/friend-requests` | POST | ✓ |
| `/friend-requests/:id/accept` | POST | ✓ |
| `/friend-requests/:id/decline` | POST | ✓ |

**Tables:** `friend_requests`, `friendships`, `notifications`

---

## 7. Media & File Uploads

The app currently simulates camera/gallery with snackbars. Real implementation:

### 7.1 Direct Multipart (small files, ≤ 10 MB)

`POST /media` — `multipart/form-data`, part `file`, field `purpose`

**`purpose` enum:** `avatar` · `league_logo` · `team_logo` · `post_image` · `certification_doc`

**Response `201`**
```json
{ "id": "md_1", "url": "https://cdn.sportyqo.com/...", "mime": "image/jpeg", "bytes": 843201, "width": 1200, "height": 1200 }
```

### 7.2 Presigned Upload (videos, > 10 MB)

**Step 1** — `POST /media/presign`
```json
{ "filename": "century.mp4", "mime": "video/mp4", "bytes": 84300000, "purpose": "post_video" }
```
→
```json
{ "media_id": "md_9", "upload_url": "https://s3…", "fields": { … }, "expires_in": 900 }
```

**Step 2** — client `PUT`s bytes directly to `upload_url`.

**Step 3** — `POST /media/:id/complete` → `{ "status": "processing" }`
Server transcodes → emits `media:{id}:ready` over WS and sets `media.status = ready`.

### 7.3 Upload Constraints

| Purpose | Max size | Allowed MIME | Notes |
|---|---|---|---|
| `avatar` | 5 MB | `image/jpeg`, `image/png`, `image/webp` | square crop, 512×512 output |
| `league_logo` / `team_logo` | 2 MB | `image/png`, `image/jpeg` | transparent PNG preferred |
| `post_image` | 10 MB | `image/*` | max 10 per post |
| `post_video` | 200 MB | `video/mp4`, `video/quicktime` | presigned only; HLS transcode |
| `certification_doc` | 10 MB | `image/*`, `application/pdf` | max 5 files, private ACL |

All uploads: virus-scan on ingest, strip EXIF GPS, reject on MIME/extension mismatch.
**Tables:** `media`

---

## 8. Notifications

### 8.1 List

`GET /notifications?page=1&limit=20&unread_only=false` — **Auth:** ✓
Screens: `_NotificationScreen`, `_CoachNotificationScreen`

**Response `200`**
```json
{
  "unread_count": 3,
  "items": [
    {
      "id": "nt_1",
      "type": "points_added",
      "title": "Points Added!",
      "body": "+52 Qo points added to your profile",
      "icon": "emoji_events",
      "accent": "#FFB300",
      "read": false,
      "deep_link": "sportyqo://performance",
      "created_at": "2026-07-09T10:58:00Z"
    }
  ]
}
```

### 8.2 Mark Read

| Endpoint | Method | Auth | Body |
|---|---|:--:|---|
| `/notifications/:id/read` | POST | ✓ | — |
| `/notifications/read-all` | POST | ✓ | — |

### 8.3 Device Registration

`POST /devices` — **Auth:** ✓
```json
{ "fcm_token": "d3f…", "platform": "android", "app_version": "1.0.0" }
```
`DELETE /devices/:token` on logout.
**Tables:** `devices`

### 8.4 Preferences

`GET /users/me/settings` · `PATCH /users/me/settings` — **Auth:** ✓
Screens: `_SettingsScreen`, `_CoachSettingsScreen`

```json
{
  "notifications_enabled": true,
  "email_alerts": true,
  "dark_mode": true,
  "private_profile": false,
  "location_access": true
}
```
**Tables:** `user_settings`

### 8.5 Notification Type Catalogue

| Type | Audience | Trigger | Deep link |
|---|---|---|---|
| `points_added` | player | `POST /matches/:id/points` | `sportyqo://performance` |
| `rank_improved` | player | ranking recompute | `sportyqo://performance` |
| `achievement_unlocked` | player | milestone rule fires | `sportyqo://qo-score` |
| `new_follower` | any | `POST /users/:id/track` | `sportyqo://profile/{id}` |
| `post_liked` | author | `POST /posts/:id/like` | `sportyqo://post/{id}` |
| `post_commented` | author | `POST /posts/:id/comments` | `sportyqo://post/{id}` |
| `coach_recommended` | player | `POST /recommendations` | `sportyqo://playbook` |
| `league_update` | members | league state change | `sportyqo://league/{id}` |
| `league_created` | coach | `POST /leagues` | `sportyqo://league/{id}` |
| `match_scheduled` | members | `POST /leagues/:id/matches` | `sportyqo://match/{id}` |
| `match_result` | members | match completed | `sportyqo://match/{id}` |
| `player_joined` | coach | `POST /leagues/join` | `sportyqo://league/{id}` |
| `certification_update` | coach | admin review | `sportyqo://certification` |
| `performance_report` | coach | weekly cron | `sportyqo://performance` |
| `new_message` | recipient | `POST …/messages` | `sportyqo://chat/{id}` |

**Delivery:** FCM (Android) / APNs (iOS) / Web Push. Fan-out through a queue (BullMQ / Celery) — never inline in the request path.

---

## 9. Realtime (WebSocket) Channels

**Endpoint:** `wss://api.sportyqo.com/v1/ws?token=<access_token>`
**Protocol:** JSON frames `{ "channel": "...", "event": "...", "data": { … } }`

| Channel | Subscribers | Events | Screens |
|---|---|---|---|
| `user:{id}:notifications` | self | `created`, `read_all` | all (badge dot) |
| `player:{id}:qo_score` | self | `updated` | `home_screen`, `qo_score_card_screen`, `performance_screen` |
| `post:{id}:counts` | viewers | `like`, `comment` | `dugout_screen`, `coach_dugout_screen` |
| `league:{id}:matches` | members | `score_update`, `status_change` | `select_match_screen`, `_LeagueDetailScreen` |
| `match:{id}:live` | viewers | `ball`, `wicket`, `over_complete` | live match (future) |
| `conversation:{id}` | participants | `message`, `typing`, `read` | messaging |
| `media:{id}` | uploader | `ready`, `failed` | upload flows |

**Fallback:** if WS is unavailable, poll `GET /notifications?unread_only=true` every 30 s.

---

## 10. Database Schema

PostgreSQL. All tables carry `id UUID PK DEFAULT gen_random_uuid()`, `created_at`, `updated_at` unless noted.

### 10.1 Identity

**`users`**

| Column | Type | Notes |
|---|---|---|
| `email` | `citext` | `UNIQUE NOT NULL` |
| `password_hash` | `text` | argon2id |
| `full_name` | `text` | |
| `role` | `enum('player','coach')` | |
| `player_id` | `varchar(6)` | `UNIQUE`, nullable, `P{YY}{NNN}` |
| `phone` | `varchar(20)` | `UNIQUE`, nullable |
| `phone_verified_at` | `timestamptz` | |
| `avatar_media_id` | `uuid → media` | |
| `verified` | `boolean` | blue tick |
| `onboarding_stage` | `text` | `profile` → `sport` → `complete` |

Indexes: `idx_users_role`, `idx_users_player_id`

**`user_profiles`** — `user_id FK`, `dob`, `sport`, `sub_role`, `age_group`, `team`, `location`, `school`, `bio`, `hashtags text[]`

**`coach_profiles`** — `user_id FK`, `role_title`, `academy`, `certification`, `experience_years`, `coach_score int`, `rank int`

**`user_settings`** — `user_id FK UNIQUE`, `notifications_enabled`, `email_alerts`, `dark_mode`, `private_profile`, `location_access`

**`sessions`** — `user_id FK`, `refresh_token_hash`, `device_id`, `expires_at`, `revoked_at`

**`otp_requests`** — `phone`, `code_hash`, `purpose`, `attempts int`, `expires_at`, `verified_at`

**`devices`** — `user_id FK`, `fcm_token UNIQUE`, `platform`, `app_version`, `last_seen_at`

**`player_ids`** — `year smallint`, `last_seq int` — sequence allocator, row-locked

---

### 10.2 Leagues & Matches

**`leagues`**

| Column | Type | Notes |
|---|---|---|
| `owner_id` | `uuid → users` | coach |
| `name` | `text` | |
| `league_code` | `varchar(16)` | `UNIQUE NOT NULL` |
| `cricket_type` | `enum` | `gully`\|`professional`\|`box`\|`tennis_ball`\|`hard_ball`\|`corporate`\|`beach` |
| `gender` | `enum('mens','womens')` | |
| `location` | `text` | |
| `teams_count` | `int` | |
| `logo_media_id` | `uuid → media` | |
| `status` | `enum('draft','active','completed','archived')` | |

Indexes: `UNIQUE(league_code)`, `idx_leagues_owner`

**`teams`** — `league_id FK`, `name`, `logo_media_id`, `position int` — `UNIQUE(league_id, name)`

**`league_members`** — `league_id FK`, `team_id FK`, `user_id FK`, `role enum('player','captain')`, `status enum('active','left','removed')`, `joined_at`, `left_at` — `UNIQUE(league_id, user_id)`

**`matches`** — `league_id FK`, `team_a_id FK`, `team_b_id FK`, `starts_at`, `venue`, `status enum('scheduled','live','completed','abandoned')`, `result enum('team_a_won','team_b_won','draw','abandoned')`, `score_a jsonb`, `score_b jsonb`

**`match_participants`** — `match_id FK`, `user_id FK`, `team_id FK`, `runs int`, `balls int`, `wickets int`, `catches int`, `is_mom bool`, `qo_points_awarded int` — `UNIQUE(match_id, user_id)`

**`standings`** — `league_id FK`, `team_id FK`, `played`, `won`, `lost`, `points`, `position` — `UNIQUE(league_id, team_id)`

---

### 10.3 Scoring

**`card_tiers`** (seed/config) — `level int UNIQUE`, `label`, `threshold int`, `hex varchar(7)`

Seed: `1 Purple 1000 #7B2FFF` · `2 Green 2500 #00C853` · `3 Yellow 5000 #FFEB3B` · `4 Orange 15000 #FF9800` · `5 Red 30000 #FF3B30` · `6 Bronze Pro 50000 #CD7F32` · `7 Silver Pro 75000 #9E9E9E` · `8 Golden Pro 100000 #FFB300`

**`qo_scores`** — `user_id FK UNIQUE`, `score int`, `card_level int → card_tiers`, `rank int`, `percentile numeric`, `last_calculated_at`

**`qo_score_events`** — `user_id FK`, `source enum('match','recommendation','post','streak','milestone')`, `source_id uuid`, `points int`, `reason text`, `idempotency_key text UNIQUE`

> Immutable ledger. `qo_scores.score` is a materialised `SUM(points)` — recompute, never mutate in place.

**`rankings`** — `user_id FK`, `category text` (`U16 Cricket`), `rank int`, `total_players int`, `computed_at` — refreshed by cron, `UNIQUE(user_id, category)`

**`player_milestones`** — `user_id FK`, `key text`, `title`, `subtitle`, `achieved_at` — `UNIQUE(user_id, key)`

---

### 10.4 Social

**`posts`** — `author_id FK`, `author_type enum('player','coach','team')`, `content text`, `hashtags text[]`, `category enum('playing','certificates','team','trophies')`, `qo_points_earned int`, `like_count int`, `comment_count int`, `deleted_at`

Indexes: `idx_posts_author`, `idx_posts_created_at DESC`, GIN on `hashtags`

**`post_media`** — `post_id FK`, `media_id FK`, `position int`

**`post_likes`** — `post_id FK`, `user_id FK` — `UNIQUE(post_id, user_id)`

**`post_comments`** — `post_id FK`, `author_id FK`, `body text`, `parent_id` (self, nullable)

**`post_bookmarks`** — `post_id FK`, `user_id FK` — `UNIQUE(post_id, user_id)`

**`follows`** — `follower_id FK`, `followee_id FK` — `UNIQUE(follower_id, followee_id)`, `CHECK (follower_id <> followee_id)`

**`friend_requests`** — `from_id FK`, `to_id FK`, `status enum('pending','accepted','declined')`

**`conversations`** / **`conversation_participants`** / **`messages`** — standard DM triple

---

### 10.5 Coaching

**`recommendations`** — `coach_id FK`, `player_id FK`, `note text`, `rating numeric(2,1)`, `target enum('club','league','scout')`, `status enum('sent','viewed','accepted')`

**`certifications`** — `coach_id FK`, `certification_level text`, `issuing_body text`, `issued_on date`, `status enum('not_submitted','under_review','approved','rejected')`, `reviewed_by`, `reviewed_at`, `rejection_reason`

**`certification_documents`** — `certification_id FK`, `media_id FK`

---

### 10.6 Media & Notifications

**`media`** — `owner_id FK`, `purpose enum`, `storage_key text`, `url text`, `mime text`, `bytes bigint`, `width int`, `height int`, `duration_ms int`, `status enum('pending','processing','ready','failed')`, `acl enum('public','private')`

**`notifications`** — `user_id FK`, `type text`, `title`, `body`, `icon`, `accent varchar(7)`, `deep_link text`, `payload jsonb`, `read_at timestamptz`

Index: `idx_notifications_user_unread (user_id) WHERE read_at IS NULL`

---

### 10.7 Relationship Summary

```
users 1─1 user_profiles / coach_profiles / user_settings / qo_scores
users 1─n sessions, devices, notifications, posts, media, qo_score_events
users n─n users            via follows, friend_requests, conversations
users n─n leagues          via league_members
leagues 1─n teams, matches, standings
matches 1─n match_participants
posts 1─n post_media, post_likes, post_comments, post_bookmarks
coaches 1─n recommendations, certifications
certifications 1─n certification_documents
```

---

## 11. Error Format

All errors return this envelope:

```json
{
  "error": {
    "code": "INVALID_CODE",
    "message": "That league code doesn't exist.",
    "field": "league_code",
    "request_id": "req_01HZX…"
  }
}
```

| Status | Code family | Example |
|---|---|---|
| `400` | validation | `INVALID_CODE`, `MISSING_FIELD` |
| `401` | auth | `INVALID_CREDENTIALS`, `TOKEN_EXPIRED` |
| `403` | authz | `ROLE_MISMATCH`, `NOT_LEAGUE_OWNER` |
| `404` | missing | `PLAYER_NOT_FOUND`, `LEAGUE_NOT_FOUND` |
| `409` | conflict | `EMAIL_TAKEN`, `ALREADY_MEMBER`, `CODE_COLLISION` |
| `410` | gone | `CODE_EXPIRED`, `LEAGUE_CLOSED` |
| `413` | payload | `FILE_TOO_LARGE` |
| `415` | media | `UNSUPPORTED_MIME` |
| `422` | semantic | `WEAK_PASSWORD`, `TEAMS_COUNT_MISMATCH` |
| `429` | rate limit | `TOO_MANY_ATTEMPTS` (+ `Retry-After`) |
| `500` | server | `INTERNAL_ERROR` |

---

## Appendix A — Things to Move Off the Client

The current Flutter build fakes these. They **must** become server-owned:

1. **Player ID generation** — `generating_id_screen` uses `millisecondsSinceEpoch % 999`. Collides. Use `player_ids` sequence table under a row lock.
2. **League code generation** — `create_league_screen` builds `FALC-16-24` from name + date. Collides across same-day, same-prefix leagues. Server mints with a `UNIQUE` index + retry.
3. **Card tier thresholds** — hardcoded in `qo_score_card_screen`. Serve from `card_tiers` so tuning doesn't need an app release.
4. **Qo Score arithmetic** — never computed or displayed from client input. Score is a read-only projection of `qo_score_events`.
5. **Camera / gallery pickers** — currently snackbars. Wire to `image_picker` + `/media` or presigned upload.
6. **Video playback** — `_VideoPlayerScreen` is a static image with a fake progress bar. Wire to `video_player` / `chewie` against HLS URLs from `media.url`.
7. **Notification lists** — hardcoded arrays in every screen. Replace with `GET /notifications`.
8. **Follow/Track state** — local `bool _isFollowing`. Must round-trip to `/users/:id/track`.

## Appendix B — Suggested Stack

| Layer | Choice | Why |
|---|---|---|
| API | FastAPI (Python) or NestJS | matches earlier project intent |
| DB | PostgreSQL 16 | jsonb for scores, `citext`, `gen_random_uuid()` |
| Cache | Redis | rankings, feed cache, rate limits |
| Queue | Celery / BullMQ | notification fan-out, score recompute, transcode |
| Storage | S3 + CloudFront | presigned uploads, CDN |
| Realtime | WebSocket via Redis pub/sub | low fan-out complexity |
| Push | FCM + APNs | Android / iOS |
| SMS | Twilio or MSG91 | OTP (MSG91 cheaper in IN) |
| Video | MediaConvert / mux.com | HLS transcode |
