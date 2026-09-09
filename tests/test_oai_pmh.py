from pathlib import Path

from skemman_scraper.oai_pmh import (
    build_oai_pmh_url,
    oai_cache_path,
    oai_checkpoint_path,
    parse_oai_pmh_records,
    set_spec_from_location,
)

OAI_PAGE = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"
    xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
    xmlns:dc="http://purl.org/dc/elements/1.1/">
  <ListRecords>
    <record>
      <header>
        <identifier>oai:skemman.is:1946/30550</identifier>
        <datestamp>2018-10-15T15:55:08Z</datestamp>
        <setSpec>com_1946_2064</setSpec>
      </header>
      <metadata>
        <oai_dc:dc>
          <dc:title>QM/MM studies of molybdenum and vanadium nitrogenase</dc:title>
          <dc:creator>Barði Benediktsson 1992-</dc:creator>
          <dc:creator>Annar Höfundur</dc:creator>
          <dc:subject>Nitrogenase</dc:subject>
          <dc:subject>Meistaraprófsritgerðir</dc:subject>
          <dc:description>English abstract text.</dc:description>
          <dc:contributor>Háskóli Íslands</dc:contributor>
          <dc:type>Thesis</dc:type>
          <dc:type>Master's</dc:type>
          <dc:relation>https://example.org/source</dc:relation>
          <dc:date>2018-05</dc:date>
          <dc:identifier>http://hdl.handle.net/1946/30550</dc:identifier>
        </oai_dc:dc>
      </metadata>
    </record>
    <record>
      <header status="deleted">
        <identifier>oai:skemman.is:1946/1</identifier>
      </header>
    </record>
    <resumptionToken completeListSize="1268" cursor="0">next-token</resumptionToken>
  </ListRecords>
</OAI-PMH>
"""


def test_location_handle_maps_to_community_set():
    assert set_spec_from_location("1946/2064") == "com_1946_2064"
    assert set_spec_from_location("1946 6870") == "com_1946_6870"
    assert set_spec_from_location("col_1946_2107") == "col_1946_2107"


def test_builds_first_page_url():
    assert build_oai_pmh_url(
        "https://skemman.is",
        set_spec="com_1946_2064",
    ) == "https://skemman.is/oai/request?verb=ListRecords&metadataPrefix=oai_dc&set=com_1946_2064"


def test_resumption_token_url_uses_only_token():
    assert build_oai_pmh_url(
        "https://skemman.is",
        resumption_token="oai_dc/0",
    ) == "https://skemman.is/oai/request?verb=ListRecords&resumptionToken=oai_dc%2F0"


def test_oai_cache_path_uses_set_and_xml_extension():
    assert oai_cache_path(
        Path("cache"),
        metadata_prefix="oai_dc",
        set_spec="com_1946_2064",
        offset=100,
    ) == Path("cache/oai_dc_com_1946_2064_000100.xml")


def test_oai_cache_path_sanitizes_set_name():
    assert oai_cache_path(
        Path("cache"),
        metadata_prefix="oai dc",
        set_spec="1946/2064",
        offset=0,
    ) == Path("cache/oai_dc_1946_2064_000000.xml")


def test_oai_checkpoint_path_uses_set_name():
    assert oai_checkpoint_path(
        Path("cache"),
        metadata_prefix="oai_dc",
        set_spec="com_1946_2064",
    ) == Path("cache/oai_dc_com_1946_2064.checkpoint.json")


def test_parses_records_and_resumption_token():
    rows, token = parse_oai_pmh_records(OAI_PAGE)
    assert token == "next-token"
    assert rows == [
        {
            "id": 30550,
            "date_accepted": "2018-05-01",
            "title": "QM/MM studies of molybdenum and vanadium nitrogenase",
            "authors": "Barði Benediktsson 1992-; Annar Höfundur",
            "subjects": ["Nitrogenase", "Meistaraprófsritgerðir"],
            "descriptions": ["English abstract text."],
            "contributors": ["Háskóli Íslands"],
            "types": ["Thesis", "Master's"],
            "relations": ["https://example.org/source"],
        }
    ]
