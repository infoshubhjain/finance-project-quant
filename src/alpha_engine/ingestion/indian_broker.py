"""Broker-agnostic scaffolding for Indian market ingestion.

The live provider clients belong here in spirit, but they should stay separate
from the normalization code in `indian_fno.py`. This module defines the shape
of a broker session and the config we expect from env vars so a real Angel One
or Breeze client can be slotted in without touching analyzers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class IndianBroker(str, Enum):
    ANGEL_ONE = "angel_one"
    BREEZE = "breeze"
    DHAN = "dhan"


@dataclass(frozen=True, slots=True)
class BrokerCredentials:
    """Credentials needed by a live broker client.

    The fields are deliberately generic enough to cover either provider's auth
    scheme without baking in provider-specific HTTP logic here.
    """

    broker: IndianBroker
    api_key: str
    api_secret: str | None = None
    client_id: str | None = None
    access_token: str | None = None
    user_id: str | None = None


class BrokerNotConfiguredError(RuntimeError):
    """Raised when a requested provider is missing its required environment."""


def load_broker_credentials(broker: IndianBroker) -> BrokerCredentials:
    """Load the env contract for a provider.

    This is a small but useful seam: the real HTTP client can call this and fail
    fast with a descriptive message if the user's env is incomplete.
    """
    if broker is IndianBroker.ANGEL_ONE:
        api_key = os.getenv("ANGEL_ONE_API_KEY")
        if not api_key:
            raise BrokerNotConfiguredError("ANGEL_ONE_API_KEY is required for Angel One")
        return BrokerCredentials(
            broker=broker,
            api_key=api_key,
            api_secret=os.getenv("ANGEL_ONE_API_SECRET"),
            client_id=os.getenv("ANGEL_ONE_CLIENT_ID"),
            access_token=os.getenv("ANGEL_ONE_ACCESS_TOKEN"),
        )

    if broker is IndianBroker.DHAN:
        client_id = os.getenv("DHAN_CLIENT_ID")
        if not client_id:
            raise BrokerNotConfiguredError("DHAN_CLIENT_ID is required for Dhan")
        access_token = os.getenv("DHAN_ACCESS_TOKEN")
        if not access_token:
            raise BrokerNotConfiguredError("DHAN_ACCESS_TOKEN is required for Dhan")
        return BrokerCredentials(
            broker=broker,
            api_key=client_id,
            client_id=client_id,
            access_token=access_token,
        )

    api_key = os.getenv("BREEZE_API_KEY")
    if not api_key:
        raise BrokerNotConfiguredError("BREEZE_API_KEY is required for Breeze")
    return BrokerCredentials(
        broker=broker,
        api_key=api_key,
        api_secret=os.getenv("BREEZE_API_SECRET"),
        client_id=os.getenv("BREEZE_CLIENT_ID"),
        user_id=os.getenv("BREEZE_USER_ID"),
        access_token=os.getenv("BREEZE_SESSION_TOKEN"),
    )
