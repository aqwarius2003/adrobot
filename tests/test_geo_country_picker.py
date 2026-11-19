"""Tests for GEO country picker, catalog, and routing preview."""

import pytest
from fastapi.testclient import TestClient

from src.domain.iso_countries import (
    COUNTRY_CATALOG,
    COUNTRY_MAP,
    ISO_COUNTRY_CODES,
    get_country_by_code,
    is_valid_iso_country,
)
from src.main import create_app


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


# ============================================================================
# 1. Catalog Integrity & Single Source of Truth
# ============================================================================


def test_catalog_has_all_249_iso_countries():
    assert len(COUNTRY_CATALOG) == 249
    catalog_codes = {c["code"] for c in COUNTRY_CATALOG}
    assert catalog_codes == ISO_COUNTRY_CODES
    assert len(COUNTRY_MAP) == 249


def test_country_entries_have_required_fields():
    for item in COUNTRY_CATALOG:
        assert "code" in item and len(item["code"]) == 2
        assert item["code"].isupper()
        assert "name_ru" in item and len(item["name_ru"]) > 0
        assert "name_en" in item and len(item["name_en"]) > 0
        assert "aliases" in item and isinstance(item["aliases"], list)


def test_iso_helper_functions():
    assert is_valid_iso_country("KZ") is True
    assert is_valid_iso_country("kz") is True
    assert is_valid_iso_country("US") is True
    assert is_valid_iso_country("XX") is False
    assert is_valid_iso_country("") is False

    kz = get_country_by_code("kz")
    assert kz is not None
    assert kz["name_ru"] == "Казахстан"
    assert kz["name_en"] == "Kazakhstan"

    none_country = get_country_by_code("ZZ")
    assert none_country is None


# ============================================================================
# 2. Endpoint GET /api/geo/countries
# ============================================================================


def test_get_geo_countries_endpoint(client: TestClient):
    response = client.get("/api/geo/countries")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 249
    assert response.headers.get("cache-control") == "public, max-age=86400"

    # Specific countries verification
    by_code = {c["code"]: c for c in data}
    assert by_code["KZ"]["name_ru"] == "Казахстан"
    assert by_code["CA"]["name_ru"] == "Канада"
    assert "США" in by_code["US"]["aliases"]


# ============================================================================
# 3. Search & Ranking Validation (Matching Task Requirements)
# ============================================================================


def search_ranking(query: str, items: list[dict]) -> list[dict]:
    """Mirror of frontend ranking algorithm."""
    q = query.strip().lower().replace("ё", "е")
    if not q:
        return []

    bucket_exact_code = []
    bucket_exact_alias_or_name = []
    bucket_name_ru_starts = []
    bucket_word_or_alias_starts = []
    bucket_substr = []

    for item in items:
        code_lower = item["code"].lower()
        name_ru_norm = item["name_ru"].lower().replace("ё", "е")
        name_en_norm = item["name_en"].lower()
        aliases_norm = [a.lower().replace("ё", "е") for a in item["aliases"]]

        if code_lower == q:
            bucket_exact_code.append(item)
            continue

        if q == name_ru_norm or q == name_en_norm or any(q == a for a in aliases_norm):
            bucket_exact_alias_or_name.append(item)
            continue

        if name_ru_norm.startswith(q):
            bucket_name_ru_starts.append(item)
            continue

        import re

        words_ru = re.split(r"[\s\-_—,()]+", name_ru_norm)
        words_en = re.split(r"[\s\-_—,()]+", name_en_norm)
        if (
            any(w.startswith(q) for w in words_ru if w)
            or any(a.startswith(q) for a in aliases_norm)
            or name_en_norm.startswith(q)
            or any(w.startswith(q) for w in words_en if w)
            or code_lower.startswith(q)
        ):
            bucket_word_or_alias_starts.append(item)
            continue

        if (
            q in name_ru_norm
            or q in name_en_norm
            or any(q in a for a in aliases_norm)
            or q in code_lower
        ):
            bucket_substr.append(item)

    bucket_exact_alias_or_name.sort(key=lambda x: x["name_ru"])
    bucket_name_ru_starts.sort(key=lambda x: x["name_ru"])
    bucket_word_or_alias_starts.sort(key=lambda x: x["name_ru"])
    bucket_substr.sort(key=lambda x: x["name_ru"])

    return (
        bucket_exact_code
        + bucket_exact_alias_or_name
        + bucket_name_ru_starts
        + bucket_word_or_alias_starts
        + bucket_substr
    )


def test_search_ka_finds_all_required_examples():
    results = search_ranking("Ка", COUNTRY_CATALOG)
    found_codes = {c["code"] for c in results}

    # Kazakhstan, Canada, Cambodia, Cameroon, Qatar, Cabo Verde
    required = {"KZ", "CA", "KH", "CM", "QA", "CV"}
    assert required.issubset(found_codes)

    # Check top results order (Russian names starting with Ка)
    top_codes = [c["code"] for c in results[:7]]
    for req in ["KZ", "CA", "KH", "CM", "QA", "CV"]:
        assert req in top_codes


def test_search_aliases_and_exact_code():
    # 1. Alias 'США' -> US
    res_usa = search_ranking("США", COUNTRY_CATALOG)
    assert res_usa[0]["code"] == "US"

    # 2. English name 'Canada' -> CA
    res_ca = search_ranking("Canada", COUNTRY_CATALOG)
    assert res_ca[0]["code"] == "CA"

    # 3. Exact ISO code 'ca' -> CA
    res_code_ca = search_ranking("ca", COUNTRY_CATALOG)
    assert res_code_ca[0]["code"] == "CA"


# ============================================================================
# 4. UI Template & DOM Structure
# ============================================================================


def test_creator_template_has_geo_combobox_and_tooltips(client: TestClient):
    response = client.get("/creator")
    assert response.status_code == 200
    html = response.text

    # Label: GEO (no technical standard in label)
    assert 'GEO <span class="required">*</span>' in html
    # Placeholder: Название страны или код
    assert 'placeholder="Название страны или код"' in html
    # Helper text: Можно выбрать несколько стран
    assert "Можно выбрать несколько стран" in html

    # Combobox elements
    assert 'id="geoPickerBox"' in html
    assert 'id="geoTagsContainer"' in html
    assert 'id="geoSearchInput"' in html
    assert 'id="geoDropdown"' in html
    assert 'id="campaignGeo"' in html

    # ARIA roles
    assert 'role="combobox"' in html
    assert 'role="listbox"' in html
    assert 'aria-autocomplete="list"' in html

    # Preview Russian descriptions
    assert "Фильтр по странам" in html
    assert "Основной поток" in html
    assert "Страны не выбраны" in html

    # Reusable tooltip script and preview usage
    assert "/static/js/tooltip.js" in html
    assert 'data-tooltip="Посетители из выбранных стран перенаправляются на Google"' in html


def test_frontend_node_suite_runs():
    import shutil
    import subprocess
    from pathlib import Path

    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js not installed in environment")

    js_test_file = Path(__file__).parent / "test_frontend_geo.js"
    assert js_test_file.exists()

    result = subprocess.run([node_bin, str(js_test_file)], capture_output=True, text=True)
    assert result.returncode == 0, f"Frontend node tests failed:\n{result.stderr}\n{result.stdout}"
    assert "All frontend unit tests passed successfully!" in result.stdout
