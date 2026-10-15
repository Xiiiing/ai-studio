"""LAN gateway admission: never install services or read private credentials."""
import pytest

from scripts.preview_lan import endpoint


@pytest.mark.parametrize("host", ["0.0.0.0", "8.8.8.8", "127.0.0.1", "100.82.71.126", "::1", "example.com", "172.20.12.202\nrespond 200"])
def test_gateway_refuses_public_wildcard_and_non_lan_interfaces(host):
    with pytest.raises(ValueError):
        endpoint(host, 8788)


@pytest.mark.parametrize("port", [0, 80, 65536, True, "8788"])
def test_gateway_refuses_invalid_or_privileged_ports(port):
    with pytest.raises(ValueError):
        endpoint("172.20.12.202", port)


@pytest.mark.parametrize("host", ["172.20.12.202", "10.0.0.10", "192.168.1.10"])
def test_gateway_supports_private_lan_interfaces(host):
    assert endpoint(host, 8788) == host + ":8788"
