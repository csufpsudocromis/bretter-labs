VM_NETWORK_MODES = {"bridge", "none", "isolated", "unrestricted"}


def normalize_vm_network_mode(value: object) -> str:
    """Normalize VM network mode values from API/database into supported runtime modes."""
    mode = str(value or "bridge").strip().lower()
    # Host networking is a legacy option and maps to unrestricted pod networking.
    if mode == "host":
        return "unrestricted"
    if mode not in VM_NETWORK_MODES:
        return "bridge"
    return mode
