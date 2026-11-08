"""Legacy shim: new path app.application.magic_regex (S4-6, 2026-11-09). Remove one release later."""
from app.application.magic_regex import (  # noqa: F401
    builtin_magic_regex,
    builtin_magic_regex_labels,
    BUILTIN_VARIABLE_LABELS,
    _validate_regex_pattern,
    get_rule,
    list_rules,
    list_enabled_effective_rules_for_picker,
    get_enabled_magic_regex_map,
    upsert_rule,
    delete_rule,
)
