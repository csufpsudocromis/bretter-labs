from src.console_providers import normalize_vm_console_provider


def test_normalize_vm_console_provider_defaults_to_spice():
    assert normalize_vm_console_provider(None) == "spice"
    assert normalize_vm_console_provider("invalid") == "spice"


def test_normalize_vm_console_provider_maps_aliases():
    assert normalize_vm_console_provider("guacamole") == "guacamole"
    assert normalize_vm_console_provider("guac") == "guacamole"
    assert normalize_vm_console_provider("novnc") == "guacamole"
    assert normalize_vm_console_provider("vnc") == "guacamole"


def test_normalize_vm_console_provider_maps_guacamole_rdp_aliases():
    assert normalize_vm_console_provider("guacamole_rdp") == "guacamole_rdp"
    assert normalize_vm_console_provider("guacamole-rdp") == "guacamole_rdp"
    assert normalize_vm_console_provider("guac-rdp") == "guacamole_rdp"
    assert normalize_vm_console_provider("rdp") == "guacamole_rdp"


def test_normalize_vm_console_provider_keeps_spice():
    assert normalize_vm_console_provider("spice") == "spice"
    assert normalize_vm_console_provider("SPICE") == "spice"
