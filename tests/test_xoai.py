"""Tests for the xoai bundle listing. No network, no database."""

import xml.etree.ElementTree as ET

from skemman_scraper.xoai import (
    assign_roles,
    parse_xoai_bitstreams,
    parse_xoai_records,
)

HEADER = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
    "<ListRecords>"
)
FOOTER = "</ListRecords></OAI-PMH>"


def bitstream(name, description=None, size=1000, sid=1, mime="application/pdf"):
    parts = [
        '<element name="bitstream">',
        '<field name="name">' + name + "</field>",
    ]
    if description is not None:
        parts.append('<field name="description">' + description + "</field>")
    parts += [
        '<field name="format">' + mime + "</field>",
        '<field name="size">' + str(size) + "</field>",
        '<field name="url">https://skemman.is/bitstream/1946/1/' + str(sid) + "/x.pdf</field>",
        '<field name="sid">' + str(sid) + "</field>",
        "</element>",
    ]
    return "".join(parts)


def record(handle="1946/53215", bitstreams=(), extra="", bundle="ORIGINAL"):
    return (
        "<record><header><identifier>oai:skemman.is:" + handle + "</identifier></header>"
        "<metadata>"
        '<metadata xmlns="http://www.lyncode.com/xoai">'
        + extra
        + '<element name="bundles"><element name="bundle">'
        '<field name="name">' + bundle + "</field>"
        '<element name="bitstreams">' + "".join(bitstreams) + "</element>"
        "</element></element>"
        '<element name="others"><field name="handle">' + handle + "</field></element>"
        "</metadata></metadata></record>"
    )


def bundles_of(xml):
    """The parsed bitstream list of the first record in a document."""
    root = ET.fromstring(xml)
    metadata = root.find(".//{http://www.lyncode.com/xoai}metadata")
    return parse_xoai_bitstreams(metadata)


class TestBitstreams:
    def test_reads_name_size_type_and_url(self):
        xml = (
            HEADER
            + record(bitstreams=[bitstream("thesis.pdf", "COMPLETE_TEXT", 271244)])
            + FOOTER
        )
        (entry,) = bundles_of(xml)
        assert entry["filename"] == "thesis.pdf"
        assert entry["size_bytes"] == 271244
        assert entry["filetype"] == "PDF"
        assert entry["url"].startswith("https://skemman.is/bitstream/")
        assert entry["sid"] == 1

    def test_dspace_descriptions_become_the_icelandic_labels(self):
        xml = HEADER + record(
            bitstreams=[
                bitstream("thesis.pdf", "COMPLETE_TEXT"),
                bitstream("form.pdf", "DECLARATION", sid=2),
            ]
        ) + FOOTER
        assert [e["description"] for e in bundles_of(xml)] == ["Heildartexti", "Yfirlýsing"]

    def test_a_description_dspace_does_not_own_is_kept_as_written(self):
        xml = HEADER + record(bitstreams=[bitstream("m.pdf", "Meginmál")]) + FOOTER
        assert bundles_of(xml)[0]["description"] == "Meginmál"

    def test_only_the_original_bundle_counts(self):
        xml = HEADER + record(
            bitstreams=[bitstream("license.txt", mime="text/plain")], bundle="LICENSE"
        ) + FOOTER
        assert bundles_of(xml) == []


