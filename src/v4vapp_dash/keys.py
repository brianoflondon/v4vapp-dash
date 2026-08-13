import json
from pathlib import Path

from v4vapp_dash.config import Settings
from v4vapp_dash.models.wallet import XpubMaterial


def load_xpub_material(settings: Settings) -> XpubMaterial | None:
    if settings.dash_xpub and settings.dash_master_fingerprint:
        return XpubMaterial(
            network=settings.dash_network,
            account_xpub=settings.dash_xpub,
            master_fingerprint=settings.dash_master_fingerprint,
        )
    if settings.dash_xpub_file:
        path = Path(settings.dash_xpub_file)
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            material = XpubMaterial.model_validate(data)
            if material.network != settings.dash_network:
                raise ValueError(
                    f"xpub file network {material.network} != DASH_NETWORK {settings.dash_network}"
                )
            return material
    return None
