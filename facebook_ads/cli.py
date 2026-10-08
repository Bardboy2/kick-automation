#!/usr/bin/env python3
"""
Facebook Ads CLI — promote music/clothing content to hip-hop / afrobeat / trap audiences.

Usage examples:

  # Boost an existing Instagram reel (most common)
  python facebook_ads/cli.py create \\
    --name "Anti System Tee" \\
    --reel-id 1234567890123456 \\
    --audience streetwear \\
    --objective video_views \\
    --budget 2000 \\
    --days 5

  # List your Instagram reels/posts with their IDs
  python facebook_ads/cli.py list-reels

  # Boost an existing Facebook post
  python facebook_ads/cli.py create \\
    --name "Summer Banger" \\
    --post-id 987654321 \\
    --audience afrobeat \\
    --objective video_views \\
    --budget 2000 \\
    --days 7

  # Upload a new video
  python facebook_ads/cli.py create \\
    --name "New Drop" \\
    --video-path ./clips/clip.mp4 \\
    --message "New banger just dropped #hiphop #afrobeats" \\
    --headline "OWTY — New Single" \\
    --audience all \\
    --objective video_views \\
    --budget 2000

  # Link ad (traffic to external URL)
  python facebook_ads/cli.py create \\
    --name "Kick Stream" \\
    --link-url https://kick.com/owtyofficial \\
    --message "Live now on Kick" \\
    --headline "Join the stream" \\
    --audience hiphop \\
    --objective traffic \\
    --budget 1500

  # List campaigns, get report, pause/resume/delete
  python facebook_ads/cli.py list
  python facebook_ads/cli.py report --id 1234567890
  python facebook_ads/cli.py pause  --id 1234567890
  python facebook_ads/cli.py resume --id 1234567890
  python facebook_ads/cli.py delete --id 1234567890

  # Search interest IDs / show audience profiles
  python facebook_ads/cli.py search-interests "afrobeats"
  python facebook_ads/cli.py audiences

NOTE: --budget is in your ad account currency (NGN).
      ₦2000/day is roughly $1.30/day at current rates.
"""

import argparse
import sys

from facebook_business.adobjects.targetingsearch import TargetingSearch
from facebook_business.adobjects.campaign import Campaign
from facebook_business.exceptions import FacebookRequestError

from facebook_ads.config import init_api, FB_AD_ACCOUNT_ID, FB_PAGE_ID, FB_INSTAGRAM_ACTOR_ID
from facebook_ads import audiences as aud
from facebook_ads import api
from facebook_ads import reports


# ── Subcommand handlers ────────────────────────────────────────────────────────

def cmd_create(args: argparse.Namespace) -> None:
    init_api()

    # --reel-url is a convenience alias: look up real media ID via the API
    if args.reel_url and not args.reel_id:
        ig_actor_id = FB_INSTAGRAM_ACTOR_ID or api.get_instagram_actor_id(FB_PAGE_ID)
        args.reel_id = api.instagram_url_to_media_id(args.reel_url, ig_actor_id)
        print(f"  Reel URL → media ID: {args.reel_id}")

    sources = [bool(args.reel_id), bool(args.post_id), bool(args.video_path), bool(args.link_url and not args.reel_id and not args.post_id and not args.video_path)]
    if sum(sources) == 0:
        sys.exit("Error: provide one of --reel-url, --reel-id, --post-id, --video-path, or --link-url")
    if sum(sources) > 1:
        sys.exit("Error: provide only one creative source")

    if args.objective not in api.OBJECTIVE_MAP:
        sys.exit(f"Error: unknown objective '{args.objective}'. Choose from: {', '.join(api.OBJECTIVE_MAP)}")

    # Instagram reels always target Instagram placements only
    instagram_only = bool(args.reel_id) or args.instagram_only

    print(f"\n── Creating campaign: {args.name!r} ──")
    if instagram_only:
        print("  Placement: Instagram only (reels, feed, stories, explore)")

    # 1. Targeting
    targeting = aud.build_targeting(args.audience)

    # 2. Campaign
    campaign_id = api.create_campaign(
        ad_account_id=FB_AD_ACCOUNT_ID,
        name=args.name,
        objective=args.objective,
        status=Campaign.Status.paused,
    )

    # 3. Ad set
    adset_id = api.create_adset(
        ad_account_id=FB_AD_ACCOUNT_ID,
        campaign_id=campaign_id,
        name=f"{args.name} — AdSet",
        targeting=targeting,
        daily_budget=args.budget,
        objective=args.objective,
        days=args.days,
        instagram_only=instagram_only,
        page_id=FB_PAGE_ID,
    )

    # 4. Creative
    cta_type = api.OBJECTIVE_MAP[args.objective]["cta_type"]

    if args.reel_id:
        ig_actor_id = FB_INSTAGRAM_ACTOR_ID or api.get_instagram_actor_id(FB_PAGE_ID)
        creative_id = api.create_creative_from_instagram_reel(
            ad_account_id=FB_AD_ACCOUNT_ID,
            ig_actor_id=ig_actor_id,
            media_id=args.reel_id,
            name=f"{args.name} — Creative",
            link_url=args.link_url or None,
        )
    elif args.post_id:
        creative_id = api.create_creative_from_post(
            ad_account_id=FB_AD_ACCOUNT_ID,
            page_id=FB_PAGE_ID,
            post_id=args.post_id,
            name=f"{args.name} — Creative",
        )
    elif args.video_path:
        video_id = api.upload_video(FB_AD_ACCOUNT_ID, args.video_path)
        creative_id = api.create_creative_from_video(
            ad_account_id=FB_AD_ACCOUNT_ID,
            page_id=FB_PAGE_ID,
            video_id=video_id,
            message=args.message or "",
            headline=args.headline or args.name,
            link_url=args.link_url,
            cta_type=cta_type,
            name=f"{args.name} — Creative",
        )
    else:
        if not args.message or not args.headline:
            sys.exit("Error: --message and --headline are required for link ads")
        creative_id = api.create_creative_from_link(
            ad_account_id=FB_AD_ACCOUNT_ID,
            page_id=FB_PAGE_ID,
            link_url=args.link_url,
            message=args.message,
            headline=args.headline,
            image_url=args.image_url,
            cta_type=cta_type,
            name=f"{args.name} — Creative",
        )

    # 5. Ad
    ad_id = api.create_ad(
        ad_account_id=FB_AD_ACCOUNT_ID,
        adset_id=adset_id,
        creative_id=creative_id,
        name=f"{args.name} — Ad",
    )

    print(f"""
Done! Campaign created in PAUSED state.
  Campaign ID : {campaign_id}
  Ad Set ID   : {adset_id}
  Creative ID : {creative_id}
  Ad ID       : {ad_id}

Review and activate in Meta Ads Manager:
  https://adsmanager.facebook.com/
""")


