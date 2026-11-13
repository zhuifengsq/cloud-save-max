"""Lead-in capability assembly, isolated from application orchestration."""

import logging
from app.infrastructure.drive.factory import AdapterFactory
from app.infrastructure.drive.adapter_registry import AdapterRegistry
from app.domain.ports.lead_in import LeadInPort

logger = logging.getLogger(__name__)


class LeadInProvider:
    def create(self, account) -> LeadInPort | None:
        if account is None:
            return None
        try:
            drive_type = str(account.drive_type or "")
            config = AdapterRegistry.parse_config_json(
                drive_type, account.config_json, account.cookie
            )
            cookie = AdapterRegistry.serialize_config(drive_type, config)
            return AdapterFactory.create_adapter(
                drive_type,
                cookie,
                0,
                config=config,
                account_name=account.name,
                no_login=False,
            )
        except Exception:
            logger.warning("Lead-in capability unavailable account=%s", account.name)
            return None
