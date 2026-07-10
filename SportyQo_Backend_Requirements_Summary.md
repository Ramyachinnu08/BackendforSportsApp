# SportyQo — Technical Summary for Backend Developer

---

## 1. App Overview & Stack

**What it does:** SportyQo is a sports networking + player performance-tracking app (cricket-first, India-focused). Two roles: **Players** build profiles, earn a gamified "Qo Score" (8 card tiers), join leagues via invite codes, and post to a social feed ("Dugout"). **Coaches** create leagues (auto-generated join codes like `FALC-16-24`), record match results that award Qo points, manage a player roster, recommend players, and apply for certification.

**Current Flutter stack — important, read carefully:**

| Concern | Current state |
|---|---|
| State management | **None** — plain `StatefulWidget` + `setState` only. No Riverpod/Bloc/Provider. |
| HTTP client | **None** — all networking code (`api_client.dart`, `auth_service.dart`) was deliberately removed mid-project. No `http`, no `dio` in `pubspec.yaml`. |
| Dependencies | Only `cupertino_icons: ^1.0.8`. That's it. |
| Data layer | **No model classes exist.** Every screen holds inline `List<Map<String, dynamic>>` dummy data. |
| Images | `Image.network` against hardcoded Unsplash/ibb.co URLs. |
| Auth flow | Purely navigational — Sign Up buttons route directly to next screens; OTP screen accepts **any 6 digits**; login routes by role toggle with no credential check. |
| Persistence | None. No shared_prefs, no local DB, no token storage. |

**Implication for backend dev:** you are defining the contract from scratch. The frontend will add `dio` + a state solution and model classes during integration — nothing existing constrains your JSON shapes except the field inventories below.

**Platforms:** Android, iOS, and Flutter Web (CORS required for the web origin).

---

## 2. Complete Data Models

⚠️ **These classes do NOT exist in the codebase yet.** They are derived field-for-field from the `Map<String, dynamic>` structures actually used in the screens, plus the fields the backend must add (IDs, timestamps). Use these as the canonical contract; JSON keys should be `snake_case`, Dart fields `camelCase`.