def cmd_list_reels(args: argparse.Namespace) -> None:
    """List recent Instagram posts/reels with their media IDs."""
    init_api()
    ig_actor_id = FB_INSTAGRAM_ACTOR_ID or api.get_instagram_actor_id(FB_PAGE_ID)

    try:
        print("\nFetching Instagram media...\n")
        media_list = api.list_instagram_media(ig_actor_id, limit=20)
    except Exception:
        # instagram_basic permission not granted — guide user to use reel URL instead
        print(
            "Could not list media via API (requires instagram_basic permission).\n\n"
            "Use --reel-url instead:\n"
            "  1. Open the reel on Instagram\n"
            "  2. Tap the 3 dots → Copy link\n"
            "  3. Run:\n"
            "     python -m facebook_ads.cli create --name '...' \\\n"
            "       --reel-url 'https://www.instagram.com/reel/SHORTCODE/' \\\n"
            "       --audience streetwear --objective video_views --budget 2000\n"
        )
        return

    if not media_list:
        print("No Instagram posts found.")
        return

    print(f"{'Media ID':<22} {'Type':<10} {'Date':<14} {'Caption'}")
    print("─" * 80)
    for m in media_list:
        media_id = str(m.get("id", ""))
        media_type = str(m.get("media_type", ""))
        timestamp = str(m.get("timestamp", ""))[:10]
        caption = str(m.get("caption", "")).replace("\n", " ")[:40]
        print(f"{media_id:<22} {media_type:<10} {timestamp:<14} {caption}")

    print(f"\nUse with:  python -m facebook_ads.cli create --reel-id MEDIA_ID ...\n")


def cmd_list(args: argparse.Namespace) -> None:
    init_api()
    print()
    campaigns = reports.list_campaigns(FB_AD_ACCOUNT_ID)
    reports.print_campaign_list(campaigns)
    print(f"\n{len(campaigns)} campaign(s) total.\n")


def cmd_report(args: argparse.Namespace) -> None:
    init_api()
    preset = getattr(args, "preset", "last_7d")
    print(f"\n── Report: campaign {args.id} ({preset}) ──\n")
    insights = reports.get_campaign_insights(args.id, date_preset=preset)
    if insights is None:
        print("No data available for this period.")
    else:
        reports.print_campaign_report(insights)
    print()


def cmd_pause(args: argparse.Namespace) -> None:
    init_api()
    api.set_campaign_status(args.id, Campaign.Status.paused)
    print(f"Campaign {args.id} paused.")


def cmd_resume(args: argparse.Namespace) -> None:
    init_api()
    api.set_campaign_status(args.id, Campaign.Status.active)
    print(f"Campaign {args.id} activated.")


