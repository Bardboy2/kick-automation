"""
Core Meta Marketing API functions: campaign, adset, creative, ad creation.
Supports both Facebook posts and Instagram reels as ad creatives.
"""

import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.campaign import Campaign
from facebook_business.adobjects.adset import AdSet
from facebook_business.adobjects.adcreative import AdCreative
from facebook_business.adobjects.ad import Ad
from facebook_business.adobjects.advideo import AdVideo
from facebook_business.adobjects.page import Page
from facebook_business.adobjects.iguser import IGUser
from facebook_business.exceptions import FacebookRequestError

OBJECTIVE_MAP: dict[str, dict] = {
    "video_views": {
        "objective": Campaign.Objective.outcome_awareness,
        "optimization_goal": AdSet.OptimizationGoal.thruplay,
        "billing_event": AdSet.BillingEvent.thruplay,
        "cta_type": "WATCH_MORE",
    },
    "engagement": {
        "objective": Campaign.Objective.outcome_engagement,
        "optimization_goal": AdSet.OptimizationGoal.post_engagement,
        "billing_event": AdSet.BillingEvent.impressions,
        "cta_type": "LIKE_PAGE",
    },
    "reach": {
        "objective": Campaign.Objective.outcome_awareness,
        "optimization_goal": AdSet.OptimizationGoal.reach,
        "billing_event": AdSet.BillingEvent.impressions,
        "cta_type": "LEARN_MORE",
    },
    "traffic": {
        "objective": Campaign.Objective.outcome_traffic,
        "optimization_goal": AdSet.OptimizationGoal.link_clicks,
        "billing_event": AdSet.BillingEvent.impressions,
        "cta_type": "SHOP_NOW",
    },
    "page_likes": {
        "objective": Campaign.Objective.outcome_engagement,
        "optimization_goal": AdSet.OptimizationGoal.page_likes,
        "billing_event": AdSet.BillingEvent.impressions,
        "cta_type": "LIKE_PAGE",
    },
}

# Instagram-only placement targeting block
INSTAGRAM_PLACEMENTS = {
    "publisher_platforms": ["instagram"],
    "instagram_positions": ["reels", "stream", "story", "explore", "explore_home"],
}


# ── Instagram helpers ──────────────────────────────────────────────────────────

def get_instagram_actor_id(page_id: str) -> str:
    """Return the Instagram Business Account ID linked to a Facebook Page."""
    page = Page(page_id)
    result = page.api_get(fields=["instagram_business_account"])
    ig = result.get("instagram_business_account")
    if not ig:
        raise RuntimeError(
            f"No Instagram Business Account linked to Page {page_id}. "
            "Connect your Instagram account in Facebook Page Settings → Instagram."
        )
    ig_actor_id = ig["id"]
    print(f"  Instagram actor ID: {ig_actor_id}")
    return ig_actor_id


def instagram_url_to_media_id(url: str, ig_actor_id: str) -> str:
    """Look up the real Instagram media ID for a reel/post URL via the Graph API."""
    match = re.search(r'instagram\.com/(?:reels?|p|tv)/([A-Za-z0-9_-]+)', url)
    if not match:
        raise ValueError(
            f"Cannot extract shortcode from: {url}\n"
            "Expected: https://www.instagram.com/reel/SHORTCODE/"
        )
    shortcode = match.group(1)

    ig_user = IGUser(ig_actor_id)
    media_pages = ig_user.get_media(fields=["id", "permalink"], params={"limit": 50})
    for media in media_pages:
        if shortcode in media.get("permalink", ""):
            media_id = str(media["id"])
            print(f"  Resolved '{shortcode}' → media ID {media_id}")
            return media_id

    raise ValueError(
        f"Could not find reel '{shortcode}' in your Instagram account.\n"
        "Run 'python -m facebook_ads.cli list-reels' to see available posts."
    )


def list_instagram_media(ig_actor_id: str, limit: int = 20) -> list:
    """Return recent Instagram posts/reels with id, type, caption, permalink, timestamp."""
    ig_user = IGUser(ig_actor_id)
    media = ig_user.get_media(
        fields=["id", "media_type", "permalink", "caption", "timestamp"],
        params={"limit": limit},
    )
    return [dict(m) for m in media]