class TestAssignRoles:
    def test_complete_text_wins_over_a_larger_declaration(self):
        # The case that broke size-based selection: 53215's declaration form is
        # a 1.5 MB scan next to a 265 kB thesis.
        files = assign_roles(
            [
                {"filename": "t.pdf", "description": "Heildartexti", "filetype": "PDF",
                 "size_bytes": 271244, "sid": 1},
                {"filename": "y.pdf", "description": "Yfirlýsing", "filetype": "PDF",
                 "size_bytes": 1543808, "sid": 2},
            ]
        )
        assert [f["role"] for f in files] == ["primary", "secondary"]

    def test_without_a_full_text_the_largest_non_declaration_pdf_wins(self):
        files = assign_roles(
            [
                {"filename": "Yfirlýsing um meðferð lokaverkefna.pdf", "description": None,
                 "filetype": "PDF", "size_bytes": 9_000_000, "sid": 1},
                {"filename": "ritgerd.pdf", "description": None, "filetype": "PDF",
                 "size_bytes": 500_000, "sid": 2},
                {"filename": "kafli.pdf", "description": None, "filetype": "PDF",
                 "size_bytes": 100_000, "sid": 3},
            ]
        )
        assert [f["role"] for f in files] == ["secondary", "primary", "secondary"]

    def test_a_lone_file_is_the_thesis(self):
        (entry,) = assign_roles(
            [{"filename": "a.pdf", "description": None, "filetype": "PDF",
              "size_bytes": 10, "sid": 1}]
        )
        assert entry["role"] == "primary"

    def test_a_swapped_pair_is_read_by_the_filenames(self):
        # 35842: the submitter attached the two files the wrong way round, so
        # DSpace has the declaration as COMPLETE_TEXT and the thesis as the
        # declaration. Both names say plainly which is which.
        files = assign_roles(
            [
                {"filename": "Mesay Fekadu Biru Thesis.pdf", "description": "Yfirlýsing",
                 "filetype": "PDF", "size_bytes": 5_411_834, "sid": 1},
                {"filename": "declaration of access.pdf", "description": "Heildartexti",
                 "filetype": "PDF", "size_bytes": 374_653, "sid": 2},
            ]
        )
        assert [f["role"] for f in files] == ["primary", "secondary"]

    def test_an_abbreviated_form_name_is_still_a_form(self):
        # 41556: "yfirl.pdf", with no description at all.
        files = assign_roles(
            [
                {"filename": "Lokaritger____27_ma_.pdf", "description": "Yfirlýsing",
                 "filetype": "PDF", "size_bytes": 3_845_327, "sid": 1},
                {"filename": "yfirl.pdf", "description": None,
                 "filetype": "PDF", "size_bytes": 342_476, "sid": 2},
            ]
        )
        assert [f["role"] for f in files] == ["primary", "secondary"]

    def test_a_thesis_merely_named_after_the_repository_is_kept(self):
        (entry,) = assign_roles(
            [{"filename": "skemman - bs.pdf", "description": "Heildartexti",
              "filetype": "PDF", "size_bytes": 100, "sid": 1}]
        )
        assert entry["role"] == "primary"

    def test_an_item_of_nothing_but_forms_has_no_primary(self):
        files = assign_roles(
            [{"filename": "Yfirlysing.pdf", "description": "Yfirlýsing", "filetype": "PDF",
              "size_bytes": 10, "sid": 1}]
        )
        assert [f["role"] for f in files] == ["secondary"]


class TestParseRecords:
    def test_reads_the_handle_advisors_and_degree(self):
        extra = (
            '<element name="dc">'
            '<element name="description"><element name="advisor"><element name="none">'
            '<field name="value">Tómas Philip Rúnarsson 1968-</field>'
            '<field name="value">Helga Ingimundardóttir 1985-</field>'
            "</element></element></element>"
            '<element name="type"><element name="degree"><element name="none">'
            '<field name="value">Master\'s</field>'
            "</element></element></element>"
            "</element>"
        )
        xml = (
            HEADER
            + record(extra=extra, bitstreams=[bitstream("t.pdf", "COMPLETE_TEXT")])
            + FOOTER
        )
        (row,), token = parse_xoai_records(xml)
        assert row["id"] == 53215
        assert row["degree"] == "Master's"
        assert row["advisors"] == [
            "Tómas Philip Rúnarsson 1968-",
            "Helga Ingimundardóttir 1985-",
        ]
        assert token is None

    def test_resumption_token_is_returned(self):
        xml = (
            HEADER
            + record(bitstreams=[bitstream("t.pdf")])
            + "<resumptionToken>xoai///com_1946_2064/100</resumptionToken>"
            + FOOTER
        )
        _, token = parse_xoai_records(xml)
        assert token == "xoai///com_1946_2064/100"

    def test_no_records_matched_is_not_an_error(self):
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
            '<error code="noRecordsMatch">nothing</error></OAI-PMH>'
        )
        assert parse_xoai_records(xml) == ([], None)
