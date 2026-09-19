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
# So the floor is 3.13.6, and only FINAL releases of it: 3.14.0b1 compares
# (3, 14, 0) >= (3, 13, 6) while failing all three checks above, and it let
# jimemo#y9p8's payload lint clean (measured; gh-69426 landed in 3.14.0b2).
# Every boundary therefore checks sys.version_info[3] == "final" too.
#
# This number is the contract; it cannot see a
# distro that backported one of these fixes into an older release or
# reverted one in a newer release. What detects a parser that misbehaves at
# or above the floor is the test suite -- the 318-case canary and the nine
# jimemo#y9p8 payload vectors in tests/test_lint.py -- and
# src/jimemo/_parser_floor.py records why runtime probing was tried and
# dropped, plus what the floor does and does not fix. Keep this constant, the ./jimemo
# launcher's literal, install.sh's comparison and the CI matrix in step;
# tests/test_python_floor.py pins all four to this one value.
PYTHON_FLOOR = (3, 13, 6)