# ── Campaign / AdSet ───────────────────────────────────────────────────────────

def create_campaign(
    ad_account_id: str,
    name: str,
    objective: str,
    status: str = Campaign.Status.paused,
) -> str:
    """Create a campaign and return its ID. Starts paused by default."""
    obj_config = OBJECTIVE_MAP.get(objective)
    if obj_config is None:
        raise ValueError(
            f"Unknown objective '{objective}'. Choose from: {', '.join(OBJECTIVE_MAP)}"
        )

    account = AdAccount(ad_account_id)
    campaign = account.create_campaign(
        fields=[Campaign.Field.id],
        params={
            Campaign.Field.name: name,
            Campaign.Field.objective: obj_config["objective"],
            Campaign.Field.status: status,
            Campaign.Field.special_ad_categories: [],
            "is_adset_budget_sharing_enabled": False,
        },
    )
    campaign_id = campaign[Campaign.Field.id]
    print(f"  Campaign created: {campaign_id}")
    return campaign_id


def create_adset(
    ad_account_id: str,
    campaign_id: str,
    name: str,
    targeting: dict,
    daily_budget: float,
    objective: str,
    days: Optional[int] = None,
    instagram_only: bool = False,
    page_id: Optional[str] = None,
    status: str = AdSet.Status.paused,
) -> str:
    """
    Create an ad set and return its ID.
    daily_budget is in the ad account's currency (NGN for this account).
    Set instagram_only=True to restrict delivery to Instagram placements only.
    """
    obj_config = OBJECTIVE_MAP[objective]
    # Meta API expects budget in the smallest currency unit (kobo for NGN, cents for USD)
    daily_budget_minor = int(daily_budget * 100)

    full_targeting = dict(targeting)
    if instagram_only:
        full_targeting.update(INSTAGRAM_PLACEMENTS)

    params: dict = {
        AdSet.Field.name: name,
        AdSet.Field.campaign_id: campaign_id,
        AdSet.Field.daily_budget: daily_budget_minor,
        AdSet.Field.billing_event: obj_config["billing_event"],
        AdSet.Field.optimization_goal: obj_config["optimization_goal"],
        AdSet.Field.targeting: full_targeting,
        AdSet.Field.status: status,
        AdSet.Field.bid_strategy: AdSet.BidStrategy.lowest_cost_without_cap,
    }

    if days:
        end_dt = datetime.now(timezone.utc) + timedelta(days=days)
        params[AdSet.Field.end_time] = end_dt.strftime("%Y-%m-%dT%H:%M:%S+0000")

    account = AdAccount(ad_account_id)
    adset = account.create_ad_set(fields=[AdSet.Field.id], params=params)
    adset_id = adset[AdSet.Field.id]
    print(f"  Ad set created: {adset_id}")
    return adset_id


# ── Creatives ─────────────────────────────────────────────────────────────────

def create_creative_from_instagram_reel(
    ad_account_id: str,
    ig_actor_id: str,
    media_id: str,
    name: str = "Creative",
    link_url: Optional[str] = None,
) -> str:
    """Create an ad creative from an existing Instagram post or reel."""
    account = AdAccount(ad_account_id)
    destination = link_url or "https://www.instagram.com/owtygram"
    params: dict = {
        AdCreative.Field.name: name,
        "source_instagram_media_id": media_id,
        "instagram_user_id": ig_actor_id,
        "call_to_action": {"type": "SHOP_NOW", "value": {"link": destination}},
    }
    creative = account.create_ad_creative(
        fields=[AdCreative.Field.id],
        params=params,
    )
    creative_id = creative[AdCreative.Field.id]
    print(f"  Creative (from Instagram reel) created: {creative_id}")
    return creative_id


