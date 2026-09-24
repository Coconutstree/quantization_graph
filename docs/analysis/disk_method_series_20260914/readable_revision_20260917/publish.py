"""Apply the three text replacements, retaining title and image block identities.

Every mutation is revision guarded and followed by a full read. Saved original
blocks are checked before replacement/deletion, so concurrent edits stop the run.
Usage: python .../publish.py 04   (or 05)
"""
from pathlib import Path
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
KEYS = {"04": "04_og_lvq", "05": "05_glass_nsg"}


def tree(doc):
    return ET.fromstring("<doc>" + doc["content"] + "</doc>")


def serial(node):
    return ET.tostring(node, encoding="unicode")


def normalized_text(root):
    return re.sub(r"\s+", "", "".join(root.itertext()))


def cli(args, output):
    result = subprocess.run(["lark-cli", "docs", *args, "--as", "user"],
                            cwd=REPO, capture_output=True, text=True, timeout=60)
    (HERE / output).write_text(result.stdout)
    (HERE / (output + ".stderr")).write_text(result.stderr)
    if result.returncode:
        raise RuntimeError(f"CLI failed ({result.returncode}); see {output}.stderr")
    payload = json.loads(result.stdout)
    if not payload.get("ok"):
        raise RuntimeError(f"CLI returned an error; see {output}")
    data = payload["data"]
    if data.get("result", "success") != "success" or data.get("warnings"):
        raise RuntimeError(f"Review partial result or warnings: {output}")
    return data


def publish(short):
    key = KEYS[short]
    before = json.loads((HERE / f"{short}_before.json").read_text())["data"]["document"]
    token = before["document_id"]
    original = tree(before)
    groups = [[]]
    images = []
    for node in original:
        if node.tag == "title":
            continue
        if node.tag == "img":
            images.append((node.attrib["id"], node.attrib["src"]))
            groups.append([])
        else:
            # The source has no other resources. Fail closed if that changes.
            assert node.tag in ("p", "h1", "h2", "table", "ol", "ul"), node.tag
            groups[-1].append(node)
    assert len(groups) == 3 and all(groups) and len(images) == 2

    def fetch(label):
        doc = cli(["+fetch", "--doc", token, "--detail", "full"],
                  f"{short}_{label}.json")["document"]
        current_images = [(e.attrib["id"], e.attrib["src"]) for e in tree(doc).findall("img")]
        assert current_images == images, "Existing image block or token changed"
        return doc

    current = fetch("preflight")
    assert normalized_text(tree(current)) == normalized_text(original), "Document changed since drafting"
    assert current["revision_id"] == before["revision_id"], "Document revision changed since drafting"
    obsolete = []
    for index, group in enumerate(groups):
        by_id = {e.attrib.get("id"): e for e in tree(current)}
        for old in group:
            assert serial(by_id[old.attrib["id"]]) == serial(old), "Source block changed"
        path = HERE / f"{short}_part{index}.xml"
        cli(["+update", "--doc", token, "--command", "block_replace",
             "--block-id", group[0].attrib["id"], "--revision-id", str(current["revision_id"]),
             "--doc-format", "xml", "--content", "@./" + str(path.relative_to(REPO))],
            f"{short}_replace{index}.json")
        current = fetch(f"after_replace{index}")
        obsolete.extend(group[1:])
        print(f"{short}: text segment {index + 1}/3 verified, revision {current['revision_id']}", flush=True)

    by_id = {e.attrib.get("id"): e for e in tree(current)}
    for old in obsolete:
        assert serial(by_id[old.attrib["id"]]) == serial(old), "Obsolete source block changed"
    cli(["+update", "--doc", token, "--command", "block_delete",
         "--block-id", ",".join(e.attrib["id"] for e in obsolete),
         "--revision-id", str(current["revision_id"])], f"{short}_delete_old.json")
    current = fetch("after")
    expected = ET.fromstring("<doc>" + (HERE.parent / f"{key}.xml").read_text() + "</doc>")
    actual = tree(current)
    assert normalized_text(actual) == normalized_text(expected), "Final text differs from local source"
    assert len(actual.findall("h1")) == 2
    assert len(actual.findall("img")) == 2
    report = {
        "document_id": token,
        "url": "https://my.feishu.cn/docx/" + token,
        "revision_before": before["revision_id"],
        "revision_after": current["revision_id"],
        "body_text_matches_local": True,
        "image_block_ids_and_tokens_preserved": images,
        "top_level_parts": [e.text for e in actual.findall("h1")],
        "tables": len(actual.findall("table")),
        "old_text_blocks_deleted": len(obsolete),
    }
    (HERE / f"{short}_verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    publish(sys.argv[1])
