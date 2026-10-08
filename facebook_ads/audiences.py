"""
Predefined audience targeting profiles for hip-hop / afrobeat / trap content
and fashion / streetwear / lifestyle.

Each profile defines:
  - interests: list of interest names (resolved to IDs via the search API at runtime)
  - age_min / age_max
  - genders: [] means all; [1] = male, [2] = female
  - geo: countries or regions
  - description: human-readable summary
"""

import json
from pathlib import Path
from facebook_business.adobjects.targetingsearch import TargetingSearch

PROFILES: dict[str, dict] = {
    "hiphop": {
        "description": "US/UK/CA hip-hop fans, ages 18-35",
        "age_min": 18,
        "age_max": 35,
        "genders": [],
        "geo": {"countries": ["US", "GB", "CA"]},
        "interests": [
            "Hip hop music",
            "Rap music",
            "Drake",
            "Kendrick Lamar",
            "Travis Scott",
            "J. Cole",
            "Lil Baby",
            "Gunna",
            "Music videos",
            "SoundCloud",
            "Spotify",
        ],
    },
    "afrobeat": {
        "description": "Global afrobeats fans, ages 18-35 (NG/GH/UK/US/CA)",
        "age_min": 18,
        "age_max": 35,
        "genders": [],
        "geo": {"countries": ["NG", "GH", "GB", "US", "CA", "ZA"]},
        "interests": [
            "Afrobeats",
            "Afropop",
            "Burna Boy",
            "Wizkid",
            "Davido",
            "Rema",
            "Afro Nation",
            "African music",
            "Music videos",
            "Amapiano",
        ],
    },
    "trap": {
        "description": "Global trap fans, ages 18-30",
        "age_min": 18,
        "age_max": 30,
        "genders": [],
        "geo": {"countries": ["US", "GB", "CA", "AU"]},
        "interests": [
            "Trap music",
            "Travis Scott",
            "Future (rapper)",
            "Migos",
            "21 Savage",
            "Playboi Carti",
            "Young Thug",
            "Hip hop music",
            "SoundCloud",
        ],
    },
    "all": {
        "description": "Combined hiphop + afrobeat + trap fans, ages 18-35, worldwide",
        "age_min": 18,
        "age_max": 35,
        "genders": [],
        "geo": {"countries": ["US", "GB", "CA", "NG", "GH", "AU", "ZA"]},
        "interests": [
            "Hip hop music",
            "Afrobeats",
            "Trap music",
            "Rap music",
            "Music videos",
            "Burna Boy",
            "Drake",
            "Travis Scott",
            "Spotify",
            "SoundCloud",
        ],
    },
    "global_music": {
        "description": "Broad global music video watchers, ages 18-40",
        "age_min": 18,
        "age_max": 40,
        "genders": [],
        "geo": {"countries": ["US", "GB", "CA", "AU", "NG", "GH", "ZA", "FR", "DE"]},
        "interests": [
            "Music videos",
            "Hip hop music",
            "R&B music",
            "Afrobeats",
            "Music streaming",
            "YouTube Music",
            "Spotify",
            "Apple Music",
        ],
    },
    "fashion": {
        "description": "Broad fashion & lifestyle buyers, ages 18-40, US/UK/CA/AU",
        "age_min": 18,
        "age_max": 40,
        "genders": [],
        "geo": {"countries": ["US", "GB", "CA", "AU"]},
        "interests": [
            "Fashion",
            "Clothing",
            "Streetwear",
            "Online shopping",
            "Shopping",
            "Lifestyle (sociology)",
            "Instagram",
            "Style",
            "Shoes",
            "Sneakers",
        ],
    },
    "streetwear": {
        "description": "Streetwear / hiphop culture shoppers, ages 18-34, US/UK/CA",
        "age_min": 18,
        "age_max": 34,
        "genders": [],
        "geo": {"countries": ["US", "GB", "CA"]},
        "interests": [
            "Streetwear",
            "Sneakers",
            "Supreme (brand)",
            "Off-White",
            "Urban fashion",
            "Hip hop music",
            "Afrobeats",
            "Nike",
            "Jordan Brand",
            "Fashion",
            "Hypebeast",
        ],
    },
    "lagos": {
        "description": "Lagos State fashion & streetwear buyers, ages 20-40",
        "age_min": 20,
        "age_max": 40,
        "genders": [],
        "geo": {"regions": [{"key": "2607"}]},  # Lagos State, Nigeria
        "interests": [
            "Streetwear",
            "Fashion",
            "Clothing",
            "Urban fashion",
            "Afrobeats",
            "Hip hop music",
            "Online shopping",
            "Shopping",
            "Sneakers",
            "Style",
        ],
    },
}


_CACHE_PATH = Path(__file__).parent.parent / "data" / "fb_interest_cache.json"


def _load_cache() -> dict:
    if _CACHE_PATH.exists():
        return json.loads(_CACHE_PATH.read_text())
    return {}


def _save_cache(cache: dict) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(json.dumps(cache, indent=2))


def resolve_interests(interest_names: list[str]) -> list[dict]:
    """
    Convert a list of interest name strings into Facebook interest objects
    [{id, name}, ...] suitable for targeting_spec flexible_spec.
    Results are cached locally to avoid repeated API lookups.
    """
    cache = _load_cache()
    resolved: list[dict] = []
    updated = False

    for name in interest_names:
        if name in cache:
            resolved.append(cache[name])
            continue

        results = TargetingSearch.search(params={
            "q": name,
            "type": "adinterest",
            "limit": 10,
        })

        if not results:
            print(f"  [warn] No interest found for '{name}' — skipping")
            continue

        # Prefer exact name match; fall back to first result
        best = next(
            (r for r in results if r.get("name", "").lower() == name.lower()),
            results[0],
        )

        entry = {"id": best["id"], "name": best["name"]}
        cache[name] = entry
        resolved.append(entry)
        updated = True
        print(f"  Interest: '{name}' → '{best['name']}' (id {best['id']})")

    if updated:
        _save_cache(cache)

    return resolved


def build_targeting(profile_name: str) -> dict:
    """
    Build a complete Facebook targeting_spec dict for the given profile name.
    """
    profile = PROFILES.get(profile_name)
    if profile is None:
        raise ValueError(
            f"Unknown audience profile '{profile_name}'. "
            f"Choose from: {', '.join(PROFILES)}"
        )

    print(f"Resolving interests for audience '{profile_name}'...")
    interest_objects = resolve_interests(profile["interests"])

    targeting: dict = {
        "age_min": profile["age_min"],
        "age_max": profile["age_max"],
        "geo_locations": profile["geo"],
        "flexible_spec": [{"interests": interest_objects}],
        "targeting_automation": {"advantage_audience": 0},
    }
    if profile["genders"]:
        targeting["genders"] = profile["genders"]

    return targeting