def create_creative_from_post(
    ad_account_id: str,
    page_id: str,
    post_id: str,
    name: str = "Creative",
) -> str:
    """Create an ad creative from an existing Facebook Page post."""
    account = AdAccount(ad_account_id)
    creative = account.create_ad_creative(
        fields=[AdCreative.Field.id],
        params={
            AdCreative.Field.name: name,
            AdCreative.Field.object_story_id: f"{page_id}_{post_id}",
        },
    )
    creative_id = creative[AdCreative.Field.id]
    print(f"  Creative (from FB post) created: {creative_id}")
    return creative_id


def create_creative_from_video(
    ad_account_id: str,
    page_id: str,
    video_id: str,
    message: str,
    headline: str,
    link_url: Optional[str],
    cta_type: str = "WATCH_MORE",
    name: str = "Creative",
) -> str:
    """Create an ad creative from an uploaded Facebook video."""
    video_data: dict = {
        "video_id": video_id,
        "title": headline,
        "message": message,
    }
    if link_url:
        video_data["call_to_action"] = {
            "type": cta_type,
            "value": {"link": link_url},
        }

    account = AdAccount(ad_account_id)
    creative = account.create_ad_creative(
        fields=[AdCreative.Field.id],
        params={
            AdCreative.Field.name: name,
            AdCreative.Field.object_story_spec: {
                "page_id": page_id,
                "video_data": video_data,
            },
        },
    )
    creative_id = creative[AdCreative.Field.id]
    print(f"  Creative (from video) created: {creative_id}")
    return creative_id


def create_creative_from_link(
    ad_account_id: str,
    page_id: str,
    link_url: str,
    message: str,
    headline: str,
    image_url: Optional[str] = None,
    cta_type: str = "LEARN_MORE",
    name: str = "Creative",
) -> str:
    """Create a link ad creative (drives traffic to an external URL)."""
    link_data: dict = {
        "link": link_url,
        "message": message,
        "name": headline,
        "call_to_action": {"type": cta_type},
    }
    if image_url:
        link_data["picture"] = image_url

    account = AdAccount(ad_account_id)
    creative = account.create_ad_creative(
        fields=[AdCreative.Field.id],
        params={
            AdCreative.Field.name: name,
            AdCreative.Field.object_story_spec: {
                "page_id": page_id,
                "link_data": link_data,
            },
        },
    )
    creative_id = creative[AdCreative.Field.id]
    print(f"  Creative (link ad) created: {creative_id}")
    return creative_id


def upload_video(ad_account_id: str, video_path: str) -> str:
    """Upload a local video and return its ID. Polls until processing is done."""
    print(f"  Uploading video: {video_path}")
    video = AdVideo(parent_id=ad_account_id)
    video[AdVideo.Field.filepath] = video_path
    video.remote_create()
    video_id = video[AdVideo.Field.id]
    print(f"  Video uploaded: {video_id} — waiting for processing...")

    for _ in range(60):
        time.sleep(10)
        status_obj = AdVideo(video_id).api_get(fields=["status"])
        processing_status = status_obj.get("status", {}).get("video_status", "")
        if processing_status == "ready":
            print("  Video ready.")
            return video_id
        if processing_status == "error":
            raise RuntimeError(f"Facebook video processing failed for {video_id}")

    raise TimeoutError("Video processing timed out after 10 minutes.")


# ── Ad ────────────────────────────────────────────────────────────────────────

def create_ad(
    ad_account_id: str,
    adset_id: str,
    creative_id: str,
    name: str,
    status: str = Ad.Status.paused,
) -> str:
    """Create the final Ad object linking creative + ad set."""
    account = AdAccount(ad_account_id)
    ad = account.create_ad(
        fields=[Ad.Field.id],
        params={
            Ad.Field.name: name,
            Ad.Field.adset_id: adset_id,
            Ad.Field.creative: {"creative_id": creative_id},
            Ad.Field.status: status,
        },
    )
    ad_id = ad[Ad.Field.id]
    print(f"  Ad created: {ad_id}")
    return ad_id


# ── Campaign management ───────────────────────────────────────────────────────

def set_campaign_status(campaign_id: str, status: str) -> None:
    Campaign(campaign_id).api_update(params={Campaign.Field.status: status})


def delete_campaign(campaign_id: str) -> None:
    Campaign(campaign_id).api_delete()
