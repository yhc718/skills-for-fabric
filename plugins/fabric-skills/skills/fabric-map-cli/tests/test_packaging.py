import json
from pathlib import Path
import re
import unittest


ROOT = next(parent for parent in Path(__file__).resolve().parents
            if (parent / "package.json").is_file())
SOURCE = ROOT / "skills" / "fabric-map-cli"
PLUGIN = ROOT / "plugins" / "fabric-skills"
PACKAGED = PLUGIN / "skills" / "fabric-map-cli"


def files(root):
    return {path.relative_to(root): path for path in root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"}


class PackagingTests(unittest.TestCase):
    def test_plugin_copy_matches_source(self):
        source, packaged = files(SOURCE), files(PACKAGED)
        self.assertEqual(set(source), set(packaged))
        for relative, path in source.items():
            with self.subTest(path=relative):
                self.assertEqual(path.read_bytes(), packaged[relative].read_bytes())

    def test_learn_links_are_locale_neutral(self):
        for root in (SOURCE, PACKAGED):
            for path in root.rglob("*.md"):
                with self.subTest(path=path):
                    self.assertNotRegex(
                        path.read_text(encoding="utf-8"),
                        r"https://learn\.microsoft\.com/[a-z]{2}-[a-z]{2}/",
                    )

    def test_relative_documentation_links_resolve(self):
        for root in (SOURCE, PACKAGED):
            for path in root.rglob("*.md"):
                for target in re.findall(r"\[[^\]]+\]\(([^)\s]+)\)", path.read_text(encoding="utf-8")):
                    if "://" in target or target.startswith("#"):
                        continue
                    with self.subTest(path=path, target=target):
                        self.assertTrue((path.parent / target.split("#", 1)[0]).is_file())

    def test_marketplace_and_plugin_registrations_are_preserved(self):
        for host in (Path(".github") / "plugin", Path(".claude-plugin")):
            marketplace = json.loads((ROOT / host / "marketplace.json").read_text(encoding="utf-8"))
            entries = [entry for entry in marketplace["plugins"] if entry["name"] == "fabric-skills"]
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["source"], "./plugins/fabric-skills")
            manifest = json.loads((PLUGIN / host / "plugin.json").read_text(encoding="utf-8"))
            for entry in (entries[0], manifest):
                with self.subTest(host=host, name=entry["name"]):
                    self.assertEqual(entry["skills"].count("./skills/fabric-map-cli"), 1)


if __name__ == "__main__":
    unittest.main()
