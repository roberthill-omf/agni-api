import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv


@dataclass(frozen=True)
class AgniConfig:
    org_id: str
    key_id: str
    key_value: str
    api_base: str = "https://ag01c01.agni.arista.io/api"
    auth_url: str = "https://ag01c01.agni.arista.io/cvcue/keyLogin"

    @classmethod
    def from_env(cls, dotenv_path: Optional[str] = None) -> "AgniConfig":
        """Load configuration from environment variables, optionally loading a .env first."""
        load_dotenv(dotenv_path=dotenv_path)

        org_id = os.getenv("AGNI_ORG_ID")
        key_id = os.getenv("KEY_ID")
        key_value = os.getenv("KEY_VALUE")

        missing = [name for name, value in [("AGNI_ORG_ID", org_id), ("KEY_ID", key_id), ("KEY_VALUE", key_value)] if not value]
        if missing:
            missing_vars = ", ".join(missing)
            raise ValueError(f"Missing required environment variables: {missing_vars}")

        return cls(org_id=org_id, key_id=key_id, key_value=key_value)
