"""
Performance reporting for Facebook ad campaigns.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.campaign import Campaign
from facebook_business.adobjects.adsinsights import AdsInsights


INSIGHT_FIELDS = [
    AdsInsights.Field.campaign_name,
    AdsInsights.Field.campaign_id,
    AdsInsights.Field.impressions,
    AdsInsights.Field.reach,
    AdsInsights.Field.clicks,
    AdsInsights.Field.spend,
    AdsInsights.Field.video_thruplay_watched_actions,
    AdsInsights.Field.video_avg_time_watched_actions,
    AdsInsights.Field.actions,
    AdsInsights.Field.cost_per_action_type,
    AdsInsights.Field.cpm,
    AdsInsights.Field.ctr,
    AdsInsights.Field.date_start,
    AdsInsights.Field.date_stop,
]

CAMPAIGN_FIELDS = [
    Campaign.Field.id,
    Campaign.Field.name,
    Campaign.Field.objective,
    Campaign.Field.status,
    Campaign.Field.created_time,
    Campaign.Field.daily_budget,
    Campaign.Field.lifetime_budget,
]


def list_campaigns(ad_account_id: str) -> list[dict]:
    """Return all campaigns for the ad account (basic fields)."""
    account = AdAccount(ad_account_id)
    campaigns = account.get_campaigns(fields=CAMPAIGN_FIELDS)
    return [dict(c) for c in campaigns]


def get_campaign_insights(
    campaign_id: str,
    date_preset: str = "last_7d",
) -> Optional[dict]:
    """
    Fetch aggregate insights for a single campaign.
    date_preset options: today, yesterday, last_7d, last_30d, last_month, lifetime
    """
    campaign = Campaign(campaign_id)
    insights = campaign.get_insights(
        fields=INSIGHT_FIELDS,
        params={
            "date_preset": date_preset,
            "level": "campaign",
        },
    )
    return dict(insights[0]) if insights else None


def _fmt(val: Optional[str], decimals: int = 0) -> str:
    if val is None:
        return "—"
    try:
        n = float(val)
        if decimals:
            return f"{n:,.{decimals}f}"
        return f"{int(n):,}"
    except (ValueError, TypeError):
        return str(val)


def _find_action(actions: Optional[list], action_type: str) -> Optional[str]:
    if not actions:
        return None
    for a in actions:
        if a.get("action_type") == action_type:
            return a.get("value")
    return None


def print_campaign_list(campaigns: list[dict]) -> None:
    if not campaigns:
        print("No campaigns found.")
        return

    col_w = [30, 18, 20, 10, 15]
    header = (
        f"{'Name':<{col_w[0]}} {'ID':<{col_w[1]}} {'Objective':<{col_w[2]}} "
        f"{'Status':<{col_w[3]}} {'Created':<{col_w[4]}}"
    )
    print(header)
    print("─" * sum(col_w))
    for c in campaigns:
        created = c.get("created_time", "")[:10]
        print(
            f"{str(c.get('name',''))[:col_w[0]-1]:<{col_w[0]}} "
            f"{str(c.get('id','')):<{col_w[1]}} "
            f"{str(c.get('objective',''))[:col_w[2]-1]:<{col_w[2]}} "
            f"{str(c.get('status','')):<{col_w[3]}} "
            f"{created:<{col_w[4]}}"
        )


def print_campaign_report(insights: dict) -> None:
    actions = insights.get("actions", [])
    thruplay = _find_action(insights.get("video_thruplay_watched_actions"), "video_view")

    lines = [
        ("Campaign",    insights.get("campaign_name", "—")),
        ("ID",          insights.get("campaign_id", "—")),
        ("Period",      f"{insights.get('date_start','')} → {insights.get('date_stop','')}"),
        ("",            ""),
        ("Spend",       f"${_fmt(insights.get('spend'), 2)}"),
        ("Impressions", _fmt(insights.get("impressions"))),
        ("Reach",       _fmt(insights.get("reach"))),
        ("CPM",         f"${_fmt(insights.get('cpm'), 2)}"),
        ("CTR",         f"{_fmt(insights.get('ctr'), 2)}%"),
        ("Clicks",      _fmt(insights.get("clicks"))),
        ("ThruPlays",   _fmt(thruplay)),
        ("Post likes",  _fmt(_find_action(actions, "like"))),
        ("Comments",    _fmt(_find_action(actions, "comment"))),
        ("Shares",      _fmt(_find_action(actions, "share"))),
        ("Page likes",  _fmt(_find_action(actions, "page_like"))),
    ]
    max_label = max(len(l) for l, _ in lines if l)
    for label, value in lines:
        if label:
            print(f"  {label:<{max_label}}  {value}")
        else:
            print()
