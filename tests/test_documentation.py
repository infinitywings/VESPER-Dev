"""Check repository navigation and GitHub form structure without network access."""

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = [*ROOT.glob("*.md"), *ROOT.joinpath("docs").glob("*.md"), ROOT / "data/README.md"]


def test_documentation_local_links_exist():
    missing = []
    for document in DOCUMENTS:
        # Code examples are not navigation links.
        text = re.sub(r"```[^\n]*\n.*?```", "", document.read_text(), flags=re.DOTALL)
        for match in re.finditer(r"\[[^\]]*\]\(([^)]+)\)", text):
            target = match.group(1)
            parsed = urlsplit(target)
            if parsed.scheme or target.startswith("#"):
                continue
            resolved = document.parent / unquote(parsed.path)
            if not resolved.exists():
                missing.append(f"{document.relative_to(ROOT)}: {target}")
    assert not missing, "Missing local links: " + "; ".join(missing)


def test_documentation_fences_are_balanced():
    for document in DOCUMENTS:
        fences = re.findall(r"^```", document.read_text(), flags=re.MULTILINE)
        assert len(fences) % 2 == 0, document.relative_to(ROOT)


def test_github_issue_forms_have_required_fields_and_unique_ids():
    forms = list(ROOT.joinpath(".github/ISSUE_TEMPLATE").glob("*.yml"))
    assert len(forms) == 2
    names = []
    for path in forms:
        form = yaml.safe_load(path.read_text())
        assert all(form.get(key) for key in ("name", "description", "body")), path.name
        names.append(form["name"])
        ids = [entry["id"] for entry in form["body"] if "id" in entry]
        assert len(ids) == len(set(ids)), path.name
        for entry in form["body"]:
            assert entry["type"] in {"markdown", "input", "textarea", "dropdown", "checkboxes"}
            assert entry.get("attributes"), path.name
    assert len(names) == len(set(names))


def test_software_citation_preserves_manuscript_identity():
    citation = yaml.safe_load(ROOT.joinpath("CITATION.cff").read_text())
    assert citation["title"] == "VESPER"
    preferred = citation["preferred-citation"]
    assert preferred["type"] == "unpublished"
    assert preferred["title"].endswith("with Cross-Layer Observability")
