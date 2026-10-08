import os
from pathlib import Path
from dotenv import load_dotenv
from facebook_business.api import FacebookAdsApi

_env_path = Path(__file__).parent.parent / "config" / ".env"
load_dotenv(_env_path)

FB_APP_ID = os.getenv("FB_APP_ID", "")
FB_APP_SECRET = os.getenv("FB_APP_SECRET", "")
FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN", "")
FB_AD_ACCOUNT_ID = os.getenv("FB_AD_ACCOUNT_ID", "")       # e.g. act_1234567890
FB_PAGE_ID = os.getenv("FB_PAGE_ID", "")                   # Facebook Page ID
FB_INSTAGRAM_ACTOR_ID = os.getenv("FB_INSTAGRAM_ACTOR_ID", "")  # Instagram Business Account ID


def init_api() -> None:
    missing = [k for k, v in {
        "FB_APP_ID": FB_APP_ID,
        "FB_APP_SECRET": FB_APP_SECRET,
        "FB_ACCESS_TOKEN": FB_ACCESS_TOKEN,
        "FB_AD_ACCOUNT_ID": FB_AD_ACCOUNT_ID,
        "FB_PAGE_ID": FB_PAGE_ID,
    }.items() if not v]
    if missing:
        raise EnvironmentError(
            f"Missing Facebook credentials in config/.env: {', '.join(missing)}\n"
            "See the 'Facebook API Setup' section in the README."
        )
    FacebookAdsApi.init(FB_APP_ID, FB_APP_SECRET, FB_ACCESS_TOKEN)
