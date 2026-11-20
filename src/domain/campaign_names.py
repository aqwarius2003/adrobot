"""Campaign name rules shared by the creator and the demo tracker."""


class CampaignNameConflictError(ValueError):
    def __init__(self):
        super().__init__("Кампания с таким названием уже существует. Укажите другое название.")


def normalize_campaign_name(raw_name: str) -> str:
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise ValueError("Укажите название кампании.")
    return raw_name.strip()


def campaign_name_key(name: str) -> str:
    return str(name or "").strip().casefold()
