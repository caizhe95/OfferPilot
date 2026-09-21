"""Versioned provider price snapshots used only when the configured model matches."""

from __future__ import annotations

from typing import NotRequired, TypedDict


class PriceSnapshot(TypedDict):
    version: str
    source_url: str
    currency: str
    effective_at: str
    input_per_million: str
    output_per_million: str
    audio_per_minute: NotRequired[str]


class PriceFields(TypedDict, total=False):
    price_version: str
    price_source_url: str
    price_currency: str
    price_effective_at: str
    input_price_per_million: str
    output_price_per_million: str
    audio_price_per_minute: str


# The default project model is provider-specific and has no frozen public price
# in this repository. Unknown prices deliberately remain unknown until reviewed.
PRICE_SNAPSHOTS: dict[tuple[str, str], PriceSnapshot] = {}


def price_for(provider: str, model: str) -> PriceSnapshot | None:
    return PRICE_SNAPSHOTS.get((provider.strip().lower(), model.strip().lower()))


def price_fields(provider: str, model: str) -> PriceFields:
    snapshot = price_for(provider, model)
    if snapshot is None:
        return {}
    fields: PriceFields = {
        "price_version": snapshot["version"],
        "price_source_url": snapshot["source_url"],
        "price_currency": snapshot["currency"],
        "price_effective_at": snapshot["effective_at"],
        "input_price_per_million": snapshot["input_per_million"],
        "output_price_per_million": snapshot["output_per_million"],
    }
    if snapshot.get("audio_per_minute"):
        fields["audio_price_per_minute"] = snapshot["audio_per_minute"]
    return fields