```dart
/// users table — both roles
class User {
  final String id;                 // UUID
  final String fullName;
  final String email;
  final String role;               // 'player' | 'coach'
  final String? playerId;          // 'P26001' — server-generated P{YY}{NNN}, players only
  final String? phone;             // E.164, coaches (OTP-verified)
  final String? avatarUrl;
  final bool verified;             // blue tick (coach: set by certification approval)
  final String onboardingStage;    // 'profile' | 'sport' | 'complete'
  final DateTime createdAt;
}

/// Player profile (user_profiles)
class PlayerProfile {
  final String userId;
  final DateTime? dob;             // e.g. 2008-05-16 — NEVER exposed publicly (minors)
  final String sport;              // 'Cricket'
  final String subRole;            // 'Batsman' / 'Batter' / 'Bowler' ...
  final String ageGroup;           // 'U16' — derived from dob, public-safe
  final String? team;              // display name, e.g. 'Alpha Warriors'
  final String? location;          // city-level only, e.g. 'Mumbai, India'
  final String? school;            // 'St. Xavier's School' — private field
  final String? bio;
  final List<String> hashtags;
}

/// Coach profile (coach_profiles)
class CoachProfile {
  final String userId;
  final String roleTitle;          // 'Head Coach'
  final String academy;            // 'Falcons Cricket Academy'
  final String? location;          // 'Bangalore, Karnataka'
  final String certificationLabel; // 'BCCI Level 3'
  final int experienceYears;       // 6
  final int coachScore;
  final int? rank;                 // #3
  final String? rankScope;         // 'Karnataka Coaches'
  final String? bio;
}

/// Qo Score snapshot (qo_scores) + tier config
class QoScore {
  final int score;                 // e.g. 242
  final CardTier card;
  final int rank;                  // #12 / #14
  final String percentileLabel;    // 'Top 15%' / 'Top 5%'
  final int deltaWeek;             // +63
  final int deltaMonth;            // +35
  final List<double> trend;        // sparkline points
}

class CardTier {
  final int level;                 // 1..8
  final String label;              // 'Purple Card' ... 'Golden Pro'
  final int threshold;             // 1000, 2500, 5000, 15000, 30000, 50000, 75000, 100000
  final String hex;                // '#7B2FFF' etc. — client themes whole screen from this
  final bool unlocked;
}

/// Immutable points ledger entry (qo_score_events) — source of truth
class QoScoreEvent {
  final String id;
  final String userId;
  final String source;             // 'match' | 'recommendation' | 'post' | 'streak' | 'milestone'
  final String? sourceId;
  final int points;
  final String reason;
  final DateTime createdAt;
}

/// Dugout feed post — fields taken verbatim from screen maps
class Post {
  final String id;
  final PostAuthor author;
  final String content;            // hashlines starting '#' are themed client-side
  final List<String> hashtags;
  final List<MediaItem> media;
  final int qoPointsEarned;        // 'Qo Score Earned +12'
  final int likeCount;
  final int commentCount;
  final bool viewerLiked;          // was `liked` in dummy maps
  final bool viewerBookmarked;
  final DateTime createdAt;        // rendered as '2h ago'
}

class PostAuthor {
  final String id;
  final String name;               // 'Aarav Mehta'
  final String type;               // 'player' | 'coach' | 'team'  ← drives theme color
  final bool verified;
  final String roleLine;           // 'Cricket • Batter • U16'
  final String? avatarUrl;
  final int qoScore;               // badge on post header, e.g. 92
}

/// Public profile detail (opened from a dugout post)
class PublicProfile {
  final String id;
  final String name;
  final String type;               // player | coach | team
  final bool verified;
  final String sportLine;          // 'Cricket Player' / 'Head Coach'
  final String location;           // 'Bengaluru, India'
  final String? avatarUrl;
  final String bio;
  final int postsCount;            // 124
  final int followersCount;        // 2845 (client formats '2,845')
  final int followingCount;        // 512
  final bool viewerFollowing;
  final Map<String, List<MediaItem>> tabs; // playing|certificate|teams|trophies|update
}

/// Playbook media / video grid item — from screen maps (title/subtitle/date/image/duration)
class MediaItem {
  final String id;
  final String type;               // 'image' | 'video'
  final String url;                // HLS for video (player screen is currently fake)
  final String thumbnailUrl;
  final String? title;             // 'Century vs DSO Academy'
  final String? subtitle;          // '125 Runs'
  final DateTime? date;
  final int? durationMs;           // was '2:34' string in dummy data
  final String? category;          // 'playing'|'certificates'|'team'|'trophies'
}

/// League — from create_league_screen fields
class League {
  final String id;
  final String ownerId;            // coach
  final String name;               // 'Falcons U16 Premier League'
  final String leagueCode;         // 'FALC-16-24' — SERVER-generated, unique
  final String cricketType;        // gully|professional|box|tennis_ball|hard_ball|corporate|beach
  final String gender;             // "Men's" | "Women's"  (store as mens|womens)
  final String location;
  final int teamsCount;            // min 2, default 8 in UI
  final List<Team> teams;
  final String? logoUrl;
  final String status;             // draft|active|completed|archived
}

class Team {
  final String id;
  final String leagueId;
  final String name;               // 'Falcons FC', unique within league
  final String? logoUrl;
}

class LeagueMembership {
  final String leagueId;
  final String teamId;
  final String userId;
  final String status;             // active|left|removed
  final DateTime joinedAt;
}

/// Match — merges home 'Upcoming Match' + _AllMatchesScreen + performance maps
class Match {
  final String id;
  final String leagueId;
  final Team teamA;                // 'Alpha Warriors'
  final Team teamB;                // 'Thunder Strikers'
  final DateTime startsAt;         // '24 May 2025' + '06:00 PM'
  final String venue;              // 'Green Field Arena'
  final String status;             // scheduled|live|completed|abandoned
  final String? result;            // team_a_won|team_b_won|draw — UI chips Won/Lost/Upcoming
}

/// Per-player match stats — from select_players + performance match tiles
class MatchParticipant {
  final String matchId;
  final String userId;
  final String teamId;
  final int runs;                  // 78
  final int balls;                 // 45
  final int wickets;
  final int catches;               // 1
  final bool isMom;                // 'MOM ⭐' badge
  final int qoPointsAwarded;       // '+63'
}

/// Standings row — from _LeagueDetailScreen (pos/team/pts/isMe)
class StandingRow {
  final int position;
  final String teamId;
  final String teamName;
  final int points;
  final bool isMe;
}

/// Coach recommendation — from playbook card (Rahul Dravid, 5.0, quote, date)
class Recommendation {
  final String id;
  final String coachId;
  final String coachName;
  final String coachTitle;         // 'Head Coach • India U19'
  final bool coachVerified;
  final String playerId;
  final double rating;             // 5.0
  final String note;               // the quoted text
  final DateTime createdAt;        // '12 May 2025'
  final String status;             // sent|viewed|accepted
}

/// Coach roster entry — coach_performance screen (name/role/team/pts)
class RosterPlayer {
  final String userId;
  final String playerId;           // 'P26001' — added via this
  final String name;
  final String subRole;            // 'Batsman' / 'All Rounder' / 'Wicket Keeper'
  final String team;
  final int qoScore;               // 'pts' in dummy maps
  final String status;             // active|inactive
}

/// Notification — fields verbatim from the 4 hardcoded screen arrays
class AppNotification {
  final String id;
  final String type;               // points_added|new_follower|... (15-type catalogue)
  final String title;              // 'Points Added!'
  final String body;               // was `subtitle`
  final String icon;               // material icon name, e.g. 'emoji_events'
  final String accent;             // hex, e.g. '#FFB300' — was Color in dummy data
  final String? deepLink;          // 'sportyqo://performance'
  final bool read;
  final DateTime createdAt;        // rendered '2m ago'
}

/// Settings toggles — from _SettingsScreen switches
class UserSettings {
  final bool notificationsEnabled;
  final bool emailAlerts;
  final bool darkMode;
  final bool privateProfile;
  final bool locationAccess;
}

/// Milestone — performance 'My Qo Journey' timeline
class Milestone {
  final String key;    // started_playing|first_league|first_tournament|purple_card|green_card|coach_recommended
  final String title;
  final String subtitle;
  final bool done;
}

class Certification {
  final String id;
  final String coachId;
  final String certificationLevel; // 'BCCI Level 3'
  final String issuingBody;
  final DateTime issuedOn;
  final String status;             // not_submitted|under_review|approved|rejected
  final List<String> documentUrls; // private, signed URLs
}
```

