__version__ = "0.0.3"

# The oldest CPython jimemo supports, as (major, minor, micro). It is a
# THREE-component floor on purpose: what jimemo needs landed in patch
# releases, so "3.13" would admit interpreters that still disagree with a
# browser's HTML tokenizer, and lint's self-containment check would then
# judge different markup than the browser applies (jimemo#y9p8,
# jimemo#gaga). Measured, per release:
#
#   3.13.0, 3.13.3   html.parser decodes a semicolonless character
#                    reference inside an attribute (CPython gh-69426) AND
#                    drops the text of an unclosed <style> (gh-86155) --
#                    jimemo's own suite fails on 3.13.3
#   3.13.4, 3.13.5   both fixed; <div title==""id id=grad> still splits
#                    where a browser does not
#   3.13.6           all three match a browser
#
# So the floor is 3.13.6. A version number is only the contract a human can
# act on, never the guarantee -- a distro can backport or revert either
# fix -- so src/jimemo/lint.py MEASURES the running parser at import and
# refuses to load if it disagrees. Keep this constant, the ./jimemo
# launcher's literal, install.sh's comparison and the CI matrix in step;
# tests/test_python_floor.py pins all four to this one value.
PYTHON_FLOOR = (3, 13, 6)
