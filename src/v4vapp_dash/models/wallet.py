from typing import Literal

from pydantic import BaseModel, Field

Network = Literal["mainnet", "testnet", "regtest"]


class Derivation(BaseModel):
    account: int = 0
    change: int
    index: int
    path: str


class XpubMaterial(BaseModel):
    network: Network
    account_xpub: str
    master_fingerprint: str = Field(min_length=8, max_length=8)
