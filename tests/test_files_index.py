"""The item page's file table is what plans the download queue."""

from skemman_scraper.files_index import parse_file_table, parse_size

ITEM_HTML = """
<div class="dataTable t-data-grid"><table class="t-data-grid">
<thead><tr class="c1">
  <th id="t1" class="name">Skráarnafn</th><th id="t2" class="desc">Stærð</th>
  <th id="t3" class="size">Aðgangur</th><th id="t4">Lýsing</th><th id="t5">Skráartegund</th>
  <th>&nbsp;</th>
</tr></thead>
<tbody>
  <tr class="c0">
    <td headers="t1">Ritgerd.pdf</td><td headers="t2">86,08 MB</td>
    <td headers="t3">Opinn</td><td headers="t4">Heildartexti</td><td headers="t5">PDF</td>
    <td><a class="btn" href="/bitstream/1946/12546/1/Ritgerd.pdf">Skoða/Opna</a></td>
  </tr>
  <tr class="c1">
    <td headers="t1">Yfirlysing.pdf</td><td headers="t2">302,58 KB</td>
    <td headers="t3">Lokaður</td><td headers="t4">Yfirlýsing</td><td headers="t5">PDF</td>
    <td><a class="btn" href="/bitstream/1946/12546/2/Yfirlysing.pdf">Skoða/Opna</a></td>
  </tr>
</tbody>
</table></div>
"""


class TestParseSize:
    def test_icelandic_decimal_comma(self):
        # 86,08 MB, not 86.08 -- Skemman writes sizes the Icelandic way.
        assert parse_size("86,08 MB") == int(86.08 * 1024**2)

    def test_units(self):
        assert parse_size("512 B") == 512
        assert parse_size("1,5 KB") == int(1.5 * 1024)
        assert parse_size("2 GB") == 2 * 1024**3

    def test_case_and_spacing(self):
        assert parse_size("  4,00 mb  ") == 4 * 1024**2

    def test_unparseable(self):
        assert parse_size("") is None
        assert parse_size("unknown") is None
        assert parse_size(None) is None


class TestParseFileTable:
    def test_reads_every_column(self):
        rows = parse_file_table(ITEM_HTML)
        assert len(rows) == 2
        first = rows[0]
        assert first["filename"] == "Ritgerd.pdf"
        assert first["size_label"] == "86,08 MB"
        assert first["access"] == "Opinn"
        assert first["filetype"] == "PDF"
        assert first["href"] == "/bitstream/1946/12546/1/Ritgerd.pdf"

    def test_keeps_closed_files_so_they_can_be_skipped(self):
        # The closed row has to survive parsing: knowing a file is closed is
        # what lets the loader avoid requesting it.
        rows = parse_file_table(ITEM_HTML)
        assert rows[1]["access"] == "Lokaður"

    def test_ignores_tables_that_are_not_the_file_table(self):
        other = '<table class="t-data-grid"><thead><tr><th>Ár</th></tr></thead>' \
                "<tbody><tr><td>2018</td></tr></tbody></table>"
        assert parse_file_table(other) == []

    def test_no_table_at_all(self):
        assert parse_file_table("<html><body>ekkert hér</body></html>") == []
