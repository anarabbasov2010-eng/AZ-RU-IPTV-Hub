from scripts.pipeline import canon, clean_name, categories
def test_canon():
    assert canon("HTTPS://Example.COM:443/a#x")=="https://example.com/a"
def test_name():
    assert clean_name("  AzTV   Live ")=="AzTV Live"
def test_categories():
    cfg={"category_keywords":{"sports":["sport","football"],"movies":["movie"]}}
    assert "sports" in categories("Football HD","",cfg)