**Model gotchas from the dummy data:**
- Dummy `time: '2h ago'` strings → backend sends ISO timestamps; client formats.
- Dummy `followers: '2,845'` (string!) → backend sends `int`; client formats.
- Dummy `color: Color(0xFFFFB300)` in notifications → backend sends hex string.
- Demo shows three different scores (720 home / 242 performance / 92 playbook) and names (Alex/Aarav) — intentional placeholders; there is ONE user with ONE score.
- Settings screen shows Player ID `SQ784512` — a stale mock; the decided format is `P{YY}{NNN}` everywhere.

---

## 3. Authentication & Security

**Methods expected:**
- **Email + password** — sole login method. Login screen has a **role toggle**; the API must take `role` and return `403 ROLE_MISMATCH` if it doesn't match the account.
- **SMS OTP (coaches only)** — 6-digit code, part of coach registration (enter mobile → verification sent → access code screens). ⚠️ Client currently accepts any 6 digits; server-side verification is the only real gate. TTL 300 s, 5 attempts max, send-rate 3/15 min per phone.
- **No social login** (no Google/Apple) at MVP.
- **No biometrics, no magic links.**

**Sessions:** JWT access token (15 min) + rotating refresh token (30 days, reuse-detection revokes family). Logout revokes refresh token + deletes device push token, client returns to Choose Role screen.

**Also required though no UI exists yet:** password reset (email token) and email verification — industry-mandatory, build the endpoints.

**User roles:**
| Role | Can | Cannot |
|---|---|---|
| `player` | own profile/settings, join/leave leagues by code, view own score/performance/matches, post, like/comment/bookmark, follow ("track"), friend requests, DM | create leagues/matches, submit points, recommend, touch anyone's score |
| `coach` | everything social + create/manage OWN leagues/teams/matches, submit match points (the only score-mutation path besides recommendations), roster add-by-Player-ID, recommend players, certification | manage other coaches' leagues, self-approve certification, edit scores directly |
| `admin` (server-only) | certification review, moderation, point corrections | — (all actions audit-logged) |

**Security notes:** most players are **minors** (U16) — never expose `dob`, `school`, `phone`, or precise location publicly; only `age_group` + city. India DPDP Act applies (deletion rights, consent). Private-profile toggle hides tabs/media from non-followers.

---

## 4. Feature & API Checklist

**Auth**
- [ ] `POST /auth/register` (role: player|coach)
- [ ] `POST /auth/login` (role-checked) · `POST /auth/refresh` · `POST /auth/logout`
- [ ] `POST /auth/otp/send` · `POST /auth/otp/verify` (SMS, coach onboarding)
- [ ] `POST /auth/password/forgot` · `POST /auth/password/reset` · email verification
- [ ] `DELETE /users/me` (soft delete, 30-day grace)

