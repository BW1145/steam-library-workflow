"""Resolve the Steam type collections from saved gameplay and multiplayer decisions."""

def type_collections(classification, network=None):
    if classification.get("primary_type") == "黄油":
        return ["黄油"]
    if (network or {}).get("collection_policy") == "multiplayer_only":
        return []
    if classification.get("primary_type") == "其他":
        return []
    names = [classification.get("primary_type"), *classification.get("secondary_types", [])]
    return list(dict.fromkeys(name for name in names if name))
