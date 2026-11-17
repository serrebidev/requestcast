from pathlib import Path


def test_base_template_footer_lists_every_project_link():
    html = (
        Path(__file__).resolve().parents[1]
        / "requestcast"
        / "templates"
        / "base.html"
    ).read_text()
    for href in [
        "https://github.com/serrebidev/requestcast",
        "https://git.serrebiradio.com/serrebi/requestcast",
        "https://projects.serrebiradio.com",
        "https://t.me/SerrebiProjects",
        "https://git.serrebiradio.com/serrebi/requestcast/src/branch/main/CREDITS.md",
    ]:
        assert href in html, href
