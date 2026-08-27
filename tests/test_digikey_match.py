from backend.services.digikey import _find_product


def test_find_product_accepts_underscore_vs_slash():
    products = [{"ManufacturerProductNumber": "25AA1024-I/SM", "DatasheetUrl": "http://a.pdf"}]
    picked = _find_product("25AA1024-I_SM", products)
    assert picked is not None
    assert picked["DatasheetUrl"] == "http://a.pdf"


def test_find_product_accepts_family_orderable():
    products = [
        {"ManufacturerProductNumber": "24AA025E64-I/SN", "DatasheetUrl": "http://b.pdf"},
    ]
    picked = _find_product("24AA025E64", products)
    assert picked is not None
    assert picked["DatasheetUrl"] == "http://b.pdf"


def test_find_product_rejects_unrelated():
    products = [{"ManufacturerProductNumber": "GRM21BR61A106KE19L", "DatasheetUrl": "http://c.pdf"}]
    assert _find_product("10uF", products) is None
    assert _find_product("CH340", [{"ManufacturerProductNumber": "CH340E"}]) is None
