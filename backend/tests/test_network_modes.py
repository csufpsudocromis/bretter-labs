from src.network_modes import normalize_vm_network_mode


def test_normalize_vm_network_mode_defaults_to_bridge():
    assert normalize_vm_network_mode(None) == "bridge"
    assert normalize_vm_network_mode("invalid") == "bridge"


def test_normalize_vm_network_mode_maps_host_to_unrestricted():
    assert normalize_vm_network_mode("host") == "unrestricted"
    assert normalize_vm_network_mode("HOST") == "unrestricted"


def test_normalize_vm_network_mode_keeps_supported_modes():
    assert normalize_vm_network_mode("bridge") == "bridge"
    assert normalize_vm_network_mode("unrestricted") == "unrestricted"
    assert normalize_vm_network_mode("none") == "none"
    assert normalize_vm_network_mode("isolated") == "isolated"
