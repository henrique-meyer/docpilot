from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_compose_stores_uploads_in_azurite() -> None:
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "image: mcr.microsoft.com/azure-storage/azurite:3.33.0" in text
    assert "BlobEndpoint=http://azurite:10000/devstoreaccount1;" in text
    assert "--blobHost 0.0.0.0" in text