**Profiles & IDs**
- [ ] `GET/PATCH /users/me` (player fields; multipart avatar)
- [ ] `POST /users/me/sport` → **server-side Player ID allocation** `P{YY}{NNN}` (sequence table, idempotent — replaces the client's `millisecondsSinceEpoch % 999` hack)
- [ ] `PATCH /coaches/me` · `GET/PATCH /users/me/settings`
- [ ] `GET /users/:id/profile` (public, private-profile gated)

**Scoring (core domain)**
- [ ] Append-only `qo_score_events` ledger; `qo_scores` = materialized SUM — **no direct score writes ever**
- [ ] `GET /players/me/qo-score` (score, tier, breakdown bars, tips, full 8-tier ladder)
- [ ] `GET /config/card-tiers` — the 8 tiers/thresholds/hexes currently hardcoded in the app
- [ ] `GET /players/me/performance` (card progress, ranking #N of M + percentile, monthly journey graph, milestone timeline, recent matches with badges)
- [ ] Rankings cron per category ('U16 Cricket'), cached

**Leagues & Matches**
- [ ] `POST /leagues` — creates league + N teams, **server-mints unique code** (`FALC-16-24` style, unique index + collision retry); min 2 teams; 7-value cricket_type enum (also `GET /config/cricket-types`)
- [ ] `GET /coaches/me/leagues` · `GET /leagues/:id` (detail + standings) · `GET /leagues/:id/code` (+share_url, QR)
- [ ] `POST /leagues/join` (by code + team pick; notifies coach) · `DELETE /leagues/:id/membership` (Exit Team)
- [ ] `GET/POST /leagues/:id/matches` · `GET /leagues/:id/players`
- [ ] `POST /matches/:id/points` — **idempotent (Idempotency-Key required)**; writes ledger events, recomputes standings/rankings, fires notifications
- [ ] `GET /players/me/matches` (paginated) · `GET /players/me/dashboard` (home aggregate)

**Social (Dugout)**
- [ ] `GET /feed` — tabs all|players|coaches|teams|following, name search, latest sort, pagination
- [ ] `POST /posts` (multipart, ≤10 media, category for playbook tabs) — "Add New" upload flow
- [ ] `POST/DELETE /posts/:id/like` · bookmark · `GET/POST /posts/:id/comments` · `POST /posts/:id/share`
- [ ] `POST/DELETE /users/:id/track` (Follow/Track button — one relationship, two UI labels)
- [ ] Friend requests (send/accept/decline) — the "+person" button
- [ ] Minimal DM API (`POST /conversations`, `GET/POST messages`) — Message button ships without a chat screen yet

**Coach tools**
- [ ] `GET /coaches/me/dashboard` (quick stats: players/matches/win-rate)
- [ ] `GET/POST /coaches/me/players` (roster; add by Player ID)
- [ ] `POST /recommendations` (per-player Send; +25 pts to player; 1 per coach-player per 30 days)
- [ ] `GET/POST /coaches/me/certification` (docs upload, under_review → approved sets verified badge) + admin review endpoints
- [ ] `GET /coaches/me/playbook` / `GET /players/me/playbook` aggregates

**Media**
- [ ] `POST /media` multipart ≤10 MB (avatar 5 MB, logos 2 MB, images 10 MB)
- [ ] Presigned S3 flow for video ≤200 MB → HLS transcode → ready webhook (video player screen is currently a static-image fake; it will consume real HLS URLs)
- [ ] Private ACL + signed URLs for certification docs; EXIF-GPS strip; virus scan

**Notifications & Realtime**
- [ ] `GET /notifications` (+unread count) · mark-one · mark-all — replaces 4 hardcoded arrays
- [ ] `POST/DELETE /devices` (FCM token registry)
- [ ] Push fan-out via queue — 15 notification types (points_added, rank_improved, new_follower, coach_recommended, match_scheduled, certification_update, player_joined, etc.), each with icon + accent hex + deep_link
- [ ] WebSocket channels: `user:{id}:notifications`, `player:{id}:qo_score`, `post:{id}:counts`, `league:{id}:matches`, `conversation:{id}`, `media:{id}`
- [ ] Scheduled: weekly coach performance report, match reminders (2 h before)

---

## 5. Third-Party Integrations

**Currently in the app: none.** No Firebase, no analytics, no payment SDK — the only external touchpoints are hardcoded dev image URLs (Unsplash, ibb.co) that will be replaced by your CDN.

**Required server-side for launch:**

| Service | Purpose | Auth |
|---|---|---|
| **FCM** (Firebase Cloud Messaging) | Android/web push | service-account JSON |
| **APNs** | iOS push | .p8 key + key/team ID |
| **SMS provider** — MSG91 (India-cheap) or Twilio | coach OTP delivery | API key |
| **S3-compatible storage + CDN** (CloudFront) | all media, presigned video uploads | IAM keys |
| **Video transcoder** — AWS MediaConvert or Mux | MP4 → HLS for playbook videos | IAM role / API token |
| **Email** — SES/Postmark/SMTP | verify email, password reset, certification decisions | API key |
| **Sentry** | error tracking | DSN |

**Explicitly NOT needed:** payments/Stripe (no monetization at MVP), Google Maps (locations are free-text strings), any AI provider (no AI features), Google/Apple sign-in. WhatsApp/SMS "Share" buttons on the league-code screen are client-side share intents — the server only supplies the `share_url` string.
