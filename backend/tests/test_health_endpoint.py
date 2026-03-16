def test_health_endpoint_reports_ok(client):
    response = client.get("/health")
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok"}
