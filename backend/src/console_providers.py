VM_CONSOLE_PROVIDERS = {"spice", "guacamole"}


def normalize_vm_console_provider(value: object) -> str:
    provider = str(value or "spice").strip().lower()
    if provider in {"guacamole", "guac", "vnc", "novnc"}:
        return "guacamole"
    if provider in VM_CONSOLE_PROVIDERS:
        return provider
    return "spice"
