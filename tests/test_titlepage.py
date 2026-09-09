"""The title page is the ground truth for faculty, credits and degree."""

from skemman_scraper.titlepage_load import (
    PAGE_MARKER,
    _is_stale,
    _split_marker,
    item_url,
    parse_titlepage,
)

# An HI title page, as pypdf lays it out: one field per line.
HI_PAGE = """Mid-Holocene eruptive activity in the Hekla volcanic system
Daniel Freyr Jonsson
60 ECTS thesis submitted in partial fulfillment of a
Magister Scientiarum degree in Geology
MS Committee
Esther Ruth Gudmundsdottir
Bergrun Arna Oladottir
Olgeir Sigmarsson
Master's Examiner
Magnus A Sigurgeirsson
Faculty of Earth Sciences
School of Engineering and Natural Sciences
University of Iceland
Reykjavik, May 2018
"""

# An Icelandic page names the deild directly and never says "Faculty of".
IS_PAGE = """Samanburður á ólínulegri og línulegri jarðskjálftagreiningu
Ástmar Karl Steinarsson
30 eininga ritgerð til meistaraprófs (MSc) í byggingarverkfræði
Leiðbeinendur
Bjarni Bessason
Haukur J. Eiríksson
Umhverfis- og byggingaverkfræðideild
Verkfræði- og náttúruvísindasvið
Háskóli Íslands
Júní 2009
"""


class TestParseTitlepage:
    def test_reads_the_stated_faculty_and_school(self):
        out = parse_titlepage(HI_PAGE)
        assert out["faculty"] == "Earth Sciences"
        assert out["school"] == "Engineering and Natural Sciences"

    def test_credits_and_degree(self):
        out = parse_titlepage(HI_PAGE)
        # ECTS appears nowhere in Skemman's metadata; the title page is the
        # only place it is stated.
        assert out["ects"] == 60
        assert out["degree"] == "Magister Scientiarum"

    def test_subject_stops_at_the_next_heading(self):
        # "degree in Geology" is followed by "MS Committee" on the next line;
        # the subject must not run on into it.
        assert parse_titlepage(HI_PAGE)["subject"] == "Geology"

    def test_committee_members(self):
        out = parse_titlepage(HI_PAGE)
        assert out["committee"] == (
            "Esther Ruth Gudmundsdottir; Bergrun Arna Oladottir; Olgeir Sigmarsson"
        )

    def test_committee_stops_before_the_examiner(self):
        assert "Magnus" not in parse_titlepage(HI_PAGE)["committee"]

    def test_year(self):
        assert parse_titlepage(HI_PAGE)["year_on_page"] == 2018

    def test_icelandic_page_gives_deild_not_faculty(self):
        out = parse_titlepage(IS_PAGE)
        assert "faculty" not in out
        assert out["deild"] == "Umhverfis- og byggingaverkfræðideild"
        assert out["svid"] == "Verkfræði- og náttúruvísindasvið"

    def test_icelandic_advisors_do_not_swallow_the_deild(self):
        # The deild line follows the advisor names; it is an institution, not
        # a person, and must not be read as one.
        advisors = parse_titlepage(IS_PAGE)["advisors"]
        assert advisors == "Bjarni Bessason; Haukur J. Eiríksson"

    def test_empty_input(self):
        assert parse_titlepage("") == {}


class TestSplitMarker:
    def test_reads_both_counts(self):
        text, pages, kept = _split_marker(
            f"{PAGE_MARKER}106\t20\nfyrsta lina\nonnur lina"
        )
        assert (pages, kept) == (106, 20)
        assert text == "fyrsta lina\nonnur lina"

    def test_old_marker_has_no_kept_count(self):
        # Caches written before the second field cannot say how much was kept.
        text, pages, kept = _split_marker(f"{PAGE_MARKER}106\nhali")
        assert (pages, kept) == (106, None)
        assert text == "hali"

    def test_scanned_pdf_has_a_count_but_no_text(self):
        text, pages, kept = _split_marker(f"{PAGE_MARKER}42\t20\n")
        assert (pages, kept) == (42, 20)
        assert text == ""

    def test_text_without_a_marker(self):
        text, pages, kept = _split_marker("engin merking hér")
        assert pages is None and kept is None
        assert text == "engin merking hér"

    def test_unparseable_marker(self):
        text, pages, kept = _split_marker(f"{PAGE_MARKER}ekkitala\nhali")
        assert pages is None and kept is None
        assert text == "hali"


class TestIsStale:
    def test_a_short_cache_is_stale(self):
        assert _is_stale(106, 8, 20) is True

    def test_a_full_cache_is_not(self):
        assert _is_stale(106, 20, 20) is False

    def test_a_document_shorter_than_the_window_is_not(self):
        # Six pages kept out of a six-page thesis is everything there is.
        assert _is_stale(6, 6, 20) is False

    def test_an_old_cache_is_stale_because_it_cannot_say(self):
        assert _is_stale(106, None, 20) is True


def test_item_url_matches_the_handle():
    # The same id keys the database row, the cached files and the handle URL.
    assert item_url(10688) == "https://skemman.is/handle/1946/10688"