def cmd_delete(args: argparse.Namespace) -> None:
    init_api()
    confirm = input(f"Delete campaign {args.id}? This cannot be undone. [y/N] ")
    if confirm.lower() != "y":
        print("Aborted.")
        return
    api.delete_campaign(args.id)
    print(f"Campaign {args.id} deleted.")


def cmd_search_interests(args: argparse.Namespace) -> None:
    init_api()
    print(f"\nSearching interests for: {args.query!r}\n")
    results = TargetingSearch.search(params={"q": args.query, "type": "adinterest", "limit": 15})
    if not results:
        print("No results found.")
        return
    print(f"{'Name':<40} {'ID':<20} {'Audience size'}")
    print("─" * 75)
    for r in results:
        name = str(r.get("name", ""))[:38]
        rid = str(r.get("id", ""))
        size = r.get("audience_size_lower_bound", "")
        print(f"{name:<40} {rid:<20} {size:,}" if isinstance(size, int) else f"{name:<40} {rid:<20} {size}")
    print()


def cmd_audiences(args: argparse.Namespace) -> None:
    print("\nAvailable audience profiles:\n")
    for name, profile in aud.PROFILES.items():
        countries = ", ".join(profile["geo"]["countries"])
        print(f"  {name:<15} {profile['description']}")
        print(f"  {'':15} Countries: {countries}")
        print(f"  {'':15} Age: {profile['age_min']}–{profile['age_max']}")
        print()


# ── Argument parser ────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python facebook_ads/cli.py",
        description="Facebook/Instagram Ads manager for music and clothing content",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # create
    p_create = sub.add_parser("create", help="Create a new ad campaign")
    p_create.add_argument("--name", required=True, help="Campaign name")
    p_create.add_argument(
        "--audience", default="all", choices=list(aud.PROFILES.keys()),
        help="Predefined audience profile (default: all)",
    )
    p_create.add_argument(
        "--objective", default="video_views", choices=list(api.OBJECTIVE_MAP.keys()),
        help="Campaign objective (default: video_views)",
    )
    p_create.add_argument(
        "--budget", type=float, default=2000.0,
        help="Daily budget in your account currency — NGN (default: ₦2000)",
    )
    p_create.add_argument("--days", type=int, default=None, help="Run for N days (no limit if omitted)")
    p_create.add_argument("--instagram-only", action="store_true", help="Restrict to Instagram placements only")

    # creative sources
    p_create.add_argument("--reel-url", default=None, help="Instagram reel URL (e.g. https://www.instagram.com/reel/ABC123/) — easiest way to boost a reel")
    p_create.add_argument("--reel-id", default=None, help="Instagram reel numeric media ID (alternative to --reel-url)")
    p_create.add_argument("--post-id", default=None, help="Boost an existing Facebook Page post by its numeric ID")
    p_create.add_argument("--video-path", default=None, help="Upload a local video file")
    p_create.add_argument("--link-url", default=None, help="External URL (YouTube, Kick, Spotify, etc.)")

    # creative metadata
    p_create.add_argument("--message", default=None, help="Ad copy / caption text")
    p_create.add_argument("--headline", default=None, help="Ad headline")
    p_create.add_argument("--image-url", default=None, help="Thumbnail image URL (for link ads)")

    # list-reels
    sub.add_parser("list-reels", help="List your Instagram posts/reels with media IDs")

    # list campaigns
    sub.add_parser("list", help="List all campaigns in the ad account")

    # report
    p_report = sub.add_parser("report", help="Show performance stats for a campaign")
    p_report.add_argument("--id", required=True, help="Campaign ID")
    p_report.add_argument(
        "--preset", default="last_7d",
        choices=["today", "yesterday", "last_7d", "last_30d", "last_month", "lifetime"],
    )

    # pause / resume / delete
    for cmd in ("pause", "resume", "delete"):
        p = sub.add_parser(cmd, help=f"{cmd.capitalize()} a campaign")
        p.add_argument("--id", required=True, help="Campaign ID")

    # search-interests
    p_search = sub.add_parser("search-interests", help="Find Facebook interest IDs by keyword")
    p_search.add_argument("query", help="Search term (e.g. 'afrobeats')")

    # audiences
    sub.add_parser("audiences", help="List available audience targeting profiles")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    handlers = {
        "create": cmd_create,
        "list-reels": cmd_list_reels,
        "list": cmd_list,
        "report": cmd_report,
        "pause": cmd_pause,
        "resume": cmd_resume,
        "delete": cmd_delete,
        "search-interests": cmd_search_interests,
        "audiences": cmd_audiences,
    }

    try:
        handlers[args.command](args)
    except FacebookRequestError as e:
        print(f"\nFacebook API error: {e.api_error_message()}")
        print(f"  Code: {e.api_error_code()}  Subcode: {e.api_error_subcode()}")
        print(f"  Body: {e.body()}")
        sys.exit(1)
    except (EnvironmentError, ValueError, RuntimeError, TimeoutError) as e:
        print(f"\nError: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
